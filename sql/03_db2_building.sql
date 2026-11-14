-- =============================================================================
-- 03_db2_building.sql — DB2 Smart Building
--   buildings · spaces · devices · device_space_assignments · metric_types ·
--   telemetry · maintenance_actions
-- Re-runnable. Depends on 00 (extensions, schemas) and 01 (core).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Buildings (registry rows reconciled to canonical core.places)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS building.buildings (
    building_id          VARCHAR(32)  PRIMARY KEY,
    building_name_ko     VARCHAR(255) NOT NULL,
    building_name_en     VARCHAR(255),
    building_use         VARCHAR(24)  NOT NULL
        CHECK (building_use IN ('OFFICE', 'CONVENTION', 'CULTURE', 'EDUCATION', 'RETAIL', 'TESTBED')),
    road_address         VARCHAR(255),
    floors_above         SMALLINT     NOT NULL CHECK (floors_above >= 1),
    floors_below         SMALLINT     NOT NULL DEFAULT 0 CHECK (floors_below >= 0),
    place_id             VARCHAR(64)  REFERENCES core.places(place_id),   -- NULL = not reconciled yet
    geom                 public.GEOMETRY(Point, 4326) NOT NULL,           -- registry centroid
    source_id            VARCHAR(32)  NOT NULL REFERENCES core.source_registry(source_id),
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT ck_buildings_in_songdo CHECK (core.is_in_songdo(geom))
);
COMMENT ON TABLE  building.buildings IS 'Building registry rows; place_id links each one to its canonical place';
COMMENT ON COLUMN building.buildings.place_id IS 'Result of address/centroid reconciliation (Scenario DB2-1)';

CREATE INDEX IF NOT EXISTS idx_buildings_geom ON building.buildings USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_buildings_geog ON building.buildings USING GIST ((geom::public.geography));

-- ---------------------------------------------------------------------------
-- Space hierarchy: floor -> room / plant room (self-referencing tree)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS building.spaces (
    space_id         VARCHAR(48)  PRIMARY KEY,
    building_id      VARCHAR(32)  NOT NULL REFERENCES building.buildings(building_id),
    parent_space_id  VARCHAR(48),
    space_type       VARCHAR(16)  NOT NULL
        CHECK (space_type IN ('FLOOR', 'ROOM', 'PLANT_ROOM', 'LOBBY')),
    space_name       VARCHAR(128) NOT NULL,
    floor_number     SMALLINT     NOT NULL CHECK (floor_number <> 0),      -- -1 = B1, 1 = 1F
    area_m2          NUMERIC(8,1) CHECK (area_m2 > 0),
    CONSTRAINT uq_spaces_building_space UNIQUE (building_id, space_id),
    CONSTRAINT uq_spaces_name UNIQUE (building_id, parent_space_id, space_name),
    -- the composite FK forces a child to be in the SAME building as its parent
    CONSTRAINT fk_spaces_parent FOREIGN KEY (building_id, parent_space_id)
        REFERENCES building.spaces (building_id, space_id),
    CONSTRAINT ck_spaces_floor_is_root CHECK ((space_type = 'FLOOR') = (parent_space_id IS NULL))
);
COMMENT ON TABLE building.spaces IS 'Interior spaces as a tree: floors at the root, rooms and plant rooms below';
CREATE INDEX IF NOT EXISTS idx_spaces_parent ON building.spaces (parent_space_id);

-- ---------------------------------------------------------------------------
-- Devices and where they are installed over time
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS building.devices (
    device_id       VARCHAR(48)  PRIMARY KEY,
    device_type     VARCHAR(24)  NOT NULL
        CHECK (device_type IN ('TEMP_SENSOR', 'CO2_SENSOR', 'POWER_METER', 'HVAC_CONTROLLER')),
    manufacturer    VARCHAR(64)  NOT NULL,
    model           VARCHAR(64)  NOT NULL,
    serial_number   VARCHAR(64)  NOT NULL UNIQUE,
    protocol        VARCHAR(16)  NOT NULL CHECK (protocol IN ('BACNET', 'MODBUS', 'MQTT')),
    installed_on    DATE         NOT NULL,
    current_status  VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE'
        CHECK (current_status IN ('ACTIVE', 'FAULT', 'MAINTENANCE', 'RETIRED')),
    source_id       VARCHAR(32)  NOT NULL REFERENCES core.source_registry(source_id),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp()
);
COMMENT ON TABLE building.devices IS 'IoT devices; location is NOT stored here, see device_space_assignments';

CREATE TABLE IF NOT EXISTS building.device_space_assignments (
    assignment_id   BIGSERIAL    PRIMARY KEY,
    device_id       VARCHAR(48)  NOT NULL REFERENCES building.devices(device_id),
    space_id        VARCHAR(48)  NOT NULL REFERENCES building.spaces(space_id),
    assigned_at     TIMESTAMPTZ  NOT NULL,
    unassigned_at   TIMESTAMPTZ,                                           -- NULL = still installed here
    CONSTRAINT ck_assignment_period CHECK (unassigned_at IS NULL OR unassigned_at > assigned_at),
    -- A device is in at most one space at any instant (valid-time history, no overlaps).
    CONSTRAINT ex_assignment_no_overlap EXCLUDE USING GIST (
        device_id WITH =,
        tstzrange(assigned_at, unassigned_at) WITH &&
    )
);
COMMENT ON TABLE building.device_space_assignments IS
    'Valid-time history of device locations. Moving a device closes one row and opens another; nothing is overwritten';
