"""Scenario registry — the single list of every task the platform can run.

The UI builds its menu from this file, the API runs scenarios from it, and the
tests iterate over it. To deliver a sub-scenario: fill in `sql` (+ `params`) and
flip `status` to "ready".

SQL rules: SELECT only (it runs in a READ ONLY transaction), parameters as
%(name)s placeholders, never string formatting.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
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
    type: str = "float"          # float | int | str | datetime
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
    Scenario("DB2-1", "building", "Building & address reconciliation",
             "Links building registry rows to canonical PostGIS centroids."),
    Scenario("DB2-2", "building", "Space hierarchy & device registration",
             "Floors, plant rooms and the IoT devices installed in them."),
    Scenario("DB2-3", "building", "Device relocation history",
             "Closes the old assignment with unassigned_at and opens the new one."),
    Scenario("DB2-4", "building", "Missing heartbeat & fault detection",
             "Anti-join: devices with no telemetry in the last 15 minutes."),
    Scenario("DB2-5", "building", "VIP facility state retrieval",
             "Live indoor temperature, power use and maintenance history."),
    # --------------------------------------------------------------- tourism
    Scenario("DB3-1", "tourism", "Attractions & accessibility metadata",
             "Cultural venues with wheelchair access and parking."),
    Scenario("DB3-2", "tourism", "Event management & schedule integrity",
             "end_date >= start_date and valid daily operating intervals."),
    Scenario("DB3-3", "tourism", "Official statistics vs. synthetic samples",
             "Monthly government totals kept apart from hourly sample counts."),
    Scenario("DB3-4", "tourism", "Open attractions near transit",
             "Attractions open on the requested weekday within a radius of a stop."),
    Scenario("DB3-5", "tourism", "Temporal demand & crowd forecasting",
             "Conference attendance against nearby transit capacity."),
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
