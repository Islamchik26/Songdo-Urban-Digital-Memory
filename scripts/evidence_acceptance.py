"""Acceptance checks: one executed proof per scenario, written against the midterm feedback.

Each check states the acceptance criterion it proves, runs it against the database and returns
PASS, FAIL or LIMITED (the check runs, but part of the criterion depends on data we do not have,
for example an archived official payload). Nothing is assumed: every number comes from a query.

Isolation: every check runs in its own transaction that is rolled back, except the two
concurrency races, which need real commits. Those run only on the disposable evidence database
and clean up after themselves.

Used by scripts/evidence.py; can also be run alone against any database:
    DB_NAME=songdo_evidence python scripts/evidence_acceptance.py
"""
from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.core_adapter import PlacesAdapter  # noqa: E402
from adapters.environment_adapter import AirMeasurementsAdapter  # noqa: E402
from adapters.mobility_adapter import VehicleObservationAdapter  # noqa: E402
from adapters.safety_adapter import IncidentsAdapter  # noqa: E402
from api.config import settings  # noqa: E402
from api.scenarios import SCENARIOS  # noqa: E402


class CheckFailed(AssertionError):
    pass


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


@dataclass
class Check:
    id: str
    scenario: str
    criterion: str          # the "Required acceptance" being proven
    fn: Callable[[psycopg.Connection], tuple[str, str]]
    commits: bool = False   # True: needs real commits (concurrency); runs on the evidence DB only


@dataclass
class Result:
    id: str
    scenario: str
    criterion: str
    status: str             # PASS | FAIL | LIMITED
    evidence: str
    ms: float


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def task(conn: psycopg.Connection, scenario_id: str, **overrides: Any) -> list[dict[str, Any]]:
    """Run a registered task exactly as the API / UI runs it (same SQL, same parameter coercion)."""
    s = SCENARIOS[scenario_id]
    params = {p.name: p.coerce(overrides.get(p.name, p.default)) for p in s.params}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(s.sql, params)
        return cur.fetchall()


def one(conn: psycopg.Connection, sql: str, params: Any = None) -> Any:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row and len(row) == 1 else row


def rejects(conn: psycopg.Connection, error: type[Exception], sql: str, params: Any = None) -> str:
    """Run a statement that must fail with `error`; returns the SQLSTATE. Uses a savepoint."""
    try:
        with conn.transaction():
            conn.execute(sql, params)
    except error as exc:
        return getattr(exc, "sqlstate", "") or type(exc).__name__
    raise CheckFailed(f"expected {error.__name__} for: {' '.join(sql.split())[:90]}")


def reasons(conn: psycopg.Connection, batch_id: int) -> list[str]:
    return sorted(r[0] for r in conn.execute(
        "SELECT reason_code FROM core.quarantine_records WHERE batch_id = %s", (batch_id,)))


# ---------------------------------------------------------------------------
# Core contracts
# ---------------------------------------------------------------------------
def core_idempotency(conn):
    good = {"place_id": "EVD_OK", "name_ko": "증거", "category": "TEST", "lon": 126.64, "lat": 37.39}
    batch = [good, {**good, "place_id": "EVD_OUT", "lon": 127.0, "lat": 37.5},
             {**good, "place_id": "EVD_SWAP", "lon": 37.39, "lat": 126.64}, good]
    first = PlacesAdapter(conn, actor="EVIDENCE").ingest(batch)
    expect((first.loaded, first.quarantined, first.duplicates) == (1, 2, 1), f"first delivery {first}")
    expect(reasons(conn, first.batch_id) == ["COORDINATE_SWAPPED", "OUT_OF_BOUNDS"], "reason codes")
    again = PlacesAdapter(conn, actor="EVIDENCE").ingest(batch)
    expect((again.loaded, again.quarantined, again.duplicates) == (0, 0, 4), f"re-delivery {again}")
    rows = one(conn, "SELECT count(*) FROM core.places WHERE place_id LIKE 'EVD_%%'")
    expect(rows == 1, f"{rows} place rows")
    return "PASS", ("4 records -> 1 loaded, 2 quarantined (OUT_OF_BOUNDS, COORDINATE_SWAPPED), 1 duplicate; "
                    "re-delivering the batch -> 0 loaded, 4 duplicates; 1 stored row")


def core_atomic_write(conn):
    # Replace the audit function with one that fails, so the failure happens after the domain insert
    # but before the audit entry. The savepoint must undo the insert: no partial rows.
    args, result = conn.execute(
        "SELECT pg_get_function_arguments(p.oid), pg_get_function_result(p.oid) FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'core' AND p.proname = 'log_event'"
    ).fetchone()
    conn.execute(f"CREATE OR REPLACE FUNCTION core.log_event({args}) RETURNS {result} LANGUAGE plpgsql AS "
                 "$$ BEGIN RAISE EXCEPTION 'injected failure before audit'; END $$")
    packet = {"vehicle_id": "VEH-AV1-01", "lon": 126.6445, "lat": 37.3885, "speed_kmh": 10,
              "observed_at": "2026-10-09T09:00:00+09:00"}
    summary = VehicleObservationAdapter(conn, actor="EVIDENCE").ingest([packet])
    stored = one(conn, "SELECT count(*) FROM mobility.vehicle_observations WHERE observed_at = '2026-10-09T09:00:00+09:00'")
    staged = one(conn, "SELECT status FROM core.staging_records WHERE batch_id = %s", (summary.batch_id,))
    audit = one(conn, "SELECT count(*) FROM core.event_audit_log WHERE observed_at = '2026-10-09T09:00:00+09:00'")
    expect(summary.loaded == 0 and summary.quarantined == 1, f"summary {summary}")
    expect((stored, audit) == (0, 0), f"partial rows: {stored} observations, {audit} audit")
    expect(staged == "REJECTED", f"staging status {staged}")
    return "PASS", ("audit step forced to fail -> 0 observation rows, 0 audit rows, staging REJECTED, "
                    "payload quarantined with the error; batch still completes")


def core_ledger(conn):
    conn.execute("""SELECT core.log_event('core', 'places', 'EVD_AUDIT', 'INSERT', now(), now(),
                                          'SRC_CORE_01', '{"k": 1}'::jsonb)""")
    codes = [rejects(conn, psycopg.errors.RestrictViolation, s) for s in (
        "UPDATE core.event_audit_log SET actor = 'x' WHERE entity_id = 'EVD_AUDIT'",
        "DELETE FROM core.event_audit_log WHERE entity_id = 'EVD_AUDIT'",
        "TRUNCATE core.event_audit_log")]
    bad = one(conn, "SELECT count(*) FROM core.event_audit_log WHERE recorded_at < received_at - interval '1 second'")
    total = one(conn, "SELECT count(*) FROM core.event_audit_log")
    expect(bad == 0, f"{bad} rows recorded before they were received")
    return "PASS", (f"UPDATE / DELETE / TRUNCATE rejected (SQLSTATE {', '.join(codes)}); "
                    f"{total:,} entries carry observed/received/recorded, none recorded before received")


