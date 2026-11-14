"""Scenario registry — the single list of every task the platform can run.

The UI builds its menu from this file, the API runs scenarios from it, and the
tests iterate over it. To deliver a sub-scenario: fill in `sql` (+ `params`) and
flip `status` to "ready".

SQL rules: SELECT only (it runs in a READ ONLY transaction), parameters as
%(name)s placeholders, never string formatting.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from typing import Any

DOMAINS = {
    "core": "Core · Shared Contracts",
    "mobility": "DB1 · Mobility",
    "building": "DB2 · Smart Building",
    "tourism": "DB3 · Tourism",
    "environment": "DB4 · Environment",
    "safety": "DB5 · Safety & Incident",
}


@dataclass(frozen=True)
class Param:
    name: str
    label: str
    type: str = "float"          # float | int | str | datetime | date | time
    default: Any = None
    min: float | None = None
    max: float | None = None

    def coerce(self, raw: Any) -> Any:
        """Validate one user-supplied value. Raises ValueError with a readable message."""
        if raw is None or raw == "":
            if self.default is None:
                raise ValueError(f"{self.name} is required")
            raw = self.default
        try:
            if self.type == "float":
                value: Any = float(raw)
            elif self.type == "int":
                value = int(raw)
            elif self.type == "datetime":
                value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if value.tzinfo is None:
                    raise ValueError("timezone offset is required, e.g. 2026-10-02T08:20:00+09:00")
            elif self.type == "date":
                value = date.fromisoformat(str(raw))
            elif self.type == "time":
                value = time.fromisoformat(str(raw))
            else:
                value = str(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{self.name}: {exc}") from None
        if self.type in ("float", "int"):
            if self.min is not None and value < self.min:
                raise ValueError(f"{self.name} must be >= {self.min}")
            if self.max is not None and value > self.max:
                raise ValueError(f"{self.name} must be <= {self.max}")
        return value


@dataclass(frozen=True)
class Scenario:
    id: str
    domain: str
    title: str
    summary: str
    status: str = "planned"      # planned | ready
    sql: str | None = None
    params: tuple[Param, ...] = field(default_factory=tuple)

    def public(self) -> dict[str, Any]:
        return asdict(self)


LON = Param("lon", "Longitude", "float", 126.6548, -180, 180)
LAT = Param("lat", "Latitude", "float", 37.3888, -90, 90)
RADIUS = Param("radius_m", "Radius (m)", "float", 1000, 1, 20000)
LIMIT = Param("limit", "Max rows", "int", 50, 1, 500)
ROUTE = Param("route_number", "Route number (8, SD-A, AV-1)", "str", "8")
DIRECTION = Param("direction", "Direction (0 or 1)", "int", 0, 0, 1)
BUILDING = Param("building_id", "Building ID (e.g. BLD_G_TOWER, BLD_POSCO_TOWER)", "str", "BLD_G_TOWER")
AS_OF_BLD = Param("as_of", "As of time (ISO, with offset)", "datetime", "2026-10-02T10:00:00+09:00")

_SCENARIOS: list[Scenario] = [
    # ------------------------------------------------------------------ core
    Scenario(
        "CORE-1", "core", "Places near a point",
        "Metre-accurate radius search on core.places using ST_DWithin on geography.",
        "ready",
        """
        SELECT place_id, name_ko, name_en, category,
               ST_X(geom) AS lon, ST_Y(geom) AS lat,
               ROUND(ST_Distance(geom::geography,
                     ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography)::numeric, 1)::float8 AS dist_m
        FROM core.places
        WHERE ST_DWithin(geom::geography,
                         ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography, %(radius_m)s)
        ORDER BY dist_m
        """,
        (LON, LAT, RADIUS),
    ),
    Scenario(
        "CORE-2", "core", "Source registry",
        "Every dataset allowed into the platform with its verification level (1–4).",
        "ready",
        """
        SELECT source_id, title_ko, title_en, provider, verification_level, provenance, fallback_strategy
        FROM core.source_registry
        ORDER BY source_id
        """,
    ),
    Scenario(
        "CORE-3", "core", "Quarantined records",
        "Rejected payloads with their reason code — nothing is silently dropped.",
        "ready",
        """
        SELECT quarantine_id, source_id, domain_schema, target_table, reason_code, reason_detail,
               payload, recorded_at
        FROM core.quarantine_records
        ORDER BY recorded_at DESC
        LIMIT %(limit)s
        """,
        (LIMIT,),
    ),
    Scenario(
        "CORE-4", "core", "Audit ledger (latest events)",
        "Append-only event log with observed / received / recorded timestamps.",
        "ready",
        """
        SELECT audit_id, domain_schema, table_name, entity_id, action,
               observed_at, received_at, recorded_at, source_lineage, actor
        FROM core.event_audit_log
        ORDER BY audit_id DESC
        LIMIT %(limit)s
        """,
        (LIMIT,),
    ),
    # -------------------------------------------------------------- mobility
    Scenario(
        "DB1-1", "mobility", "Ordered route sequence",
        "Stops of a route in order; (route_id, direction, sequence_order) guarantees ordering.",
        "ready",
        """
        SELECT rs.sequence_order, s.stop_id, s.stop_number, s.stop_name_ko,
               ST_X(s.geom) AS lon, ST_Y(s.geom) AS lat,
               ROUND(ST_Distance(s.geom::geography, LAG(s.geom::geography) OVER w)::numeric)::int
                   AS metres_from_previous
        FROM mobility.routes r
        JOIN mobility.route_stops rs USING (route_id)
        JOIN mobility.stops s USING (stop_id)
        WHERE r.route_number = %(route_number)s AND rs.direction = %(direction)s
        WINDOW w AS (ORDER BY rs.sequence_order)
        ORDER BY rs.sequence_order
        """,
        (ROUTE, DIRECTION),
    ),
    Scenario(
        "DB1-2", "mobility", "Trip execution & stop arrivals",
        "Dwell time and lateness come from generated columns; load is a running sum.",
        "ready",
        """
        SELECT e.sequence_order, s.stop_name_ko, e.scheduled_arrival, e.observed_arrival,
               e.lateness_seconds, e.dwell_seconds, e.boardings, e.alightings,
               SUM(e.boardings - e.alightings) OVER (ORDER BY e.sequence_order) AS passengers_on_board
        FROM mobility.trip_stop_events e
        JOIN mobility.stops s USING (stop_id)
        WHERE e.trip_id = %(trip_id)s
        ORDER BY e.sequence_order
        """,
        (Param("trip_id", "Trip ID", "str", "T20261002-8-0-0800"),),
    ),
    Scenario(
        "DB1-3", "mobility", "Historical position replay",
        "Where was every vehicle at time T? DISTINCT ON point-in-time reconstruction.",
        "ready",
        """
        SELECT DISTINCT ON (o.vehicle_id)
               o.vehicle_id, v.vehicle_type, o.trip_id,
               ST_X(o.geom) AS lon, ST_Y(o.geom) AS lat,
               o.speed_kmh::float8 AS speed_kmh,
               o.battery_state_of_charge_pct::float8 AS battery_state_of_charge_pct, o.observed_at,
               ROUND(EXTRACT(EPOCH FROM (%(as_of)s::timestamptz - o.observed_at)))::int AS staleness_seconds
        FROM mobility.vehicle_observations o
        JOIN mobility.vehicles v USING (vehicle_id)
        WHERE o.observed_at <= %(as_of)s::timestamptz
        ORDER BY o.vehicle_id, o.observed_at DESC
        """,
        (Param("as_of", "As of time (ISO, with offset)", "datetime", "2026-10-02T08:20:00+09:00"),),
    ),
    Scenario(
        "DB1-4", "mobility", "Peak-hour lateness & gap analysis",
        "LAG(observed_arrival) per stop gives the real headway; flags bunching and gaps.",
        "ready",
        """
        WITH arrivals AS (
            SELECT t.direction, e.stop_id, e.trip_id, e.observed_arrival, e.lateness_seconds,
                   EXTRACT(EPOCH FROM e.observed_arrival  - LAG(e.observed_arrival)  OVER w)::int AS headway_seconds,
                   EXTRACT(EPOCH FROM e.scheduled_arrival - LAG(e.scheduled_arrival) OVER w)::int AS scheduled_headway_seconds
            FROM mobility.trip_stop_events e
            JOIN mobility.trips t USING (trip_id)
            JOIN mobility.routes r USING (route_id)
            WHERE r.route_number = %(route_number)s
              AND e.observed_arrival >= %(from_time)s::timestamptz
              AND e.observed_arrival <  %(to_time)s::timestamptz
            WINDOW w AS (PARTITION BY t.route_id, t.direction, e.stop_id ORDER BY e.observed_arrival)
        )
        SELECT a.direction, s.stop_name_ko, a.trip_id, a.observed_arrival, a.lateness_seconds,
               a.headway_seconds, a.scheduled_headway_seconds,
               CASE WHEN a.headway_seconds < 0.75 * a.scheduled_headway_seconds THEN 'BUNCHED'
                    WHEN a.headway_seconds > 1.25 * a.scheduled_headway_seconds THEN 'GAP'
                    ELSE 'OK' END AS headway_status
        FROM arrivals a
        JOIN mobility.stops s USING (stop_id)
        WHERE a.headway_seconds IS NOT NULL
        ORDER BY ABS(a.headway_seconds - a.scheduled_headway_seconds) DESC, a.observed_arrival
        LIMIT %(limit)s
        """,
        (ROUTE,
         Param("from_time", "From (ISO, with offset)", "datetime", "2026-10-02T07:00:00+09:00"),
         Param("to_time", "To (ISO, with offset)", "datetime", "2026-10-02T09:30:00+09:00"),
         LIMIT),
    ),
    Scenario(
        "DB1-5", "mobility", "Smart Mobility telemetry: candidate stops",
        "For a vehicle's latest GPS points, the stops within the radius (ST_DWithin on geography).",
        "ready",
        """
        SELECT o.observation_id, o.vehicle_id, o.observed_at, o.speed_kmh::float8 AS speed_kmh,
               c.stop_id, c.stop_name_ko, c.distance_m
        FROM (
            SELECT observation_id, vehicle_id, observed_at, speed_kmh, geom
            FROM mobility.vehicle_observations
            WHERE vehicle_id = %(vehicle_id)s
            ORDER BY observed_at DESC
            LIMIT %(last_n)s
        ) o
        CROSS JOIN LATERAL (
            SELECT s.stop_id, s.stop_name_ko,
                   ROUND(ST_Distance(o.geom::geography, s.geom::geography)::numeric, 1)::float8 AS distance_m
            FROM mobility.stops s
            WHERE ST_DWithin(o.geom::geography, s.geom::geography, %(radius_m)s)
            ORDER BY distance_m
            LIMIT 3
        ) c
        ORDER BY o.observed_at DESC, c.distance_m
        """,
        (Param("vehicle_id", "Vehicle ID", "str", "VEH-AV1-01"),
         Param("radius_m", "Radius (m)", "float", 400, 1, 5000),
         Param("last_n", "Latest GPS points", "int", 5, 1, 100)),
    ),
    # -------------------------------------------------------------- building
    Scenario(
        "DB2-1", "building", "Building & address reconciliation",
        "Compares each registry building with its linked place and the nearest canonical place.",
        "ready",
        """
        SELECT b.building_id, b.building_name_ko, b.road_address AS registry_address,
               b.place_id AS linked_place, n.place_id AS nearest_place, n.distance_m,
               (b.road_address IS NOT DISTINCT FROM n.road_address) AS address_matches,
               CASE WHEN b.place_id IS NULL THEN 'UNLINKED'
                    WHEN b.place_id = n.place_id AND n.distance_m <= %(max_distance_m)s
                         AND b.road_address IS NOT DISTINCT FROM n.road_address THEN 'CONFIRMED'
                    ELSE 'REVIEW' END AS reconciliation_status
        FROM building.buildings b
        LEFT JOIN LATERAL (
            SELECT pl.place_id, pl.road_address,
                   ROUND(ST_Distance(b.geom::geography, pl.geom::geography)::numeric, 1)::float8 AS distance_m
            FROM core.places pl
            ORDER BY b.geom <-> pl.geom
            LIMIT 1
        ) n ON TRUE
        ORDER BY b.building_id
        """,
        (Param("max_distance_m", "Max centroid distance (m)", "float", 150, 1, 5000),),
    ),
    Scenario(
        "DB2-2", "building", "Space hierarchy & device registration",
        "Recursive walk of the floor / room tree with the devices installed in each space.",
        "ready",
        """
        WITH RECURSIVE tree AS (
            SELECT s.space_id, s.space_type, s.floor_number, 1 AS depth, s.space_name::text AS path
            FROM building.spaces s
            WHERE s.building_id = %(building_id)s AND s.parent_space_id IS NULL
            UNION ALL
            SELECT c.space_id, c.space_type, c.floor_number, t.depth + 1, t.path || ' > ' || c.space_name
            FROM building.spaces c
            JOIN tree t ON c.parent_space_id = t.space_id
        )
        SELECT t.path, t.space_type, t.floor_number, t.depth,
               COUNT(d.device_id) AS devices,
               STRING_AGG(d.device_id || ' (' || d.device_type || ')', ', ' ORDER BY d.device_id) AS installed
        FROM tree t
        LEFT JOIN building.device_space_assignments a ON a.space_id = t.space_id AND a.unassigned_at IS NULL
        LEFT JOIN building.devices d ON d.device_id = a.device_id
        GROUP BY t.space_id, t.path, t.space_type, t.floor_number, t.depth
        ORDER BY t.floor_number, t.path
        """,
        (BUILDING,),
    ),
    Scenario(
        "DB2-3", "building", "Device relocation history",
        "Every space a device has been in; a move closes one row (unassigned_at) and opens the next.",
        "ready",
        """
        SELECT a.assignment_id, sp.building_id, sp.space_name, sp.floor_number,
               a.assigned_at, a.unassigned_at,
               ROUND(EXTRACT(EPOCH FROM (COALESCE(a.unassigned_at, %(as_of)s::timestamptz) - a.assigned_at))
                     / 86400, 1)::float8 AS days_in_space,
               (a.unassigned_at IS NULL) AS is_current
        FROM building.device_space_assignments a
        JOIN building.spaces sp USING (space_id)
        WHERE a.device_id = %(device_id)s
        ORDER BY a.assigned_at
        """,
        (Param("device_id", "Device ID", "str", "DEV-PT-TMP-03"), AS_OF_BLD),
    ),
    Scenario(
        "DB2-4", "building", "Missing heartbeat & fault detection",
        "Anti-join: devices with no telemetry in the last N minutes before time T.",
        "ready",
        """
        SELECT d.device_id, d.device_type, d.current_status, sp.building_id, sp.space_name,
               last.observed_at AS last_seen,
               ROUND(EXTRACT(EPOCH FROM (%(as_of)s::timestamptz - last.observed_at)) / 60)::int AS silent_minutes,
               CASE WHEN last.observed_at IS NULL THEN 'NEVER_REPORTED'
                    WHEN d.current_status = 'ACTIVE' THEN 'UNDETECTED_FAULT'
                    ELSE 'KNOWN_ISSUE' END AS finding
        FROM building.devices d
        LEFT JOIN building.device_space_assignments a ON a.device_id = d.device_id AND a.unassigned_at IS NULL
        LEFT JOIN building.spaces sp ON sp.space_id = a.space_id
        LEFT JOIN LATERAL (
            SELECT MAX(t.observed_at) AS observed_at
            FROM building.telemetry t
            WHERE t.device_id = d.device_id AND t.observed_at <= %(as_of)s::timestamptz
        ) last ON TRUE
        WHERE d.current_status <> 'RETIRED'
          AND NOT EXISTS (
              SELECT 1 FROM building.telemetry t
              WHERE t.device_id = d.device_id
                AND t.observed_at >  %(as_of)s::timestamptz - make_interval(mins => %(silent_minutes)s)
                AND t.observed_at <= %(as_of)s::timestamptz)
        ORDER BY last.observed_at NULLS FIRST, d.device_id
        """,
        (AS_OF_BLD, Param("silent_minutes", "Silent for more than (min)", "int", 15, 1, 1440)),
    ),
    Scenario(
        "DB2-5", "building", "VIP facility state retrieval",
        "Latest good reading per device in a building, with its age and maintenance status.",
        "ready",
        """
        SELECT sp.floor_number, sp.space_name, d.device_id, d.device_type, d.current_status,
               l.metric, l.value::float8 AS value, mt.unit, l.observed_at,
               ROUND(EXTRACT(EPOCH FROM (%(as_of)s::timestamptz - l.observed_at)) / 60)::int AS age_minutes,
               m.last_maintenance, m.open_actions
        FROM building.spaces sp
        JOIN building.device_space_assignments a ON a.space_id = sp.space_id AND a.unassigned_at IS NULL
        JOIN building.devices d USING (device_id)
        LEFT JOIN LATERAL (
            SELECT t.metric, t.value, t.observed_at
            FROM building.telemetry t
            WHERE t.device_id = d.device_id AND t.quality_flag = 'GOOD'
              AND t.observed_at <= %(as_of)s::timestamptz
            ORDER BY t.observed_at DESC
            LIMIT 1
        ) l ON TRUE
        LEFT JOIN building.metric_types mt ON mt.metric_code = l.metric
        LEFT JOIN LATERAL (
            SELECT MAX(ma.started_at) AS last_maintenance,
                   COUNT(*) FILTER (WHERE ma.completed_at IS NULL) AS open_actions
            FROM building.maintenance_actions ma
            WHERE ma.device_id = d.device_id
        ) m ON TRUE
        WHERE sp.building_id = %(building_id)s
        ORDER BY sp.floor_number, sp.space_name, d.device_id
        """,
        (BUILDING, AS_OF_BLD),
    ),
    # --------------------------------------------------------------- tourism
    Scenario(
        "DB3-1", "tourism", "Attractions & accessibility metadata",
        "Venues with wheelchair access and parking, plus the nearest bus stop.",
        "ready",
        """
        SELECT a.attraction_id, a.attraction_name_ko, a.category, a.is_wheelchair_accessible,
               a.has_parking, a.parking_spaces, a.admission_fee_krw,
               n.stop_name_ko AS nearest_stop, n.distance_m AS stop_distance_m
        FROM tourism.attractions a
        LEFT JOIN LATERAL (
            SELECT s.stop_name_ko,
                   ROUND(ST_Distance(a.geom::geography, s.geom::geography)::numeric, 1)::float8 AS distance_m
            FROM mobility.stops s
            ORDER BY a.geom <-> s.geom
            LIMIT 1
        ) n ON TRUE
        WHERE (%(wheelchair_only)s = 0 OR a.is_wheelchair_accessible)
          AND (%(parking_only)s = 0 OR a.has_parking)
        ORDER BY a.attraction_name_ko
        """,
        (Param("wheelchair_only", "Wheelchair accessible only (0 or 1)", "int", 0, 0, 1),
         Param("parking_only", "With parking only (0 or 1)", "int", 0, 0, 1)),
    ),
    Scenario(
        "DB3-2", "tourism", "Event management & schedule integrity",
        "Events in a date range, checked against the venue's operating hours on every event day.",
        "ready",
        """
        SELECT e.event_id, e.event_name_ko, a.attraction_name_ko AS venue, e.event_type,
               e.start_date, e.end_date, (e.end_date - e.start_date + 1) AS days,
               e.daily_start_time, e.daily_end_time, e.expected_attendance,
               CASE WHEN e.is_cancelled THEN 'CANCELLED'
                    WHEN EXISTS (
                        SELECT 1
                        FROM generate_series(e.start_date::timestamp, e.end_date::timestamp, INTERVAL '1 day') AS d
                        WHERE NOT EXISTS (
                            SELECT 1 FROM tourism.operating_hours h
                            WHERE h.attraction_id = e.attraction_id
                              AND h.day_of_week = EXTRACT(DOW FROM d)::int
                              AND h.opens_at <= e.daily_start_time AND h.closes_at >= e.daily_end_time))
                    THEN 'OUTSIDE_OPERATING_HOURS'
                    ELSE 'OK' END AS schedule_check
        FROM tourism.events e
        JOIN tourism.attractions a USING (attraction_id)
        WHERE daterange(e.start_date, e.end_date, '[]') && daterange(%(from_date)s::date, %(to_date)s::date, '[]')
        ORDER BY e.start_date, e.event_id
        """,
        (Param("from_date", "From date (YYYY-MM-DD)", "date", "2026-09-01"),
         Param("to_date", "To date (YYYY-MM-DD)", "date", "2026-10-31")),
    ),
    Scenario(
        "DB3-3", "tourism", "Official statistics vs. synthetic samples",
        "Monthly totals and hourly samples live in separate tables; this compares them for one month.",
        "ready",
        """
        SELECT a.attraction_name_ko, o.year_month, o.visitor_count AS monthly_total,
               r.provenance AS monthly_total_provenance,
               s.sample_total, s.sample_hours,
               ROUND(100.0 * s.sample_total / NULLIF(o.visitor_count, 0), 1)::float8 AS sample_coverage_pct
        FROM tourism.aggregate_visits o
        JOIN tourism.attractions a USING (attraction_id)
        JOIN core.source_registry r ON r.source_id = o.source_id
        LEFT JOIN LATERAL (
            SELECT SUM(v.visitor_count) AS sample_total, COUNT(*) AS sample_hours
            FROM tourism.sample_visits v
            WHERE v.attraction_id = o.attraction_id
              AND v.hour_start >= o.year_month::timestamp AT TIME ZONE 'Asia/Seoul'
              AND v.hour_start <  (o.year_month + INTERVAL '1 month') AT TIME ZONE 'Asia/Seoul'
        ) s ON TRUE
        WHERE o.year_month = date_trunc('month', %(month)s::date)::date
        ORDER BY o.visitor_count DESC
        """,
        (Param("month", "Month (any date in it, YYYY-MM-DD)", "date", "2026-09-01"),),
    ),
    Scenario(
        "DB3-4", "tourism", "Open attractions near transit",
        "Attractions within a radius of a bus stop that are open on the given weekday and time.",
        "ready",
        """
        SELECT a.attraction_id, a.attraction_name_ko, a.category,
               TO_CHAR(h.opens_at, 'HH24:MI') AS opens_at, TO_CHAR(h.closes_at, 'HH24:MI') AS closes_at,
               a.is_wheelchair_accessible, a.has_parking,
               ROUND(ST_Distance(a.geom::geography, st.geom::geography)::numeric, 1)::float8 AS distance_m,
               st.stop_name_ko AS from_stop
        FROM mobility.stops st
        JOIN tourism.attractions a
          ON ST_DWithin(a.geom::geography, st.geom::geography, %(radius_m)s)
        JOIN tourism.operating_hours h
          ON h.attraction_id = a.attraction_id
         AND h.day_of_week = %(day_of_week)s
         AND h.opens_at <= %(at_time)s::time AND h.closes_at > %(at_time)s::time
        WHERE st.stop_number = %(stop_number)s
        ORDER BY distance_m
        """,
        (Param("stop_number", "Stop number (on the stop sign)", "str", "38367"),
         Param("radius_m", "Radius (m)", "float", 1500, 1, 20000),
         Param("day_of_week", "Day of week (0 = Sun ... 6 = Sat)", "int", 5, 0, 6),
         Param("at_time", "Time (HH:MM)", "time", "14:00")),
    ),
    Scenario(
        "DB3-5", "tourism", "Temporal demand & crowd forecasting",
        "Expected event attendance against the bus seats arriving nearby before the event starts.",
        "ready",
        """
        WITH active AS (
            SELECT e.event_id, e.event_name_ko, e.daily_start_time, e.expected_attendance,
                   a.attraction_name_ko, a.geom,
                   (%(event_date)s::date + e.daily_start_time) AT TIME ZONE 'Asia/Seoul' AS starts_at
            FROM tourism.events e
            JOIN tourism.attractions a USING (attraction_id)
            WHERE NOT e.is_cancelled
              AND %(event_date)s::date BETWEEN e.start_date AND e.end_date
        )
        SELECT ev.event_id, ev.event_name_ko, ev.attraction_name_ko AS venue, ev.daily_start_time,
               ev.expected_attendance, cap.arriving_trips, cap.seat_capacity,
               ROUND(ev.expected_attendance::numeric / NULLIF(cap.seat_capacity, 0), 1)::float8
                   AS attendees_per_seat,
               CASE WHEN cap.seat_capacity = 0 THEN 'NO_SERVICE'
                    WHEN ev.expected_attendance > cap.seat_capacity THEN 'OVER_CAPACITY'
                    ELSE 'OK' END AS transit_status
        FROM active ev
        CROSS JOIN LATERAL (
            SELECT COUNT(*) AS arriving_trips, COALESCE(SUM(t.capacity), 0) AS seat_capacity
            FROM (
                SELECT DISTINCT tr.trip_id, v.capacity
                FROM mobility.stops s
                JOIN mobility.trip_stop_events te USING (stop_id)
                JOIN mobility.trips tr USING (trip_id)
                JOIN mobility.vehicles v USING (vehicle_id)
                WHERE ST_DWithin(s.geom::geography, ev.geom::geography, %(radius_m)s)
                  AND te.scheduled_arrival >  ev.starts_at - make_interval(mins => %(window_min)s)
                  AND te.scheduled_arrival <= ev.starts_at
                  AND tr.status <> 'CANCELLED'
            ) t
        ) cap
        ORDER BY ev.expected_attendance DESC
        """,
        (Param("event_date", "Event date (YYYY-MM-DD)", "date", "2026-10-02"),
         Param("radius_m", "Walking radius to stops (m)", "float", 500, 1, 5000),
         Param("window_min", "Arrival window before start (min)", "int", 120, 1, 720)),
    ),
    # ----------------------------------------------------------- environment
    Scenario("DB4-1", "environment", "Station registration & unit standards",
             "AirKorea Songdo and Dongchun stations with Korea MOE thresholds."),
    Scenario("DB4-2", "environment", "Telemetry ingestion & quality flags",
             "PM10, PM2.5, O3, NO2, CO, SO2 with sensor validation."),
    Scenario("DB4-3", "environment", "Fault handling & late arrivals",
             "Rejects '-' fault readings; late packets handled by upsert."),
    Scenario("DB4-4", "environment", "Hourly rollup & threshold alerts",
             "Hourly statistics; alert when PM2.5 exceeds 75 µg/m³."),
    Scenario("DB4-5", "environment", "Contextual air exposure service",
             "Nearest-station reading with distance and measurement age."),
    # ---------------------------------------------------------------- safety
    Scenario("DB5-1", "safety", "Public safety facilities registration",
             "Songdo Fire Station, Sinsong 119 Safety Center and police boxes."),
    Scenario("DB5-2", "safety", "Incident creation & severity triage",
             "Emergency reports with coordinates and SLA targets."),
    Scenario("DB5-3", "safety", "State machine transitions & drills",
             "REPORTED → DISPATCHED → ON_SCENE → RESOLVED with full audit history."),
    Scenario("DB5-4", "safety", "Response time analytics & hotspots",
             "Response duration by station and spatial incident clusters."),
    Scenario("DB5-5", "safety", "Smart Mobility hazard avoidance",
             "Active incident restriction zones for autonomous path planners."),
]

SCENARIOS: dict[str, Scenario] = {s.id: s for s in _SCENARIOS}
