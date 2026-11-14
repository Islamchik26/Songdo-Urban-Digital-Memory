-- =============================================================================
-- 04_db3_tourism.sql — DB3 Tourism
--   attractions · operating_hours · events · aggregate_visits · sample_visits
-- Re-runnable. Depends on 00 (extensions, schemas) and 01 (core).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Attractions with accessibility metadata
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tourism.attractions (
    attraction_id             VARCHAR(32)  PRIMARY KEY,
    attraction_name_ko        VARCHAR(255) NOT NULL,
    attraction_name_en        VARCHAR(255),
    category                  VARCHAR(24)  NOT NULL
        CHECK (category IN ('PARK', 'CULTURE', 'CONVENTION', 'SHOPPING', 'LANDMARK')),
    road_address              VARCHAR(255),
    is_wheelchair_accessible  BOOLEAN      NOT NULL,
    has_parking               BOOLEAN      NOT NULL,
    parking_spaces            INTEGER      CHECK (parking_spaces > 0),
    admission_fee_krw         INTEGER      NOT NULL DEFAULT 0 CHECK (admission_fee_krw >= 0),
    place_id                  VARCHAR(64)  REFERENCES core.places(place_id),
    geom                      public.GEOMETRY(Point, 4326) NOT NULL,
    source_id                 VARCHAR(32)  NOT NULL REFERENCES core.source_registry(source_id),
    created_at                TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT ck_attractions_in_songdo CHECK (core.is_in_songdo(geom)),
    -- a space count only makes sense when there is parking
    CONSTRAINT ck_attractions_parking CHECK (has_parking OR parking_spaces IS NULL)
);
COMMENT ON TABLE tourism.attractions IS 'Visitor attractions and venues in Songdo with accessibility metadata';

CREATE INDEX IF NOT EXISTS idx_attractions_geom ON tourism.attractions USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_attractions_geog ON tourism.attractions USING GIST ((geom::public.geography));

-- ---------------------------------------------------------------------------
-- Operating hours: one row per open interval. No row for a weekday = closed that day.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tourism.operating_hours (
    hours_id       BIGSERIAL    PRIMARY KEY,
    attraction_id  VARCHAR(32)  NOT NULL REFERENCES tourism.attractions(attraction_id),
    day_of_week    SMALLINT     NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),   -- 0 = Sunday (EXTRACT(DOW))
    opens_at       TIME         NOT NULL,
    closes_at      TIME         NOT NULL,                                        -- '24:00' = end of day
    CONSTRAINT ck_hours_order CHECK (closes_at > opens_at),
    CONSTRAINT uq_hours UNIQUE (attraction_id, day_of_week, opens_at),
    -- Two intervals of the same attraction on the same day may not overlap
    -- (e.g. 11:00-15:00 and 17:00-21:00 is fine; 11:00-15:00 and 14:00-18:00 is rejected).
    CONSTRAINT ex_hours_no_overlap EXCLUDE USING GIST (
        attraction_id WITH =,
        day_of_week WITH =,
        int4range((EXTRACT(EPOCH FROM opens_at) / 60)::INTEGER,
                  (EXTRACT(EPOCH FROM closes_at) / 60)::INTEGER) WITH &&
    )
);
COMMENT ON TABLE tourism.operating_hours IS 'Daily open intervals per weekday; a missing weekday means closed';

-- ---------------------------------------------------------------------------
-- Events
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tourism.events (
    event_id             VARCHAR(48)  PRIMARY KEY,
    attraction_id        VARCHAR(32)  NOT NULL REFERENCES tourism.attractions(attraction_id),
    event_name_ko        VARCHAR(255) NOT NULL,
    event_name_en        VARCHAR(255),
    event_type           VARCHAR(16)  NOT NULL
        CHECK (event_type IN ('CONFERENCE', 'FESTIVAL', 'EXHIBITION', 'CONCERT')),
    start_date           DATE         NOT NULL,
    end_date             DATE         NOT NULL,
    daily_start_time     TIME         NOT NULL,
    daily_end_time       TIME         NOT NULL,
    expected_attendance  INTEGER      NOT NULL CHECK (expected_attendance > 0),   -- per day
    is_cancelled         BOOLEAN      NOT NULL DEFAULT FALSE,
    source_id            VARCHAR(32)  NOT NULL REFERENCES core.source_registry(source_id),
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT ck_events_dates CHECK (end_date >= start_date),
    CONSTRAINT ck_events_times CHECK (daily_end_time > daily_start_time)
);
COMMENT ON TABLE  tourism.events IS 'Scheduled events at an attraction; cancelled events are kept, not deleted';
COMMENT ON COLUMN tourism.events.expected_attendance IS 'Expected visitors per event day';
CREATE INDEX IF NOT EXISTS idx_events_dates ON tourism.events USING GIST (daterange(start_date, end_date, '[]'));
CREATE INDEX IF NOT EXISTS idx_events_attraction ON tourism.events (attraction_id);

-- ---------------------------------------------------------------------------
-- Visitor numbers: official monthly totals and granular samples are separate tables
-- on purpose. They have different grain, different provenance and must never be mixed.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tourism.aggregate_visits (
    attraction_id  VARCHAR(32)  NOT NULL REFERENCES tourism.attractions(attraction_id),
    year_month     DATE         NOT NULL CHECK (EXTRACT(DAY FROM year_month) = 1),   -- first day of the month
    visitor_count  INTEGER      NOT NULL CHECK (visitor_count >= 0),
    source_id      VARCHAR(32)  NOT NULL REFERENCES core.source_registry(source_id),
    recorded_at    TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (attraction_id, year_month)
);
COMMENT ON TABLE tourism.aggregate_visits IS 'Published monthly visitor totals (statistics grain: attraction x month)';

CREATE TABLE IF NOT EXISTS tourism.sample_visits (
    attraction_id  VARCHAR(32)  NOT NULL REFERENCES tourism.attractions(attraction_id),
    hour_start     TIMESTAMPTZ  NOT NULL
        CHECK (EXTRACT(EPOCH FROM hour_start)::BIGINT % 3600 = 0),                   -- aligned to the hour
    visitor_count  INTEGER      NOT NULL CHECK (visitor_count >= 0),
    source_id      VARCHAR(32)  NOT NULL REFERENCES core.source_registry(source_id),
    received_at    TIMESTAMPTZ  NOT NULL,
    recorded_at    TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (attraction_id, hour_start)
);
COMMENT ON TABLE tourism.sample_visits IS
    'Hourly counts from a sensor sample (grain: attraction x hour). Covers only part of all visitors';
CREATE INDEX IF NOT EXISTS idx_sample_visits_hour ON tourism.sample_visits (hour_start);