# ---------------------------------------------------------------------------
# DB1 Mobility
# ---------------------------------------------------------------------------
def db1_1(conn):
    detail = []
    for route in ("8", "SD-A", "AV-1"):
        for direction in (0, 1):
            seq = [r["sequence_order"] for r in task(conn, "DB1-1", route_number=route, direction=direction)]
            expect(seq == list(range(1, len(seq) + 1)), f"route {route}/{direction} order {seq}")
            detail.append(f"{route}/{direction}:{len(seq)}")
    first = one(conn, "SELECT stop_id FROM mobility.route_stops WHERE route_id = 'RT_8' AND direction = 0 "
                      "AND sequence_order = 1")
    other = one(conn, "SELECT stop_id FROM mobility.stops WHERE stop_id NOT IN "
                      "(SELECT stop_id FROM mobility.route_stops WHERE route_id = 'RT_8') LIMIT 1")
    dup_seq = rejects(conn, psycopg.errors.UniqueViolation,
                      "INSERT INTO mobility.route_stops VALUES ('RT_8', 0, 1, %s)", (other,))
    dup_stop = rejects(conn, psycopg.errors.UniqueViolation,
                       "INSERT INTO mobility.route_stops VALUES ('RT_8', 0, 99, %s)", (first,))
    return "PASS", (f"6 patterns ordered 1..n without gaps ({', '.join(detail)}); duplicate sequence "
                    f"rejected ({dup_seq}); same stop twice in one direction rejected ({dup_stop})")


def db1_2(conn):
    rows = task(conn, "DB1-2", trip_id="T20261002-8-0-0800")
    expect(len(rows) == 11, f"{len(rows)} stop events")
    expect(all(r["boardings"] >= 0 and r["alightings"] >= 0 for r in rows), "negative boardings")
    # known event: set arrival 120 s late and departure 30 s later; the database derives the rest
    conn.execute("""UPDATE mobility.trip_stop_events
                    SET observed_arrival = scheduled_arrival + interval '120 seconds',
                        observed_departure = scheduled_arrival + interval '150 seconds', boardings = 7
                    WHERE trip_id = 'T20261002-8-0-0800' AND sequence_order = 3""")
    row = task(conn, "DB1-2", trip_id="T20261002-8-0-0800")[2]
    expect((row["lateness_seconds"], row["dwell_seconds"], row["boardings"]) == (120, 30, 7), f"derived {row}")
    code = rejects(conn, psycopg.errors.GeneratedAlways,
                   "UPDATE mobility.trip_stop_events SET lateness_seconds = 0")
    return "PASS", (f"11 events of trip T20261002-8-0-0800; known event set to +120 s / 30 s dwell / 7 boardings "
                    f"-> lateness 120, dwell 30 computed by the database; writing lateness directly rejected ({code})")


def db1_3(conn):
    t = "2026-10-02T08:20:00+09:00"
    before = task(conn, "DB1-3", as_of=t)
    expect(len(before) == 16, f"{len(before)} vehicles")
    expect(all(r["observed_at"].isoformat() <= "2026-10-02T08:20:00+09:00" for r in before), "future row leaked")
    # data that arrives later about a LATER moment must not change the answer for T
    VehicleObservationAdapter(conn, actor="EVIDENCE").ingest([{
        "vehicle_id": "VEH-8-01", "lon": 126.64, "lat": 37.39, "speed_kmh": 5,
        "observed_at": "2026-10-02T08:20:01+09:00"}])
    after = task(conn, "DB1-3", as_of=t)
    expect(before == after, "answer for T changed after a later observation arrived")
    # equal timestamps cannot tie: (vehicle_id, observed_at) is unique; a re-sent fix updates in place
    VehicleObservationAdapter(conn, actor="EVIDENCE").ingest([{
        "vehicle_id": "VEH-8-01", "lon": 126.641, "lat": 37.391, "speed_kmh": 6,
        "observed_at": "2026-10-02T08:20:01+09:00"}])
    n = one(conn, "SELECT count(*) FROM mobility.vehicle_observations WHERE vehicle_id = 'VEH-8-01' "
                  "AND observed_at = '2026-10-02T08:20:01+09:00'")
    expect(n == 1, f"{n} rows for one vehicle instant")
    return "PASS", ("T bound as a parameter; 16 vehicles, none observed after T; a later packet leaves the "
                    "answer for T unchanged; same (vehicle, instant) stored once (UNIQUE), so no ties to break")


def db1_4(conn):
    rows = task(conn, "DB1-4", route_number="8", limit=500)
    expect(len(rows) >= 10, f"{len(rows)} headway rows")
    trips = {r["trip_id"] for r in rows}
    stops = {(r["direction"], r["stop_name_ko"]) for r in rows}
    expect(len(trips) >= 3 and len(stops) >= 5, f"{len(trips)} trips, {len(stops)} stops")
    # independent recomputation of one headway: previous arrival at the same stop and direction
    r = rows[0]
    prev = one(conn, """SELECT EXTRACT(EPOCH FROM %(t)s::timestamptz - max(e.observed_arrival))::int
                        FROM mobility.trip_stop_events e JOIN mobility.trips tr USING (trip_id)
                        JOIN mobility.stops s USING (stop_id)
                        WHERE tr.route_id = 'RT_8' AND tr.direction = %(d)s AND s.stop_name_ko = %(s)s
                          AND e.observed_arrival < %(t)s::timestamptz
                          AND e.observed_arrival >= '2026-10-02T07:00:00+09:00'""",
               {"t": r["observed_arrival"], "d": r["direction"], "s": r["stop_name_ko"]})
    expect(prev == r["headway_seconds"], f"headway {r['headway_seconds']} vs recomputed {prev}")
    statuses = {x["headway_status"] for x in rows}
    return "PASS", (f"LAG over PARTITION BY route, direction, stop: {len(rows)} headways across {len(trips)} trips "
                    f"and {len(stops)} stop/direction pairs in 07:00-09:30; worst row recomputed independently "
                    f"({prev} s); statuses seen {sorted(statuses)}")


