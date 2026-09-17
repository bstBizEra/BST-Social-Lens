"""Extraction run orchestration (001C §8): candidates → RULE_V1 → append to extract.*."""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from .fields import METHOD, RULES_VERSION
from .keywords import KEYWORD_GROUPS_VERSION
from .rules import extract_observation
from ..geo.resolver import apply_polygon_lookup, resolve as resolve_locations

log = logging.getLogger("lens-api.extract")
FX_CURRENCIES = ("USD", "THB")


async def run_extraction(
    store: Any,
    *,
    trigger: str,
    since: datetime | None = None,
    limit: int = 500,
    force: bool = False,
    pgcrypto: bool = False,
    contact_salt: str = "",
) -> dict[str, Any]:
    """One run. Idempotent per (record_key, content_hash, method_version) unless `force`."""
    run_id = await store.start_run(trigger, METHOD, RULES_VERSION, KEYWORD_GROUPS_VERSION)
    records_in = observations_out = claims_out = 0
    error: str | None = None
    fx_cache: dict[tuple[str, str | None], tuple[Decimal, str, str] | None] = {}
    geo = getattr(store, "geo", None)
    gaz = await geo.gazetteer() if geo is not None else None
    try:
        rows = await store.candidates(since, limit, force, RULES_VERSION)
        records_in = len(rows)
        for rec in rows:
            post_date = rec["created_at"].date().isoformat() if rec.get("created_at") else None
            # FX needs the DB (async) while the rules are pure/sync: pre-resolve both currencies for this date.
            for cur in FX_CURRENCIES:
                k = (cur, post_date)
                if k not in fx_cache:
                    fx_cache[k] = await store.fx_lookup(cur, post_date)
            fx = lambda cur, d, _c=fx_cache: _c.get((cur, d))  # noqa: E731
            obs = extract_observation(rec.get("text"), rec.get("record_type") or "post", rec.get("author_name"), post_date, fx=fx, contact_salt=contact_salt)
            resolutions = resolve_locations(obs.claims, gaz) if geo is not None else None
            if resolutions and getattr(getattr(geo, "db", None), "postgis", False):
                lookups = {}
                for i, r in enumerate(resolutions):
                    if r.point_source in ("MAP_URL", "TEXT_COORDINATE") and r.lat is not None and r.lng is not None:
                        lookups[i] = await geo.admin_for_point(r.lat, r.lng)
                apply_polygon_lookup(resolutions, lookups)
            _, n = await store.insert_observation(run_id, rec, obs, pgcrypto, resolutions, geo, gaz.admin_version if gaz else None)
            observations_out += 1
            claims_out += n
    except Exception as e:  # noqa: BLE001 — recorded on the run row, then re-raised for the caller
        error = f"{type(e).__name__}: {e}"
        raise
    finally:
        await store.finish_run(run_id, records_in, observations_out, claims_out, error)
    return {"run_id": run_id, "records_in": records_in, "observations_out": observations_out, "claims_out": claims_out}
