"""/api/v1/environment — stations, latest readings, measurements, air exposure, alerts, ingestion."""
from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, Field

from adapters.environment_adapter import AirMeasurementsAdapter
from api.database import fetch_all, pool
from api.routers.core_router import require_token

router = APIRouter(prefix="/api/v1/environment", tags=["environment"])

LATEST_SQL = """
    SELECT l.type_code, t.name_en, l.measured_value::float8 AS value, t.unit, l.quality_flag, l.observed_at,
           ROUND(EXTRACT(EPOCH FROM (COALESCE(%(as_of)s::timestamptz, now()) - l.observed_at)) / 60)::int
               AS age_minutes,
           CASE WHEN l.measured_value > vb.threshold_value THEN 'VERY_BAD'
                WHEN l.measured_value > b.threshold_value  THEN 'BAD'
                ELSE 'NORMAL' END AS grade
    FROM (
        SELECT DISTINCT ON (m.type_code) m.type_code, m.measured_value, m.quality_flag, m.observed_at
        FROM environment.measurements m
        WHERE m.station_id = %(station_id)s AND m.quality_flag = 'GOOD'
          AND m.observed_at <= COALESCE(%(as_of)s::timestamptz, now())
        ORDER BY m.type_code, m.observed_at DESC
    ) l
    JOIN environment.measurement_types t USING (type_code)
    LEFT JOIN environment.alert_rules b  ON b.type_code  = l.type_code AND b.severity  = 'BAD'
    LEFT JOIN environment.alert_rules vb ON vb.type_code = l.type_code AND vb.severity = 'VERY_BAD'
    ORDER BY l.type_code
"""


class ReadingIn(BaseModel):
    station_name: str = Field(min_length=1, max_length=64, examples=["송도"])
    type_code: str = Field(pattern="^(PM10|PM25|O3|NO2|CO|SO2)$", examples=["PM25"])
    value: float | str | None = Field(examples=[23], description="number, or AirKorea's '-' fault marker")
    data_time: str = Field(examples=["2026-10-03 09:00"],
                           description="'YYYY-MM-DD HH:MM' in Korean time (24:00 allowed) or ISO-8601 with offset")
    flag: str | None = Field(None, max_length=32)


def _station_or_404(name: str) -> dict[str, Any]:
    rows = fetch_all(
        """SELECT station_id, station_name_ko, network, address, ST_X(geom) AS lon, ST_Y(geom) AS lat
           FROM environment.stations WHERE station_name_ko = %(name)s""", {"name": name})
    if not rows:
        raise HTTPException(404, f"Unknown station {name!r}")
    return rows[0]


@router.get("/stations", summary="Monitoring stations with their latest reading time (DB4-1)")
def stations() -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT s.station_id, s.station_name_ko, s.network, s.address, s.measured_items, s.established_year,
               ST_X(s.geom) AS lon, ST_Y(s.geom) AS lat,
               (SELECT MAX(m.observed_at) FROM environment.measurements m WHERE m.station_id = s.station_id)
                   AS last_reading_at
        FROM environment.stations s
        ORDER BY s.station_name_ko
        """
    )


@router.get("/stations/{station_name}/latest", summary="Latest good reading per pollutant (DB4-2)")
def latest(station_name: str, as_of: AwareDatetime | None = Query(None)) -> dict[str, Any]:
    station = _station_or_404(station_name)
    return {"station": station,
            "readings": fetch_all(LATEST_SQL, {"station_id": station["station_id"], "as_of": as_of})}


@router.get("/stations/{station_name}/measurements", summary="Raw measurements of one pollutant in a time range")
def measurements(
    station_name: str,
    from_time: AwareDatetime = Query(..., alias="from"),
    to_time: AwareDatetime = Query(..., alias="to"),
    type_code: str = Query("PM25", pattern="^(PM10|PM25|O3|NO2|CO|SO2)$"),
    limit: int = Query(500, ge=1, le=5000),
) -> list[dict[str, Any]]:
    if to_time <= from_time:
        raise HTTPException(422, "'to' must be after 'from'")
    if to_time - from_time > timedelta(days=31):
        raise HTTPException(422, "range is limited to 31 days")
    station = _station_or_404(station_name)
    return fetch_all(
        """SELECT type_code, measured_value::float8 AS value, quality_flag, observed_at, received_at, source_id
           FROM environment.measurements
           WHERE station_id = %(id)s AND type_code = %(type)s
             AND observed_at >= %(from)s AND observed_at < %(to)s
           ORDER BY observed_at LIMIT %(limit)s""",
        {"id": station["station_id"], "type": type_code, "from": from_time, "to": to_time, "limit": limit})


@router.get("/exposure", summary="Air quality at a point: nearest station, distance, reading age (DB4-5)")
def exposure(
    lon: float = Query(126.6445, ge=-180, le=180),
    lat: float = Query(37.3885, ge=-90, le=90),
    as_of: AwareDatetime | None = Query(None, description="ISO-8601 with offset; default = now"),
) -> dict[str, Any]:
    nearest = fetch_all(
        """
        SELECT s.station_id, s.station_name_ko, s.network,
               ROUND(ST_Distance(s.geom::geography,
                     ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography)::numeric, 1)::float8 AS distance_m
        FROM environment.stations s
        ORDER BY s.geom::geography <-> ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography
        LIMIT 1
        """, {"lon": lon, "lat": lat})
    if not nearest:
        raise HTTPException(404, "No stations registered")
    return {"point": {"lon": lon, "lat": lat}, "nearest_station": nearest[0],
            "readings": fetch_all(LATEST_SQL, {"station_id": nearest[0]["station_id"], "as_of": as_of})}


@router.get("/alerts", summary="Threshold alerts (DB4-4)")
def alerts(status: str | None = Query(None, pattern="^(ACTIVE|RESOLVED)$")) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT a.alert_id, s.station_name_ko, a.type_code, r.severity, r.threshold_value::float8 AS threshold,
               a.peak_value::float8 AS peak_value, a.started_hour, a.last_exceeded_hour, a.status,
               a.resolved_at, a.alert_message
        FROM environment.alerts a
        JOIN environment.alert_rules r USING (rule_id)
        JOIN environment.stations s USING (station_id)
        WHERE %(status)s::varchar IS NULL OR a.status = %(status)s::varchar
        ORDER BY a.started_hour DESC, a.alert_id
        """, {"status": status})


@router.post("/measurements", status_code=202, dependencies=[Depends(require_token)],
             summary="Ingest readings (faults quarantined, late packets upserted), then refresh rollup and alerts")
def ingest_measurements(readings: Annotated[list[ReadingIn], Body(min_length=1, max_length=2000)]) -> dict[str, Any]:
    with pool.connection() as conn:
        summary = AirMeasurementsAdapter(conn, actor="API").ingest([r.model_dump() for r in readings])
        span = conn.execute(
            """SELECT MIN(observed_at) AS lo, MAX(observed_at) + INTERVAL '1 hour' AS hi
               FROM core.staging_records WHERE batch_id = %s AND status = 'LOADED'""",
            (summary.batch_id,)).fetchone()
        new_alerts = 0
        if span and span["lo"] is not None:
            conn.execute("SELECT environment.refresh_hourly_summaries(%s, %s)", (span["lo"], span["hi"]))
            new_alerts = conn.execute("SELECT environment.evaluate_alerts(%s, %s) AS n",
                                      (span["lo"], span["hi"])).fetchone()["n"]
    return {**summary.as_dict(), "new_alerts": new_alerts}