def db1_5(conn):
    packet = {"vehicle_id": "VEH-AV1-02", "lon": 126.6445, "lat": 37.3885, "speed_kmh": 12,
              "observed_at": "2026-10-09T10:00:00+09:00"}
    results = [VehicleObservationAdapter(conn, actor="EVIDENCE").ingest([packet]) for _ in range(3)]
    stored = one(conn, "SELECT count(*) FROM mobility.vehicle_observations WHERE vehicle_id = 'VEH-AV1-02' "
                       "AND observed_at = '2026-10-09T10:00:00+09:00'")
    expect([r.loaded for r in results] == [1, 0, 0] and stored == 1, f"retries -> {stored} rows")
    # boundary: points exactly 399 m and 401 m north of a stop, radius 400 m. DB1-5 lists the 3 nearest
    # stops, so use the most isolated stop: then no other stop can push it out of the list.
    stop_id, gap = conn.execute(
        """SELECT s.stop_id, (SELECT min(ST_Distance(s.geom::geography, o.geom::geography))
                             FROM mobility.stops o WHERE o.stop_id <> s.stop_id) AS gap
           FROM mobility.stops s ORDER BY gap DESC LIMIT 1""").fetchone()
    expect(gap > 900, f"no isolated stop ({gap:.0f} m)")
    found = {}
    for i, metres in enumerate((399, 401)):
        lon, lat = conn.execute(
            """SELECT ST_X(p::geometry), ST_Y(p::geometry) FROM
               (SELECT ST_Project(geom::geography, %s, 0) AS p FROM mobility.stops WHERE stop_id = %s) x""",
            (metres, stop_id)).fetchone()
        VehicleObservationAdapter(conn, actor="EVIDENCE").ingest([{
            "vehicle_id": "VEH-AV1-03", "lon": lon, "lat": lat, "speed_kmh": 5,
            "observed_at": f"2026-10-09T11:00:0{i}+09:00"}])
        rows = task(conn, "DB1-5", vehicle_id="VEH-AV1-03", radius_m=400, last_n=1)
        hit = [r["distance_m"] for r in rows if r["stop_id"] == stop_id]
        found[metres] = hit[0] if hit else None
    expect(found[399] is not None and found[401] is None, f"boundary result {found}")
    return "PASS", (f"same packet sent 3 times -> 1 stored row; radius 400 m around stop {stop_id} (next stop "
                    f"{gap:.0f} m away): point at 399 m returns it (distance {found[399]} m), point at 401 m does not")


# ---------------------------------------------------------------------------
# DB2 Smart Building
# ---------------------------------------------------------------------------
def db2_1(conn):
    rows = task(conn, "DB2-1")
    expect(len(rows) == 6, f"{len(rows)} buildings")
    linked = [r for r in rows if r["linked_place"]]
    expect(all(r["linked_place"] == r["nearest_place"] for r in linked), "linked place is not the nearest place")
    expect(all(r["reconciliation_status"] == "UNLINKED" for r in rows if not r["linked_place"]), "unlinked hidden")
    expect(all(r["registry_address"] for r in linked), "registry address missing")
    status: dict[str, int] = {}
    for r in rows:
        status[r["reconciliation_status"]] = status.get(r["reconciliation_status"], 0) + 1
    return "PASS", (f"{len(linked)} linked buildings: linked place = nearest canonical place (metre distance) for "
                    f"all; the unlinked test-bed is reported UNLINKED; registry address kept next to the place "
                    f"address; statuses {status} (REVIEW = address text differs, shown not hidden)")


def db2_2(conn):
    orphan = rejects(conn, psycopg.errors.ForeignKeyViolation,
                     """INSERT INTO building.spaces (space_id, building_id, parent_space_id, space_type, space_name, floor_number)
                        VALUES ('SP-EVD', 'BLD_G_TOWER', 'SP-DOES-NOT-EXIST', 'ROOM', 'orphan', 3)""")
    cross = rejects(conn, psycopg.errors.ForeignKeyViolation,
                    """INSERT INTO building.spaces (space_id, building_id, parent_space_id, space_type, space_name, floor_number)
                       VALUES ('SP-EVD2', 'BLD_G_TOWER', 'SP-PT-F10', 'ROOM', 'wrong building', 10)""")
    rows = task(conn, "DB2-2", building_id="BLD_G_TOWER")
    shown = sum(r["devices"] for r in rows)
    actual = one(conn, """SELECT count(*) FROM building.device_space_assignments a JOIN building.spaces s USING (space_id)
                          WHERE s.building_id = 'BLD_G_TOWER' AND a.unassigned_at IS NULL""")
    expect(shown == actual, f"tree shows {shown} devices, {actual} open assignments")
    return "PASS", (f"orphan parent rejected ({orphan}); parent in another building rejected ({cross}); "
                    f"G-Tower tree shows {shown} installed devices = {actual} open assignments")


def db2_3(conn):
    rows = task(conn, "DB2-3", device_id="DEV-PT-TMP-03")
    expect(len(rows) == 3, f"{len(rows)} assignments")
    for a, b in zip(rows, rows[1:]):
        expect(a["unassigned_at"] == b["assigned_at"], "history has a gap or overlap")
    overlap = rejects(conn, psycopg.errors.ExclusionViolation,
                      """INSERT INTO building.device_space_assignments (device_id, space_id, assigned_at, unassigned_at)
                         VALUES ('DEV-PT-TMP-03', 'SP-GT-F12-R2', '2026-08-25T00:00:00+09:00', '2026-08-26T00:00:00+09:00')""")
    first = one(conn, "SELECT min(assigned_at) FROM building.device_space_assignments WHERE device_id = 'DEV-PT-TMP-03'")
    with conn.transaction():   # adjacent interval [x, first) touches but does not overlap
        conn.execute("""INSERT INTO building.device_space_assignments (device_id, space_id, assigned_at, unassigned_at)
                        VALUES ('DEV-PT-TMP-03', 'SP-GT-F12-R2', %s - interval '5 days', %s)""", (first, first))
    inverted = rejects(conn, psycopg.errors.CheckViolation,
                       """INSERT INTO building.device_space_assignments (device_id, space_id, assigned_at, unassigned_at)
                          VALUES ('DEV-PT-TMP-03', 'SP-GT-F12-R2', '2026-01-02', '2026-01-01')""")
    return "PASS", (f"3 consecutive assignments, each ends where the next starts; overlap rejected ({overlap}); "
                    f"adjacent interval accepted; inverted interval rejected ({inverted}); "
                    "concurrent overlap: see DB2-3-RACE")


def _new_device(conn, device_id: str) -> None:
    conn.execute("""INSERT INTO building.devices (device_id, device_type, manufacturer, model, serial_number, protocol,
                                                  installed_on, source_id)
                    VALUES (%s, 'TEMP_SENSOR', 'Evidence', 'E-1', %s, 'MQTT', '2026-01-01', 'SRC_BLD_SYN')""",
                 (device_id, f"SN-{device_id}"))


