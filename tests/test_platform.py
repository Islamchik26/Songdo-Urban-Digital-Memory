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
from adapters.core_adapter import PlacesAdapter  # noqa: E402
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
                self.assertTrue(s.sql and s.sql.strip().upper().startswith("SELECT"), s.id)


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


if __name__ == "__main__":
    unittest.main()
