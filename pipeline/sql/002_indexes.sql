-- Run after loading (bulk COPY is faster without indexes).

-- Split huge hazard polygons (one flood polygon has 500k+ vertices) so point-in-polygon stays cheap.
WITH big AS (DELETE FROM koko.flood_depth WHERE ST_NPoints(geom) > 256 RETURNING scale, river_class, depth_rank, geom)
INSERT INTO koko.flood_depth (scale, river_class, depth_rank, geom)
SELECT scale, river_class, depth_rank, ST_Subdivide(geom, 256) FROM big;

WITH big AS (DELETE FROM koko.flood_duration WHERE ST_NPoints(geom) > 256 RETURNING river_class, duration_rank, geom)
INSERT INTO koko.flood_duration (river_class, duration_rank, geom)
SELECT river_class, duration_rank, ST_Subdivide(geom, 256) FROM big;

WITH big AS (DELETE FROM koko.flood_collapse WHERE ST_NPoints(geom) > 256 RETURNING river_class, kind, geom)
INSERT INTO koko.flood_collapse (river_class, kind, geom)
SELECT river_class, kind, ST_Subdivide(geom, 256) FROM big;

WITH big AS (DELETE FROM koko.landslide WHERE ST_NPoints(geom) > 256 RETURNING phenomenon, zone, name, location, geom)
INSERT INTO koko.landslide (phenomenon, zone, name, location, geom)
SELECT phenomenon, zone, name, location, ST_Subdivide(geom, 256) FROM big;

WITH big AS (DELETE FROM koko.storm_surge WHERE ST_NPoints(geom) > 256 RETURNING depth_label, depth_min_m, depth_max_m, geom)
INSERT INTO koko.storm_surge (depth_label, depth_min_m, depth_max_m, geom)
SELECT depth_label, depth_min_m, depth_max_m, ST_Subdivide(geom, 256) FROM big;

WITH big AS (DELETE FROM koko.tsunami WHERE ST_NPoints(geom) > 256 RETURNING depth_label, depth_min_m, depth_max_m, geom)
INSERT INTO koko.tsunami (depth_label, depth_min_m, depth_max_m, geom)
SELECT depth_label, depth_min_m, depth_max_m, ST_Subdivide(geom, 256) FROM big;
CREATE INDEX IF NOT EXISTS wards_geom_gix          ON koko.wards          USING gist (geom);
CREATE INDEX IF NOT EXISTS flood_depth_geom_gix    ON koko.flood_depth    USING gist (geom);
CREATE INDEX IF NOT EXISTS flood_duration_geom_gix ON koko.flood_duration USING gist (geom);
CREATE INDEX IF NOT EXISTS flood_collapse_geom_gix ON koko.flood_collapse USING gist (geom);
CREATE INDEX IF NOT EXISTS landslide_geom_gix      ON koko.landslide      USING gist (geom);
CREATE INDEX IF NOT EXISTS tsunami_geom_gix        ON koko.tsunami        USING gist (geom);
CREATE INDEX IF NOT EXISTS storm_surge_geom_gix    ON koko.storm_surge    USING gist (geom);
CREATE INDEX IF NOT EXISTS shelters_geom_gix       ON koko.shelters       USING gist (geom);
CREATE INDEX IF NOT EXISTS buildings_geom_gix      ON koko.buildings      USING gist (geom);
CREATE INDEX IF NOT EXISTS buildings_ward_idx      ON koko.buildings (ward_code);

ANALYZE koko.wards, koko.flood_depth, koko.flood_duration, koko.flood_collapse,
        koko.landslide, koko.tsunami, koko.storm_surge, koko.shelters, koko.buildings;