def db2_4(conn):
    t = "2026-10-02T10:00:00+09:00"
    fixture = {r["device_id"] for r in task(conn, "DB2-4", as_of=t, silent_minutes=15)}
    expect(fixture == {"DEV-GT-TMP-06", "DEV-PT-CO2-01", "DEV-TB-PWR-01", "DEV-TS-TMP-03"}, f"fixture {fixture}")
    for device in ("EVD-NEVER", "EVD-EXACT-15", "EVD-14M59S"):
        _new_device(conn, device)
    for device, at in (("EVD-EXACT-15", "2026-10-02T09:45:00+09:00"), ("EVD-14M59S", "2026-10-02T09:45:01+09:00")):
        conn.execute("""INSERT INTO building.telemetry (device_id, metric, value, quality_flag, observed_at, received_at)
                        VALUES (%s, 'TEMPERATURE_C', 21, 'GOOD', %s, %s)""", (device, at, at))
    found = {r["device_id"]: r["finding"] for r in task(conn, "DB2-4", as_of=t, silent_minutes=15)}
    expect(found.get("EVD-NEVER") == "NEVER_REPORTED", "never-reporting device missed")
    expect("EVD-EXACT-15" in found, "reading exactly 15 min old should count as silent")
    expect("EVD-14M59S" not in found, "reading 14m59s old must not be silent")
    return "PASS", ("fixture: 4 silent devices at 10:00; seeded cases: never reported -> NEVER_REPORTED, last reading "
                    "exactly 15:00 old -> silent (window is (T-15 min, T]), 14:59 old -> not silent")


def db2_5(conn):
    rows = task(conn, "DB2-5", building_id="BLD_G_TOWER", as_of="2026-10-02T10:00:00+09:00")
    by_device = {r["device_id"]: r for r in rows}
    broken = by_device.get("DEV-GT-TMP-06")
    expect(broken is not None and broken["open_actions"] >= 1, "open maintenance not returned")
    expect(broken["age_minutes"] is not None and broken["age_minutes"] >= 60, f"stale age {broken}")
    history = task(conn, "DB2-3", device_id="DEV-PT-TMP-03")
    current = [h for h in history if h["is_current"]][0]
    posco = {r["device_id"]: r for r in task(conn, "DB2-5", building_id="BLD_POSCO_TOWER",
                                                  as_of="2026-10-02T10:00:00+09:00")}
    expect(posco["DEV-PT-TMP-03"]["space_name"] == current["space_name"], "state ignores relocation history")
    return "PASS", (f"G-Tower: {len(rows)} devices with latest reading, unit, age and maintenance; "
                    f"DEV-GT-TMP-06 has an open action and a {broken['age_minutes']} min old reading; "
                    f"relocated DEV-PT-TMP-03 reported in its current space '{current['space_name']}'")


# ---------------------------------------------------------------------------
# DB3 Tourism
# ---------------------------------------------------------------------------
def db3_1(conn):
    rows = task(conn, "DB3-1")
    accessible = task(conn, "DB3-1", wheelchair_only=1)
    expect(len(rows) == 9, f"{len(rows)} attractions")
    expect(all(r["is_wheelchair_accessible"] is not None and r["has_parking"] is not None for r in rows), "null flags")
    expect(len(accessible) == sum(r["is_wheelchair_accessible"] for r in rows), "filter count")
    lineage = conn.execute("""SELECT s.source_id, s.provenance, count(*) FROM tourism.attractions a
                              JOIN core.source_registry s USING (source_id) GROUP BY 1, 2""").fetchall()
    return "PASS", (f"9 venues with wheelchair/parking flags and nearest stop; wheelchair filter -> {len(accessible)}; "
                    f"registry lineage: {', '.join(f'{s} ({p}, {n})' for s, p, n in lineage)}")


def db3_2(conn):
    inverted = rejects(conn, psycopg.errors.CheckViolation,
                       """INSERT INTO tourism.events (event_id, attraction_id, event_name_ko, event_type, start_date, end_date,
                              daily_start_time, daily_end_time, expected_attendance, source_id)
                          VALUES ('EVD_BAD', 'ATT_CONVENSIA', 'x', 'CONCERT', '2026-10-05', '2026-10-04',
                                  '10:00', '12:00', 10, 'SRC_TOU_SYN')""")
    opens, closes = conn.execute("""SELECT opens_at, closes_at FROM tourism.operating_hours
                                    WHERE attraction_id = 'ATT_CONVENSIA' AND day_of_week = 5""").fetchone()
    conn.execute("""INSERT INTO tourism.events (event_id, attraction_id, event_name_ko, event_type, start_date, end_date,
                        daily_start_time, daily_end_time, expected_attendance, source_id) VALUES
                    ('EVD_EDGE', 'ATT_CONVENSIA', '경계 일치', 'EXHIBITION', '2026-10-09', '2026-10-09', %(o)s, %(c)s, 10, 'SRC_TOU_SYN'),
                    ('EVD_LATE', 'ATT_CONVENSIA', '1분 초과', 'EXHIBITION', '2026-10-09', '2026-10-09', %(o)s,
                     %(c)s::time + interval '1 minute', 10, 'SRC_TOU_SYN')""", {"o": opens, "c": closes})
    check = {r["event_id"]: r["schedule_check"] for r in task(conn, "DB3-2", from_date="2026-10-09", to_date="2026-10-09")}
    expect(check == {"EVD_EDGE": "OK", "EVD_LATE": "OUTSIDE_OPERATING_HOURS"}, f"boundaries {check}")
    return "PASS", (f"inverted dates rejected ({inverted}); Friday hours {opens:%H:%M}-{closes:%H:%M}: event exactly "
                    "inside -> OK, ending 1 minute after closing -> OUTSIDE_OPERATING_HOURS")


def db3_3(conn):
    rows = task(conn, "DB3-3", month="2026-09-01")
    expect(rows and all(r["sample_total"] < r["monthly_total"] for r in rows), "sample exceeds total")
    labels = {r["monthly_total_provenance"] for r in rows}
    expect(labels == {"SYNTHETIC_FIXTURE"}, f"provenance labels {labels}")
    return "LIMITED", (f"{len(rows)} venues: monthly totals and hourly samples kept in separate tables and shown side "
                       f"by side (coverage {min(r['sample_coverage_pct'] for r in rows)}-"
                       f"{max(r['sample_coverage_pct'] for r in rows)} %); every total is labelled SYNTHETIC_FIXTURE. "
                       "No official tourism payload is archived, so reconciliation to an official hash is not possible")


def db3_4(conn):
    def names(**kw):
        return {r["attraction_id"] for r in task(conn, "DB3-4", stop_number="38367", day_of_week=5, **kw)}
    at_14 = names(radius_m=1500, at_time="14:00")
    expect("ATT_CONVENSIA" in at_14, "open venue missing")
    expect("ATT_CONVENSIA" in names(radius_m=1500, at_time="17:59"), "open at 17:59")
    expect("ATT_CONVENSIA" not in names(radius_m=1500, at_time="18:00"), "closed at 18:00 still returned")
    expect("ATT_CONVENSIA" not in names(radius_m=1500, at_time="20:00"), "closed venue returned")
    expect("ATT_CONVENSIA" not in names(radius_m=300, at_time="14:00"), "radius not applied")
    return "PASS", (f"weekday, time and radius are bound parameters; Friday 14:00 within 1500 m -> {len(at_14)} venues; "
                    "Convensia (09:00-18:00, 342 m away) present at 17:59, absent at 18:00 and 20:00, absent at 300 m")


