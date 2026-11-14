-- =============================================================================
-- 02_db1_mobility.sql — DB1 Mobility
--   stops · routes · route_stops · vehicles · trips · trip_stop_events ·
--   vehicle_observations
-- Re-runnable. Depends on 00 (extensions, schemas) and 01 (core).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Stops (real data: MOLIT national bus stop file, Songdo subset)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mobility.stops (
    stop_id        VARCHAR(32)  PRIMARY KEY,               -- 정류장번호, e.g. ICB364000367
    stop_number    VARCHAR(16)  NOT NULL UNIQUE,           -- 모바일단축번호 printed on the stop sign
    stop_name_ko   VARCHAR(255) NOT NULL,
    stop_name_en   VARCHAR(255),
    geom           public.GEOMETRY(Point, 4326) NOT NULL,
    managing_bis   VARCHAR(32)  NOT NULL,                  -- 관리도시명
    collected_on   DATE,                                   -- 정보수집일
    source_id      VARCHAR(32)  NOT NULL REFERENCES core.source_registry(source_id),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT ck_stops_in_songdo CHECK (core.is_in_songdo(geom))
);
COMMENT ON TABLE mobility.stops IS 'Bus stops inside the Songdo bbox; one row per physical stop pole';

CREATE INDEX IF NOT EXISTS idx_stops_geom ON mobility.stops USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_stops_geog ON mobility.stops USING GIST ((geom::public.geography));
CREATE INDEX IF NOT EXISTS idx_stops_name ON mobility.stops (stop_name_ko);

-- ---------------------------------------------------------------------------
-- Routes and their ordered stop pattern
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mobility.routes (
    route_id       VARCHAR(32)  PRIMARY KEY,
    route_number   VARCHAR(16)  NOT NULL UNIQUE,
    route_name_ko  VARCHAR(255) NOT NULL,
    route_name_en  VARCHAR(255),
    route_type     VARCHAR(24)  NOT NULL
        CHECK (route_type IN ('TRUNK', 'BRANCH', 'EXPRESS', 'AUTONOMOUS_SHUTTLE')),
    source_id      VARCHAR(32)  NOT NULL REFERENCES core.source_registry(source_id),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp()
);
COMMENT ON TABLE mobility.routes IS 'Transit routes serving Songdo';

CREATE TABLE IF NOT EXISTS mobility.route_stops (
    route_id        VARCHAR(32) NOT NULL REFERENCES mobility.routes(route_id),
    direction       SMALLINT    NOT NULL CHECK (direction IN (0, 1)),   -- 0 outbound, 1 inbound
    sequence_order  INTEGER     NOT NULL CHECK (sequence_order > 0),
    stop_id         VARCHAR(32) NOT NULL REFERENCES mobility.stops(stop_id),
    -- candidate key 1: position in the pattern
    PRIMARY KEY (route_id, direction, sequence_order),
    -- candidate key 2: a stop appears once per direction
    CONSTRAINT uq_route_stops_stop UNIQUE (route_id, direction, stop_id)
);
COMMENT ON TABLE mobility.route_stops IS
    'Ordered stop pattern. (route_id, direction, sequence_order) guarantees a gap-free ordering key';

-- ---------------------------------------------------------------------------
-- Vehicles and trips
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mobility.vehicles (
    vehicle_id     VARCHAR(32)  PRIMARY KEY,
    plate_number   VARCHAR(16)  NOT NULL UNIQUE,
    vehicle_type   VARCHAR(24)  NOT NULL CHECK (vehicle_type IN ('BUS', 'EV_BUS', 'AUTONOMOUS_SHUTTLE')),
    capacity       INTEGER      NOT NULL CHECK (capacity BETWEEN 1 AND 200),
    is_active      BOOLEAN      NOT NULL DEFAULT TRUE,
    source_id      VARCHAR(32)  NOT NULL REFERENCES core.source_registry(source_id),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp()
);
COMMENT ON TABLE mobility.vehicles IS 'Fleet: buses and autonomous shuttles';

