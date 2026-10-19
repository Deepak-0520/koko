-- KOKO core schema. All geometries are EPSG:4326 (JGD2011 differs from WGS84 by centimetres).
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS koko;

CREATE TABLE IF NOT EXISTS koko.wards (
    code     text PRIMARY KEY,
    name_ja  text NOT NULL,
    name_en  text NOT NULL,
    geom     geometry(MultiPolygon, 4326) NOT NULL
);

-- 洪水浸水想定区域: depth rank per scale (L1 planned / L2 maximum assumed)
CREATE TABLE IF NOT EXISTS koko.flood_depth (
    id          bigserial PRIMARY KEY,
    scale       text NOT NULL CHECK (scale IN ('L1', 'L2')),
    river_class text NOT NULL,          -- designated | other
    depth_rank  smallint NOT NULL,      -- KSJ water_depth_code 1..6
    geom        geometry(Geometry, 4326) NOT NULL
);

CREATE TABLE IF NOT EXISTS koko.flood_duration (
    id            bigserial PRIMARY KEY,
    river_class   text NOT NULL,
    duration_rank smallint NOT NULL,    -- KSJ flood_duration_code 1..7
    geom          geometry(Geometry, 4326) NOT NULL
);

-- 家屋倒壊等氾濫想定区域 (1 = flood flow, 2 = bank erosion)
CREATE TABLE IF NOT EXISTS koko.flood_collapse (
    id          bigserial PRIMARY KEY,
    river_class text NOT NULL,
    kind        smallint NOT NULL,
    geom        geometry(Geometry, 4326) NOT NULL
);

CREATE TABLE IF NOT EXISTS koko.landslide (
    id         bigserial PRIMARY KEY,
    phenomenon smallint NOT NULL,       -- 1 steep slope, 2 debris flow, 3 landslide
    zone       smallint NOT NULL,       -- 1 warning, 2 special warning, 3/4 pending
    name       text,
    location   text,
    geom       geometry(Geometry, 4326) NOT NULL
);

CREATE TABLE IF NOT EXISTS koko.tsunami (
    id          bigserial PRIMARY KEY,
    depth_label text,
    depth_min_m real,
    depth_max_m real,
    geom        geometry(Geometry, 4326) NOT NULL
);

CREATE TABLE IF NOT EXISTS koko.storm_surge (LIKE koko.tsunami INCLUDING ALL);

CREATE TABLE IF NOT EXISTS koko.shelters (
    id           bigserial PRIMARY KEY,
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
    ward_code         text NOT NULL,
    usage_code        text,
    usage_label       text,
    height_m          real,
    storeys_above     smallint,
    storeys_below     smallint,
    address           text,
    flood_max_depth_m real,             -- max L2 river flood depth assigned to this building by PLATEAU
    risks             jsonb NOT NULL DEFAULT '[]',
    geom              geometry(Geometry, 4326) NOT NULL
);

CREATE TABLE IF NOT EXISTS koko.source_meta (
    source    text PRIMARY KEY,
    title     text NOT NULL,
    url       text NOT NULL,
    rows      integer,
    loaded_at timestamptz NOT NULL DEFAULT now()
);
