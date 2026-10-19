-- Display geometry for the drill-down map, derived from koko.municipalities. Re-run after loading boundaries.
-- Municipalities: ~50 m tolerance. Prefectures: union of simplified municipalities, then ~300 m tolerance.

ALTER TABLE koko.municipalities ADD COLUMN IF NOT EXISTS geom_display geometry(MultiPolygon, 4326);
UPDATE koko.municipalities
SET geom_display = ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SimplifyPreserveTopology(geom, 0.0005)), 3))
WHERE geom_display IS NULL;
CREATE INDEX IF NOT EXISTS municipalities_display_gix ON koko.municipalities USING gist (geom_display);

DROP TABLE IF EXISTS koko.prefectures;
CREATE TABLE koko.prefectures AS
SELECT pref_code AS code,
       min(pref_ja) AS name_ja,
       min(pref_en) AS name_en,
       count(*)::int AS municipalities,
       -- simplified borders can self-intersect: make each valid, then union on a 1e-5° grid (robust overlay)
       ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SimplifyPreserveTopology(
           ST_UnaryUnion(ST_Collect(ST_MakeValid(ST_SimplifyPreserveTopology(geom, 0.002))), 0.00001),
           0.003)), 3)) AS geom
FROM koko.municipalities
GROUP BY pref_code;
ALTER TABLE koko.prefectures ADD PRIMARY KEY (code);
CREATE INDEX prefectures_geom_gix ON koko.prefectures USING gist (geom);

-- Cached statistics per area (computed on first request, refreshed when newer data is loaded)
CREATE TABLE IF NOT EXISTS koko.area_stats (
    code         text PRIMARY KEY,      -- 2-digit prefecture or 5-digit municipality code
    stats        jsonb NOT NULL,
    data_version text NOT NULL,
    computed_at  timestamptz NOT NULL DEFAULT now()
);
