-- =============================================================================
-- 06_db5_safety.sql — DB5 Safety & Incident
--   facilities · severity_levels · incident_types · incidents ·
--   incident_status_history · response_drills
-- Re-runnable. Depends on 00 (extensions, schemas) and 01 (core).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Public safety facilities
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS safety.facilities (
    facility_id       VARCHAR(32)  PRIMARY KEY,
    facility_name_ko  VARCHAR(255) NOT NULL,
    facility_name_en  VARCHAR(255),
    facility_type     VARCHAR(24)  NOT NULL
        CHECK (facility_type IN ('FIRE_STATION', 'SAFETY_CENTER_119', 'POLICE_BOX')),
    responder         VARCHAR(8)   NOT NULL CHECK (responder IN ('FIRE', 'POLICE')),
    road_address      VARCHAR(255),
    geom              public.GEOMETRY(Point, 4326) NOT NULL,
    source_id         VARCHAR(32)  NOT NULL REFERENCES core.source_registry(source_id),
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT ck_facilities_in_songdo CHECK (core.is_in_songdo(geom)),
    -- the responder follows from the facility type
    CONSTRAINT ck_facilities_responder CHECK ((facility_type = 'POLICE_BOX') = (responder = 'POLICE'))
);
COMMENT ON TABLE safety.facilities IS 'Fire stations, 119 safety centers and police boxes serving Songdo';
CREATE INDEX IF NOT EXISTS idx_facilities_geom ON safety.facilities USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_facilities_geog ON safety.facilities USING GIST ((geom::public.geography));

-- ---------------------------------------------------------------------------
-- Reference data: severity (with its response-time target) and incident types
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS safety.severity_levels (
    severity            VARCHAR(8)  PRIMARY KEY,
    severity_rank       SMALLINT    NOT NULL UNIQUE,                  -- 1 = most urgent
    sla_target_seconds  INTEGER     NOT NULL CHECK (sla_target_seconds > 0)
);
COMMENT ON TABLE safety.severity_levels IS 'Triage levels; the SLA target is the time allowed from report to arrival on scene';

INSERT INTO safety.severity_levels (severity, severity_rank, sla_target_seconds) VALUES
    ('CRITICAL', 1, 300), ('HIGH', 2, 420), ('MEDIUM', 3, 600), ('LOW', 4, 900)
ON CONFLICT (severity) DO NOTHING;

CREATE TABLE IF NOT EXISTS safety.incident_types (
    type_code                     VARCHAR(24)  PRIMARY KEY,
    name_ko                       VARCHAR(64)  NOT NULL,
    name_en                       VARCHAR(64)  NOT NULL,
    responder                     VARCHAR(8)   NOT NULL CHECK (responder IN ('FIRE', 'POLICE')),
    default_restriction_radius_m  INTEGER      NOT NULL CHECK (default_restriction_radius_m BETWEEN 0 AND 2000)
);
COMMENT ON TABLE safety.incident_types IS 'Incident categories with the responder that handles them and the default closed-off radius';

INSERT INTO safety.incident_types (type_code, name_ko, name_en, responder, default_restriction_radius_m) VALUES
    ('FIRE',             '화재',       'Fire',                 'FIRE',   300),
    ('MEDICAL',          '응급의료',   'Medical emergency',    'FIRE',    50),
    ('HAZMAT',           '위험물 누출', 'Hazardous material',   'FIRE',   500),
    ('FLOOD',            '침수',       'Flooding',             'FIRE',   200),
    ('TRAFFIC_ACCIDENT', '교통사고',   'Traffic accident',     'POLICE', 150),
    ('ROAD_OBSTRUCTION', '도로 장애물', 'Road obstruction',     'POLICE', 100)
ON CONFLICT (type_code) DO NOTHING;

-- ---------------------------------------------------------------------------
-- State machine
--   REPORTED -> DISPATCHED -> ON_SCENE -> RESOLVED -> CLOSED
--   REPORTED -> CANCELLED,  DISPATCHED -> CANCELLED
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION safety.valid_incident_transition(p_from VARCHAR, p_to VARCHAR)
RETURNS BOOLEAN
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$
    SELECT COALESCE((p_from, p_to) IN (
        ('REPORTED',   'DISPATCHED'),
        ('REPORTED',   'CANCELLED'),
        ('DISPATCHED', 'ON_SCENE'),
        ('DISPATCHED', 'CANCELLED'),
        ('ON_SCENE',   'RESOLVED'),
        ('RESOLVED',   'CLOSED')
    ), FALSE)
$$;
COMMENT ON FUNCTION safety.valid_incident_transition(VARCHAR, VARCHAR) IS
    'TRUE when an incident may move from p_from to p_to';

