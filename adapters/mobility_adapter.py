"""DB1 Mobility adapters: bus stops (official CSV) and vehicle GPS telemetry."""
from __future__ import annotations

from typing import Any

import psycopg

from adapters.base_adapter import VALID, BaseAdapter, ValidationResult, parse_timestamp


class StopsAdapter(BaseAdapter):
    """Rows of 국토교통부_전국 버스정류장 위치정보, keyed by their original Korean headers.

    The same physical Incheon stop is published twice: once by 인천BIS and once by
    경기BIS (Gyeonggi buses also serve it). 인천BIS is the authority for Incheon,
    so rows from any other BIS are quarantined as DUPLICATE_AUTHORITY.
    """
    source_id = "SRC_MOB_03"
    domain_schema = "mobility"
    target_table = "stops"
    required_fields = ("정류장번호", "정류장명", "모바일단축번호", "관리도시명")
    lon_field = "경도"
    lat_field = "위도"
    authority = "인천BIS"

    def validate(self, record: dict[str, Any]) -> ValidationResult:
        base = super().validate(record)
        if not base.ok:
            return base
        if record["관리도시명"] != self.authority:
            return ValidationResult(
                False, "DUPLICATE_AUTHORITY",
                f"published by {record['관리도시명']}; {self.authority} is the authority for Incheon stops")
        return VALID

    def load(self, cur: psycopg.Cursor, record: dict[str, Any]) -> tuple[str, str]:
        cur.execute(
            """INSERT INTO mobility.stops
                   (stop_id, stop_number, stop_name_ko, geom, managing_bis, collected_on, source_id)
               VALUES (%(id)s, %(number)s, %(name)s,
                       ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326), %(bis)s, %(collected)s, %(source)s)
               ON CONFLICT (stop_id) DO UPDATE
                   SET stop_number = EXCLUDED.stop_number, stop_name_ko = EXCLUDED.stop_name_ko,
                       geom = EXCLUDED.geom, managing_bis = EXCLUDED.managing_bis,
                       collected_on = EXCLUDED.collected_on, source_id = EXCLUDED.source_id
               RETURNING stop_id, (xmax = 0) AS inserted""",
            {
                "id": record["정류장번호"].strip(),
                "number": record["모바일단축번호"].strip(),
                "name": record["정류장명"].strip(),
                "lon": float(record["경도"]),
                "lat": float(record["위도"]),
                "bis": record["관리도시명"].strip(),
                "collected": record.get("정보수집일") or None,
                "source": self.source_id,
            },
        )
        stop_id, inserted = cur.fetchone()
        return stop_id, "INSERT" if inserted else "UPDATE"


class VehicleObservationAdapter(BaseAdapter):
    """High-frequency GPS telemetry from buses / autonomous shuttles (Scenario DB1-5)."""
    source_id = "SRC_MOB_SYN"
    domain_schema = "mobility"
    target_table = "vehicle_observations"
    required_fields = ("vehicle_id", "observed_at", "speed_kmh")

    def validate(self, record: dict[str, Any]) -> ValidationResult:
        base = super().validate(record)
        if not base.ok:
            return base
        if parse_timestamp(record["observed_at"]) is None:
            return ValidationResult(False, "SCHEMA_VIOLATION",
                                    "observed_at must be ISO-8601 with a timezone offset")
        return VALID

    def load(self, cur: psycopg.Cursor, record: dict[str, Any]) -> tuple[str, str]:
        # A corrected packet for the same (vehicle, instant) replaces the earlier values;
        # the audit ledger keeps both versions.
        cur.execute(
            """INSERT INTO mobility.vehicle_observations
                   (vehicle_id, trip_id, geom, speed_kmh, heading_deg, battery_state_of_charge_pct,
                    observed_at, received_at, source_id)
               VALUES (%(vehicle_id)s, %(trip_id)s, ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326),
                       %(speed)s, %(heading)s, %(battery)s, %(observed_at)s, clock_timestamp(), %(source)s)
               ON CONFLICT (vehicle_id, observed_at) DO UPDATE
                   SET trip_id = EXCLUDED.trip_id, geom = EXCLUDED.geom, speed_kmh = EXCLUDED.speed_kmh,
                       heading_deg = EXCLUDED.heading_deg,
                       battery_state_of_charge_pct = EXCLUDED.battery_state_of_charge_pct,
                       received_at = EXCLUDED.received_at
               RETURNING observation_id, (xmax = 0) AS inserted""",
            {
                "vehicle_id": record["vehicle_id"],
                "trip_id": record.get("trip_id"),
                "lon": float(record["lon"]),
                "lat": float(record["lat"]),
                "speed": record["speed_kmh"],
                "heading": record.get("heading_deg"),
                "battery": record.get("battery_state_of_charge_pct"),
                "observed_at": parse_timestamp(record["observed_at"]),
                "source": self.source_id,
            },
        )
        observation_id, inserted = cur.fetchone()
        return str(observation_id), "INSERT" if inserted else "UPDATE"
