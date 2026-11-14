-- =============================================================================
-- 01_core_schema.sql — shared contracts used by all five domain databases
--   source registry · study area · places · batches · staging · quarantine · audit
-- Re-runnable. PostGIS functions are schema-qualified (public.*) inside function
-- bodies so that pg_restore (which runs with an empty search_path) still works.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Songdo Teaching Bounding Box
--   126.600 <= lon <= 126.685 , 37.350 <= lat <= 37.425   (WGS84 / EPSG:4326)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.songdo_bbox()
RETURNS public.geometry
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$ SELECT public.ST_MakeEnvelope(126.600, 37.350, 126.685, 37.425, 4326) $$;

-- IMMUTABLE is required because this function feeds a STORED generated column.
CREATE OR REPLACE FUNCTION core.is_in_songdo(g public.geometry)
RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$
    SELECT g IS NOT NULL
       AND public.ST_SRID(g) = 4326
       AND public.ST_Covers(public.ST_MakeEnvelope(126.600, 37.350, 126.685, 37.425, 4326), g)
$$;

COMMENT ON FUNCTION core.is_in_songdo(public.geometry) IS
    'TRUE when the geometry lies fully inside the Songdo Teaching Bounding Box';

CREATE TABLE IF NOT EXISTS core.study_areas (
    area_id     VARCHAR(32)  PRIMARY KEY,
    name_ko     VARCHAR(255) NOT NULL,
    name_en     VARCHAR(255) NOT NULL,
    geom        public.GEOMETRY(Polygon, 4326) NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp()
);
COMMENT ON TABLE core.study_areas IS 'Canonical study-area polygons (Songdo teaching bbox)';

