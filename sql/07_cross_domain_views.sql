-- =============================================================================
-- 07_cross_domain_views.sql — cross-team federation
--   3 live views (always current) + 3 materialized views (precomputed, refreshed on demand)
-- Re-runnable. Depends on every earlier migration (00–06).
--
-- The three view names and their columns follow the course handbook. Two deliberate
-- differences from the handbook's SQL are marked "DIFFERS" below.
--
-- "Nearest X" is always ranked by ST_Distance on geography (metres). Ranking by
-- `geom <-> geom` on lon/lat degrees is wrong here: at 37.4 N a degree of longitude is
-- only 0.79 of a degree of latitude, so it picked 동막 (1722 m) instead of 송도 (1478 m)
-- as the station nearest to IGC. The candidate sets are small (5-513 rows).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. Attractions and their events near transit stops (mobility x tourism)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW cross_domain.v_attractions_events_near_stops AS
SELECT
    s.stop_id, s.stop_name_ko AS stop_name, s.stop_number,
    a.attraction_id, a.attraction_name_ko AS attraction_name,
    ROUND(ST_Distance(s.geom::geography, a.geom::geography)::NUMERIC, 1) AS distance_meters,
    e.event_id, e.event_name_ko AS active_event_name, e.expected_attendance,
    e.start_date AS event_start_date, e.end_date AS event_end_date
FROM mobility.stops s
JOIN tourism.attractions a
  ON ST_DWithin(s.geom::geography, a.geom::geography, 500.0)
LEFT JOIN tourism.events e
  ON a.attraction_id = e.attraction_id AND e.is_cancelled = FALSE;

COMMENT ON VIEW cross_domain.v_attractions_events_near_stops IS
    'Every (stop, attraction) pair within 500 m, with the attraction''s non-cancelled events';

-- ---------------------------------------------------------------------------
-- 2. Building devices with outdoor air-quality context (building x environment)
--    DIFFERS: the nearest station and its latest PM readings are resolved once per
--    building and then joined to the devices, instead of once per device. Same rows,
--    far fewer lookups.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW cross_domain.v_building_devices_with_environmental_context AS
SELECT
    b.building_id, b.building_name_ko, d.device_id, d.device_type, d.current_status,
    ctx.station_name_ko AS nearest_env_station,
    ctx.distance_to_station_meters,
    ctx.type_code AS pollutant_type, ctx.measured_value AS outdoor_value, ctx.observed_at AS reading_time
FROM building.buildings b
JOIN building.spaces sp ON b.building_id = sp.building_id
JOIN building.device_space_assignments dsa ON sp.space_id = dsa.space_id AND dsa.unassigned_at IS NULL
JOIN building.devices d ON dsa.device_id = d.device_id
LEFT JOIN LATERAL (
    SELECT st.station_name_ko,
           ROUND(ST_Distance(b.geom::geography, st.geom::geography)::NUMERIC, 1) AS distance_to_station_meters,
           lem.type_code, lem.measured_value, lem.observed_at
    FROM (
        SELECT s.station_id, s.station_name_ko, s.geom
        FROM environment.stations s
        ORDER BY ST_Distance(b.geom::geography, s.geom::geography)
        LIMIT 1
    ) st
    LEFT JOIN LATERAL (
        SELECT DISTINCT ON (m.type_code) m.type_code, m.measured_value, m.observed_at
        FROM environment.measurements m
        WHERE m.station_id = st.station_id AND m.quality_flag = 'GOOD'
          AND m.type_code IN ('PM25', 'PM10')
        ORDER BY m.type_code, m.observed_at DESC
    ) lem ON TRUE
) ctx ON TRUE;

COMMENT ON VIEW cross_domain.v_building_devices_with_environmental_context IS
    'Each installed device with the latest good PM2.5 / PM10 reading of the station nearest to its building';

-- ---------------------------------------------------------------------------
-- 3. Open incidents with the stops they affect and the local air alert
--    (safety x mobility x environment)
--    DIFFERS: the handbook joins EVERY active alert in the city to every incident
--    (ON al.status = 'ACTIVE'). Here an incident only gets the active alerts of the
--    monitoring station nearest to it. Drills are excluded.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW cross_domain.v_incidents_mobility_environmental_impact AS
SELECT
    inc.incident_id, inc.title, it.name_ko AS incident_type, inc.severity, inc.current_status,
    s.stop_id, s.stop_name_ko AS nearby_stop_name,
    ROUND(ST_Distance(inc.geom::geography, s.geom::geography)::NUMERIC, 1) AS distance_to_stop_meters,
    st.station_name_ko AS nearest_env_station,
    al.alert_message AS active_env_alert
