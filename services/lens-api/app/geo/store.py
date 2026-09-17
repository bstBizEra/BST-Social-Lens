"""Persistence for geography (001D): admin-version import, gazetteer loading, resolved locations, stats.

Append-only except `geo.admin_versions.is_current` (a pointer, not evidence). No L0/L1 statements.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .gazetteer import Gazetteer
from .resolver import Resolution


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


class GeoStore:
    def __init__(self, db: Any) -> None:
        self.db = db
        self._gaz: Gazetteer | None = None
        self._gaz_version: str | None = None

    # ------------------------------------------------------------ admin versions

    async def import_admin(self, payload: dict[str, Any], source_ref: str, make_current: bool = True) -> dict[str, Any]:
        """Import one admin version from a JSON payload {admin_version, provinces[], districts[], villages[], aliases[]?}.
        Existing versions are never modified; re-importing the same version id is rejected."""
        version = payload["admin_version"]
        checksum = hashlib.sha256(_j({k: payload[k] for k in ("provinces", "districts", "villages") if k in payload}).encode()).hexdigest()
        async with self.db.pool.acquire() as con:
            async with con.transaction():
                exists = await con.fetchval("SELECT 1 FROM geo.admin_versions WHERE admin_version=$1", version)
                if exists:
                    raise ValueError(f"admin_version {version} already imported (versions are immutable; use a new id)")
                await con.execute(
                    "INSERT INTO geo.admin_versions (admin_version, source_ref, checksum, note) VALUES ($1,$2,$3,$4)",
                    version, source_ref, checksum, payload.get("_note"),
                )
                for r in payload["provinces"]:
                    await con.execute(
                        "INSERT INTO geo.provinces (admin_version, code, name_lo, name_en, name_variants, centroid_lat, centroid_lng, geom_geojson) VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb)",
                        version, r["code"], r["name_lo"], r.get("name_en"), list(r.get("name_variants") or []), r.get("centroid_lat"), r.get("centroid_lng"), _j(r["geom"]) if r.get("geom") else None,
                    )
                for r in payload["districts"]:
                    await con.execute(
                        "INSERT INTO geo.districts (admin_version, code, province_code, name_lo, name_en, name_variants, centroid_lat, centroid_lng, geom_geojson) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)",
                        version, r["code"], r["province_code"], r["name_lo"], r.get("name_en"), list(r.get("name_variants") or []), r.get("centroid_lat"), r.get("centroid_lng"), _j(r["geom"]) if r.get("geom") else None,
                    )
                for r in payload["villages"]:
                    await con.execute(
                        "INSERT INTO geo.villages (admin_version, code, district_code, province_code, name_lo, name_en, name_variants, centroid_lat, centroid_lng, geom_geojson) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)",
                        version, r["code"], r["district_code"], r["province_code"], r["name_lo"], r.get("name_en"), list(r.get("name_variants") or []), r.get("centroid_lat"), r.get("centroid_lng"), _j(r["geom"]) if r.get("geom") else None,
                    )
                for a in payload.get("aliases") or []:
                    await con.execute(
                        "INSERT INTO geo.name_aliases (level, code, alias, source) VALUES ($1,$2,$3,$4) ON CONFLICT (level, code, alias) DO NOTHING",
                        a["level"], a["code"], a["alias"], a.get("source", "IMPORT"),
                    )
                if make_current:
                    await con.execute("UPDATE geo.admin_versions SET is_current = (admin_version = $1)", version)
        self._gaz = None  # reload on next use
        return {"admin_version": version, "checksum": checksum, "provinces": len(payload["provinces"]), "districts": len(payload["districts"]),
                "villages": len(payload["villages"]), "aliases": len(payload.get("aliases") or []), "is_current": make_current}

    async def add_alias(self, level: str, code: str, alias: str, source: str = "REVIEW") -> None:
        async with self.db.pool.acquire() as con:
            await con.execute("INSERT INTO geo.name_aliases (level, code, alias, source) VALUES ($1,$2,$3,$4) ON CONFLICT DO NOTHING", level, code, alias, source)
        self._gaz = None

    async def gazetteer(self) -> Gazetteer:
        """Current admin version as an in-memory gazetteer (cached until an import/alias invalidates it)."""
        if self._gaz is not None:
            return self._gaz
        async with self.db.pool.acquire() as con:
            version = await con.fetchval("SELECT admin_version FROM geo.admin_versions WHERE is_current LIMIT 1")
            if not version:
                self._gaz = Gazetteer(None)
                return self._gaz
            p = await con.fetch("SELECT code, name_lo, name_en, name_variants, centroid_lat, centroid_lng FROM geo.provinces WHERE admin_version=$1", version)
            d = await con.fetch("SELECT code, province_code, name_lo, name_en, name_variants, centroid_lat, centroid_lng FROM geo.districts WHERE admin_version=$1", version)
            v = await con.fetch("SELECT code, district_code, province_code, name_lo, name_en, name_variants, centroid_lat, centroid_lng FROM geo.villages WHERE admin_version=$1", version)
            a = await con.fetch("SELECT level, code, alias FROM geo.name_aliases")
        self._gaz = Gazetteer.from_rows(version, [dict(r) for r in p], [dict(r) for r in d], [dict(r) for r in v], [dict(r) for r in a])
        self._gaz_version = version
        return self._gaz

    # ------------------------------------------------------------ resolutions

    async def insert_resolutions(self, con: Any, observation_id: int, run_id: int, claim_ids: list[int], resolutions: list[Resolution], admin_version: str | None) -> int:
        """Called inside the observation's transaction (same connection) so an observation and its locations land together."""
        for r in resolutions:
            await con.execute(
                """INSERT INTO geo.resolved_locations
                   (observation_id, run_id, input_claim_ids, admin_version, province_code, district_code, village_code, lat, lng,
                    precision, point_source, confidence, resolver_method, resolver_version, is_primary, signals, review_status)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16::jsonb,$17)""",
                observation_id, run_id, [claim_ids[i] for i in r.input_claim_indexes], admin_version if (r.province_code or r.district_code or r.village_code) else None,
                r.province_code, r.district_code, r.village_code, r.lat, r.lng, r.precision, r.point_source, r.confidence,
                r.resolver_method, r.resolver_version, r.is_primary, _j(r.signals), r.review_status,
            )
        return len(resolutions)

    async def locations_for(self, observation_id: int) -> list[dict[str, Any]]:
        async with self.db.pool.acquire() as con:
            rows = await con.fetch("SELECT * FROM geo.resolved_locations WHERE observation_id=$1 ORDER BY is_primary DESC, confidence DESC", observation_id)
        return [_row(r) for r in rows]

    async def stats(self) -> dict[str, Any]:
        async with self.db.pool.acquire() as con:
            version = await con.fetchrow("SELECT admin_version, imported_at, source_ref FROM geo.admin_versions WHERE is_current LIMIT 1")
            counts = await con.fetchrow(
                """SELECT (SELECT count(*) FROM geo.provinces p JOIN geo.admin_versions v ON v.admin_version=p.admin_version AND v.is_current) AS provinces,
                          (SELECT count(*) FROM geo.districts d JOIN geo.admin_versions v ON v.admin_version=d.admin_version AND v.is_current) AS districts,
                          (SELECT count(*) FROM geo.villages w JOIN geo.admin_versions v ON v.admin_version=w.admin_version AND v.is_current) AS villages,
                          (SELECT count(*) FROM geo.name_aliases) AS aliases"""
            )
            prec = await con.fetch(
                """SELECT l.precision, count(*) AS n FROM geo.resolved_locations l
                   JOIN extract.current_observations o ON o.observation_id = l.observation_id WHERE l.is_primary GROUP BY 1 ORDER BY 2 DESC"""
            )
            totals = await con.fetchrow(
                """SELECT count(*) AS observations_with_location,
                          count(*) FILTER (WHERE l.lat IS NOT NULL) AS with_point,
                          count(*) FILTER (WHERE l.precision IN ('TEXT_ONLY','UNKNOWN')) AS unresolved,
                          count(*) FILTER (WHERE l.signals::text LIKE '%conflict%') AS conflicts,
                          count(*) FILTER (WHERE l.confidence IS NULL) AS without_confidence
                   FROM geo.resolved_locations l JOIN extract.current_observations o ON o.observation_id = l.observation_id WHERE l.is_primary"""
            )
            loc_claims = await con.fetchval(
                """SELECT count(DISTINCT c.observation_id) FROM extract.claims c JOIN extract.current_observations o ON o.observation_id=c.observation_id
                   WHERE c.field IN ('LOCATION_TEXT','MAP_URL','COORDINATE')"""
            )
        t = dict(totals)
        n = t["observations_with_location"] or 0
        return {
            "admin_version": dict(version) if version else None,
            "gazetteer": dict(counts),
            **t,
            "observations_with_location_claims": loc_claims,
            "precision_assigned_share": round(n / loc_claims, 4) if loc_claims else None,  # 001D §9.5: must be 1.0
            "unresolved_share": round(t["unresolved"] / n, 4) if n else None,
            "by_precision": {r["precision"]: r["n"] for r in prec},
        }


def _row(r: Any) -> dict[str, Any]:
    d = dict(r)
    for k in ("signals",):
        if isinstance(d.get(k), str):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    if isinstance(d.get("confidence"), float):
        d["confidence"] = round(d["confidence"], 4)
    return d
