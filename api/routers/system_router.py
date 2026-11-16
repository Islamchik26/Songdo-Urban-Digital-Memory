"""/api/v1/system — what the web console needs beyond the domain endpoints.

  GET  /map/layers           everything that does not move: stops, route patterns, stations, attractions,
                             buildings, safety facilities, landmarks, plus the time range of the history
  GET  /map/frame?as_of=T    everything that changes with time, rebuilt from history for moment T:
                             vehicles, active incidents, air quality per station, active air alerts
  GET  /activity             latest ingestion batches, quarantined records and audit-ledger entries
  GET  /verification         PASS / WARN / FAIL checklist of the whole platform (handbook quality gates)
  POST /run-tests            runs the unittest suite in a separate process (token required)
"""
from __future__ import annotations

import subprocess
import sys
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime

from api.config import ROOT
from api.database import fetch_all, fetch_one
from api.routers.core_router import require_token
from api.routers.safety_router import ACTIVE_AS_OF_SQL

router = APIRouter(prefix="/api/v1/system", tags=["system"])

NOW = "COALESCE(%(as_of)s::timestamptz, now())"


# ---------------------------------------------------------------------------
# Map
# ---------------------------------------------------------------------------
@router.get("/map/layers", summary="Static map layers and the time range covered by the history")
def map_layers() -> dict[str, Any]:
    return {
        "stops": fetch_all(
            """SELECT stop_id, stop_number, stop_name_ko AS name, ST_X(geom) AS lon, ST_Y(geom) AS lat
               FROM mobility.stops ORDER BY stop_id"""),
        "routes": fetch_all(
            """SELECT r.route_number, r.route_name_ko AS name, r.route_type, rs.direction,
                      json_agg(json_build_array(ST_X(s.geom), ST_Y(s.geom)) ORDER BY rs.sequence_order) AS path,
                      json_agg(s.stop_name_ko ORDER BY rs.sequence_order) AS stop_names
               FROM mobility.routes r
               JOIN mobility.route_stops rs USING (route_id)
               JOIN mobility.stops s USING (stop_id)
               GROUP BY r.route_number, r.route_name_ko, r.route_type, rs.direction
               ORDER BY r.route_number, rs.direction"""),
        "stations": fetch_all(
            """SELECT station_id, station_name_ko AS name, network, address, ST_X(geom) AS lon, ST_Y(geom) AS lat
               FROM environment.stations ORDER BY station_name_ko"""),
        "attractions": fetch_all(
            """SELECT attraction_id, attraction_name_ko AS name, attraction_name_en AS name_en, category,
                      is_wheelchair_accessible, has_parking, ST_X(geom) AS lon, ST_Y(geom) AS lat
               FROM tourism.attractions ORDER BY attraction_name_ko"""),
        "buildings": fetch_all(
            """SELECT building_id, building_name_ko AS name, building_name_en AS name_en, building_use,
                      floors_above, ST_X(geom) AS lon, ST_Y(geom) AS lat
               FROM building.buildings ORDER BY building_id"""),
        "facilities": fetch_all(
            """SELECT facility_id, facility_name_ko AS name, facility_name_en AS name_en, facility_type, responder,
                      ST_X(geom) AS lon, ST_Y(geom) AS lat
               FROM safety.facilities ORDER BY facility_type, facility_name_ko"""),
        "bbox": fetch_one(
            """SELECT ST_XMin(b) AS min_lon, ST_YMin(b) AS min_lat, ST_XMax(b) AS max_lon, ST_YMax(b) AS max_lat
               FROM core.songdo_bbox() b"""),
        "time_range": fetch_one(
            """SELECT MIN(observed_at) AS first_observation, MAX(observed_at) AS last_observation,
                      (SELECT MAX(observed_at) FROM mobility.vehicle_observations
                       WHERE observed_at < DATE '2026-10-03') AS fixture_day_end
               FROM mobility.vehicle_observations"""),
    }