FROM safety.incidents inc
JOIN safety.incident_types it ON inc.type_code = it.type_code
JOIN mobility.stops s ON ST_DWithin(inc.geom::geography, s.geom::geography, 600.0)
LEFT JOIN LATERAL (
    SELECT es.station_id, es.station_name_ko
    FROM environment.stations es
    ORDER BY ST_Distance(inc.geom::geography, es.geom::geography)
    LIMIT 1
) st ON TRUE
LEFT JOIN environment.alerts al ON al.station_id = st.station_id AND al.status = 'ACTIVE'
-- Same rows as the handbook's NOT IN ('CLOSED', 'RESOLVED', 'CANCELLED'), written as the positive
-- list so the planner can use the partial index idx_incidents_active instead of scanning every incident.
WHERE inc.current_status IN ('REPORTED', 'DISPATCHED', 'ON_SCENE')
  AND NOT inc.is_drill;

COMMENT ON VIEW cross_domain.v_incidents_mobility_environmental_impact IS
    'Open incidents, the stops within 600 m, and active air alerts at the nearest monitoring station';

-- =============================================================================
-- Materialized views: heavy aggregates and spatial joins computed once and read many times.
-- Each has a UNIQUE index, which REFRESH ... CONCURRENTLY needs (readers are never blocked).
-- =============================================================================

-- Response time per facility, month and drill flag (precomputes Scenario DB5-4)
CREATE MATERIALIZED VIEW IF NOT EXISTS cross_domain.mv_facility_response_stats AS
SELECT f.facility_id, f.facility_name_ko, f.facility_type,
       date_trunc('month', i.reported_at AT TIME ZONE 'Asia/Seoul')::DATE AS month,
       i.is_drill,
       COUNT(*)                                                                          AS incidents,
       ROUND(AVG(EXTRACT(EPOCH FROM h.changed_at - i.reported_at)))::INTEGER             AS avg_response_s,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (
             ORDER BY EXTRACT(EPOCH FROM h.changed_at - i.reported_at)))::INTEGER         AS median_response_s,
       ROUND(PERCENTILE_CONT(0.9) WITHIN GROUP (
             ORDER BY EXTRACT(EPOCH FROM h.changed_at - i.reported_at)))::INTEGER         AS p90_response_s,
       COUNT(*) FILTER (WHERE EXTRACT(EPOCH FROM h.changed_at - i.reported_at) <= sl.sla_target_seconds)
                                                                                         AS sla_met
FROM safety.incidents i
JOIN safety.incident_status_history h ON h.incident_id = i.incident_id AND h.to_status = 'ON_SCENE'
JOIN safety.severity_levels sl ON sl.severity = i.severity
JOIN safety.facilities f ON f.facility_id = i.assigned_facility_id
GROUP BY f.facility_id, f.facility_name_ko, f.facility_type, 4, i.is_drill;

CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_facility_response
    ON cross_domain.mv_facility_response_stats (facility_id, month, is_drill);

-- Scheduled service per stop and hour: arrivals, seats, lateness, boardings
CREATE MATERIALIZED VIEW IF NOT EXISTS cross_domain.mv_stop_hourly_service AS
SELECT e.stop_id,
       date_trunc('hour', e.scheduled_arrival)        AS hour_start,
       COUNT(*)                                       AS arrivals,
       SUM(v.capacity)                                AS seat_capacity,
       ROUND(AVG(e.lateness_seconds))::INTEGER        AS avg_lateness_s,
       MAX(e.lateness_seconds)                        AS max_lateness_s,
       SUM(e.boardings)                               AS boardings,
       SUM(e.alightings)                              AS alightings
FROM mobility.trip_stop_events e
JOIN mobility.trips t ON t.trip_id = e.trip_id AND t.status <> 'CANCELLED'
JOIN mobility.vehicles v ON v.vehicle_id = t.vehicle_id
GROUP BY e.stop_id, date_trunc('hour', e.scheduled_arrival);

CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_stop_hourly
    ON cross_domain.mv_stop_hourly_service (stop_id, hour_start);
CREATE INDEX IF NOT EXISTS idx_mv_stop_hourly_hour
    ON cross_domain.mv_stop_hourly_service (hour_start);

