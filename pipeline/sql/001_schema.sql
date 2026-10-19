-- KOKO core schema (v2: nationwide). All geometries are EPSG:4326
-- (JGD2011 differs from WGS84 by centimetres). Every hazard row carries `src`, the staged file it
-- came from (a prefecture code or a flood mesh), so files can be reloaded one at a time.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS koko;

CREATE TABLE IF NOT EXISTS koko.municipalities (
    code      text PRIMARY KEY,
    pref_code text NOT NULL,
    pref_ja   text NOT NULL,
    pref_en   text NOT NULL,
    city_ja   text,
    ward_ja   text,
    name_ja   text,
    geom      geometry(MultiPolygon, 4326) NOT NULL
);
-- Subdivided copy for fast point-in-polygon lookups (coastlines have huge polygons)
CREATE TABLE IF NOT EXISTS koko.municipality_parts (
    code text NOT NULL,
    geom geometry(Geometry, 4326) NOT NULL
);

-- 洪水浸水想定区域: depth rank per scale (L1 planned / L2 maximum assumed)
CREATE TABLE IF NOT EXISTS koko.flood_depth (
    src         text NOT NULL,
    scale       text NOT NULL CHECK (scale IN ('L1', 'L2')),
    river_class text NOT NULL,          -- designated | other
    depth_rank  smallint NOT NULL,      -- KSJ water_depth_code 1..6
    geom        geometry(Geometry, 4326) NOT NULL
);

CREATE TABLE IF NOT EXISTS koko.flood_duration (
    src           text NOT NULL,
    river_class   text NOT NULL,
    duration_rank smallint NOT NULL,    -- KSJ flood_duration_code 1..7
    geom          geometry(Geometry, 4326) NOT NULL
);

-- 家屋倒壊等氾濫想定区域 (1 = flood flow, 2 = bank erosion)
CREATE TABLE IF NOT EXISTS koko.flood_collapse (
    src         text NOT NULL,
    river_class text NOT NULL,
    kind        smallint NOT NULL,
    geom        geometry(Geometry, 4326) NOT NULL
);

CREATE TABLE IF NOT EXISTS koko.landslide (
    src        text NOT NULL,
    phenomenon smallint NOT NULL,       -- 1 steep slope, 2 debris flow, 3 landslide
    zone       smallint NOT NULL,       -- 1 warning, 2 special warning, 3/4 pending
    name       text,
    location   text,
    geom       geometry(Geometry, 4326) NOT NULL
);

CREATE TABLE IF NOT EXISTS koko.tsunami (
    src         text NOT NULL,
    depth_label text,
    depth_min_m real,
    depth_max_m real,
    geom        geometry(Geometry, 4326) NOT NULL
);

CREATE TABLE IF NOT EXISTS koko.storm_surge (LIKE koko.tsunami INCLUDING ALL);

CREATE TABLE IF NOT EXISTS koko.shelters (
    src          text NOT NULL,
    name         text,
    address      text,
    remarks      text,
    flood        boolean NOT NULL DEFAULT false,
    landslide    boolean NOT NULL DEFAULT false,
    storm_surge  boolean NOT NULL DEFAULT false,
    earthquake   boolean NOT NULL DEFAULT false,
    tsunami      boolean NOT NULL DEFAULT false,
    fire         boolean NOT NULL DEFAULT false,
    inland_flood boolean NOT NULL DEFAULT false,
    volcano      boolean NOT NULL DEFAULT false,
    geom         geometry(Point, 4326) NOT NULL
);

CREATE TABLE IF NOT EXISTS koko.buildings (
    id                text PRIMARY KEY,
    src               text NOT NULL,     -- PLATEAU dataset (city or Tokyo ward code)
    city_code         text,              -- the building's own municipality code
    usage_code        text,
    usage_label       text,
    height_m          real,
    storeys_above     smallint,
    storeys_below     smallint,
    address           text,
    flood_max_depth_m real,              -- max L2 river flood depth assigned to this building by PLATEAU
    risks             jsonb NOT NULL DEFAULT '[]',
    geom              geometry(Geometry, 4326) NOT NULL
);

CREATE TABLE IF NOT EXISTS koko.source_meta (
    source text PRIMARY KEY,
    title  text NOT NULL,
    url    text NOT NULL
);

CREATE TABLE IF NOT EXISTS koko.loaded_files (
    layer      text NOT NULL,
    src        text NOT NULL,
    file_mtime double precision NOT NULL,
    rows       integer NOT NULL,
    loaded_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (layer, src)
);

-- Indexes live with the tables: loads are incremental, so they are never dropped.
CREATE INDEX IF NOT EXISTS municipalities_geom_gix    ON koko.municipalities     USING gist (geom);
CREATE INDEX IF NOT EXISTS municipality_parts_geom_gix ON koko.municipality_parts USING gist (geom);
CREATE INDEX IF NOT EXISTS municipality_parts_code_idx ON koko.municipality_parts (code);
CREATE INDEX IF NOT EXISTS flood_depth_geom_gix       ON koko.flood_depth        USING gist (geom);
CREATE INDEX IF NOT EXISTS flood_depth_src_idx        ON koko.flood_depth        (src);
CREATE INDEX IF NOT EXISTS flood_duration_geom_gix    ON koko.flood_duration     USING gist (geom);
CREATE INDEX IF NOT EXISTS flood_duration_src_idx     ON koko.flood_duration     (src);
CREATE INDEX IF NOT EXISTS flood_collapse_geom_gix    ON koko.flood_collapse     USING gist (geom);
CREATE INDEX IF NOT EXISTS flood_collapse_src_idx     ON koko.flood_collapse     (src);
CREATE INDEX IF NOT EXISTS landslide_geom_gix         ON koko.landslide          USING gist (geom);
CREATE INDEX IF NOT EXISTS landslide_src_idx          ON koko.landslide          (src);
CREATE INDEX IF NOT EXISTS tsunami_geom_gix           ON koko.tsunami            USING gist (geom);
CREATE INDEX IF NOT EXISTS tsunami_src_idx            ON koko.tsunami            (src);
CREATE INDEX IF NOT EXISTS storm_surge_geom_gix       ON koko.storm_surge        USING gist (geom);
CREATE INDEX IF NOT EXISTS storm_surge_src_idx        ON koko.storm_surge        (src);
CREATE INDEX IF NOT EXISTS shelters_geom_gix          ON koko.shelters           USING gist (geom);
CREATE INDEX IF NOT EXISTS shelters_src_idx           ON koko.shelters           (src);
CREATE INDEX IF NOT EXISTS buildings_geom_gix         ON koko.buildings          USING gist (geom);
CREATE INDEX IF NOT EXISTS buildings_src_idx          ON koko.buildings          (src);