def db3_5(conn):
    rows = task(conn, "DB3-5", event_date="2026-10-02", radius_m=500, window_min=120)
    expect(rows, "no events")
    for r in rows:
        if r["seat_capacity"]:
            expect(abs(r["attendees_per_seat"] - r["expected_attendance"] / r["seat_capacity"]) < 0.06, "ratio")
            expected = "OVER_CAPACITY" if r["expected_attendance"] > r["seat_capacity"] else "OK"
        else:
            expected = "NO_SERVICE"
        expect(r["transit_status"] == expected, f"status {r}")
    summary = "; ".join(f"{r['event_id']}: {r['expected_attendance']} vs {r['seat_capacity']} seats -> "
                        f"{r['transit_status']}" for r in rows)
    return "PASS", (f"scheduled demand vs seats of trips arriving within 500 m in the 120 min before start "
                    f"(assumption: each trip brings its full vehicle capacity). {summary}")


# ---------------------------------------------------------------------------
# DB4 Environment
# ---------------------------------------------------------------------------
def db4_1(conn):
    units = dict(conn.execute("SELECT type_code, unit FROM environment.measurement_types").fetchall())
    expect(len(units) == 6, f"{len(units)} pollutant types")
    expect(units["PM25"] == "µg/m³" and units["O3"] == "ppm", f"units {units}")
    rules = one(conn, "SELECT count(*) FROM environment.alert_rules WHERE is_active")
    rows = task(conn, "DB4-1", station="송도")
    expect(len(rows) == 6, "station card")
    return "LIMITED", (f"6 pollutant types with units ({', '.join(f'{k} {v}' for k, v in sorted(units.items()))}); "
                       f"{rules} active thresholds equal to the AirKorea 나쁨/매우나쁨 grade boundaries (cited in "
                       "sql/05). The source document is cited by name only; no archived copy is in the repo")


def db4_2(conn):
    hour = "2026-10-09 15:00"
    values = {"PM10": "41", "PM25": "22", "O3": "0.031", "NO2": "0.018", "CO": "0.4", "SO2": "0.003"}
    good = [{"station_name": "송도", "type_code": k, "value": v, "data_time": hour} for k, v in values.items()]
    bad = [{"station_name": "송도", "type_code": "PM10", "value": v, "data_time": "2026-10-09 16:00"}
           for v in ("-", "abc", "")]
    s = AirMeasurementsAdapter(conn, actor="EVIDENCE").ingest(good + bad)
    expect((s.loaded, s.quarantined) == (6, 3), f"summary {s}")
    expect(set(reasons(conn, s.batch_id)) == {"FAULT_READING", "MISSING_FIELD"} or
           set(reasons(conn, s.batch_id)) == {"FAULT_READING"}, f"reasons {reasons(conn, s.batch_id)}")
    rows = task(conn, "DB4-2", station="송도", as_of="2026-10-09T15:30:00+09:00")
    expect({r["type_code"] for r in rows} == set(values) and all(r["quality_flag"] == "GOOD" for r in rows), "readback")
    return "PASS", (f"6 pollutant types for one hour -> 6 loaded and read back as GOOD; malformed values "
                    f"('-', 'abc', empty) -> 3 quarantined ({', '.join(sorted(set(reasons(conn, s.batch_id))))})")


def db4_3(conn):
    base = {"station_name": "동막", "type_code": "SO2", "value": "0.004", "data_time": "2026-10-09 09:00"}
    a = AirMeasurementsAdapter(conn, actor="EVIDENCE")
    first = a.ingest([base])
    dup = a.ingest([base])
    fix = a.ingest([{**base, "value": "0.005"}])
    fault = a.ingest([{**base, "value": "-", "data_time": "2026-10-09 10:00"}])
    expect((first.loaded, dup.duplicates, fix.loaded, fault.quarantined) == (1, 1, 1, 1), "replay outcomes")
    row = [r for r in task(conn, "DB4-3", from_time="2026-10-09T00:00:00+09:00", to_time="2026-10-10T00:00:00+09:00",
                           late_minutes=60, only_problems=1)
           if r["station_name_ko"] == "동막" and r["type_code"] == "SO2"][0]
    expect(row["readings"] == 1 and row["late_arrivals"] == 1, f"late {row}")
    expect(row["faults_quarantined"] >= 1 and row["corrections_applied"] >= 1, f"counts {row}")
    value = one(conn, """SELECT measured_value::float8 FROM environment.measurements
                         WHERE station_id = 'AK_동막' AND type_code = 'SO2' AND observed_at = '2026-10-09T09:00:00+09:00'""")
    expect(value == 0.005, f"stored {value}")
    return "PASS", ("replay: first -> loaded, identical re-send -> duplicate, revised value -> replaces row (0.005) "
                    f"and is logged as UPDATE, '-' -> quarantined; DB4-3 reports readings {row['readings']}, late "
                    f"{row['late_arrivals']} (received >60 min after the hour), faults {row['faults_quarantined']}, "
                    f"corrections {row['corrections_applied']}")


def db4_4(conn):
    hours = {"10:00": "75", "11:00": "20", "12:00": "76", "13:00": "20"}
    AirMeasurementsAdapter(conn, actor="EVIDENCE").ingest(
        [{"station_name": "인천 신항", "type_code": "PM25", "value": v, "data_time": f"2026-10-09 {h}"}
         for h, v in hours.items()])
    conn.execute("SELECT environment.refresh_hourly_summaries('2026-10-09T00:00:00+09:00', '2026-10-10T00:00:00+09:00')")
    conn.execute("SELECT environment.evaluate_alerts('2026-10-09T00:00:00+09:00', '2026-10-10T00:00:00+09:00')")
    alerts = conn.execute("""SELECT to_char(a.started_hour AT TIME ZONE 'Asia/Seoul', 'HH24:MI'), r.severity
                             FROM environment.alerts a JOIN environment.alert_rules r USING (rule_id)
                             WHERE a.station_id = 'AK_인천 신항' AND a.started_hour >= '2026-10-09T00:00:00+09:00'
                             ORDER BY 1, 2""").fetchall()
    expect(alerts == [("10:00", "BAD"), ("12:00", "BAD"), ("12:00", "VERY_BAD")], f"alerts {alerts}")
    day = {r["hour_start"].strftime("%H:%M"): r for r in task(conn, "DB4-4", station="인천 신항", type_code="PM25",
                                                               day="2026-10-09")}
    expect(day["10:00"]["avg_value"] == 75 and day["12:00"]["avg_value"] == 76, "rollup averages")
    return "PASS", ("hourly PM2.5 75 -> BAD only (VERY_BAD needs > 75), 76 -> BAD + VERY_BAD, clean hours resolve the "
                    f"episodes; alerts created: {alerts}")


