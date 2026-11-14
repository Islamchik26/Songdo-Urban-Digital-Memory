"""DB3 Tourism adapters: attractions (with accessibility metadata) and hourly visit samples."""
from __future__ import annotations

from typing import Any

import psycopg

from adapters.base_adapter import VALID, BaseAdapter, ValidationResult, parse_timestamp


def _as_bool(value: Any) -> bool | None:
    """Open-data files write booleans as Y/N, 가능/불가, true/false..."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("y", "yes", "true", "1", "가능", "있음", "유"):
        return True
    if text in ("n", "no", "false", "0", "불가", "없음", "무"):
        return False
    return None


class AttractionsAdapter(BaseAdapter):
    source_id = "SRC_TOU_SYN"
    domain_schema = "tourism"
    target_table = "attractions"
    required_fields = ("attraction_id", "attraction_name_ko", "category",
                       "is_wheelchair_accessible", "has_parking")

    def validate(self, record: dict[str, Any]) -> ValidationResult:
        base = super().validate(record)
        if not base.ok:
            return base
        for field in ("is_wheelchair_accessible", "has_parking"):
            if _as_bool(record[field]) is None:
                return ValidationResult(False, "SCHEMA_VIOLATION", f"{field} is not a yes/no value: {record[field]!r}")
        return VALID

    def load(self, cur: psycopg.Cursor, record: dict[str, Any]) -> tuple[str, str]:
        has_parking = _as_bool(record["has_parking"])
        cur.execute(
            """INSERT INTO tourism.attractions
                   (attraction_id, attraction_name_ko, attraction_name_en, category, road_address,
                    is_wheelchair_accessible, has_parking, parking_spaces, admission_fee_krw, geom, source_id)
               VALUES (%(id)s, %(ko)s, %(en)s, %(category)s, %(address)s, %(wheelchair)s, %(parking)s,
                       %(spaces)s, %(fee)s, ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326), %(source)s)
               ON CONFLICT (attraction_id) DO UPDATE
                   SET attraction_name_ko = EXCLUDED.attraction_name_ko,
                       attraction_name_en = EXCLUDED.attraction_name_en, category = EXCLUDED.category,
                       road_address = EXCLUDED.road_address,
                       is_wheelchair_accessible = EXCLUDED.is_wheelchair_accessible,
                       has_parking = EXCLUDED.has_parking, parking_spaces = EXCLUDED.parking_spaces,
                       admission_fee_krw = EXCLUDED.admission_fee_krw, geom = EXCLUDED.geom
               RETURNING attraction_id, (xmax = 0) AS inserted""",
            {
                "id": record["attraction_id"],
                "ko": record["attraction_name_ko"],
                "en": record.get("attraction_name_en"),
                "category": record["category"],
                "address": record.get("road_address"),
                "wheelchair": _as_bool(record["is_wheelchair_accessible"]),
                "parking": has_parking,
                "spaces": record.get("parking_spaces") if has_parking else None,
                "fee": record.get("admission_fee_krw") or 0,
                "lon": float(record["lon"]),
                "lat": float(record["lat"]),
                "source": self.source_id,
            },
        )
        attraction_id, inserted = cur.fetchone()
        return attraction_id, "INSERT" if inserted else "UPDATE"


class VisitSamplesAdapter(BaseAdapter):
    """Hourly visitor counts from a counting sensor: {attraction_id, hour_start, visitor_count}."""
    source_id = "SRC_TOU_SYN"
    domain_schema = "tourism"
    target_table = "sample_visits"
    required_fields = ("attraction_id", "hour_start")
    lon_field = None
    lat_field = None

    def validate(self, record: dict[str, Any]) -> ValidationResult:
        base = super().validate(record)
        if not base.ok:
            return base
        hour = parse_timestamp(record["hour_start"])
        if hour is None:
            return ValidationResult(False, "SCHEMA_VIOLATION", "hour_start must be ISO-8601 with a timezone offset")
        if hour.minute or hour.second or hour.microsecond:
            return ValidationResult(False, "SCHEMA_VIOLATION", f"hour_start is not on the hour: {record['hour_start']}")
        count = record.get("visitor_count")
        if isinstance(count, bool) or not isinstance(count, int):
            return ValidationResult(False, "FAULT_READING", f"visitor_count is not a whole number: {count!r}")
        if count < 0:
            return ValidationResult(False, "FAULT_READING", f"visitor_count is negative: {count}")
        return VALID

    def observed_at(self, record: dict[str, Any], received_at):  # the hour the count describes
        return parse_timestamp(record["hour_start"]) or received_at

    def load(self, cur: psycopg.Cursor, record: dict[str, Any]) -> tuple[str, str]:
        cur.execute(
            """INSERT INTO tourism.sample_visits (attraction_id, hour_start, visitor_count, source_id, received_at)
               VALUES (%(id)s, %(hour)s, %(count)s, %(source)s, clock_timestamp())
               ON CONFLICT (attraction_id, hour_start) DO UPDATE
                   SET visitor_count = EXCLUDED.visitor_count, received_at = EXCLUDED.received_at
               RETURNING (xmax = 0) AS inserted""",
            {"id": record["attraction_id"], "hour": parse_timestamp(record["hour_start"]),
             "count": record["visitor_count"], "source": self.source_id},
        )
        (inserted,) = cur.fetchone()
        return f"{record['attraction_id']}@{record['hour_start']}"[:64], "INSERT" if inserted else "UPDATE"
