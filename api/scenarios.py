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
    Scenario("DB1-1", "mobility", "Ordered route sequence",
             "Stops of Route 8 in order; (route_id, direction, sequence_order) guarantees ordering."),
    Scenario("DB1-2", "mobility", "Trip execution & stop arrivals",
             "Dwell times, boardings and lateness computed by generated columns."),
    Scenario("DB1-3", "mobility", "Historical position replay",
             "Where was every vehicle at time T? DISTINCT ON point-in-time reconstruction."),
    Scenario("DB1-4", "mobility", "Peak-hour lateness & gap analysis",
             "LAG(observed_arrival) window function detects bunching and headway gaps."),
    Scenario("DB1-5", "mobility", "Smart Mobility telemetry ingestion",
             "High-frequency GPS ingestion with candidate stops within 400 m."),
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