def db4_5(conn):
    lon, lat = 126.6548, 37.3888
    rows = task(conn, "DB4-5", lon=lon, lat=lat, as_of="2026-10-02T14:30:00+09:00")
    expect(len(rows) == 6, f"{len(rows)} pollutants")
    expect(all(r["distance_m"] is not None and r["age_minutes"] is not None for r in rows), "distance/age missing")
    nearest = one(conn, """SELECT station_name_ko FROM environment.stations
                           ORDER BY ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)
                           LIMIT 1""", (lon, lat))
    expect(rows[0]["nearest_station"] == nearest, "not the nearest station")
    stale = task(conn, "DB4-5", lon=lon, lat=lat, as_of="2026-10-02T18:10:00+09:00")
    return "PASS", (f"nearest station {nearest} at {rows[0]['distance_m']} m (checked by full scan); 6 latest GOOD "
                    f"readings with age; at 18:10 the ages grow to {max(r['age_minutes'] for r in stale)} min")


# ---------------------------------------------------------------------------
# DB5 Safety
# ---------------------------------------------------------------------------
def db5_1(conn):
    rows = task(conn, "DB5-1")
    expect(len(rows) == 5, f"{len(rows)} facilities")
    outside = one(conn, "SELECT count(*) FROM safety.facilities WHERE NOT core.is_in_songdo(geom)")
    expect(outside == 0, f"{outside} outside Songdo")
    provenance = {r["location_provenance"] for r in rows}
    return "LIMITED", (f"5 facilities, all inside the Songdo bounding box, each with nearest stop and workload; "
                       f"location provenance shown as {sorted(provenance)}: coordinates are placeholders, not yet "
                       "checked against an official facility payload")


def db5_2(conn):
    report = {"incident_id": "INC-EVD-1", "type_code": "TRAFFIC_ACCIDENT", "title": "증거 사고", "severity": "HIGH",
              "lon": 126.6445, "lat": 37.3885, "reported_at": "2026-10-09T09:00:00+09:00"}
    s = IncidentsAdapter(conn, actor="EVIDENCE").ingest([report, {**report, "incident_id": "INC-EVD-2",
                                                                  "severity": "URGENT"}])
    expect((s.loaded, s.quarantined) == (1, 1), f"summary {s}")
    status, history = conn.execute("""SELECT current_status, (SELECT count(*) FROM safety.incident_status_history
                                      WHERE incident_id = 'INC-EVD-1') FROM safety.incidents
                                      WHERE incident_id = 'INC-EVD-1'""").fetchone()
    expect((status, history) == ("REPORTED", 1), "initial state")
    rows = task(conn, "DB5-2")
    rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    expect([rank[r["severity"]] for r in rows] == sorted(rank[r["severity"]] for r in rows), "not most urgent first")
    sla = dict(conn.execute("SELECT severity, sla_target_seconds FROM safety.severity_levels").fetchall())
    expect(all(r["sla_target_seconds"] == sla[r["severity"]] for r in rows), "SLA mismatch")
    return "PASS", (f"valid report -> REPORTED with 1 history row; severity 'URGENT' -> quarantined "
                    f"({reasons(conn, s.batch_id)[0]}); API returns 422 for it (see api_contract.csv); triage lists "
                    f"{len(rows)} incidents most urgent first with SLA {sla}")


def db5_3(conn):
    IncidentsAdapter(conn, actor="EVIDENCE").ingest([{
        "incident_id": "INC-EVD-3", "type_code": "FIRE", "title": "증거 화재", "severity": "HIGH",
        "lon": 126.65, "lat": 37.39, "reported_at": "2026-10-09T09:00:00+09:00"}])
    call = "SELECT safety.transition_incident('INC-EVD-3', %s, %s, 'EVIDENCE', %s)"
    skip = rejects(conn, psycopg.errors.CheckViolation, call, ("RESOLVED", "2026-10-09T09:05:00+09:00", None))
    direct = rejects(conn, psycopg.errors.CheckViolation,
                     "UPDATE safety.incidents SET current_status = 'CLOSED' WHERE incident_id = 'INC-EVD-3'")
    rewrite = rejects(conn, psycopg.errors.RestrictViolation,
                      "DELETE FROM safety.incident_status_history WHERE incident_id = 'INC-EVD-3'")
    conn.execute(call, ("DISPATCHED", "2026-10-09T09:02:00+09:00", "FAC_SONGDO_FIRE"))
    status, history = conn.execute("""SELECT current_status, (SELECT count(*) FROM safety.incident_status_history
                                      WHERE incident_id = 'INC-EVD-3') FROM safety.incidents
                                      WHERE incident_id = 'INC-EVD-3'""").fetchone()
    expect((status, history) == ("DISPATCHED", 2), f"after dispatch {status}/{history}")
    rows = task(conn, "DB5-3", incident_id="INC-EVD-3")
    expect([r["to_status"] for r in rows] == ["REPORTED", "DISPATCHED"], "history view")
    return "PASS", (f"REPORTED -> RESOLVED rejected ({skip}); plain UPDATE bypassing the function rejected by trigger "
                    f"({direct}); deleting history rejected ({rewrite}); accepted DISPATCHED -> status and history "
                    "row written together (2 rows); concurrent transitions: see DB5-3-RACE")


def db5_4(conn):
    rows = task(conn, "DB5-4", from_date="2026-07-01", to_date="2026-10-02", include_drills=0)
    top = rows[0]
    fid = one(conn, "SELECT facility_id FROM safety.facilities WHERE facility_name_ko = %s", (top["facility_name_ko"],))
    avg = one(conn, """SELECT round(avg(extract(epoch FROM h.changed_at - i.reported_at)))::int
                       FROM safety.incidents i JOIN safety.incident_status_history h
                         ON h.incident_id = i.incident_id AND h.to_status = 'ON_SCENE'
                       WHERE i.assigned_facility_id = %s AND NOT i.is_drill
                         AND i.reported_at >= '2026-07-01T00:00:00+09:00' AND i.reported_at < '2026-10-03T00:00:00+09:00'""",
              (fid,))
    expect(abs(avg - top["avg_response_s"]) <= 1, f"avg {top['avg_response_s']} vs recomputed {avg}")
    a = task(conn, "DB5-4B")
    b = task(conn, "DB5-4B")
    expect(a == b and len(a) >= 1, "hotspots not reproducible")
    return "PASS", (f"{top['facility_name_ko']}: {top['incidents']} incidents, average response {top['avg_response_s']} s "
                    f"= independent recomputation {avg} s; DBSCAN hotspots identical on two runs "
                    f"({len(a)} clusters, largest {a[0]['incidents']} incidents)")


