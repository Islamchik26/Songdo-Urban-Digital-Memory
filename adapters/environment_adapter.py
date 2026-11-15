"""DB4 Environment adapters: AirKorea stations and pollutant measurements."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg

from adapters.base_adapter import VALID, BaseAdapter, ValidationResult

KST = timezone(timedelta(hours=9))

# AirKorea response field -> our pollutant code (mirrors environment.measurement_types.api_field)
API_FIELDS = {"pm10Value": "PM10", "pm25Value": "PM25", "o3Value": "O3",
              "no2Value": "NO2", "coValue": "CO", "so2Value": "SO2"}
FLAG_FIELDS = {"PM10": "pm10Flag", "PM25": "pm25Flag", "O3": "o3Flag",
               "NO2": "no2Flag", "CO": "coFlag", "SO2": "so2Flag"}


def station_id_for(name: str) -> str:
    return f"AK_{name.strip()}"


def parse_airkorea_time(value: Any) -> datetime | None:
    """'2026-11-15 18:00' (Korean local time) -> aware datetime.

    AirKorea writes midnight as '24:00' of the previous day, which no date
    library accepts, so it is mapped to 00:00 of the next day. ISO-8601 strings
    with an offset are accepted as well.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        if len(text) == 16 and text[10] == " ":
            day, clock = text.split(" ")
            if clock == "24:00":
                return datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=KST) + timedelta(days=1)
            return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=KST)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except ValueError:
        return None


def explode_airkorea_item(item: dict[str, Any], station_name: str | None = None) -> list[dict[str, Any]]:
    """One AirKorea row (all pollutants of one station-hour) -> one record per pollutant."""
    name = station_name or item.get("stationName")
    return [{"station_name": name, "type_code": code, "value": item.get(field),
             "data_time": item.get("dataTime"), "flag": item.get(FLAG_FIELDS[code])}
            for field, code in API_FIELDS.items()]


class AirStationsAdapter(BaseAdapter):
    """Rows of the AirKorea station list. Note the trap: dmX is LATITUDE and dmY is LONGITUDE."""
    source_id = "SRC_ENV_02"
    domain_schema = "environment"
    target_table = "stations"
    required_fields = ("stationName", "mangName")
    lon_field = "dmY"
    lat_field = "dmX"

    def load(self, cur: psycopg.Cursor, record: dict[str, Any]) -> tuple[str, str]:
        year = str(record.get("year") or "").strip()
        cur.execute(
            """INSERT INTO environment.stations
                   (station_id, station_name_ko, address, network, measured_items, established_year, geom, source_id)
               VALUES (%(id)s, %(name)s, %(addr)s, %(network)s, %(items)s, %(year)s,
                       ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326), %(source)s)
               ON CONFLICT (station_id) DO UPDATE
                   SET address = EXCLUDED.address, network = EXCLUDED.network,
                       measured_items = EXCLUDED.measured_items, established_year = EXCLUDED.established_year,
                       geom = EXCLUDED.geom, source_id = EXCLUDED.source_id
               RETURNING station_id, (xmax = 0) AS inserted""",
            {
                "id": station_id_for(record["stationName"]),
                "name": record["stationName"].strip(),
                "addr": record.get("addr"),
                "network": record["mangName"].strip(),
                "items": record.get("item"),
                "year": int(year) if year.isdigit() else None,
                "lon": float(record["dmY"]),
                "lat": float(record["dmX"]),
                "source": self.source_id,
            },
        )
        station_id, inserted = cur.fetchone()
        return station_id, "INSERT" if inserted else "UPDATE"


class AirMeasurementsAdapter(BaseAdapter):
    """One reading = {station_name, type_code, value, data_time[, flag]}.

    - '-' / blank / non-numeric value (AirKorea's marker for 점검, 통신장애, 자료이상...) is
      quarantined as FAULT_READING and never reaches the measurements table;
    - a numeric value outside the pollutant's plausible range is stored as SUSPECT;
    - a later packet for the same (station, pollutant, hour) replaces the value (late
      arrival / official correction); the audit ledger keeps both versions;
    - an unregistered station fails the foreign key and is quarantined as FK_VIOLATION.
    """
    source_id = "SRC_ENV_01"
    domain_schema = "environment"
    target_table = "measurements"
    required_fields = ("station_name", "type_code", "data_time")
    lon_field = None
    lat_field = None

    def validate(self, record: dict[str, Any]) -> ValidationResult:
        base = super().validate(record)
        if not base.ok:
            return base
        raw = record.get("value")
        flag = f" (flag: {record['flag']})" if record.get("flag") else ""
        try:
            value = float(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return ValidationResult(False, "FAULT_READING", f"value is not numeric: {raw!r}{flag}")
        if not math.isfinite(value):
            return ValidationResult(False, "FAULT_READING", f"value is not finite: {raw!r}{flag}")
        if parse_airkorea_time(record["data_time"]) is None:
            return ValidationResult(False, "SCHEMA_VIOLATION", f"unreadable data_time: {record['data_time']!r}")
        return VALID

    def observed_at(self, record: dict[str, Any], received_at: datetime) -> datetime:
        return parse_airkorea_time(record["data_time"]) or received_at

    def load(self, cur: psycopg.Cursor, record: dict[str, Any]) -> tuple[str, str]:
        cur.execute(
            """INSERT INTO environment.measurements
                   (station_id, type_code, measured_value, quality_flag, observed_at, received_at, source_id)
               SELECT %(station_id)s::varchar, %(type)s::varchar, %(value)s::numeric,
                      COALESCE((SELECT CASE WHEN %(value)s::numeric BETWEEN t.min_valid AND t.max_valid
                                            THEN 'GOOD' ELSE 'SUSPECT' END
                                FROM environment.measurement_types t WHERE t.type_code = %(type)s::varchar), 'GOOD'),
                      %(observed_at)s::timestamptz, clock_timestamp(), %(source)s::varchar
               ON CONFLICT (station_id, type_code, observed_at) DO UPDATE
                   SET measured_value = EXCLUDED.measured_value, quality_flag = EXCLUDED.quality_flag,
                       received_at = EXCLUDED.received_at, source_id = EXCLUDED.source_id
               RETURNING measurement_id, (xmax = 0) AS inserted""",
            {
                "station_id": station_id_for(str(record["station_name"])),
                "type": str(record["type_code"]).upper(),
                "value": float(record["value"]),
                "observed_at": parse_airkorea_time(record["data_time"]),
                "source": record.get("source_id") or self.source_id,
            },
        )
        measurement_id, inserted = cur.fetchone()
        return str(measurement_id), "INSERT" if inserted else "UPDATE"