-- ---------------------------------------------------------------------------
-- Incidents (current state) and their full status history
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS safety.incidents (
    incident_id            VARCHAR(32)  PRIMARY KEY,
    type_code              VARCHAR(24)  NOT NULL REFERENCES safety.incident_types(type_code),
    title                  VARCHAR(255) NOT NULL,
    severity               VARCHAR(8)   NOT NULL REFERENCES safety.severity_levels(severity),
    current_status         VARCHAR(12)  NOT NULL DEFAULT 'REPORTED'
        CHECK (current_status IN ('REPORTED', 'DISPATCHED', 'ON_SCENE', 'RESOLVED', 'CLOSED', 'CANCELLED')),
    reported_at            TIMESTAMPTZ  NOT NULL,
    assigned_facility_id   VARCHAR(32)  REFERENCES safety.facilities(facility_id),
    restriction_radius_m   INTEGER      NOT NULL CHECK (restriction_radius_m BETWEEN 0 AND 2000),
    is_drill               BOOLEAN      NOT NULL DEFAULT FALSE,
    geom                   public.GEOMETRY(Point, 4326) NOT NULL,
    source_id              VARCHAR(32)  NOT NULL REFERENCES core.source_registry(source_id),
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT ck_incidents_in_songdo CHECK (core.is_in_songdo(geom)),
    -- nobody can be on scene without a unit having been assigned
    CONSTRAINT ck_incidents_assigned CHECK (
        current_status IN ('REPORTED', 'CANCELLED') OR assigned_facility_id IS NOT NULL)
);
COMMENT ON TABLE  safety.incidents IS 'Emergency reports. current_status is changed only through safety.transition_incident()';
COMMENT ON COLUMN safety.incidents.restriction_radius_m IS 'Radius closed to traffic around the incident while it is active';

CREATE INDEX IF NOT EXISTS idx_incidents_geom ON safety.incidents USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_incidents_geog ON safety.incidents USING GIST ((geom::public.geography));
CREATE INDEX IF NOT EXISTS idx_incidents_reported ON safety.incidents (reported_at);
CREATE INDEX IF NOT EXISTS idx_incidents_active ON safety.incidents (current_status)
    WHERE current_status IN ('REPORTED', 'DISPATCHED', 'ON_SCENE');

CREATE TABLE IF NOT EXISTS safety.incident_status_history (
    history_id    BIGSERIAL    PRIMARY KEY,
    incident_id   VARCHAR(32)  NOT NULL REFERENCES safety.incidents(incident_id),
    from_status   VARCHAR(12),                                     -- NULL only for the initial REPORTED row
    to_status     VARCHAR(12)  NOT NULL,
    changed_at    TIMESTAMPTZ  NOT NULL,
    actor         VARCHAR(64)  NOT NULL DEFAULT 'SYSTEM',
    facility_id   VARCHAR(32)  REFERENCES safety.facilities(facility_id),
    note          TEXT,
    recorded_at   TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    -- every stored step must itself be a legal transition
    CONSTRAINT ck_history_transition CHECK (
        (from_status IS NULL AND to_status = 'REPORTED')
        OR safety.valid_incident_transition(from_status, to_status)),
    -- the machine has no loops, so each status is reached at most once per incident
    CONSTRAINT uq_history_status UNIQUE (incident_id, to_status)
);
COMMENT ON TABLE safety.incident_status_history IS
    'Append-only log of every status change; the status of any incident at any past time T is read from here';