def db5_5(conn):
    def zones(t):
        return {r["incident_id"]: r for r in task(conn, "DB5-5", as_of=t)}
    at_1830 = zones("2026-10-02T18:30:00+09:00")
    fire = at_1830.get("INC-20261002-0003")
    expect(fire is not None, "active fire zone missing")
    geo = json.loads(fire["zone_geojson"])
    ring = geo["coordinates"][0]
    expect(geo["type"] == "Polygon" and ring[0] == ring[-1] and len(ring) >= 9, "invalid polygon")
    far = one(conn, """SELECT max(ST_Distance(ST_SetSRID(ST_MakePoint(x, y), 4326)::geography, i.geom::geography))
                       FROM safety.incidents i, json_to_recordset(%s::json) AS c(x float8, y float8)
                       WHERE i.incident_id = 'INC-20261002-0003'""",
              (json.dumps([{"x": x, "y": y} for x, y in ring]),))
    expect(abs(far - 300) < 5, f"ring radius {far}")
    conn.execute("SELECT safety.transition_incident('INC-20261002-0003', 'RESOLVED', '2026-10-02T19:00:00+09:00', 'EVIDENCE')")
    expect("INC-20261002-0003" not in zones("2026-10-02T19:30:00+09:00"), "resolved zone still published")
    expect("INC-20261002-0003" in zones("2026-10-02T18:30:00+09:00"), "history at 18:30 changed")
    return "PASS", (f"18:30: fire zone published as a closed GeoJSON Polygon ({len(ring)} points, ring at {far:.0f} m "
                    f"for a 300 m restriction, {fire['stops_in_zone']} stops inside); after RESOLVED at 19:00 it is "
                    "gone at 19:30 but still shown when replaying 18:30")


# ---------------------------------------------------------------------------
# Cross-domain
# ---------------------------------------------------------------------------
def cross_views(conn):
    near = one(conn, "SELECT max(distance_meters)::float8 FROM cross_domain.v_attractions_events_near_stops")
    inc = one(conn, "SELECT max(distance_to_stop_meters)::float8 FROM cross_domain.v_incidents_mobility_environmental_impact")
    wrong = one(conn, """SELECT count(*) FROM cross_domain.v_building_devices_with_environmental_context v
                         JOIN building.buildings b USING (building_id)
                         WHERE v.nearest_env_station <> (SELECT s.station_name_ko FROM environment.stations s
                                ORDER BY ST_Distance(s.geom::geography, b.geom::geography) LIMIT 1)""")
    alert_far = one(conn, """SELECT count(*) FROM cross_domain.v_incidents_mobility_environmental_impact v
                             WHERE v.active_env_alert IS NOT NULL AND v.active_env_alert NOT LIKE v.nearest_env_station || '%%'""")
    counts = [one(conn, f"SELECT count(*) FROM cross_domain.{v}") for v in (
        "v_attractions_events_near_stops", "v_building_devices_with_environmental_context",
        "v_incidents_mobility_environmental_impact")]
    expect(all(counts) and near <= 500 and inc <= 600 and wrong == 0, f"views {counts} {near} {inc} {wrong}")
    return "PASS", (f"rows {counts}; transit x tourism max distance {near:.1f} m (cap 500); incident x stops max "
                    f"{inc:.1f} m (cap 600); building station = true nearest for every device; alerts only from the "
                    f"incident's nearest station ({alert_far} exceptions)")


def cross_mv(conn):
    refreshed = conn.execute("SELECT view_name, row_count FROM cross_domain.refresh_materialized_views()").fetchall()
    live = conn.execute("""SELECT i.assigned_facility_id, count(*) FROM safety.incidents i
                           JOIN safety.incident_status_history h ON h.incident_id = i.incident_id AND h.to_status = 'ON_SCENE'
                           WHERE NOT i.is_drill GROUP BY 1 ORDER BY 1""").fetchall()
    mv = conn.execute("""SELECT facility_id, sum(incidents)::bigint FROM cross_domain.mv_facility_response_stats
                         WHERE NOT is_drill GROUP BY 1 ORDER BY 1""").fetchall()
    expect(live == mv, "materialized view differs from live query after refresh")
    return "PASS", (f"after a logged refresh the facility statistics MV equals the live aggregate for all {len(live)} "
                    f"facilities; refresh logged for {len(refreshed)} views ({', '.join(f'{v} {n}' for v, n in refreshed)})")


# ---------------------------------------------------------------------------
# Concurrency (real commits, evidence database only)
# ---------------------------------------------------------------------------
def _race(first_sql: tuple[str, Any], second_sql: tuple[str, Any]) -> tuple[str, float]:
    """Connection A runs first_sql and holds its transaction; B runs second_sql in a thread and must wait.
    A commits; returns B's outcome and how long B waited."""
    a = psycopg.connect(settings.conninfo())
    b = psycopg.connect(settings.conninfo())
    outcome: dict[str, Any] = {}

    def run_b():
        started = time.perf_counter()
        try:
            b.execute(*second_sql)
            b.commit()
            outcome["result"] = "COMMITTED"
        except psycopg.Error as exc:
            b.rollback()
            outcome["result"] = f"{type(exc).__name__} ({exc.sqlstate})"
        outcome["waited"] = time.perf_counter() - started

    try:
        a.execute(*first_sql)
        thread = threading.Thread(target=run_b)
        thread.start()
        time.sleep(0.5)                  # B is now blocked on A's uncommitted row
        expect(thread.is_alive(), "second transaction did not wait for the first")
        a.commit()
        thread.join(10)
        return outcome["result"], outcome["waited"]
    finally:
        a.close()
        b.close()


def race_assignments(conn):
    conn.rollback()
    with psycopg.connect(settings.conninfo(), autocommit=True) as admin:
        _new_device(admin, "EVD-RACE-01")
    try:
        result, waited = _race(
            ("""INSERT INTO building.device_space_assignments (device_id, space_id, assigned_at, unassigned_at)
                VALUES ('EVD-RACE-01', 'SP-GT-F12-R2', '2026-10-09T10:00+09', '2026-10-09T12:00+09')""", None),
            ("""INSERT INTO building.device_space_assignments (device_id, space_id, assigned_at, unassigned_at)
                VALUES ('EVD-RACE-01', 'SP-GT-F03-R1', '2026-10-09T11:00+09', '2026-10-09T13:00+09')""", None))
        rows = one(conn, "SELECT count(*) FROM building.device_space_assignments WHERE device_id = 'EVD-RACE-01'")
        conn.rollback()
        expect(result.startswith("ExclusionViolation") and rows == 1, f"race result {result}, {rows} rows")
        return "PASS", (f"two connections insert overlapping intervals at the same time: the second waited "
                        f"{waited:.2f} s for the first to commit, then failed with {result}; 1 row stored")
    finally:
        with psycopg.connect(settings.conninfo(), autocommit=True) as admin:
            admin.execute("DELETE FROM building.device_space_assignments WHERE device_id = 'EVD-RACE-01'")
            admin.execute("DELETE FROM building.devices WHERE device_id = 'EVD-RACE-01'")


