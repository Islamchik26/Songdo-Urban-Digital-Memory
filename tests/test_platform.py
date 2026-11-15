"""Platform test suite.   python -m unittest tests/test_platform.py

Pure tests always run. Database tests are skipped (not failed) when PostgreSQL
is unreachable. Database tests run inside a transaction that is rolled back, so
they leave no rows behind.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from adapters.base_adapter import calculate_payload_hash, validate_coordinate  # noqa: E402
from adapters.building_adapter import BuildingTelemetryAdapter  # noqa: E402
from adapters.core_adapter import PlacesAdapter  # noqa: E402
from adapters.environment_adapter import (  # noqa: E402
    AirMeasurementsAdapter, AirStationsAdapter, explode_airkorea_item, parse_airkorea_time)
from adapters.mobility_adapter import StopsAdapter, VehicleObservationAdapter  # noqa: E402
from adapters.safety_adapter import IncidentsAdapter, IncidentTransitionsAdapter  # noqa: E402
from adapters.tourism_adapter import AttractionsAdapter, VisitSamplesAdapter  # noqa: E402
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


class AirKoreaParsingTests(unittest.TestCase):
    def test_midnight_is_written_as_24_00(self):
        self.assertEqual(parse_airkorea_time("2026-11-14 24:00").isoformat(), "2026-11-15T00:00:00+09:00")
        self.assertEqual(parse_airkorea_time("2026-11-15 18:00").isoformat(), "2026-11-15T18:00:00+09:00")
        self.assertIsNone(parse_airkorea_time("yesterday"))
        self.assertIsNone(parse_airkorea_time("2026-11-15T18:00:00"))       # ISO without offset is ambiguous

    def test_one_api_row_becomes_six_readings(self):
        item = {"dataTime": "2026-11-15 18:00", "pm10Value": "41", "pm25Value": "-", "pm25Flag": "통신장애",
                "o3Value": "0.059", "no2Value": "0.013", "coValue": "0.5", "so2Value": "0.004"}
        records = explode_airkorea_item(item, "송도")
        self.assertEqual({r["type_code"] for r in records}, {"PM10", "PM25", "O3", "NO2", "CO", "SO2"})
        pm25 = next(r for r in records if r["type_code"] == "PM25")
        self.assertEqual((pm25["value"], pm25["flag"]), ("-", "통신장애"))


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
    def test_tourism_read_endpoints(self):
        everything = self.client.get("/api/v1/tourism/attractions").json()
        accessible = self.client.get("/api/v1/tourism/attractions", params={"wheelchair_accessible": True}).json()
        self.assertTrue(0 < len(accessible) < len(everything))
        self.assertTrue(all(a["is_wheelchair_accessible"] for a in accessible))

        def open_ids(at):
            rows = self.client.get("/api/v1/tourism/attractions/open", params={"at": at, "radius_m": 3000}).json()
            return {r["attraction_id"] for r in rows}
        friday, monday = open_ids("2026-10-02T14:00:00+09:00"), open_ids("2026-10-05T14:00:00+09:00")
        self.assertIn("ATT_TRIBOWL", friday)
        self.assertNotIn("ATT_TRIBOWL", monday)                 # closed on Mondays
        self.assertNotIn("ATT_HANOK_VILLAGE", open_ids("2026-10-02T16:00:00+09:00"))   # between lunch and dinner
        # the same instant written in UTC gives the same answer
        self.assertEqual(friday, open_ids("2026-10-02T05:00:00+00:00"))

        events = self.client.get("/api/v1/tourism/events").json()
        self.assertNotIn("EVT_PARK_MARKET", {e["event_id"] for e in events})          # cancelled
        r = self.client.get("/api/v1/tourism/events", params={"from_date": "2026-10-05", "to_date": "2026-10-01"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.client.get("/api/v1/tourism/attractions/NOPE/visits").status_code, 404)

    @needs_db
    def test_scenario_bad_date_range_is_422_not_500(self):
        r = self.client.post("/api/v1/scenarios/DB3-2/run",
                             json={"params": {"from_date": "2026-10-05", "to_date": "2026-10-01"}})
        self.assertEqual(r.status_code, 422)
        r = self.client.post("/api/v1/scenarios/DB3-4/run", json={"params": {"at_time": "25:99"}})
        self.assertEqual(r.status_code, 422)

    @needs_db
    def test_environment_read_endpoints(self):
        stations = self.client.get("/api/v1/environment/stations").json()
        self.assertEqual({s["station_name_ko"] for s in stations}, {"송도", "아암", "동막", "동춘", "인천 신항"})

        def pm25(as_of):
            body = self.client.get("/api/v1/environment/stations/송도/latest", params={"as_of": as_of}).json()
            return next(r for r in body["readings"] if r["type_code"] == "PM25")
        self.assertEqual(pm25("2026-10-02T09:00:00+09:00")["grade"], "NORMAL")
        self.assertEqual(pm25("2026-10-02T14:30:00+09:00")["grade"], "VERY_BAD")     # inside the episode

        exposure = self.client.get("/api/v1/environment/exposure",
                                   params={"lon": 126.6548, "lat": 37.3821, "as_of": "2026-10-02T09:00:00+09:00"}).json()
        self.assertEqual(exposure["nearest_station"]["station_name_ko"], "송도")
        self.assertLess(exposure["nearest_station"]["distance_m"], 100)
        self.assertEqual(len(exposure["readings"]), 6)

        self.assertEqual(self.client.get("/api/v1/environment/stations/없는곳/latest").status_code, 404)
        r = self.client.get("/api/v1/environment/stations/송도/measurements",
                            params={"from": "2026-10-03T00:00:00+09:00", "to": "2026-10-02T00:00:00+09:00"})
        self.assertEqual(r.status_code, 422)

    @needs_db
    def test_alert_episodes_from_seed(self):
        alerts = self.client.get("/api/v1/environment/alerts").json()
        very_bad = {(a["station_name_ko"], a["status"]) for a in alerts
                    if a["type_code"] == "PM25" and a["severity"] == "VERY_BAD"
                    and a["started_hour"].startswith("2026-10-02")}
        # 송도's afternoon episode cleared; 아암's evening episode was still on at midnight.
        # (아암 turns RESOLVED once later clean data is ingested, e.g. by scripts/ingest_airkorea.py.)
        self.assertIn(("송도", "RESOLVED"), very_bad)
        self.assertTrue({("아암", "ACTIVE"), ("아암", "RESOLVED")} & very_bad)
        songdo = [a for a in alerts if a["station_name_ko"] == "송도" and a["severity"] == "VERY_BAD"]
        self.assertEqual(len(songdo), 1)                      # one episode, not one alert per hour
        self.assertGreater(songdo[0]["peak_value"], 75)

    @needs_db
    def test_incident_state_at_time_t(self):
        def active(as_of):
            rows = self.client.get("/api/v1/safety/incidents/active", params={"as_of": as_of}).json()
            return {r["incident_id"][-4:]: r["status"] for r in rows if r["incident_id"].startswith("INC-20261002-")}
        self.assertEqual(active("2026-10-02T08:10:00+09:00"), {"0001": "DISPATCHED"})
        self.assertEqual(active("2026-10-02T08:20:00+09:00"), {"0001": "ON_SCENE"})
        self.assertEqual(active("2026-10-02T11:00:00+09:00"), {})                        # 0001 closed, nothing else yet
        self.assertEqual(active("2026-10-02T18:30:00+09:00"), {"0003": "ON_SCENE", "0004": "REPORTED"})
        self.assertEqual(active("2026-10-02T23:00:00+09:00"),
                         {"0003": "ON_SCENE", "0004": "REPORTED", "0005": "DISPATCHED"})

        detail = self.client.get("/api/v1/safety/incidents/INC-20261002-0001").json()
        self.assertEqual([h["to_status"] for h in detail["history"]],
                         ["REPORTED", "DISPATCHED", "ON_SCENE", "RESOLVED", "CLOSED"])
        self.assertEqual(self.client.get("/api/v1/safety/incidents/NOPE").status_code, 404)

    @needs_db
    def test_hazard_zones_geojson(self):
        t = "2026-10-02T18:30:00+09:00"
        zones = self.client.get("/api/v1/safety/hazard-zones", params={"as_of": t}).json()
        self.assertEqual(zones["type"], "FeatureCollection")
        by_id = {f["properties"]["incident_id"]: f for f in zones["features"]}
        self.assertEqual(set(by_id), {"INC-20261002-0003", "INC-20261002-0004"})
        fire = by_id["INC-20261002-0003"]
        self.assertEqual(fire["geometry"]["type"], "Polygon")
        # a point 100 m from the fire is blocked; a point across town is not
        inside = self.client.get("/api/v1/safety/hazard-zones", params={
            "as_of": t, "lon": fire["properties"]["lon"] + 0.001, "lat": fire["properties"]["lat"]}).json()
        self.assertEqual(inside["point"]["blocked_by"], ["INC-20261002-0003"])
        outside = self.client.get("/api/v1/safety/hazard-zones",
                                  params={"as_of": t, "lon": 126.6328, "lat": 37.3751}).json()
        self.assertEqual(outside["point"]["blocked_by"], [])
        self.assertEqual(self.client.get("/api/v1/safety/hazard-zones", params={"lon": 126.64}).status_code, 422)

    def test_safety_write_endpoints_validate_input(self):
        headers = {"X-API-Token": settings.ingest_api_token}
        report = {"incident_id": "INC-X", "type_code": "FIRE", "title": "t", "severity": "HIGH",
                  "lon": 126.64, "lat": 37.39, "reported_at": "2026-10-03T09:00:00+09:00"}
        self.assertEqual(self.client.post("/api/v1/safety/incidents", json=report).status_code, 401)
        for bad in ({**report, "severity": "URGENT"}, {**report, "type_code": "ALIENS"},
                    {**report, "reported_at": "2026-10-03 09:00"}):
            r = self.client.post("/api/v1/safety/incidents", json=bad, headers=headers)
            self.assertEqual(r.status_code, 422, bad)
        r = self.client.post("/api/v1/safety/incidents/INC-X/transition", json={"to_status": "REPORTED"},
                             headers=headers)
        self.assertEqual(r.status_code, 422)

    @needs_db
    def test_cross_domain_endpoints(self):
        near = self.client.get("/api/v1/cross/attractions-near-stops",
                               params={"stop_number": "38367", "on_date": "2026-10-02"}).json()
        self.assertTrue(near)
        self.assertTrue(all(r["distance_meters"] <= 500 for r in near))
        self.assertIn("EVT_SMARTCITY_EXPO", {r["event_id"] for r in near})
        self.assertNotIn("EVT_PARK_MARKET", {r["event_id"] for r in near})          # cancelled

        ctx = self.client.get("/api/v1/cross/building-context/BLD_G_TOWER").json()
        self.assertEqual({r["nearest_env_station"] for r in ctx}, {"아암"})
        self.assertEqual({r["pollutant_type"] for r in ctx}, {"PM10", "PM25"})
        self.assertEqual(self.client.get("/api/v1/cross/building-context/NOPE").status_code, 404)

        impact = self.client.get("/api/v1/cross/incident-impact").json()
        self.assertTrue(impact)
        self.assertTrue(all(r["current_status"] in ("REPORTED", "DISPATCHED", "ON_SCENE") for r in impact))
        self.assertTrue(all(r["distance_to_stop_meters"] <= 600 for r in impact))

        cards = self.client.get("/api/v1/cross/place-context").json()
        self.assertEqual(len(cards), 15)
        self.assertTrue(all(c["nearest_stop_name"] and c["nearest_fire_unit"] for c in cards))
        self.assertEqual(self.client.post("/api/v1/cross/refresh").status_code, 401)
        fresh = self.client.get("/api/v1/cross/freshness").json()
        self.assertEqual({f["view_name"] for f in fresh},
                         {"mv_facility_response_stats", "mv_stop_hourly_service", "mv_place_context"})


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


@needs_db
class TourismDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(settings.conninfo())
        self.conn.execute("SELECT 1")  # open the transaction; everything below is rolled back

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def _rejects(self, error, sql):
        with self.assertRaises(error):
            with self.conn.transaction():
                self.conn.execute(sql)

    def test_schedule_constraints(self):
        # overlapping open intervals on the same day
        self._rejects(psycopg.errors.ExclusionViolation,
                      """INSERT INTO tourism.operating_hours (attraction_id, day_of_week, opens_at, closes_at)
                         VALUES ('ATT_HANOK_VILLAGE', 3, '14:00', '18:00')""")
        # event that ends before it starts
        self._rejects(psycopg.errors.CheckViolation,
                      """INSERT INTO tourism.events (event_id, attraction_id, event_name_ko, event_type, start_date,
                             end_date, daily_start_time, daily_end_time, expected_attendance, source_id)
                         VALUES ('EVT_BAD', 'ATT_TRIBOWL', 'x', 'CONCERT', '2026-10-05', '2026-10-04',
                                 '10:00', '12:00', 10, 'SRC_TOU_SYN')""")
        # hourly sample that is not on the hour
        self._rejects(psycopg.errors.CheckViolation,
                      """INSERT INTO tourism.sample_visits (attraction_id, hour_start, visitor_count, source_id, received_at)
                         VALUES ('ATT_TRIBOWL', '2026-10-03T10:30:00+09:00', 5, 'SRC_TOU_SYN', now())""")

    def test_monthly_totals_and_samples_stay_separate(self):
        month_rows, sample_rows, coverage = self.conn.execute(
            """SELECT (SELECT count(*) FROM tourism.aggregate_visits WHERE year_month = '2026-09-01'),
                      (SELECT count(*) FROM tourism.sample_visits
                       WHERE hour_start >= '2026-09-01+09' AND hour_start < '2026-10-01+09'),
                      (SELECT sum(visitor_count) FROM tourism.sample_visits
                       WHERE hour_start >= '2026-09-01+09' AND hour_start < '2026-10-01+09')::float
                      / (SELECT sum(visitor_count) FROM tourism.aggregate_visits WHERE year_month = '2026-09-01')"""
        ).fetchone()
        self.assertEqual(sample_rows, month_rows * 30 * 24)     # one row per attraction per hour
        self.assertTrue(0.2 < coverage < 0.5)                    # the sample is a fraction of the total

    def test_tourism_adapters(self):
        base = {"attraction_id": "ATT_TEST", "attraction_name_ko": "테스트 전시관", "category": "CULTURE",
                "is_wheelchair_accessible": "가능", "has_parking": "N", "lon": 126.64, "lat": 37.39}
        summary = AttractionsAdapter(self.conn, actor="TEST").ingest([
            base,
            {**base, "attraction_id": "ATT_TEST_FAR", "lon": 126.978, "lat": 37.5665},
            {**base, "attraction_id": "ATT_TEST_BOOL", "has_parking": "maybe"},
        ])
        self.assertEqual((summary.loaded, summary.quarantined), (1, 2))
        self.assertEqual(self.conn.execute(
            "SELECT is_wheelchair_accessible, has_parking FROM tourism.attractions WHERE attraction_id = 'ATT_TEST'"
        ).fetchone(), (True, False))

        sample = {"attraction_id": "ATT_TRIBOWL", "hour_start": "2026-10-03T10:00:00+09:00", "visitor_count": 12}
        summary = VisitSamplesAdapter(self.conn, actor="TEST").ingest([
            sample,
            {**sample, "hour_start": "2026-10-03T11:00:00+09:00", "visitor_count": -4},
            {**sample, "hour_start": "2026-10-03T12:15:00+09:00"},
            {**sample, "attraction_id": "ATT_NOPE"},
        ])
        self.assertEqual((summary.loaded, summary.quarantined), (1, 3))
        reasons = sorted(r[0] for r in self.conn.execute(
            "SELECT reason_code FROM core.quarantine_records WHERE batch_id = %s", (summary.batch_id,)))
        self.assertEqual(reasons, ["FAULT_READING", "FK_VIOLATION", "SCHEMA_VIOLATION"])


@needs_db
class EnvironmentDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(settings.conninfo())
        self.conn.execute("SELECT 1")  # open the transaction; everything below is rolled back

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def _reasons(self, batch_id):
        return sorted(r[0] for r in self.conn.execute(
            "SELECT reason_code FROM core.quarantine_records WHERE batch_id = %s", (batch_id,)))

    def test_station_adapter_handles_the_dmx_dmy_trap(self):
        base = {"stationName": "테스트측정소", "addr": "인천 연수구", "mangName": "도시대기",
                "item": "PM10, PM2.5", "year": "2024"}
        summary = AirStationsAdapter(self.conn, actor="TEST").ingest([
            {**base, "dmX": "37.3900", "dmY": "126.6400"},                                   # dmX is latitude
            {**base, "stationName": "뒤바뀐측정소", "dmX": "126.6400", "dmY": "37.3900"},     # swapped
            {**base, "stationName": "서울측정소", "dmX": "37.5665", "dmY": "126.9780"},       # outside Songdo
        ])
        self.assertEqual((summary.loaded, summary.quarantined), (1, 2))
        self.assertEqual(self._reasons(summary.batch_id), ["COORDINATE_SWAPPED", "OUT_OF_BOUNDS"])
        lon, lat = self.conn.execute(
            "SELECT ST_X(geom), ST_Y(geom) FROM environment.stations WHERE station_id = 'AK_테스트측정소'").fetchone()
        self.assertEqual((lon, lat), (126.64, 37.39))

    def test_faults_are_quarantined_and_corrections_upserted(self):
        first = {"station_name": "송도", "type_code": "PM25", "value": "40", "data_time": "2026-10-05 09:00"}
        adapter = AirMeasurementsAdapter(self.conn, actor="TEST")
        summary = adapter.ingest([
            first,
            {**first, "value": "-", "flag": "통신장애", "data_time": "2026-10-05 10:00"},     # fault marker
            {**first, "value": "9999", "data_time": "2026-10-05 11:00"},                       # impossible
            {**first, "station_name": "없는측정소"},                                            # unknown station
            {**first, "data_time": "2026-10-05 24:00"},                                        # midnight
        ])
        self.assertEqual((summary.loaded, summary.quarantined), (3, 2))
        self.assertEqual(self._reasons(summary.batch_id), ["FAULT_READING", "FK_VIOLATION"])

        # the official value for 09:00 is revised later: the row is replaced, the ledger keeps both
        again = adapter.ingest([{**first, "value": "44"}, first])
        self.assertEqual((again.loaded, again.duplicates), (1, 1))
        rows = self.conn.execute(
            """SELECT observed_at AT TIME ZONE 'Asia/Seoul', measured_value::float8, quality_flag
               FROM environment.measurements m JOIN environment.stations s USING (station_id)
               WHERE s.station_name_ko = '송도' AND type_code = 'PM25'
                 AND observed_at >= '2026-10-05+09' AND observed_at < '2026-10-07+09'
               ORDER BY observed_at""").fetchall()
        self.assertEqual([(r[0].isoformat(), r[1], r[2]) for r in rows],
                         [("2026-10-05T09:00:00", 44.0, "GOOD"), ("2026-10-05T11:00:00", 9999.0, "SUSPECT"),
                          ("2026-10-06T00:00:00", 40.0, "GOOD")])
        actions = [r[0] for r in self.conn.execute(
            """SELECT action FROM core.event_audit_log
               WHERE table_name = 'measurements' AND actor = 'TEST'
                 AND payload_snapshot->>'data_time' = '2026-10-05 09:00' ORDER BY audit_id""")]
        self.assertEqual(actions, ["INSERT", "UPDATE"])

    def test_rollup_ignores_suspect_values_and_alert_lifecycle(self):
        hours = {"09:00": "30", "10:00": "90", "11:00": "95", "12:00": "20"}
        readings = [{"station_name": "동춘", "type_code": "PM25", "value": v, "data_time": f"2026-10-06 {h}"}
                    for h, v in hours.items()]
        readings.append({"station_name": "동춘", "type_code": "PM25", "value": "9999", "data_time": "2026-10-06 12:30"})
        AirMeasurementsAdapter(self.conn, actor="TEST").ingest(readings)

        def evaluate(until):
            self.conn.execute("SELECT environment.refresh_hourly_summaries('2026-10-06+09', %s)", (until,))
            self.conn.execute("SELECT environment.evaluate_alerts('2026-10-06+09', %s)", (until,))
            return self.conn.execute(
                """SELECT r.severity, a.status, a.peak_value::float8,
                          (a.last_exceeded_hour - a.started_hour) = INTERVAL '1 hour'
                   FROM environment.alerts a JOIN environment.alert_rules r USING (rule_id)
                   WHERE a.station_id = 'AK_동춘' AND a.started_hour >= '2026-10-06+09'
                   ORDER BY r.severity""").fetchall()

        # up to 11:59 the episode is running: one ACTIVE alert per rule, spanning 10:00 and 11:00
        self.assertEqual(evaluate("2026-10-06T12:00:00+09:00"),
                         [("BAD", "ACTIVE", 95.0, True), ("VERY_BAD", "ACTIVE", 95.0, True)])
        # the clean 12:00 hour resolves both; evaluating again must not create duplicates
        after = evaluate("2026-10-06T13:00:00+09:00")
        self.assertEqual(after, [("BAD", "RESOLVED", 95.0, True), ("VERY_BAD", "RESOLVED", 95.0, True)])
        self.assertEqual(evaluate("2026-10-06T13:00:00+09:00"), after)
        noon = self.conn.execute(
            """SELECT avg_value::float8, sample_count FROM environment.hourly_summaries
               WHERE station_id = 'AK_동춘' AND type_code = 'PM25'
                 AND hour_start = '2026-10-06T12:00:00+09:00'""").fetchone()
        self.assertEqual(noon, (20.0, 1))                     # the 9999 reading was SUSPECT and left out

    def test_real_airkorea_sample_goes_through_the_pipeline(self):
        sample = json.loads((Path(__file__).resolve().parent.parent / "fixtures" / "airkorea_sample.json")
                            .read_text(encoding="utf-8"))
        records = [r for name, items in sample["readings"].items() for item in items
                   for r in explode_airkorea_item(item, name)]
        faults = sum(1 for r in records if r["value"] in ("-", "", None))
        self.assertGreater(faults, 0)                                          # the sample contains a real fault

        stations = AirStationsAdapter(self.conn, actor="TEST").ingest(sample["stations"])
        result = AirMeasurementsAdapter(self.conn, actor="TEST").ingest(records)
        if stations.duplicates == 0:
            # fresh database: the Songdo subset loads, the rest of Incheon and the faults are quarantined
            self.assertEqual((stations.loaded, stations.quarantined), (5, len(sample["stations"]) - 5))
            self.assertEqual((result.loaded, result.quarantined), (len(records) - faults, faults))
        else:
            # real data was already ingested (scripts/ingest_airkorea.py): stations are all known,
            # and every reading is either a duplicate or an hour the live API has since revised
            self.assertEqual((stations.loaded, stations.quarantined), (0, 0))
            self.assertEqual(result.loaded + result.quarantined + result.duplicates, len(records))
            self.assertLessEqual(result.quarantined, faults)

@needs_db
class SafetyDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(settings.conninfo())
        self.conn.execute("SELECT 1")  # open the transaction; everything below is rolled back

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    REPORT = {"incident_id": "INC-TEST-1", "type_code": "TRAFFIC_ACCIDENT", "title": "테스트 사고",
              "severity": "HIGH", "lon": 126.6445, "lat": 37.3885, "reported_at": "2026-10-03T09:00:00+09:00"}

    def _step(self, to_status, at, facility=None):
        self.conn.execute("SELECT safety.transition_incident(%s, %s, %s, 'TEST', %s)",
                          ("INC-TEST-1", to_status, at, facility))

    def test_seeded_history_is_consistent(self):
        # every incident's current status equals the last row of its history
        mismatches = self.conn.execute(
            """SELECT count(*) FROM safety.incidents i
               JOIN LATERAL (SELECT to_status FROM safety.incident_status_history h
                             WHERE h.incident_id = i.incident_id
                             ORDER BY changed_at DESC, history_id DESC LIMIT 1) last ON TRUE
               WHERE last.to_status <> i.current_status""").fetchone()[0]
        self.assertEqual(mismatches, 0)
        total, with_history = self.conn.execute(
            """SELECT (SELECT count(*) FROM safety.incidents),
                      (SELECT count(DISTINCT incident_id) FROM safety.incident_status_history)""").fetchone()
        self.assertEqual(total, with_history)
        self.assertGreaterEqual(total, 1000)

    def test_full_lifecycle_through_the_state_machine(self):
        summary = IncidentsAdapter(self.conn, actor="TEST").ingest([
            self.REPORT,
            {**self.REPORT, "incident_id": "INC-TEST-FAR", "lon": 126.978, "lat": 37.5665},      # outside Songdo
            {**self.REPORT, "incident_id": "INC-TEST-SEV", "severity": "URGENT"},
        ])
        self.assertEqual((summary.loaded, summary.quarantined), (1, 2))
        radius, status = self.conn.execute(
            "SELECT restriction_radius_m, current_status FROM safety.incidents WHERE incident_id = 'INC-TEST-1'"
        ).fetchone()
        self.assertEqual((radius, status), (150, "REPORTED"))      # radius comes from the incident type

        self._step("DISPATCHED", "2026-10-03T09:02:00+09:00", "FAC_POLICE_SONGDO1")
        self._step("ON_SCENE", "2026-10-03T09:09:00+09:00")
        self._step("RESOLVED", "2026-10-03T09:40:00+09:00")
        self._step("CLOSED", "2026-10-03T10:00:00+09:00")
        history = self.conn.execute(
            """SELECT from_status, to_status, actor, facility_id FROM safety.incident_status_history
               WHERE incident_id = 'INC-TEST-1' ORDER BY changed_at""").fetchall()
        self.assertEqual(history, [
            (None, "REPORTED", "SYSTEM", None),
            ("REPORTED", "DISPATCHED", "TEST", "FAC_POLICE_SONGDO1"),
            ("DISPATCHED", "ON_SCENE", "TEST", None),
            ("ON_SCENE", "RESOLVED", "TEST", None),
            ("RESOLVED", "CLOSED", "TEST", None)])
        actions = [r[0] for r in self.conn.execute(
            """SELECT action FROM core.event_audit_log
               WHERE domain_schema = 'safety' AND entity_id = 'INC-TEST-1' AND actor = 'TEST'
               ORDER BY audit_id""")]
        self.assertEqual(actions, ["INSERT", "DISPATCH", "STATUS_CHANGE", "STATUS_CHANGE", "STATUS_CHANGE"])

    def test_illegal_transitions_are_rejected(self):
        IncidentsAdapter(self.conn, actor="TEST").ingest([self.REPORT])

        def rejected(sql, params=()):
            with self.assertRaises(psycopg.errors.CheckViolation):
                with self.conn.transaction():
                    self.conn.execute(sql, params)

        call = "SELECT safety.transition_incident('INC-TEST-1', %s, %s, 'TEST', %s)"
        rejected(call, ("RESOLVED", "2026-10-03T09:05:00+09:00", None))                 # skips two states
        rejected(call, ("DISPATCHED", "2026-10-03T09:05:00+09:00", None))               # no facility given
        rejected(call, ("DISPATCHED", "2026-10-03T08:00:00+09:00", "FAC_POLICE_SONGDO1"))  # before the report
        # bypassing the function with a plain UPDATE is stopped by the trigger
        rejected("UPDATE safety.incidents SET current_status = 'CLOSED' WHERE incident_id = 'INC-TEST-1'")
        # and the history itself cannot be rewritten
        with self.assertRaises(psycopg.errors.RestrictViolation):
            with self.conn.transaction():
                self.conn.execute("DELETE FROM safety.incident_status_history WHERE incident_id = 'INC-TEST-1'")
        self.assertEqual(self.conn.execute(
            "SELECT current_status FROM safety.incidents WHERE incident_id = 'INC-TEST-1'").fetchone()[0], "REPORTED")
        with self.assertRaises(psycopg.errors.NoDataFound):
            with self.conn.transaction():
                self.conn.execute(call.replace("INC-TEST-1", "INC-NOPE"), ("DISPATCHED", None, "FAC_POLICE_SONGDO1"))

    def test_transition_stream_adapter_quarantines_bad_steps(self):
        IncidentsAdapter(self.conn, actor="TEST").ingest([self.REPORT])
        step = {"incident_id": "INC-TEST-1", "to_status": "DISPATCHED", "changed_at": "2026-10-03T09:02:00+09:00",
                "facility_id": "FAC_POLICE_SONGDO1"}
        summary = IncidentTransitionsAdapter(self.conn, actor="TEST").ingest([
            step,
            {**step, "to_status": "CLOSED", "changed_at": "2026-10-03T09:03:00+09:00"},     # not allowed yet
            {**step, "to_status": "ON_SCENE", "changed_at": "2026-10-03T09:10:00+09:00"},
            {**step, "incident_id": "INC-NOPE"},
        ])
        self.assertEqual((summary.loaded, summary.quarantined), (2, 2))
        self.assertEqual(self.conn.execute(
            "SELECT current_status FROM safety.incidents WHERE incident_id = 'INC-TEST-1'").fetchone()[0], "ON_SCENE")

    def test_drills_are_scored_by_the_database(self):
        wrong = self.conn.execute(
            "SELECT count(*) FROM safety.response_drills WHERE passed <> (response_seconds <= target_seconds)"
        ).fetchone()[0]
        self.assertEqual(wrong, 0)
        passed, failed = self.conn.execute(
            "SELECT count(*) FILTER (WHERE passed), count(*) FILTER (WHERE NOT passed) FROM safety.response_drills"
        ).fetchone()
        self.assertGreater(passed, 0)
        self.assertGreater(failed, 0)


@needs_db
class CrossDomainDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(settings.conninfo())
        self.conn.execute("SELECT 1")  # open the transaction; everything below is rolled back

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def test_materialized_stats_match_the_live_query(self):
        live = self.conn.execute(
            """SELECT i.assigned_facility_id, count(*),
                      round(avg(extract(epoch FROM h.changed_at - i.reported_at)))::int
               FROM safety.incidents i
               JOIN safety.incident_status_history h ON h.incident_id = i.incident_id AND h.to_status = 'ON_SCENE'
               WHERE NOT i.is_drill AND i.reported_at < '2026-10-01+09'
               GROUP BY 1 ORDER BY 1""").fetchall()
        materialized = self.conn.execute(
            """SELECT facility_id, sum(incidents)::bigint,
                      round(sum(avg_response_s::numeric * incidents) / sum(incidents))::int
               FROM cross_domain.mv_facility_response_stats
               WHERE NOT is_drill AND month < '2026-10-01'
               GROUP BY 1 ORDER BY 1""").fetchall()
        self.assertEqual([r[:2] for r in live], [r[:2] for r in materialized])     # identical counts
        for (_, _, a), (_, _, b) in zip(live, materialized):
            self.assertLessEqual(abs(a - b), 1)                                    # averages agree to the second

    def test_refresh_is_logged_and_picks_up_new_data(self):
        before = self.conn.execute(
            "SELECT count(*) FROM cross_domain.mv_place_context").fetchone()[0]
        self.conn.execute(
            """INSERT INTO core.places (place_id, name_ko, category, geom, provenance)
               VALUES ('PLC_TEST', '테스트 장소', 'TEST', ST_SetSRID(ST_MakePoint(126.64, 37.39), 4326), 'SRC_CORE_01')""")
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM cross_domain.mv_place_context").fetchone()[0], before)   # stale until refreshed
        refreshed = self.conn.execute("SELECT view_name, row_count FROM cross_domain.refresh_materialized_views()").fetchall()
        self.assertEqual(dict(refreshed)["mv_place_context"], before + 1)
        logged = self.conn.execute(
            """SELECT count(*) FROM cross_domain.refresh_log
               WHERE refreshed_at >= transaction_timestamp()""").fetchone()[0]
        self.assertEqual(logged, 3)

    def test_incident_view_follows_the_state_machine(self):
        in_view = "SELECT count(*) FROM cross_domain.v_incidents_mobility_environmental_impact WHERE incident_id = %s"
        self.assertGreater(self.conn.execute(in_view, ("INC-20261002-0003",)).fetchone()[0], 0)
        self.assertEqual(self.conn.execute(in_view, ("INC-20261002-0001",)).fetchone()[0], 0)   # closed
        self.conn.execute(
            "SELECT safety.transition_incident('INC-20261002-0003', 'RESOLVED', '2026-10-02T19:30:00+09:00', 'TEST')")
        self.assertEqual(self.conn.execute(in_view, ("INC-20261002-0003",)).fetchone()[0], 0)   # gone at once

    def test_views_use_spatial_indexes(self):
        self.conn.execute("SET LOCAL enable_seqscan = off")
        plan = "\n".join(r[0] for r in self.conn.execute(
            "EXPLAIN SELECT * FROM cross_domain.v_attractions_events_near_stops"))
        self.assertRegex(plan, r"idx_(stops|attractions)_geog")


if __name__ == "__main__":
    unittest.main()
