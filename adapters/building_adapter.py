"""DB2 Smart Building adapter: IoT telemetry (BACnet / Modbus / MQTT gateways)."""
from __future__ import annotations

import math
from typing import Any

import psycopg

from adapters.base_adapter import VALID, BaseAdapter, ValidationResult, parse_timestamp


class BuildingTelemetryAdapter(BaseAdapter):
    """One reading = {device_id, metric, value, observed_at}.

    - a non-numeric value (gateways send '-', '', 'ERR' on sensor fault) is quarantined
      as FAULT_READING;
    - a numeric value outside the metric's plausible range is stored but flagged SUSPECT,
      so analytics can ignore it while the history stays complete;
    - an unknown device or metric fails the foreign key and is quarantined as FK_VIOLATION.
    """
    source_id = "SRC_BLD_SYN"
    domain_schema = "building"
    target_table = "telemetry"
    required_fields = ("device_id", "metric", "observed_at")
    lon_field = None   # telemetry has no coordinates; location comes from the device assignment
    lat_field = None

    def validate(self, record: dict[str, Any]) -> ValidationResult:
        base = super().validate(record)
        if not base.ok:
            return base
        try:
            value = float(record.get("value"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return ValidationResult(False, "FAULT_READING", f"value is not numeric: {record.get('value')!r}")
        if not math.isfinite(value):
            return ValidationResult(False, "FAULT_READING", f"value is not finite: {record.get('value')!r}")
        if parse_timestamp(record["observed_at"]) is None:
            return ValidationResult(False, "SCHEMA_VIOLATION",
                                    "observed_at must be ISO-8601 with a timezone offset")
        return VALID

    def load(self, cur: psycopg.Cursor, record: dict[str, Any]) -> tuple[str, str]:
        # The range check runs in SQL against building.metric_types, the single source of truth.
        # A late or corrected packet for the same (device, metric, instant) replaces the value.
        cur.execute(
            """INSERT INTO building.telemetry (device_id, metric, value, quality_flag, observed_at, received_at)
               SELECT %(device_id)s::varchar, %(metric)s::varchar, %(value)s::numeric,
                      COALESCE((SELECT CASE WHEN %(value)s::numeric BETWEEN mt.min_valid AND mt.max_valid
                                            THEN 'GOOD' ELSE 'SUSPECT' END
                                FROM building.metric_types mt WHERE mt.metric_code = %(metric)s::varchar), 'GOOD'),
                      %(observed_at)s::timestamptz, clock_timestamp()
               ON CONFLICT (device_id, metric, observed_at) DO UPDATE
                   SET value = EXCLUDED.value, quality_flag = EXCLUDED.quality_flag,
                       received_at = EXCLUDED.received_at
               RETURNING telemetry_id, (xmax = 0) AS inserted""",
            {
                "device_id": record["device_id"],
                "metric": record["metric"],
                "value": float(record["value"]),
                "observed_at": parse_timestamp(record["observed_at"]),
            },
        )
        telemetry_id, inserted = cur.fetchone()
        return str(telemetry_id), "INSERT" if inserted else "UPDATE"