CREATE INDEX IF NOT EXISTS idx_assignment_space_current
    ON building.device_space_assignments (space_id) WHERE unassigned_at IS NULL;

-- Move a device: close the open assignment and open a new one, atomically.
-- FOR UPDATE serialises two concurrent relocations of the same device.
CREATE OR REPLACE FUNCTION building.relocate_device(
    p_device_id  VARCHAR,
    p_space_id   VARCHAR,
    p_at         TIMESTAMPTZ,
    p_actor      VARCHAR DEFAULT 'SYSTEM'
)
RETURNS BIGINT
LANGUAGE plpgsql
AS $$
DECLARE
    v_current  building.device_space_assignments%ROWTYPE;
    v_new_id   BIGINT;
BEGIN
    SELECT * INTO v_current
    FROM building.device_space_assignments
    WHERE device_id = p_device_id AND unassigned_at IS NULL
    FOR UPDATE;

    IF FOUND THEN
        IF v_current.space_id = p_space_id THEN
            RAISE EXCEPTION 'device % is already installed in space %', p_device_id, p_space_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF p_at <= v_current.assigned_at THEN
            RAISE EXCEPTION 'relocation time % is not after the current assignment start %',
                p_at, v_current.assigned_at USING ERRCODE = 'check_violation';
        END IF;
        UPDATE building.device_space_assignments
        SET unassigned_at = p_at
        WHERE assignment_id = v_current.assignment_id;
    END IF;

    INSERT INTO building.device_space_assignments (device_id, space_id, assigned_at)
    VALUES (p_device_id, p_space_id, p_at)
    RETURNING assignment_id INTO v_new_id;

    PERFORM core.log_event(
        'building', 'device_space_assignments', p_device_id, 'UPDATE', p_at, clock_timestamp(),
        'SRC_BLD_SYN',
        jsonb_build_object('from_space', v_current.space_id, 'to_space', p_space_id,
                           'assignment_id', v_new_id),
        p_actor);
    RETURN v_new_id;
END
$$;

-- ---------------------------------------------------------------------------
-- Telemetry
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS building.metric_types (
    metric_code   VARCHAR(24)  PRIMARY KEY,
    description   VARCHAR(128) NOT NULL,
    unit          VARCHAR(16)  NOT NULL,
    min_valid     NUMERIC      NOT NULL,
    max_valid     NUMERIC      NOT NULL,
    CHECK (max_valid > min_valid)
);
COMMENT ON TABLE building.metric_types IS 'Unit and plausible range per metric; readings outside the range are flagged SUSPECT';

INSERT INTO building.metric_types (metric_code, description, unit, min_valid, max_valid) VALUES
    ('TEMPERATURE_C', 'Air temperature',           '°C',  -20,   60),
    ('CO2_PPM',       'CO2 concentration',         'ppm', 300, 5000),
    ('POWER_KW',      'Electrical power demand',   'kW',    0, 20000)
ON CONFLICT (metric_code) DO NOTHING;

CREATE TABLE IF NOT EXISTS building.telemetry (
    telemetry_id   BIGSERIAL     PRIMARY KEY,
    device_id      VARCHAR(48)   NOT NULL REFERENCES building.devices(device_id),
    metric         VARCHAR(24)   NOT NULL REFERENCES building.metric_types(metric_code),
    value          NUMERIC(10,3) NOT NULL,
    quality_flag   VARCHAR(8)    NOT NULL DEFAULT 'GOOD' CHECK (quality_flag IN ('GOOD', 'SUSPECT')),
    observed_at    TIMESTAMPTZ   NOT NULL,
    received_at    TIMESTAMPTZ   NOT NULL,
    recorded_at    TIMESTAMPTZ   NOT NULL DEFAULT clock_timestamp(),
    -- natural key; also the index behind "latest reading per device" and heartbeat checks
    CONSTRAINT uq_telemetry_reading UNIQUE (device_id, metric, observed_at)
);
COMMENT ON TABLE building.telemetry IS 'Device readings. Any reading also counts as a heartbeat (Scenario DB2-4)';
CREATE INDEX IF NOT EXISTS idx_telemetry_time ON building.telemetry (observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_telemetry_device_time ON building.telemetry (device_id, observed_at DESC);

-- ---------------------------------------------------------------------------
-- Maintenance
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS building.maintenance_actions (
    action_id     BIGSERIAL    PRIMARY KEY,
    device_id     VARCHAR(48)  NOT NULL REFERENCES building.devices(device_id),
    action_type   VARCHAR(24)  NOT NULL
        CHECK (action_type IN ('INSPECTION', 'CALIBRATION', 'REPAIR', 'REPLACEMENT', 'FIRMWARE_UPDATE')),
    description   TEXT         NOT NULL,
    technician    VARCHAR(64)  NOT NULL,
    started_at    TIMESTAMPTZ  NOT NULL,
    completed_at  TIMESTAMPTZ,                                             -- NULL = still open
    CONSTRAINT ck_maintenance_period CHECK (completed_at IS NULL OR completed_at >= started_at),
    CONSTRAINT uq_maintenance UNIQUE (device_id, action_type, started_at)
);
COMMENT ON TABLE building.maintenance_actions IS 'Work done on a device; an open action has completed_at IS NULL';
