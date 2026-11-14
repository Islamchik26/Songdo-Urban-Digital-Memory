"""Platform test suite.   python -m unittest tests/test_platform.py

Pure tests always run. Database tests are skipped (not failed) when PostgreSQL
is unreachable. Database tests run inside a transaction that is rolled back, so
they leave no rows behind.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from adapters.base_adapter import calculate_payload_hash, validate_coordinate  # noqa: E402
from adapters.building_adapter import BuildingTelemetryAdapter  # noqa: E402
from adapters.core_adapter import PlacesAdapter  # noqa: E402
from adapters.mobility_adapter import StopsAdapter, VehicleObservationAdapter  # noqa: E402
from api.config import settings  # noqa: E402
from api.main import app  # noqa: E402
from api.scenarios import SCENARIOS  # noqa: E402


def _db_available() -> bool:
    try:
        with psycopg.connect(settings.conninfo()) as conn:
            conn.execute("SELECT 1 FROM core.places LIMIT 1")
        return True
    except psycopg.Error:
        return False


DB_OK = _db_available()
needs_db = unittest.skipUnless(DB_OK, "PostgreSQL / core schema not available")


class CoordinateValidationTests(unittest.TestCase):
    def test_valid_songdo_coordinate(self):
        self.assertTrue(validate_coordinate(126.6548, 37.3888).ok)

    def test_out_of_bounds(self):
        result = validate_coordinate(127.0, 37.5)  # Seoul
        self.assertEqual(result.reason_code, "OUT_OF_BOUNDS")

    def test_swapped_lat_lon(self):
        result = validate_coordinate(37.3888, 126.6548)
        self.assertEqual(result.reason_code, "COORDINATE_SWAPPED")

    def test_malformed_and_missing(self):
        self.assertEqual(validate_coordinate("ABC", 37.38).reason_code, "MALFORMED_COORDINATE")
        self.assertEqual(validate_coordinate(float("nan"), 37.38).reason_code, "MALFORMED_COORDINATE")
        self.assertEqual(validate_coordinate(None, None).reason_code, "MISSING_COORDINATE")


class PayloadHashTests(unittest.TestCase):
    def test_hash_ignores_key_order_and_keeps_utf8(self):
        a = calculate_payload_hash({"vehicle": "V001", "name": "송도", "speed": 20})
        b = calculate_payload_hash({"speed": 20, "name": "송도", "vehicle": "V001"})
        self.assertEqual(a, b)
        self.assertRegex(a, r"^[0-9a-f]{64}$")
        self.assertNotEqual(a, calculate_payload_hash({"vehicle": "V001", "name": "송도", "speed": 21}))


class ScenarioRegistryTests(unittest.TestCase):
    def test_all_25_team_scenarios_registered(self):
        for db in range(1, 6):
            for n in range(1, 6):
                self.assertIn(f"DB{db}-{n}", SCENARIOS)

    def test_ready_scenarios_have_sql(self):
        for s in SCENARIOS.values():
            if s.status == "ready":
                self.assertTrue(s.sql and s.sql.strip().upper().startswith(("SELECT", "WITH")), s.id)


class ApiValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._ctx = TestClient(app)
        cls.client = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)

    def test_invalid_query_returns_422(self):
        self.assertEqual(self.client.get("/api/v1/core/places/nearby?lon=999").status_code, 422)
        self.assertEqual(self.client.get("/api/v1/core/places/nearby?radius_m=-5").status_code, 422)

    def test_ingest_requires_token(self):
        self.assertEqual(self.client.post("/api/v1/core/places", json=[]).status_code, 401)

    def test_scenario_param_validation(self):
        r = self.client.post("/api/v1/scenarios/CORE-1/run", json={"params": {"radius_m": "far"}})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.client.post("/api/v1/scenarios/NOPE/run", json={}).status_code, 404)

    @needs_db
    def test_health_and_nearby_places(self):
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "ok")
        r = self.client.post("/api/v1/scenarios/CORE-1/run", json={"params": {"radius_m": 2000}, "explain": True})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertGreater(body["row_count"], 0)
        distances = [row["dist_m"] for row in body["rows"]]
        self.assertEqual(distances, sorted(distances))
        self.assertTrue(body["explain"])

    def test_observation_endpoint_validates_input(self):
        good = {"vehicle_id": "VEH-AV1-01", "lon": 126.6445, "lat": 37.3885, "speed_kmh": 12,
                "observed_at": "2026-10-02T10:30:00+09:00"}
        self.assertEqual(self.client.post("/api/v1/mobility/observation", json=good).status_code, 401)
        headers = {"X-API-Token": settings.ingest_api_token}
        for bad in ({**good, "speed_kmh": -3}, {**good, "observed_at": "2026-10-02T10:30:00"},
                    {k: v for k, v in good.items() if k != "vehicle_id"}):
            r = self.client.post("/api/v1/mobility/observation", json=bad, headers=headers)
            self.assertEqual(r.status_code, 422, bad)

    @needs_db
    def test_every_ready_scenario_runs_with_defaults(self):
        for s in SCENARIOS.values():
            if s.status == "ready":
                r = self.client.post(f"/api/v1/scenarios/{s.id}/run", json={})
                self.assertEqual(r.status_code, 200, f"{s.id}: {r.text[:300]}")
                if s.domain != "core":
                    self.assertGreater(r.json()["row_count"], 0, s.id)

    @needs_db
    def test_route_sequence_is_gap_free(self):
        r = self.client.get("/api/v1/mobility/routes/8/stops?direction=0")
        self.assertEqual(r.status_code, 200)
        orders = [row["sequence_order"] for row in r.json()]
        self.assertEqual(orders, list(range(1, len(orders) + 1)))
        self.assertEqual(self.client.get("/api/v1/mobility/routes/NOPE/stops").status_code, 404)

    @needs_db
    def test_point_in_time_replay(self):
        t = "2026-10-02T08:20:00+09:00"
        rows = self.client.get("/api/v1/mobility/vehicles/positions", params={"as_of": t}).json()
        self.assertGreater(len(rows), 0)
        self.assertEqual(len(rows), len({r["vehicle_id"] for r in rows}))        # one row per vehicle
        self.assertTrue(all(r["staleness_seconds"] >= 0 for r in rows))           # nothing from the future
        before_service = self.client.get("/api/v1/mobility/vehicles/positions",
                                         params={"as_of": "2026-10-02T05:00:00+09:00"}).json()
        self.assertEqual(before_service, [])
        geo = self.client.get("/api/v1/mobility/stops.geojson").json()
        self.assertEqual(geo["type"], "FeatureCollection")
        self.assertGreater(len(geo["features"]), 100)

    @needs_db
    def test_building_state_and_silent_devices(self):
        t = "2026-10-02T10:00:00+09:00"
        state = self.client.get("/api/v1/building/buildings/BLD_G_TOWER/state", params={"as_of": t})
        self.assertEqual(state.status_code, 200)
        summary = state.json()["summary"]
        self.assertTrue(18 < summary["avg_indoor_temperature_c"] < 28)
        self.assertEqual(summary["open_maintenance_actions"], 1)
        self.assertEqual(self.client.get("/api/v1/building/buildings/NOPE/state").status_code, 404)

        silent = self.client.get("/api/v1/building/devices/silent", params={"as_of": t}).json()
        self.assertEqual({d["device_id"] for d in silent},
                         {"DEV-GT-TMP-06", "DEV-PT-CO2-01", "DEV-TB-PWR-01", "DEV-TS-TMP-03"})
        # at 08:00 only the device that never reported is silent
        early = self.client.get("/api/v1/building/devices/silent",
                                params={"as_of": "2026-10-02T08:00:00+09:00"}).json()
        self.assertEqual([d["device_id"] for d in early], ["DEV-TS-TMP-03"])

    def test_building_write_endpoints_validate_input(self):
        headers = {"X-API-Token": settings.ingest_api_token}
        reading = {"device_id": "DEV-GT-TMP-01", "metric": "TEMPERATURE_C", "value": 22.0,
                   "observed_at": "2026-10-02T10:32:00+09:00"}
        self.assertEqual(self.client.post("/api/v1/building/telemetry", json=reading).status_code, 401)
        for bad in ({**reading, "value": "-"}, {**reading, "observed_at": "yesterday"}):
            r = self.client.post("/api/v1/building/telemetry", json=bad, headers=headers)
            self.assertEqual(r.status_code, 422, bad)
        r = self.client.post("/api/v1/building/devices/DEV-GT-TMP-01/relocate", json={"space_id": "X"},
                             headers=headers)
        self.assertEqual(r.status_code, 422)


@needs_db
class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(settings.conninfo())
        # Open the transaction now, so that conn.transaction() inside the adapter
        # becomes a savepoint instead of a transaction that commits on its own.
        self.conn.execute("SELECT 1")

    def tearDown(self):
        self.conn.rollback()  # nothing a test writes is ever committed
        self.conn.close()

    def test_is_in_songdo_function(self):
        inside, outside = self.conn.execute(
            """SELECT core.is_in_songdo(ST_SetSRID(ST_MakePoint(126.6548, 37.3888), 4326)),
                      core.is_in_songdo(ST_SetSRID(ST_MakePoint(127.0, 37.5), 4326))"""
        ).fetchone()
        self.assertTrue(inside)
        self.assertFalse(outside)

    def test_pipeline_loads_quarantines_and_deduplicates(self):
        good = {"place_id": "TEST_OK", "name_ko": "테스트", "category": "TEST", "lon": 126.64, "lat": 37.39}
        outside = {**good, "place_id": "TEST_OUT", "lon": 127.0, "lat": 37.5}
        swapped = {**good, "place_id": "TEST_SWAP", "lon": 37.39, "lat": 126.64}

        first = PlacesAdapter(self.conn, actor="TEST").ingest([good, outside, swapped, good])
        self.assertEqual((first.loaded, first.quarantined, first.duplicates), (1, 2, 1))

        reasons = {r[0] for r in self.conn.execute(
            "SELECT reason_code FROM core.quarantine_records WHERE batch_id = %s", (first.batch_id,))}
        self.assertEqual(reasons, {"OUT_OF_BOUNDS", "COORDINATE_SWAPPED"})

        audit = self.conn.execute(
            "SELECT action, actor FROM core.event_audit_log WHERE entity_id = 'TEST_OK'").fetchall()
        self.assertEqual(audit, [("INSERT", "TEST")])

        # re-delivery of the whole batch changes nothing
        again = PlacesAdapter(self.conn, actor="TEST").ingest([good, outside, swapped])
        self.assertEqual((again.loaded, again.quarantined, again.duplicates), (0, 0, 3))

    def test_audit_log_is_append_only(self):
        self.conn.execute(
            """SELECT core.log_event('core', 'places', 'TEST_AUDIT', 'INSERT', now(), now(),
                                     'SRC_CORE_01', '{"k": 1}'::jsonb)""")
        for statement in ("UPDATE core.event_audit_log SET actor = 'x' WHERE entity_id = 'TEST_AUDIT'",
                          "DELETE FROM core.event_audit_log WHERE entity_id = 'TEST_AUDIT'"):
            with self.assertRaises(psycopg.errors.RestrictViolation):
                with self.conn.transaction():
                    self.conn.execute(statement)

    def test_radius_query_can_use_gist_index(self):
        self.conn.execute("SET LOCAL enable_seqscan = off")
        plan = "\n".join(r[0] for r in self.conn.execute(
            """EXPLAIN SELECT place_id FROM core.places
               WHERE ST_DWithin(geom::geography,
                     ST_SetSRID(ST_MakePoint(126.6548, 37.3888), 4326)::geography, 1000)"""))
        self.assertIn("idx_places_geog", plan)


@needs_db
class MobilityDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(settings.conninfo())
        self.conn.execute("SELECT 1")  # open the transaction; everything below is rolled back

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def test_lateness_and_dwell_are_generated(self):
        mismatches = self.conn.execute(
            """SELECT count(*) FROM mobility.trip_stop_events
               WHERE lateness_seconds <> EXTRACT(EPOCH FROM observed_arrival - scheduled_arrival)::int
                  OR dwell_seconds   <> EXTRACT(EPOCH FROM observed_departure - observed_arrival)::int""").fetchone()[0]
        self.assertEqual(mismatches, 0)
        with self.assertRaises(psycopg.errors.GeneratedAlways):
            with self.conn.transaction():
                self.conn.execute("UPDATE mobility.trip_stop_events SET lateness_seconds = 0")

    def test_vehicle_cannot_run_overlapping_trips(self):
        with self.assertRaises(psycopg.errors.ExclusionViolation):
            with self.conn.transaction():
                self.conn.execute(
                    """INSERT INTO mobility.trips (trip_id, route_id, direction, vehicle_id, service_date,
                                                   scheduled_departure, scheduled_arrival, source_id)
                       SELECT 'T-OVERLAP', route_id, direction, vehicle_id, service_date,
                              scheduled_departure + interval '1 minute', scheduled_arrival, source_id
                       FROM mobility.trips WHERE trip_id = 'T20261002-8-0-0800'""")

    def test_stops_adapter_real_file_rules(self):
        base = {"정류장명": "테스트정류장", "위도": "37.3900", "경도": "126.6400", "정보수집일": "2026-11-09",
                "도시코드": "23", "도시명": "인천광역시"}
        rows = [
            {**base, "정류장번호": "ICB_TEST_1", "모바일단축번호": "99001", "관리도시명": "인천BIS"},
            {**base, "정류장번호": "GGB_TEST_1", "모바일단축번호": "99001", "관리도시명": "경기BIS"},
            {**base, "정류장번호": "ICB_TEST_2", "모바일단축번호": "99002", "관리도시명": "인천BIS",
             "위도": "37.4563", "경도": "126.7052"},                       # Incheon mainland
            {**base, "정류장번호": "ICB_TEST_3", "모바일단축번호": "99003", "관리도시명": "인천BIS",
             "위도": "126.6400", "경도": "37.3900"},                       # lat/lon swapped
        ]
        summary = StopsAdapter(self.conn, actor="TEST").ingest(rows)
        self.assertEqual((summary.loaded, summary.quarantined), (1, 3))
        reasons = sorted(r[0] for r in self.conn.execute(
            "SELECT reason_code FROM core.quarantine_records WHERE batch_id = %s", (summary.batch_id,)))
        self.assertEqual(reasons, ["COORDINATE_SWAPPED", "DUPLICATE_AUTHORITY", "OUT_OF_BOUNDS"])
        name = self.conn.execute("SELECT stop_name_ko FROM mobility.stops WHERE stop_id = 'ICB_TEST_1'").fetchone()[0]
        self.assertEqual(name, "테스트정류장")   # Korean text survives the round trip

    def test_observation_adapter(self):
        good = {"vehicle_id": "VEH-AV1-01", "lon": 126.6445, "lat": 37.3885, "speed_kmh": 14.0,
                "observed_at": "2026-10-03T09:00:00+09:00"}
        rows = [good,
                {**good, "vehicle_id": "NO-SUCH-VEHICLE"},
                {**good, "lon": 126.9780, "lat": 37.5665, "observed_at": "2026-10-03T09:00:10+09:00"},
                good]
        summary = VehicleObservationAdapter(self.conn, actor="TEST").ingest(rows)
        self.assertEqual((summary.loaded, summary.quarantined, summary.duplicates), (1, 2, 1))
        reasons = sorted(r[0] for r in self.conn.execute(
            "SELECT reason_code FROM core.quarantine_records WHERE batch_id = %s", (summary.batch_id,)))
        self.assertEqual(reasons, ["FK_VIOLATION", "OUT_OF_BOUNDS"])

    def test_candidate_stop_search_uses_gist_index(self):
        self.conn.execute("SET LOCAL enable_seqscan = off")
        plan = "\n".join(r[0] for r in self.conn.execute(
            """EXPLAIN SELECT s.stop_id FROM mobility.stops s
               WHERE ST_DWithin(s.geom::geography,
                     ST_SetSRID(ST_MakePoint(126.6445, 37.3885), 4326)::geography, 400)"""))
        self.assertIn("idx_stops_geog", plan)


@needs_db
class BuildingDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(settings.conninfo())
        self.conn.execute("SELECT 1")  # open the transaction; everything below is rolled back

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def test_relocation_closes_old_assignment_and_opens_new(self):
        at = "2026-10-02T11:00:00+09:00"
        self.conn.execute("SELECT building.relocate_device('DEV-GT-TMP-01', 'SP-GT-F12-R2', %s, 'TEST')", (at,))
        rows = self.conn.execute(
            """SELECT space_id, unassigned_at IS NULL FROM building.device_space_assignments
               WHERE device_id = 'DEV-GT-TMP-01' ORDER BY assigned_at""").fetchall()
        self.assertEqual(rows, [("SP-GT-F01-R1", False), ("SP-GT-F12-R2", True)])
        audit = self.conn.execute(
            """SELECT payload_snapshot->>'from_space', payload_snapshot->>'to_space' FROM core.event_audit_log
               WHERE table_name = 'device_space_assignments' AND entity_id = 'DEV-GT-TMP-01'""").fetchall()
        self.assertEqual(audit, [("SP-GT-F01-R1", "SP-GT-F12-R2")])
        # moving back in time, or to the space it is already in, is rejected
        for space, when in (("SP-GT-F03-R1", "2026-10-02T10:59:00+09:00"), ("SP-GT-F12-R2", "2026-10-02T12:00:00+09:00")):
            with self.assertRaises(psycopg.errors.CheckViolation):
                with self.conn.transaction():
                    self.conn.execute("SELECT building.relocate_device('DEV-GT-TMP-01', %s, %s)", (space, when))

    def test_device_cannot_be_in_two_spaces_at_once(self):
        with self.assertRaises(psycopg.errors.ExclusionViolation):
            with self.conn.transaction():
                self.conn.execute(
                    """INSERT INTO building.device_space_assignments (device_id, space_id, assigned_at)
                       VALUES ('DEV-GT-TMP-01', 'SP-GT-F12-R2', '2026-10-01T00:00:00+09:00')""")

    def test_room_cannot_have_parent_in_another_building(self):
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            with self.conn.transaction():
                self.conn.execute(
                    """INSERT INTO building.spaces (space_id, building_id, parent_space_id, space_type,
                                                    space_name, floor_number)
                       VALUES ('SP-TEST', 'BLD_G_TOWER', 'SP-PT-F10', 'ROOM', 'wrong parent', 10)""")

    def test_telemetry_adapter_fault_and_range_handling(self):
        good = {"device_id": "DEV-GT-TMP-01", "metric": "TEMPERATURE_C", "value": 22.5,
                "observed_at": "2026-10-03T09:00:00+09:00"}
        rows = [good,
                {**good, "value": "-", "observed_at": "2026-10-03T09:02:00+09:00"},          # sensor fault marker
                {**good, "value": 480.0, "observed_at": "2026-10-03T09:04:00+09:00"},        # impossible value
                {**good, "device_id": "DEV-NOPE", "observed_at": "2026-10-03T09:06:00+09:00"},
                good]
        summary = BuildingTelemetryAdapter(self.conn, actor="TEST").ingest(rows)
        self.assertEqual((summary.loaded, summary.quarantined, summary.duplicates), (2, 2, 1))
        reasons = sorted(r[0] for r in self.conn.execute(
            "SELECT reason_code FROM core.quarantine_records WHERE batch_id = %s", (summary.batch_id,)))
        self.assertEqual(reasons, ["FAULT_READING", "FK_VIOLATION"])
        flags = self.conn.execute(
            """SELECT value::float8, quality_flag FROM building.telemetry
               WHERE device_id = 'DEV-GT-TMP-01' AND observed_at >= '2026-10-03T00:00:00+09:00'
               ORDER BY observed_at""").fetchall()
        self.assertEqual(flags, [(22.5, "GOOD"), (480.0, "SUSPECT")])


if __name__ == "__main__":
    unittest.main()
