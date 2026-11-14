-- =============================================================================
-- 00_init_database.sql — extensions, schemas, scoped roles
-- Run as songdo_admin (superuser) while connected to songdo_urban_memory.
-- Re-runnable: every statement is idempotent.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS btree_gist;   -- needed for EXCLUDE constraints mixing = and &&

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS mobility;
CREATE SCHEMA IF NOT EXISTS building;
CREATE SCHEMA IF NOT EXISTS tourism;
CREATE SCHEMA IF NOT EXISTS environment;
CREATE SCHEMA IF NOT EXISTS safety;
CREATE SCHEMA IF NOT EXISTS cross_domain;

COMMENT ON SCHEMA core         IS 'Shared contracts: source registry, places, staging, quarantine, audit ledger';
COMMENT ON SCHEMA mobility     IS 'DB1 Mobility: stops, routes, trips, vehicle observations';
COMMENT ON SCHEMA building     IS 'DB2 Smart Building: buildings, spaces, devices, telemetry';
COMMENT ON SCHEMA tourism      IS 'DB3 Tourism: attractions, operating hours, events, visits';
COMMENT ON SCHEMA environment  IS 'DB4 Environment: stations, measurements, alerts';
COMMENT ON SCHEMA safety       IS 'DB5 Safety: facilities, incidents, status history, drills';
COMMENT ON SCHEMA cross_domain IS 'Cross-team federation views and materialized views';

-- Scoped group roles (NOLOGIN). Application logins are granted one of these.
--   songdo_readonly : query API / analysts  (SELECT only)
--   songdo_ingest   : ingestion API         (SELECT + INSERT + UPDATE, never DELETE)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'songdo_readonly') THEN
        CREATE ROLE songdo_readonly NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'songdo_ingest') THEN
        CREATE ROLE songdo_ingest NOLOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA core, mobility, building, tourism, environment, safety, cross_domain
    TO songdo_readonly, songdo_ingest;

-- Tables created later by the migration owner inherit these grants automatically.
ALTER DEFAULT PRIVILEGES IN SCHEMA core, mobility, building, tourism, environment, safety, cross_domain
    GRANT SELECT ON TABLES TO songdo_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA core, mobility, building, tourism, environment, safety
    GRANT SELECT, INSERT, UPDATE ON TABLES TO songdo_ingest;
ALTER DEFAULT PRIVILEGES IN SCHEMA core, mobility, building, tourism, environment, safety
    GRANT USAGE, SELECT ON SEQUENCES TO songdo_ingest;