-- One "context card" per canonical place: what is nearby in every domain
CREATE MATERIALIZED VIEW IF NOT EXISTS cross_domain.mv_place_context AS
SELECT p.place_id, p.name_ko, p.name_en, p.category, ST_X(p.geom) AS lon, ST_Y(p.geom) AS lat,
       stop.stop_id       AS nearest_stop_id,
       stop.stop_name_ko  AS nearest_stop_name,
       stop.distance_m    AS nearest_stop_m,
       (SELECT COUNT(*) FROM mobility.stops s
        WHERE ST_DWithin(s.geom::geography, p.geom::geography, 500.0))       AS stops_within_500m,
       (SELECT COUNT(*) FROM tourism.attractions a
        WHERE ST_DWithin(a.geom::geography, p.geom::geography, 500.0))       AS attractions_within_500m,
       env.station_name_ko AS nearest_env_station,
       env.distance_m      AS nearest_env_station_m,
       fire.facility_name_ko   AS nearest_fire_unit,
       fire.distance_m         AS nearest_fire_unit_m,
       police.facility_name_ko AS nearest_police_unit,
       police.distance_m       AS nearest_police_unit_m
FROM core.places p
LEFT JOIN LATERAL (
    SELECT s.stop_id, s.stop_name_ko,
           ROUND(ST_Distance(p.geom::geography, s.geom::geography)::NUMERIC, 1) AS distance_m
    FROM mobility.stops s ORDER BY ST_Distance(p.geom::geography, s.geom::geography) LIMIT 1
) stop ON TRUE
LEFT JOIN LATERAL (
    SELECT es.station_name_ko,
           ROUND(ST_Distance(p.geom::geography, es.geom::geography)::NUMERIC, 1) AS distance_m
    FROM environment.stations es ORDER BY ST_Distance(p.geom::geography, es.geom::geography) LIMIT 1
) env ON TRUE
LEFT JOIN LATERAL (
    SELECT f.facility_name_ko,
           ROUND(ST_Distance(p.geom::geography, f.geom::geography)::NUMERIC, 1) AS distance_m
    FROM safety.facilities f WHERE f.responder = 'FIRE' ORDER BY ST_Distance(p.geom::geography, f.geom::geography) LIMIT 1
) fire ON TRUE
LEFT JOIN LATERAL (
    SELECT f.facility_name_ko,
           ROUND(ST_Distance(p.geom::geography, f.geom::geography)::NUMERIC, 1) AS distance_m
    FROM safety.facilities f WHERE f.responder = 'POLICE' ORDER BY ST_Distance(p.geom::geography, f.geom::geography) LIMIT 1
) police ON TRUE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_place_context ON cross_domain.mv_place_context (place_id);

-- ---------------------------------------------------------------------------
-- Refresh policy
--   WHEN   after every bulk load: end of the seed, end of each ingest script,
--          and on demand through POST /api/v1/cross/refresh
--   HOW    REFRESH ... CONCURRENTLY, so queries keep reading the old contents meanwhile
--   PROOF  every refresh is logged; v_materialized_view_freshness shows the age
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cross_domain.refresh_log (
    refresh_id    BIGSERIAL    PRIMARY KEY,
    view_name     VARCHAR(64)  NOT NULL,
    refreshed_at  TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    duration_ms   NUMERIC(10,1) NOT NULL,
    row_count     BIGINT       NOT NULL
);
COMMENT ON TABLE cross_domain.refresh_log IS 'One row per materialized-view refresh';

CREATE OR REPLACE FUNCTION cross_domain.refresh_materialized_views()
RETURNS TABLE (view_name TEXT, row_count BIGINT, duration_ms NUMERIC)
LANGUAGE plpgsql
AS $$
DECLARE
    v_name   TEXT;
    v_start  TIMESTAMPTZ;
BEGIN
    FOREACH v_name IN ARRAY ARRAY['mv_facility_response_stats', 'mv_stop_hourly_service', 'mv_place_context'] LOOP
        v_start := clock_timestamp();
        EXECUTE format('REFRESH MATERIALIZED VIEW CONCURRENTLY cross_domain.%I', v_name);
        EXECUTE format('SELECT count(*) FROM cross_domain.%I', v_name) INTO row_count;
        view_name := v_name;
        duration_ms := ROUND((EXTRACT(EPOCH FROM clock_timestamp() - v_start) * 1000)::NUMERIC, 1);
        INSERT INTO cross_domain.refresh_log (view_name, duration_ms, row_count)
        VALUES (v_name, duration_ms, row_count);
        RETURN NEXT;
    END LOOP;
END
$$;

CREATE OR REPLACE VIEW cross_domain.v_materialized_view_freshness AS
SELECT DISTINCT ON (l.view_name)
       l.view_name, l.refreshed_at, l.row_count, l.duration_ms,
       ROUND(EXTRACT(EPOCH FROM clock_timestamp() - l.refreshed_at) / 60)::INTEGER AS age_minutes
FROM cross_domain.refresh_log l
ORDER BY l.view_name, l.refreshed_at DESC;