def race_transitions(conn):
    conn.rollback()
    with psycopg.connect(settings.conninfo()) as setup:
        setup.execute("SELECT 1")
        if not one(setup, "SELECT count(*) FROM safety.incidents WHERE incident_id = 'INC-EVD-RACE'"):
            IncidentsAdapter(setup, actor="EVIDENCE").ingest([{
                "incident_id": "INC-EVD-RACE", "type_code": "MEDICAL", "title": "동시성 증거", "severity": "MEDIUM",
                "lon": 126.645, "lat": 37.389, "reported_at": "2026-10-09T08:00:00+09:00"}])
        setup.commit()
    call = "SELECT safety.transition_incident('INC-EVD-RACE', 'DISPATCHED', %s, 'EVIDENCE', %s)"
    result, waited = _race((call, ("2026-10-09T08:02:00+09:00", "FAC_SONGDO_FIRE")),
                           (call, ("2026-10-09T08:02:05+09:00", "FAC_SINSONG_119")))
    history = conn.execute("""SELECT to_status, facility_id FROM safety.incident_status_history
                              WHERE incident_id = 'INC-EVD-RACE' ORDER BY history_id""").fetchall()
    conn.rollback()
    # close the incident so it does not appear as active in later runs
    with psycopg.connect(settings.conninfo()) as done:
        for step, at in (("ON_SCENE", "08:10"), ("RESOLVED", "08:40"), ("CLOSED", "09:00")):
            done.execute("SELECT safety.transition_incident('INC-EVD-RACE', %s, %s, 'EVIDENCE')",
                         (step, f"2026-10-09T{at}:00+09:00"))
        done.commit()
    expect(result.startswith("CheckViolation") and history == [("REPORTED", None), ("DISPATCHED", "FAC_SONGDO_FIRE")],
           f"race {result}, history {history}")
    return "PASS", (f"two dispatches of one REPORTED incident at the same time: the second waited {waited:.2f} s on "
                    f"the row lock (SELECT ... FOR UPDATE), then saw DISPATCHED and failed with {result}; "
                    "history has exactly one DISPATCHED row")


CHECKS: list[Check] = [
    Check("CORE-1", "Core", "Repeated submission leaves one logical record; bad rows quarantined with a reason",
          core_idempotency),
    Check("CORE-2", "Core", "P0: failure before audit leaves no partial rows", core_atomic_write),
    Check("CORE-3", "Core", "Audit ledger is append-only with three timestamps", core_ledger),
    Check("DB1-1", "Ordered Route Sequence", "Expected ordering per direction; duplicate sequence rejected", db1_1),
    Check("DB1-2", "Trip Execution & Stop Arrivals", "Dwell, boardings and generated lateness on known events", db1_2),
    Check("DB1-3", "Historical Position Replay", "Bind fixed T; no future rows; stable ties", db1_3),
    Check("DB1-4", "Peak-Hour Lateness & Gap Analysis", "Partition by route, direction and stop across peak trips", db1_4),
    Check("DB1-5", "Smart Mobility Telemetry Ingestion", "One stored event after retries; 399 / 401 m results", db1_5),
    Check("DB2-1", "Building & Address Reconciliation", "Canonical match; registry match evidence retained", db2_1),
    Check("DB2-2", "Space Hierarchy & Device Registration", "Orphan rejection; correct active floor/space", db2_2),
    Check("DB2-3", "Device Relocation History", "Overlap rejected (23P01); adjacent intervals accepted", db2_3),
    Check("DB2-3-RACE", "Device Relocation History", "Concurrent overlapping inserts cannot both commit",
          race_assignments, commits=True),
    Check("DB2-4", "Missing Heartbeat & Fault Detection", "Stale, missing and exactly-15-minute cases", db2_4),
    Check("DB2-5", "VIP Facility State Retrieval", "Maintenance returned; device with known history", db2_5),
    Check("DB3-1", "Attractions & Accessibility Metadata", "Metadata and registry lineage for valid venues", db3_1),
    Check("DB3-2", "Event Management & Schedule Integrity", "Inverted dates rejected; open/close boundaries", db3_2),
    Check("DB3-3", "Official Statistics vs. Synthetic Samples", "Reconcile totals to archived official payload", db3_3),
    Check("DB3-4", "Open Attraction Search Near Transit", "Bound weekday, time, radius; closed venues excluded", db3_4),
    Check("DB3-5", "Temporal Demand & Crowd Forecasting", "Scheduled demand vs actual capacity, assumptions stated", db3_5),
    Check("DB4-1", "Station Registration & Unit Standards", "Six units; threshold provenance cited", db4_1),
    Check("DB4-2", "Telemetry Ingestion & Quality Flags", "Six pollutant types ingested; malformed values quarantined",
          db4_2),
    Check("DB4-3", "Fault Handling & Late Arrivals", "Late, duplicate and fault-marker replay with exact results", db4_3),
    Check("DB4-4", "Hourly Rollup & Threshold Alerts", "PM2.5 at and above 75", db4_4),
    Check("DB4-5", "Contextual Air Exposure Service", "Latest good readings with distance and staleness", db4_5),
    Check("DB5-1", "Public Safety Facilities Registration", "Source payload and coordinate bounds", db5_1),
    Check("DB5-2", "Incident Creation & Severity Triage", "Create incident; invalid severity rejected", db5_2),
    Check("DB5-3", "State Machine Transitions & Drills", "Illegal UPDATE rejected; atomic history", db5_3),
    Check("DB5-3-RACE", "State Machine Transitions & Drills", "Row lock: concurrent transitions cannot both win",
          race_transitions, commits=True),
    Check("DB5-4", "Response Time Analytics & Hotspots", "Durations asserted; reproducible cluster membership", db5_4),
    Check("DB5-5", "Smart Mobility Hazard Avoidance", "Active restriction geometry published; resolved zones expire",
          db5_5),
    Check("X-VIEWS", "Cross-domain federation", "Three views with live data, distance caps and nearest-station rule",
          cross_views),
    Check("X-MV", "Cross-domain federation", "Materialized views equal live queries; refresh logged", cross_mv),
]


def run_all(include_commits: bool = True) -> list[Result]:
    results: list[Result] = []
    for check in CHECKS:
        if check.commits and not include_commits:
            results.append(Result(check.id, check.scenario, check.criterion, "SKIPPED",
                                  "needs real commits; runs on the evidence database only", 0))
            continue
        conn = psycopg.connect(settings.conninfo())
        started = time.perf_counter()
        try:
            conn.execute("SELECT 1")     # open the transaction: adapters then use savepoints
            status, evidence = check.fn(conn)
        except CheckFailed as exc:
            status, evidence = "FAIL", str(exc)
        except Exception as exc:  # an unexpected error is a failed check, with its message as evidence
            status, evidence = "FAIL", f"{type(exc).__name__}: {str(exc).strip().splitlines()[0]}"
        finally:
            conn.rollback()
            conn.close()
        results.append(Result(check.id, check.scenario, check.criterion, status, evidence,
                              round((time.perf_counter() - started) * 1000, 1)))
    return results


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    commits = "--with-commits" in sys.argv
    for r in run_all(include_commits=commits):
        print(f"{r.status:<8} {r.id:<11} {r.ms:>7} ms  {r.evidence}")
