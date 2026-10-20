-- One-time repair of invalid hazard polygons loaded before the loader made them valid.
-- Safe to re-run; only touches rows that are still invalid. Takes a while on a national build.
SET client_min_messages = warning;  -- ST_IsValid reports every self-intersection as a NOTICE

UPDATE koko.flood_depth    SET geom = ST_CollectionExtract(ST_MakeValid(geom), 3) WHERE NOT ST_IsValid(geom);
UPDATE koko.flood_duration SET geom = ST_CollectionExtract(ST_MakeValid(geom), 3) WHERE NOT ST_IsValid(geom);
UPDATE koko.flood_collapse SET geom = ST_CollectionExtract(ST_MakeValid(geom), 3) WHERE NOT ST_IsValid(geom);
UPDATE koko.landslide      SET geom = ST_CollectionExtract(ST_MakeValid(geom), 3) WHERE NOT ST_IsValid(geom);
UPDATE koko.storm_surge    SET geom = ST_CollectionExtract(ST_MakeValid(geom), 3) WHERE NOT ST_IsValid(geom);
UPDATE koko.tsunami        SET geom = ST_CollectionExtract(ST_MakeValid(geom), 3) WHERE NOT ST_IsValid(geom);

DELETE FROM koko.flood_depth    WHERE ST_IsEmpty(geom);
DELETE FROM koko.flood_duration WHERE ST_IsEmpty(geom);
DELETE FROM koko.flood_collapse WHERE ST_IsEmpty(geom);
DELETE FROM koko.landslide      WHERE ST_IsEmpty(geom);
DELETE FROM koko.storm_surge    WHERE ST_IsEmpty(geom);
DELETE FROM koko.tsunami        WHERE ST_IsEmpty(geom);
