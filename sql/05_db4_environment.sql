-- =============================================================================
-- 05_db4_environment.sql — DB4 Environment
--   stations · measurement_types · alert_rules · measurements ·
--   hourly_summaries · alerts
-- Re-runnable. Depends on 00 (extensions, schemas) and 01 (core).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Stations (real data: AirKorea station list, Songdo subset)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS environment.stations (
    station_id        VARCHAR(48)  PRIMARY KEY,                -- 'AK_' || AirKorea station name
    station_name_ko   VARCHAR(64)  NOT NULL UNIQUE,            -- 측정소명, the key used by the AirKorea API
    address           VARCHAR(255),
    network           VARCHAR(32)  NOT NULL,                   -- 측정망: 도시대기 / 도로변대기 / 항만 ...
    measured_items    VARCHAR(128),
    established_year  SMALLINT     CHECK (established_year BETWEEN 1970 AND 2100),
    geom              public.GEOMETRY(Point, 4326) NOT NULL,
    source_id         VARCHAR(32)  NOT NULL REFERENCES core.source_registry(source_id),
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT ck_env_stations_in_songdo CHECK (core.is_in_songdo(geom))
);
COMMENT ON TABLE environment.stations IS 'Air-quality monitoring stations inside the Songdo bbox';

CREATE INDEX IF NOT EXISTS idx_env_stations_geom ON environment.stations USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_env_stations_geog ON environment.stations USING GIST ((geom::public.geography));

-- ---------------------------------------------------------------------------
-- Pollutants, units and plausible ranges
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS environment.measurement_types (
    type_code    VARCHAR(8)   PRIMARY KEY,
    name_ko      VARCHAR(32)  NOT NULL,
    name_en      VARCHAR(64)  NOT NULL,
    unit         VARCHAR(16)  NOT NULL,
    min_valid    NUMERIC      NOT NULL,
    max_valid    NUMERIC      NOT NULL,
    api_field    VARCHAR(16)  NOT NULL UNIQUE,     -- field name in the AirKorea response
    CHECK (max_valid > min_valid)
);
COMMENT ON TABLE environment.measurement_types IS 'Criteria pollutants with unit and plausible range (outside = SUSPECT)';

INSERT INTO environment.measurement_types (type_code, name_ko, name_en, unit, min_valid, max_valid, api_field) VALUES
    ('PM10', '미세먼지',   'Particulate matter <= 10 um',  'µg/m³', 0, 1000, 'pm10Value'),
    ('PM25', '초미세먼지', 'Particulate matter <= 2.5 um', 'µg/m³', 0,  800, 'pm25Value'),
    ('O3',   '오존',       'Ozone',                        'ppm',   0,    1, 'o3Value'),
    ('NO2',  '이산화질소', 'Nitrogen dioxide',             'ppm',   0,    2, 'no2Value'),
    ('CO',   '일산화탄소', 'Carbon monoxide',              'ppm',   0,   50, 'coValue'),
    ('SO2',  '아황산가스', 'Sulfur dioxide',               'ppm',   0,    1, 'so2Value')
ON CONFLICT (type_code) DO NOTHING;

-- Alert thresholds: an hourly average ABOVE the value triggers the rule.
-- Values follow the AirKorea grade boundaries (나쁨 / 매우나쁨) of the Korea Ministry of Environment.
CREATE TABLE IF NOT EXISTS environment.alert_rules (
    rule_id          SERIAL       PRIMARY KEY,
    type_code        VARCHAR(8)   NOT NULL REFERENCES environment.measurement_types(type_code),
    severity         VARCHAR(12)  NOT NULL CHECK (severity IN ('BAD', 'VERY_BAD')),
    threshold_value  NUMERIC      NOT NULL CHECK (threshold_value > 0),
    is_active        BOOLEAN      NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_alert_rule UNIQUE (type_code, severity)
);
COMMENT ON TABLE environment.alert_rules IS 'Hourly-average thresholds per pollutant and severity';