INSERT INTO core.study_areas (area_id, name_ko, name_en, geom)
VALUES ('SONGDO_BBOX', '송도 교육용 경계 상자', 'Songdo Teaching Bounding Box', core.songdo_bbox())
ON CONFLICT (area_id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Master source registry (mirror of source_registry.csv)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.source_registry (
    source_id           VARCHAR(32)  PRIMARY KEY,
    title_ko            VARCHAR(255) NOT NULL,
    title_en            VARCHAR(255) NOT NULL,
    provider            VARCHAR(128) NOT NULL,
    source_url          VARCHAR(512),
    verification_level  SMALLINT     NOT NULL CHECK (verification_level BETWEEN 1 AND 4),
    provenance          VARCHAR(32)  NOT NULL
        CHECK (provenance IN ('OFFICIAL_OPEN_DATA', 'SYNTHETIC_FIXTURE', 'CURATED_MANUAL')),
    fallback_strategy   VARCHAR(255) NOT NULL,
    notes               TEXT,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp()
);
COMMENT ON TABLE  core.source_registry IS 'Every dataset that may enter the platform, with its verification level';
COMMENT ON COLUMN core.source_registry.verification_level IS
    '1 metadata page verified · 2 schema inspected · 3 real payload downloaded · 4 Songdo subset confirmed';

-- ---------------------------------------------------------------------------
-- Shared canonical places
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.places (
    place_id      VARCHAR(64)  PRIMARY KEY,
    name_ko       VARCHAR(255) NOT NULL,
    name_en       VARCHAR(255),
    category      VARCHAR(64)  NOT NULL,
    road_address  VARCHAR(255),
    geom          public.GEOMETRY(Point, 4326) NOT NULL,
    is_in_songdo  BOOLEAN GENERATED ALWAYS AS (core.is_in_songdo(geom)) STORED,
    provenance    VARCHAR(32)  NOT NULL REFERENCES core.source_registry(source_id),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp()
);
COMMENT ON TABLE core.places IS 'Canonical Songdo places shared by all domains';

CREATE INDEX IF NOT EXISTS idx_places_geom ON core.places USING GIST (geom);
-- ST_DWithin(geom::geography, ...) can only use an index built on the SAME expression.
-- Without this second index every metre-based radius query is a sequential scan.
CREATE INDEX IF NOT EXISTS idx_places_geog ON core.places USING GIST ((geom::public.geography));
CREATE INDEX IF NOT EXISTS idx_places_category ON core.places (category);

-- ---------------------------------------------------------------------------
-- Ingestion batches, staging (raw accepted payloads), quarantine (rejected)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.ingestion_batches (
    batch_id          BIGSERIAL    PRIMARY KEY,
    source_id         VARCHAR(32)  NOT NULL REFERENCES core.source_registry(source_id),
    domain_schema     VARCHAR(32)  NOT NULL,
    target_table      VARCHAR(64)  NOT NULL,
    adapter_name      VARCHAR(64)  NOT NULL,
    status            VARCHAR(16)  NOT NULL DEFAULT 'RUNNING'
        CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED')),
    rows_received     INTEGER      NOT NULL DEFAULT 0 CHECK (rows_received    >= 0),
    rows_loaded       INTEGER      NOT NULL DEFAULT 0 CHECK (rows_loaded      >= 0),
    rows_duplicate    INTEGER      NOT NULL DEFAULT 0 CHECK (rows_duplicate   >= 0),
    rows_quarantined  INTEGER      NOT NULL DEFAULT 0 CHECK (rows_quarantined >= 0),
    started_at        TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    finished_at       TIMESTAMPTZ,
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);
COMMENT ON TABLE core.ingestion_batches IS 'One row per adapter run / API ingestion call';

CREATE TABLE IF NOT EXISTS core.staging_records (
    staging_id      BIGSERIAL    PRIMARY KEY,
    batch_id        BIGINT       NOT NULL REFERENCES core.ingestion_batches(batch_id),
    source_id       VARCHAR(32)  NOT NULL REFERENCES core.source_registry(source_id),
    domain_schema   VARCHAR(32)  NOT NULL,
    target_table    VARCHAR(64)  NOT NULL,
    payload         JSONB        NOT NULL,
    payload_sha256  CHAR(64)     NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    status          VARCHAR(16)  NOT NULL DEFAULT 'STAGED'
        CHECK (status IN ('STAGED', 'LOADED', 'REJECTED')),
    entity_id       VARCHAR(64),
    observed_at     TIMESTAMPTZ  NOT NULL,
    received_at     TIMESTAMPTZ  NOT NULL,
    recorded_at     TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT uq_staging_batch_hash UNIQUE (batch_id, payload_sha256)
);
COMMENT ON TABLE core.staging_records IS
    'Raw payloads accepted by an adapter. SHA-256 of the canonical JSON makes re-delivery idempotent';

-- Cross-batch duplicate lookup (same source re-sending a payload in a later batch)
CREATE INDEX IF NOT EXISTS idx_staging_dedup
    ON core.staging_records (source_id, target_table, payload_sha256);

CREATE TABLE IF NOT EXISTS core.quarantine_records (
    quarantine_id   BIGSERIAL    PRIMARY KEY,
    batch_id        BIGINT       REFERENCES core.ingestion_batches(batch_id),
    source_id       VARCHAR(32)  NOT NULL REFERENCES core.source_registry(source_id),
    domain_schema   VARCHAR(32)  NOT NULL,
    target_table    VARCHAR(64)  NOT NULL,
    reason_code     VARCHAR(32)  NOT NULL CHECK (reason_code IN (
        'MISSING_COORDINATE', 'MALFORMED_COORDINATE', 'COORDINATE_SWAPPED', 'OUT_OF_BOUNDS',
        'MISSING_FIELD', 'FAULT_READING', 'DUPLICATE_AUTHORITY', 'SCHEMA_VIOLATION', 'FK_VIOLATION', 'OTHER')),
    reason_detail   TEXT         NOT NULL,
    payload         JSONB        NOT NULL,
    payload_sha256  CHAR(64)     NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    received_at     TIMESTAMPTZ  NOT NULL,
    recorded_at     TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    resolved_at     TIMESTAMPTZ,
    resolution_note TEXT,
    -- re-sending the same bad payload must not pile up quarantine rows
    CONSTRAINT uq_quarantine_payload UNIQUE (source_id, payload_sha256, reason_code)
);
COMMENT ON TABLE core.quarantine_records IS
    'Rejected payloads kept verbatim with the reason, never silently dropped';
CREATE INDEX IF NOT EXISTS idx_quarantine_reason ON core.quarantine_records (reason_code, recorded_at DESC);

-- ---------------------------------------------------------------------------
-- Immutable event audit log (Urban Memory ledger)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.event_audit_log (
    audit_id          BIGSERIAL    PRIMARY KEY,
    domain_schema     VARCHAR(32)  NOT NULL,
    table_name        VARCHAR(64)  NOT NULL,
    entity_id         VARCHAR(64)  NOT NULL,
    action            VARCHAR(16)  NOT NULL
        CHECK (action IN ('INSERT', 'UPDATE', 'DELETE', 'STATUS_CHANGE', 'DISPATCH')),
    observed_at       TIMESTAMPTZ  NOT NULL,
    received_at       TIMESTAMPTZ  NOT NULL,
    recorded_at       TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    source_lineage    VARCHAR(32)  NOT NULL REFERENCES core.source_registry(source_id),
    actor             VARCHAR(64)  NOT NULL DEFAULT 'SYSTEM',
    payload_snapshot  JSONB        NOT NULL
);
COMMENT ON TABLE  core.event_audit_log IS 'Append-only ledger: UPDATE, DELETE and TRUNCATE are rejected by trigger';
COMMENT ON COLUMN core.event_audit_log.observed_at IS 'When the physical event happened in Songdo';
COMMENT ON COLUMN core.event_audit_log.received_at IS 'When the adapter / ingestion API accepted the payload';
COMMENT ON COLUMN core.event_audit_log.recorded_at IS 'When PostgreSQL wrote the row (clock_timestamp)';

CREATE INDEX IF NOT EXISTS idx_audit_entity   ON core.event_audit_log (domain_schema, table_name, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_observed ON core.event_audit_log (observed_at DESC);

CREATE OR REPLACE FUNCTION core.forbid_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '%.% is append-only: % is not allowed', TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation';
END
$$;

DROP TRIGGER IF EXISTS trg_audit_no_update_delete ON core.event_audit_log;
CREATE TRIGGER trg_audit_no_update_delete
    BEFORE UPDATE OR DELETE ON core.event_audit_log
    FOR EACH ROW EXECUTE FUNCTION core.forbid_mutation();

DROP TRIGGER IF EXISTS trg_audit_no_truncate ON core.event_audit_log;
CREATE TRIGGER trg_audit_no_truncate
    BEFORE TRUNCATE ON core.event_audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION core.forbid_mutation();

-- Helper used by adapters and domain triggers to append one ledger entry.
CREATE OR REPLACE FUNCTION core.log_event(
    p_domain_schema  VARCHAR,
    p_table_name     VARCHAR,
    p_entity_id      VARCHAR,
    p_action         VARCHAR,
    p_observed_at    TIMESTAMPTZ,
    p_received_at    TIMESTAMPTZ,
    p_source_lineage VARCHAR,
    p_payload        JSONB,
    p_actor          VARCHAR DEFAULT 'SYSTEM'
)
RETURNS BIGINT
LANGUAGE sql
AS $$
    INSERT INTO core.event_audit_log
        (domain_schema, table_name, entity_id, action, observed_at, received_at,
         source_lineage, actor, payload_snapshot)
    VALUES
        (p_domain_schema, p_table_name, p_entity_id, p_action, p_observed_at, p_received_at,
         p_source_lineage, p_actor, p_payload)
    RETURNING audit_id
$$;