CREATE TABLE IF NOT EXISTS mobility.trips (
    trip_id              VARCHAR(48)  PRIMARY KEY,
    route_id             VARCHAR(32)  NOT NULL REFERENCES mobility.routes(route_id),
    direction            SMALLINT     NOT NULL CHECK (direction IN (0, 1)),
    vehicle_id           VARCHAR(32)  NOT NULL REFERENCES mobility.vehicles(vehicle_id),
    service_date         DATE         NOT NULL,
    scheduled_departure  TIMESTAMPTZ  NOT NULL,
    scheduled_arrival    TIMESTAMPTZ  NOT NULL,
    status               VARCHAR(16)  NOT NULL DEFAULT 'SCHEDULED'
        CHECK (status IN ('SCHEDULED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')),
    source_id            VARCHAR(32)  NOT NULL REFERENCES core.source_registry(source_id),
    CONSTRAINT ck_trips_time_order CHECK (scheduled_arrival > scheduled_departure),
    CONSTRAINT uq_trips_departure UNIQUE (route_id, direction, scheduled_departure),
    -- One vehicle cannot run two trips at the same time (needs btree_gist for "=" on varchar).
    CONSTRAINT ex_trips_vehicle_overlap EXCLUDE USING GIST (
        vehicle_id WITH =,
        tstzrange(scheduled_departure, scheduled_arrival) WITH &&
    ) WHERE (status <> 'CANCELLED')
);
COMMENT ON TABLE mobility.trips IS 'One scheduled run of a route by one vehicle';
CREATE INDEX IF NOT EXISTS idx_trips_route_time ON mobility.trips (route_id, direction, scheduled_departure);

CREATE TABLE IF NOT EXISTS mobility.trip_stop_events (
    trip_id             VARCHAR(48) NOT NULL REFERENCES mobility.trips(trip_id),
    sequence_order      INTEGER     NOT NULL CHECK (sequence_order > 0),
    stop_id             VARCHAR(32) NOT NULL REFERENCES mobility.stops(stop_id),
    scheduled_arrival   TIMESTAMPTZ NOT NULL,
    observed_arrival    TIMESTAMPTZ,
    observed_departure  TIMESTAMPTZ,
    boardings           INTEGER     NOT NULL DEFAULT 0 CHECK (boardings  >= 0),
    alightings          INTEGER     NOT NULL DEFAULT 0 CHECK (alightings >= 0),
    -- Derived values are computed by the database, never by application code.
    lateness_seconds    INTEGER GENERATED ALWAYS AS
        (EXTRACT(EPOCH FROM (observed_arrival - scheduled_arrival))::INTEGER) STORED,
    dwell_seconds       INTEGER GENERATED ALWAYS AS
        (EXTRACT(EPOCH FROM (observed_departure - observed_arrival))::INTEGER) STORED,
    PRIMARY KEY (trip_id, sequence_order),
    CONSTRAINT uq_trip_stop UNIQUE (trip_id, stop_id),
    CONSTRAINT ck_event_dwell CHECK (observed_departure IS NULL OR observed_departure >= observed_arrival)
);
COMMENT ON TABLE  mobility.trip_stop_events IS 'Arrival/departure of a trip at each stop of its pattern';
COMMENT ON COLUMN mobility.trip_stop_events.lateness_seconds IS 'observed - scheduled arrival; negative = early';
CREATE INDEX IF NOT EXISTS idx_events_stop_arrival ON mobility.trip_stop_events (stop_id, observed_arrival);

-- ---------------------------------------------------------------------------
-- High-frequency vehicle telemetry (append-only history, never updated in place)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mobility.vehicle_observations (
    observation_id   BIGSERIAL    PRIMARY KEY,
    vehicle_id       VARCHAR(32)  NOT NULL REFERENCES mobility.vehicles(vehicle_id),
    trip_id          VARCHAR(48)  REFERENCES mobility.trips(trip_id),
    geom             public.GEOMETRY(Point, 4326) NOT NULL,
    speed_kmh        NUMERIC(5,1) NOT NULL CHECK (speed_kmh BETWEEN 0 AND 150),
    heading_deg      NUMERIC(5,1) CHECK (heading_deg >= 0 AND heading_deg < 360),
    battery_state_of_charge_pct NUMERIC(5,2) CHECK (battery_state_of_charge_pct BETWEEN 0 AND 100),
    observed_at      TIMESTAMPTZ  NOT NULL,
    received_at      TIMESTAMPTZ  NOT NULL,
    recorded_at      TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    source_id        VARCHAR(32)  NOT NULL REFERENCES core.source_registry(source_id),
    CONSTRAINT ck_obs_in_songdo CHECK (core.is_in_songdo(geom)),
    CONSTRAINT ck_obs_received  CHECK (received_at >= observed_at - INTERVAL '5 minutes'),
    -- natural key: one position per vehicle per instant; also serves "latest before T" lookups
    CONSTRAINT uq_obs_vehicle_time UNIQUE (vehicle_id, observed_at)
);
COMMENT ON TABLE mobility.vehicle_observations IS
    'GPS telemetry history. Point-in-time state is reconstructed with DISTINCT ON (vehicle_id) ... ORDER BY observed_at DESC';

CREATE INDEX IF NOT EXISTS idx_obs_geom ON mobility.vehicle_observations USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_obs_geog ON mobility.vehicle_observations USING GIST ((geom::public.geography));
CREATE INDEX IF NOT EXISTS idx_obs_time ON mobility.vehicle_observations (observed_at DESC);