INSERT INTO environment.alert_rules (type_code, severity, threshold_value) VALUES
    ('PM10', 'BAD', 80),    ('PM10', 'VERY_BAD', 150),
    ('PM25', 'BAD', 35),    ('PM25', 'VERY_BAD', 75),
    ('O3',   'BAD', 0.090), ('O3',   'VERY_BAD', 0.150),
    ('NO2',  'BAD', 0.060), ('NO2',  'VERY_BAD', 0.200),
    ('CO',   'BAD', 9),     ('CO',   'VERY_BAD', 15),
    ('SO2',  'BAD', 0.050), ('SO2',  'VERY_BAD', 0.150)
ON CONFLICT (type_code, severity) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Measurements
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS environment.measurements (
    measurement_id  BIGSERIAL      PRIMARY KEY,
    station_id      VARCHAR(48)    NOT NULL REFERENCES environment.stations(station_id),
    type_code       VARCHAR(8)     NOT NULL REFERENCES environment.measurement_types(type_code),
    measured_value  NUMERIC(10,4)  NOT NULL,
    quality_flag    VARCHAR(8)     NOT NULL DEFAULT 'GOOD' CHECK (quality_flag IN ('GOOD', 'SUSPECT')),
    observed_at     TIMESTAMPTZ    NOT NULL,
    received_at     TIMESTAMPTZ    NOT NULL,
    recorded_at     TIMESTAMPTZ    NOT NULL DEFAULT clock_timestamp(),
    source_id       VARCHAR(32)    NOT NULL REFERENCES core.source_registry(source_id),
    -- natural key: the upsert target for late and corrected packets
    CONSTRAINT uq_measurement UNIQUE (station_id, type_code, observed_at)
);
COMMENT ON TABLE environment.measurements IS
    'Pollutant readings. Fault markers never reach this table (quarantined); a corrected packet replaces the value';
CREATE INDEX IF NOT EXISTS idx_measurements_time ON environment.measurements (observed_at DESC);

-- ---------------------------------------------------------------------------
-- Hourly rollup (a table refreshed by function, so alerts and APIs read few rows)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS environment.hourly_summaries (
    station_id    VARCHAR(48)    NOT NULL REFERENCES environment.stations(station_id),
    type_code     VARCHAR(8)     NOT NULL REFERENCES environment.measurement_types(type_code),
    hour_start    TIMESTAMPTZ    NOT NULL CHECK (EXTRACT(EPOCH FROM hour_start)::BIGINT % 3600 = 0),
    avg_value     NUMERIC(10,4)  NOT NULL,
    min_value     NUMERIC(10,4)  NOT NULL,
    max_value     NUMERIC(10,4)  NOT NULL,
    sample_count  INTEGER        NOT NULL CHECK (sample_count > 0),
    refreshed_at  TIMESTAMPTZ    NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (station_id, type_code, hour_start)
);
COMMENT ON TABLE environment.hourly_summaries IS 'Hourly statistics of GOOD measurements, per station and pollutant';
CREATE INDEX IF NOT EXISTS idx_hourly_hour ON environment.hourly_summaries (hour_start);

-- Recompute the rollup for [p_from, p_to). Safe to run again: late data simply updates the hour.
CREATE OR REPLACE FUNCTION environment.refresh_hourly_summaries(p_from TIMESTAMPTZ, p_to TIMESTAMPTZ)
RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_rows INTEGER;
BEGIN
    INSERT INTO environment.hourly_summaries
        (station_id, type_code, hour_start, avg_value, min_value, max_value, sample_count)
    SELECT m.station_id, m.type_code, date_trunc('hour', m.observed_at),
           ROUND(AVG(m.measured_value), 4), MIN(m.measured_value), MAX(m.measured_value), COUNT(*)
    FROM environment.measurements m
    WHERE m.quality_flag = 'GOOD'
      AND m.observed_at >= date_trunc('hour', p_from) AND m.observed_at < p_to
    GROUP BY m.station_id, m.type_code, date_trunc('hour', m.observed_at)
    ON CONFLICT (station_id, type_code, hour_start) DO UPDATE
        SET avg_value = EXCLUDED.avg_value, min_value = EXCLUDED.min_value, max_value = EXCLUDED.max_value,
            sample_count = EXCLUDED.sample_count, refreshed_at = clock_timestamp();
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN v_rows;
END
$$;

