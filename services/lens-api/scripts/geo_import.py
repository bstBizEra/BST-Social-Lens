#!/usr/bin/env python3
"""Lao Data Map → LensDB admin version (SLL-PROP-DATA-001D D4).

  convert  --provinces P.geojson --districts D.geojson --villages V.geojson --version <id> --out admin.json
           [--code-key code --name-lo-key name_lo --name-en-key name_en --parent-key parent_code --variants-key variants]
           Turns three GeoJSON FeatureCollections (or plain JSON arrays of features) into the JSON shape
           `POST /admin/geo/import` expects, computing centroids from geometry when the export has none and
           keeping each polygon as GeoJSON for the later PostGIS load. Property keys are configurable because
           upstream naming varies; unknown keys are reported, never guessed.
  post     --file admin.json [--url http://127.0.0.1:7710] [--source-ref laodatamap@<commit>]
           Posts the file to the running lens-api (token from .env). 409 = version already imported.
  check    --file admin.json
           Validates hierarchy (every district → province, every village → district/province), duplicate codes,
           Lao-name presence, centroid presence. Exit 1 on problems.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _features(path: Path) -> list[dict]:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(d, dict) and d.get("type") == "FeatureCollection":
        return d["features"]
    if isinstance(d, list):
        return d
    raise SystemExit(f"{path}: expected a FeatureCollection or a list of features")


def _centroid(geom: dict | None) -> tuple[float, float] | None:
    """Area-weighted centroid of (Multi)Polygon outer rings; simple mean for points/lines. None without geometry."""
    if not geom:
        return None
    t, c = geom.get("type"), geom.get("coordinates")
    if t == "Point":
        return float(c[1]), float(c[0])
    polys = [c] if t == "Polygon" else c if t == "MultiPolygon" else []
    tot_a = cx = cy = 0.0
    for poly in polys:
        ring = poly[0]
        a = px = py = 0.0
        for i in range(len(ring) - 1):
            x0, y0 = ring[i][0], ring[i][1]
            x1, y1 = ring[i + 1][0], ring[i + 1][1]
            cross = x0 * y1 - x1 * y0
            a += cross
            px += (x0 + x1) * cross
            py += (y0 + y1) * cross
        if a:
            a *= 0.5
            tot_a += a
            cx += px / 6.0
            cy += py / 6.0
    if tot_a:
        return cy / tot_a, cx / tot_a
    pts = [pt for poly in polys for ring in poly for pt in ring]
    if pts:
        return sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts)
    return None


def convert(a: argparse.Namespace) -> None:
    def row(f: dict, level: str) -> dict:
        p = f.get("properties", {})
        missing = [k for k in (a.code_key, a.name_lo_key) if k not in p]
        if missing:
            raise SystemExit(f"{level}: feature lacks {missing}; available keys: {sorted(p)} — pass --code-key/--name-lo-key")
        cen = None
        if a.lat_key in p and a.lng_key in p:
            cen = (float(p[a.lat_key]), float(p[a.lng_key]))
        else:
            cen = _centroid(f.get("geometry"))
        r = {"code": str(p[a.code_key]), "name_lo": str(p[a.name_lo_key]).strip(), "name_en": (str(p[a.name_en_key]).strip() if p.get(a.name_en_key) else None),
             "name_variants": [str(x) for x in (p.get(a.variants_key) or [])] if isinstance(p.get(a.variants_key), list) else [],
             "centroid_lat": cen[0] if cen else None, "centroid_lng": cen[1] if cen else None}
        if a.keep_geometry and f.get("geometry"):
            r["geom"] = f["geometry"]
        if level == "district":
            r["province_code"] = str(p[a.parent_key])
        if level == "village":
            r["district_code"] = str(p[a.parent_key])
            r["province_code"] = str(p[a.province_key]) if a.province_key in p else None
        return r

    provinces = [row(f, "province") for f in _features(Path(a.provinces))]
    districts = [row(f, "district") for f in _features(Path(a.districts))]
    villages = [row(f, "village") for f in _features(Path(a.villages))] if a.villages else []
    d_prov = {d["code"]: d["province_code"] for d in districts}
    for v in villages:
        if not v.get("province_code"):
            v["province_code"] = d_prov.get(v["district_code"])
    out = {"admin_version": a.version, "_note": f"converted by scripts/geo_import.py from {Path(a.provinces).name}, {Path(a.districts).name}, {Path(a.villages).name if a.villages else '-'}",
           "provinces": provinces, "districts": districts, "villages": villages, "aliases": []}
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {a.out}: {len(provinces)} provinces, {len(districts)} districts, {len(villages)} villages (geometry kept: {a.keep_geometry})")
    check_payload(out)


def check_payload(d: dict) -> int:
    problems: list[str] = []
    for lvl in ("provinces", "districts", "villages"):
        codes = [r["code"] for r in d.get(lvl, [])]
        dup = {c for c in codes if codes.count(c) > 1}
        if dup:
            problems.append(f"{lvl}: duplicate codes {sorted(dup)[:5]}")
        for r in d.get(lvl, []):
            if not r.get("name_lo"):
                problems.append(f"{lvl} {r['code']}: no name_lo")
            if r.get("centroid_lat") is None:
                problems.append(f"{lvl} {r['code']}: no centroid")
    pc = {r["code"] for r in d["provinces"]}
    dc = {r["code"] for r in d["districts"]}
    for r in d["districts"]:
        if r["province_code"] not in pc:
            problems.append(f"district {r['code']}: province {r['province_code']} unknown")
    for r in d.get("villages", []):
        if r["district_code"] not in dc:
            problems.append(f"village {r['code']}: district {r['district_code']} unknown")
        if r.get("province_code") not in pc:
            problems.append(f"village {r['code']}: province {r.get('province_code')} unknown")
    for p in problems[:30]:
        print("problem:", p)
    if len(problems) > 30:
        print(f"... {len(problems) - 30} more")
    print(f"check: {len(problems)} problem(s)")
    return 1 if problems else 0


def post(a: argparse.Namespace) -> None:
    token = os.environ.get("LENS_API_TOKEN")
    if not token and (HERE / ".env").exists():
        for line in (HERE / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("LENS_API_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"')
    if not token:
        raise SystemExit("LENS_API_TOKEN not found (env or .env)")
    body = Path(a.file).read_bytes()
    req = urllib.request.Request(f"{a.url}/admin/geo/import?source_ref={a.source_ref}", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            print(r.status, r.read().decode()[:500])
    except urllib.error.HTTPError as e:
        print(e.code, e.read().decode()[:500])
        sys.exit(1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("convert")
    c.add_argument("--provinces", required=True)
    c.add_argument("--districts", required=True)
    c.add_argument("--villages")
    c.add_argument("--version", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--code-key", default="code")
    c.add_argument("--name-lo-key", default="name_lo")
    c.add_argument("--name-en-key", default="name_en")
    c.add_argument("--parent-key", default="parent_code")
    c.add_argument("--province-key", default="province_code")
    c.add_argument("--variants-key", default="variants")
    c.add_argument("--lat-key", default="centroid_lat")
    c.add_argument("--lng-key", default="centroid_lng")
    c.add_argument("--keep-geometry", action="store_true", default=True)
    c.add_argument("--no-geometry", dest="keep_geometry", action="store_false")
    p = sub.add_parser("post")
    p.add_argument("--file", required=True)
    p.add_argument("--url", default="http://127.0.0.1:7710")
    p.add_argument("--source-ref", default="laodatamap")
    k = sub.add_parser("check")
    k.add_argument("--file", required=True)
    args = ap.parse_args()
    if args.cmd == "convert":
        convert(args)
    elif args.cmd == "post":
        post(args)
    else:
        sys.exit(check_payload(json.loads(Path(args.file).read_text(encoding="utf-8"))))