CREATE INDEX IF NOT EXISTS idx_history_incident_time ON safety.incident_status_history (incident_id, changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_history_time ON safety.incident_status_history (changed_at);

DROP TRIGGER IF EXISTS trg_history_append_only ON safety.incident_status_history;
CREATE TRIGGER trg_history_append_only
    BEFORE UPDATE OR DELETE ON safety.incident_status_history
    FOR EACH ROW EXECUTE FUNCTION core.forbid_mutation();

-- Guard: a direct UPDATE of current_status must still obey the state machine.
CREATE OR REPLACE FUNCTION safety.incidents_status_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.current_status IS DISTINCT FROM OLD.current_status
       AND NOT safety.valid_incident_transition(OLD.current_status, NEW.current_status) THEN
        RAISE EXCEPTION 'incident %: transition % -> % is not allowed',
            OLD.incident_id, OLD.current_status, NEW.current_status
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

-- Log: every creation and every status change writes one history row (and one audit event).
-- Actor, time and note are passed by safety.transition_incident() through transaction-local settings.
CREATE OR REPLACE FUNCTION safety.incidents_status_log()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_actor  VARCHAR     := COALESCE(NULLIF(current_setting('safety.actor', TRUE), ''), 'SYSTEM');
    v_note   TEXT        := NULLIF(current_setting('safety.note', TRUE), '');
    v_at     TIMESTAMPTZ := NULLIF(current_setting('safety.changed_at', TRUE), '')::TIMESTAMPTZ;
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO safety.incident_status_history (incident_id, from_status, to_status, changed_at, actor, note)
        VALUES (NEW.incident_id, NULL, 'REPORTED', NEW.reported_at, v_actor, v_note);
    ELSIF NEW.current_status IS DISTINCT FROM OLD.current_status THEN
        v_at := COALESCE(v_at, clock_timestamp());
        INSERT INTO safety.incident_status_history
            (incident_id, from_status, to_status, changed_at, actor, facility_id, note)
        VALUES (NEW.incident_id, OLD.current_status, NEW.current_status, v_at, v_actor,
                CASE WHEN NEW.current_status = 'DISPATCHED' THEN NEW.assigned_facility_id END, v_note);
        PERFORM core.log_event(
            'safety', 'incidents', NEW.incident_id,
            CASE WHEN NEW.current_status = 'DISPATCHED' THEN 'DISPATCH' ELSE 'STATUS_CHANGE' END,
            v_at, clock_timestamp(), NEW.source_id,
            jsonb_build_object('from_status', OLD.current_status, 'to_status', NEW.current_status,
                               'facility_id', NEW.assigned_facility_id, 'note', v_note),
            v_actor);
    END IF;
    RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_incidents_status_guard ON safety.incidents;
CREATE TRIGGER trg_incidents_status_guard
    BEFORE UPDATE OF current_status ON safety.incidents
    FOR EACH ROW EXECUTE FUNCTION safety.incidents_status_guard();

DROP TRIGGER IF EXISTS trg_incidents_status_log ON safety.incidents;
CREATE TRIGGER trg_incidents_status_log
    AFTER INSERT OR UPDATE OF current_status ON safety.incidents
    FOR EACH ROW EXECUTE FUNCTION safety.incidents_status_log();

-- The one way to move an incident. Locks the row, so two dispatchers acting on the
-- same incident at once are serialised and the second one sees the new status.
CREATE OR REPLACE FUNCTION safety.transition_incident(
    p_incident_id  VARCHAR,
    p_to_status    VARCHAR,
    p_at           TIMESTAMPTZ DEFAULT NULL,
    p_actor        VARCHAR DEFAULT 'SYSTEM',
    p_facility_id  VARCHAR DEFAULT NULL,
    p_note         TEXT DEFAULT NULL
)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    v_current   VARCHAR;
    v_assigned  VARCHAR;
    v_last      TIMESTAMPTZ;
    v_at        TIMESTAMPTZ := COALESCE(p_at, clock_timestamp());
BEGIN
    SELECT current_status, assigned_facility_id INTO v_current, v_assigned
    FROM safety.incidents WHERE incident_id = p_incident_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown incident %', p_incident_id USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT safety.valid_incident_transition(v_current, p_to_status) THEN
        RAISE EXCEPTION 'incident %: transition % -> % is not allowed', p_incident_id, v_current, p_to_status
            USING ERRCODE = 'check_violation';
    END IF;
    IF p_to_status = 'DISPATCHED' AND COALESCE(p_facility_id, v_assigned) IS NULL THEN
        RAISE EXCEPTION 'incident %: a facility is required to dispatch', p_incident_id
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT MAX(changed_at) INTO v_last FROM safety.incident_status_history WHERE incident_id = p_incident_id;
    IF v_at < v_last THEN
        RAISE EXCEPTION 'incident %: change time % is before the previous change %', p_incident_id, v_at, v_last
            USING ERRCODE = 'check_violation';
    END IF;

    PERFORM set_config('safety.actor', COALESCE(p_actor, 'SYSTEM'), TRUE),
            set_config('safety.note', COALESCE(p_note, ''), TRUE),
            set_config('safety.changed_at', v_at::TEXT, TRUE);
    UPDATE safety.incidents
    SET current_status = p_to_status,
        assigned_facility_id = COALESCE(p_facility_id, assigned_facility_id)
    WHERE incident_id = p_incident_id;
    PERFORM set_config('safety.actor', '', TRUE), set_config('safety.note', '', TRUE),
            set_config('safety.changed_at', '', TRUE);
    RETURN p_to_status;
END
$$;

-- ---------------------------------------------------------------------------
-- Response drills
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS safety.response_drills (
    drill_id          BIGSERIAL    PRIMARY KEY,
    incident_id       VARCHAR(32)  NOT NULL UNIQUE REFERENCES safety.incidents(incident_id),
    facility_id       VARCHAR(32)  NOT NULL REFERENCES safety.facilities(facility_id),
    held_at           TIMESTAMPTZ  NOT NULL,
    target_seconds    INTEGER      NOT NULL CHECK (target_seconds > 0),
    response_seconds  INTEGER      NOT NULL CHECK (response_seconds >= 0),
    passed            BOOLEAN GENERATED ALWAYS AS (response_seconds <= target_seconds) STORED
);
COMMENT ON TABLE safety.response_drills IS
    'Dispatch exercises: a simulated incident (is_drill) answered by a facility, scored against a target';
CREATE INDEX IF NOT EXISTS idx_drills_facility ON safety.response_drills (facility_id, held_at);