-- ---------------------------------------------------------------------------
-- Alerts: one row per exceedance EPISODE (not per hour)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS environment.alerts (
    alert_id            BIGSERIAL      PRIMARY KEY,
    rule_id             INTEGER        NOT NULL REFERENCES environment.alert_rules(rule_id),
    station_id          VARCHAR(48)    NOT NULL REFERENCES environment.stations(station_id),
    type_code           VARCHAR(8)     NOT NULL REFERENCES environment.measurement_types(type_code),
    started_hour        TIMESTAMPTZ    NOT NULL,
    last_exceeded_hour  TIMESTAMPTZ    NOT NULL,
    peak_value          NUMERIC(10,4)  NOT NULL,
    alert_message       TEXT           NOT NULL,
    status              VARCHAR(10)    NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'RESOLVED')),
    triggered_at        TIMESTAMPTZ    NOT NULL DEFAULT clock_timestamp(),
    resolved_at         TIMESTAMPTZ,
    CONSTRAINT uq_alert_episode UNIQUE (rule_id, station_id, started_hour),
    CONSTRAINT ck_alert_hours CHECK (last_exceeded_hour >= started_hour),
    CONSTRAINT ck_alert_resolved CHECK ((status = 'RESOLVED') = (resolved_at IS NOT NULL))
);
COMMENT ON TABLE environment.alerts IS
    'Threshold exceedance episodes. ACTIVE until an hour at or below the threshold follows';
CREATE INDEX IF NOT EXISTS idx_alerts_active ON environment.alerts (station_id, type_code) WHERE status = 'ACTIVE';

-- Walk the hourly summaries of [p_from, p_to) in time order and open, extend or resolve episodes.
-- Returns the number of new alerts. Safe to run again over the same hours.
CREATE OR REPLACE FUNCTION environment.evaluate_alerts(p_from TIMESTAMPTZ, p_to TIMESTAMPTZ)
RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    rec    RECORD;
    v_new  INTEGER := 0;
    v_ins  INTEGER;
BEGIN
    FOR rec IN
        SELECT r.rule_id, r.severity, r.threshold_value, h.station_id, h.type_code, h.hour_start, h.avg_value,
               s.station_name_ko, t.unit
        FROM environment.hourly_summaries h
        JOIN environment.alert_rules r ON r.type_code = h.type_code AND r.is_active
        JOIN environment.stations s ON s.station_id = h.station_id
        JOIN environment.measurement_types t ON t.type_code = h.type_code
        WHERE h.hour_start >= p_from AND h.hour_start < p_to
        ORDER BY h.hour_start, h.station_id, r.rule_id
    LOOP
        IF rec.avg_value > rec.threshold_value THEN
            -- extend the episode this hour belongs to, if there is one
            UPDATE environment.alerts
            SET last_exceeded_hour = GREATEST(last_exceeded_hour, rec.hour_start),
                peak_value = GREATEST(peak_value, rec.avg_value)
            WHERE rule_id = rec.rule_id AND station_id = rec.station_id
              AND started_hour <= rec.hour_start
              AND (status = 'ACTIVE' OR last_exceeded_hour >= rec.hour_start);
            IF NOT FOUND THEN
                INSERT INTO environment.alerts
                    (rule_id, station_id, type_code, started_hour, last_exceeded_hour, peak_value, alert_message)
                VALUES (rec.rule_id, rec.station_id, rec.type_code, rec.hour_start, rec.hour_start, rec.avg_value,
                        format('%s %s hourly average above %s %s (%s)', rec.station_name_ko, rec.type_code,
                               rec.threshold_value, rec.unit, rec.severity))
                ON CONFLICT (rule_id, station_id, started_hour) DO NOTHING;
                GET DIAGNOSTICS v_ins = ROW_COUNT;
                v_new := v_new + v_ins;
            END IF;
        ELSE
            UPDATE environment.alerts
            SET status = 'RESOLVED', resolved_at = rec.hour_start
            WHERE rule_id = rec.rule_id AND station_id = rec.station_id
              AND status = 'ACTIVE' AND last_exceeded_hour < rec.hour_start;
        END IF;
    END LOOP;
    RETURN v_new;
END
$$;