@router.get("/map/frame", summary="State of the city at moment T, rebuilt from history (time slider)")
def map_frame(
    as_of: AwareDatetime | None = Query(None, description="ISO-8601 with offset; default = now"),
    max_staleness_s: int = Query(120, ge=1, le=86400, description="older vehicle positions count as idle"),
) -> dict[str, Any]:
    params = {"as_of": as_of, "stale": max_staleness_s}
    started = time.perf_counter()
    vehicles = fetch_all(
        f"""
        SELECT o.vehicle_id, v.vehicle_type, o.trip_id, r.route_number,
               ST_X(o.geom) AS lon, ST_Y(o.geom) AS lat, o.speed_kmh::float8 AS speed_kmh,
               o.heading_deg::float8 AS heading_deg, o.battery_state_of_charge_pct::float8 AS battery_pct,
               o.observed_at, ROUND(EXTRACT(EPOCH FROM ({NOW} - o.observed_at)))::int AS staleness_seconds
        FROM mobility.vehicles v
        CROSS JOIN LATERAL (
            SELECT * FROM mobility.vehicle_observations x
            WHERE x.vehicle_id = v.vehicle_id AND x.observed_at <= {NOW}
            ORDER BY x.observed_at DESC LIMIT 1
        ) o
        LEFT JOIN mobility.trips t ON t.trip_id = o.trip_id
        LEFT JOIN mobility.routes r ON r.route_id = t.route_id
        WHERE o.observed_at > {NOW} - make_interval(secs => %(stale)s)
        ORDER BY o.vehicle_id
        """, params)
    incidents = fetch_all(
        f"""SELECT incident_id, title, type_code, type_name, severity, status, status_since, reported_at,
                   assigned_facility_id, restriction_radius_m, lon, lat
            FROM ({ACTIVE_AS_OF_SQL}) a ORDER BY reported_at""", params)
    air = fetch_all(
        f"""
        SELECT s.station_id, s.station_name_ko AS name, l.measured_value::float8 AS pm25, l.observed_at,
               ROUND(EXTRACT(EPOCH FROM ({NOW} - l.observed_at)) / 60)::int AS age_minutes,
               CASE WHEN l.measured_value IS NULL THEN 'NO_DATA'
                    WHEN l.measured_value > vb.threshold_value THEN 'VERY_BAD'
                    WHEN l.measured_value > b.threshold_value  THEN 'BAD'
                    ELSE 'NORMAL' END AS grade
        FROM environment.stations s
        LEFT JOIN LATERAL (
            SELECT m.measured_value, m.observed_at FROM environment.measurements m
            WHERE m.station_id = s.station_id AND m.type_code = 'PM25' AND m.quality_flag = 'GOOD'
              AND m.observed_at <= {NOW} AND m.observed_at > {NOW} - INTERVAL '3 hours'
            ORDER BY m.observed_at DESC LIMIT 1
        ) l ON TRUE
        LEFT JOIN environment.alert_rules b  ON b.type_code  = 'PM25' AND b.severity  = 'BAD'
        LEFT JOIN environment.alert_rules vb ON vb.type_code = 'PM25' AND vb.severity = 'VERY_BAD'
        ORDER BY s.station_name_ko
        """, params)
    alerts = fetch_all(
        f"""
        SELECT a.alert_id, s.station_name_ko AS station, a.type_code, r.severity, a.peak_value::float8 AS peak_value,
               a.started_hour, a.alert_message
        FROM environment.alerts a
        JOIN environment.alert_rules r USING (rule_id)
        JOIN environment.stations s USING (station_id)
        WHERE a.started_hour <= {NOW}
          AND (a.status = 'ACTIVE' OR {NOW} < a.last_exceeded_hour + INTERVAL '1 hour')
        ORDER BY a.started_hour
        """, params)
    moment = fetch_one(f"SELECT {NOW} AS as_of", params)
    return {
        "as_of": moment["as_of"] if moment else None,
        "vehicles": vehicles,
        "incidents": incidents,
        "air": air,
        "alerts": alerts,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


# ---------------------------------------------------------------------------
# Ingestion activity
# ---------------------------------------------------------------------------
@router.get("/activity", summary="Latest ingestion batches, quarantine records and audit-ledger entries")
def activity(limit: int = Query(15, ge=1, le=200)) -> dict[str, Any]:
    params = {"limit": limit}
    return {
        "batches": fetch_all(
            """SELECT batch_id, source_id, domain_schema, target_table, adapter_name, status, rows_received,
                      rows_loaded, rows_duplicate, rows_quarantined, started_at
               FROM core.ingestion_batches ORDER BY batch_id DESC LIMIT %(limit)s""", params),
        "quarantine": fetch_all(
            """SELECT quarantine_id, batch_id, domain_schema, target_table, reason_code, reason_detail, payload,
                      recorded_at
               FROM core.quarantine_records ORDER BY quarantine_id DESC LIMIT %(limit)s""", params),
        "audit": fetch_all(
            """SELECT audit_id, domain_schema, table_name, entity_id, action, observed_at, received_at,
                      recorded_at, source_lineage, actor
               FROM core.event_audit_log ORDER BY audit_id DESC LIMIT %(limit)s""", params),
        "totals": fetch_one(
            """SELECT (SELECT count(*) FROM core.ingestion_batches)  AS batches,
                      (SELECT count(*) FROM core.quarantine_records) AS quarantined,
                      (SELECT count(*) FROM core.event_audit_log)    AS audit_entries"""),
    }


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
DOMAIN_TABLES = [
    ("Core", "core.places"), ("Core", "core.source_registry"),
    ("DB1 Mobility", "mobility.stops"), ("DB1 Mobility", "mobility.trips"),
    ("DB1 Mobility", "mobility.vehicle_observations"),
    ("DB2 Building", "building.devices"), ("DB2 Building", "building.telemetry"),
    ("DB3 Tourism", "tourism.attractions"), ("DB3 Tourism", "tourism.events"),
    ("DB4 Environment", "environment.stations"), ("DB4 Environment", "environment.measurements"),
    ("DB5 Safety", "safety.incidents"), ("DB5 Safety", "safety.incident_status_history"),
]
CROSS_VIEWS = ["v_attractions_events_near_stops", "v_building_devices_with_environmental_context",
               "v_incidents_mobility_environmental_impact"]
SCHEMAS = ["core", "mobility", "building", "tourism", "environment", "safety", "cross_domain"]


def _check(component: str, check: str, status: str, detail: str) -> dict[str, str]:
    return {"component": component, "check": check, "status": status, "detail": detail}


@router.get("/verification", summary="Platform checklist: PASS / WARN / FAIL with details")
def verification() -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    try:
        info = fetch_one("SELECT current_setting('server_version') AS pg, PostGIS_Lib_Version() AS postgis")
    except Exception as exc:  # database down: nothing else can be checked
        raise HTTPException(503, f"Database unavailable: {exc}") from None
    checks.append(_check("PostgreSQL", "Connection", "PASS", f"server {info['pg']}"))
    checks.append(_check("PostGIS", "Extension", "PASS", f"version {info['postgis']}"))

    present = {r["nspname"] for r in fetch_all(
        "SELECT nspname FROM pg_namespace WHERE nspname = ANY(%(s)s)", {"s": SCHEMAS})}
    missing = [s for s in SCHEMAS if s not in present]
    checks.append(_check("Schemas", "core + 5 domains + cross_domain", "FAIL" if missing else "PASS",
                         f"missing: {', '.join(missing)}" if missing else f"{len(SCHEMAS)} schemas present"))

    for component, table in DOMAIN_TABLES:
        try:
            # table names come from the constant list above, never from the request
            n = fetch_one(f"SELECT count(*) AS n FROM {table}")["n"]
            checks.append(_check(component, f"{table} has rows", "PASS" if n else "FAIL", f"{n:,} rows"))
        except Exception as exc:
            checks.append(_check(component, f"{table} has rows", "FAIL", str(exc).splitlines()[0]))

    for view in CROSS_VIEWS:
        try:
            t0 = time.perf_counter()
            n = fetch_one(f"SELECT count(*) AS n FROM cross_domain.{view}")["n"]
            ms = (time.perf_counter() - t0) * 1000
            checks.append(_check("Cross-domain", view, "PASS" if n else "WARN", f"{n:,} rows in {ms:.0f} ms"))
        except Exception as exc:
            checks.append(_check("Cross-domain", view, "FAIL", str(exc).splitlines()[0]))
    for mv in fetch_all("""SELECT view_name, row_count, age_minutes FROM cross_domain.v_materialized_view_freshness
                           ORDER BY view_name"""):
        never = mv["age_minutes"] is None
        checks.append(_check("Cross-domain", f"{mv['view_name']} refreshed", "WARN" if never else "PASS",
                             "never refreshed" if never else
                             f"{mv['row_count']:,} rows, refreshed {mv['age_minutes']} min ago"))

    counts = fetch_one(
        """SELECT
             (SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
               JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = 'core' AND c.relname = 'event_audit_log' AND NOT t.tgisinternal) AS ledger_triggers,
             (SELECT count(*) FROM pg_indexes WHERE schemaname = ANY(%(s)s) AND indexdef ILIKE '%%USING gist%%')
                 AS gist_indexes,
             (SELECT count(*) FROM pg_indexes WHERE schemaname = ANY(%(s)s) AND indexdef ILIKE '%%geography%%')
                 AS geography_indexes,
             (SELECT count(*) FROM pg_constraint k JOIN pg_namespace n ON n.oid = k.connamespace
               WHERE n.nspname = ANY(%(s)s) AND k.contype = 'x') AS exclusion_constraints,
             (SELECT count(*) FROM pg_constraint k JOIN pg_namespace n ON n.oid = k.connamespace
               WHERE n.nspname = ANY(%(s)s) AND k.contype = 'c') AS check_constraints,
             (SELECT count(*) FROM core.quarantine_records) AS quarantined,
             (SELECT count(DISTINCT reason_code) FROM core.quarantine_records) AS reasons,
             (SELECT count(*) FROM core.event_audit_log) AS audit_entries,
             (SELECT count(*) FROM core.source_registry WHERE provenance = 'OFFICIAL_OPEN_DATA') AS official_sources,
             (SELECT count(*) FROM core.source_registry) AS sources""", {"s": SCHEMAS})
    checks.append(_check("Ledger", "Append-only triggers on event_audit_log",
                         "PASS" if counts["ledger_triggers"] >= 2 else "FAIL",
                         f"{counts['ledger_triggers']} triggers, {counts['audit_entries']:,} entries"))
    checks.append(_check("Indexes", "Spatial GiST indexes", "PASS" if counts["gist_indexes"] else "FAIL",
                         f"{counts['gist_indexes']} GiST, of which {counts['geography_indexes']} on geography"))
    checks.append(_check("Constraints", "Exclusion + CHECK constraints",
                         "PASS" if counts["exclusion_constraints"] else "WARN",
                         f"{counts['exclusion_constraints']} EXCLUDE, {counts['check_constraints']} CHECK"))
    checks.append(_check("Data quality", "Quarantine in use", "PASS" if counts["quarantined"] else "WARN",
                         f"{counts['quarantined']:,} records, {counts['reasons']} reason codes"))
    checks.append(_check("Provenance", "Official sources registered",
                         "PASS" if counts["official_sources"] else "WARN",
                         f"{counts['official_sources']} official of {counts['sources']} sources"))

    backups = sorted((ROOT / "outputs").glob("*.backup"), key=lambda p: p.stat().st_mtime)
    checks.append(_check("Backup", "pg_dump file in outputs/", "PASS" if backups else "WARN",
                         f"{backups[-1].name}, {backups[-1].stat().st_size / 1e6:.1f} MB" if backups
                         else "no .backup file yet (see README: Backup and restore)"))

    summary = {s: sum(1 for c in checks if c["status"] == s) for s in ("PASS", "WARN", "FAIL")}
    return {"summary": summary, "checks": checks}


@router.post("/run-tests", dependencies=[Depends(require_token)],
             summary="Run the unittest suite in a separate process (about 5-10 s)")
def run_tests() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, "-W", "ignore", "-m", "unittest", "tests/test_platform.py"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Test suite did not finish within 5 minutes") from None
    lines = (proc.stderr or proc.stdout).strip().splitlines()
    ran = next((line for line in reversed(lines) if line.startswith("Ran ")), "")
    return {"passed": proc.returncode == 0, "summary": f"{ran} - {lines[-1] if lines else ''}",
            "elapsed_s": round(time.perf_counter() - started, 1), "output_tail": lines[-40:]}
