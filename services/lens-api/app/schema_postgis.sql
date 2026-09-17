-- BST Social Lens — PostGIS layer for geo.* (SLL-PROP-DATA-001D G1). ADDITIVE; applied by init_db() ONLY when the
-- postgis extension is present (db.postgis = true). Geometry columns are built from the GeoJSON already held in
-- geom_geojson, so an admin version imported before PostGIS existed becomes spatial after the next startup.
CREATE EXTENSION IF NOT EXISTS postgis;

ALTER TABLE geo.provinces ADD COLUMN IF NOT EXISTS geom geometry(MultiPolygon, 4326);
ALTER TABLE geo.districts ADD COLUMN IF NOT EXISTS geom geometry(MultiPolygon, 4326);
ALTER TABLE geo.villages  ADD COLUMN IF NOT EXISTS geom geometry(MultiPolygon, 4326);
ALTER TABLE geo.resolved_locations ADD COLUMN IF NOT EXISTS geom geometry(Point, 4326);

-- Backfill geometry from GeoJSON where missing (idempotent; Polygon → MultiPolygon).
UPDATE geo.provinces SET geom = ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(geom_geojson::text), 4326)) WHERE geom IS NULL AND geom_geojson IS NOT NULL;
UPDATE geo.districts SET geom = ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(geom_geojson::text), 4326)) WHERE geom IS NULL AND geom_geojson IS NOT NULL;
UPDATE geo.villages  SET geom = ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(geom_geojson::text), 4326)) WHERE geom IS NULL AND geom_geojson IS NOT NULL;
UPDATE geo.resolved_locations SET geom = ST_SetSRID(ST_MakePoint(lng, lat), 4326) WHERE geom IS NULL AND lat IS NOT NULL AND lng IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_provinces_geom ON geo.provinces USING gist (geom);
CREATE INDEX IF NOT EXISTS idx_districts_geom ON geo.districts USING gist (geom);
CREATE INDEX IF NOT EXISTS idx_villages_geom  ON geo.villages  USING gist (geom);
CREATE INDEX IF NOT EXISTS idx_resolved_geom  ON geo.resolved_locations USING gist (geom) WHERE is_primary;

-- Point → deepest containing admin unit of the current version (001D §4.1 step 3). NULLs when nothing contains the point.
CREATE OR REPLACE FUNCTION geo.point_in_admin(p_lat double precision, p_lng double precision)
RETURNS TABLE (admin_version text, province_code text, district_code text, village_code text)
LANGUAGE sql STABLE AS $$
  WITH v AS (SELECT admin_version FROM geo.admin_versions WHERE is_current LIMIT 1),
       pt AS (SELECT ST_SetSRID(ST_MakePoint(p_lng, p_lat), 4326) AS g)
  SELECT v.admin_version,
         COALESCE(vl.province_code, d.province_code, pr.code),
         COALESCE(vl.district_code, d.code),
         vl.code
  FROM v, pt
  LEFT JOIN LATERAL (SELECT code, district_code, province_code FROM geo.villages  w WHERE w.admin_version = v.admin_version AND w.geom IS NOT NULL AND ST_Within(pt.g, w.geom) LIMIT 1) vl ON true
  LEFT JOIN LATERAL (SELECT code, province_code FROM geo.districts x WHERE x.admin_version = v.admin_version AND x.geom IS NOT NULL AND ST_Within(pt.g, x.geom) LIMIT 1) d ON true
  LEFT JOIN LATERAL (SELECT code FROM geo.provinces y WHERE y.admin_version = v.admin_version AND y.geom IS NOT NULL AND ST_Within(pt.g, y.geom) LIMIT 1) pr ON true;
$$;
