"""/api/v1/mobility — stops, route patterns, trips, vehicle positions, telemetry ingestion."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, Field

from adapters.mobility_adapter import VehicleObservationAdapter
from api.database import fetch_all, fetch_one, pool
from api.routers.core_router import require_token

router = APIRouter(prefix="/api/v1/mobility", tags=["mobility"])


class ObservationIn(BaseModel):
    vehicle_id: str = Field(min_length=1, max_length=32, examples=["VEH-AV1-01"])
    trip_id: str | None = Field(None, max_length=48)
    lon: float = Field(ge=-180, le=180, examples=[126.6445])
    lat: float = Field(ge=-90, le=90, examples=[37.3885])
    speed_kmh: float = Field(ge=0, le=150, examples=[18.5])
    heading_deg: float | None = Field(None, ge=0, lt=360)
    battery_state_of_charge_pct: float | None = Field(None, ge=0, le=100)
    observed_at: AwareDatetime = Field(examples=["2026-10-02T10:30:00+09:00"])


@router.get("/stops/nearby", summary="Bus stops within a radius (metres) of a point")
def stops_nearby(
    lon: float = Query(126.6445, ge=-180, le=180),
    lat: float = Query(37.3885, ge=-90, le=90),
    radius_m: float = Query(400, gt=0, le=5000),
    limit: int = Query(20, ge=1, le=200),
) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT stop_id, stop_number, stop_name_ko, ST_X(geom) AS lon, ST_Y(geom) AS lat,
               ROUND(ST_Distance(geom::geography,
                     ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography)::numeric, 1)::float8 AS dist_m
        FROM mobility.stops
        WHERE ST_DWithin(geom::geography,
                         ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography, %(radius_m)s)
        ORDER BY dist_m
        LIMIT %(limit)s
        """,
        {"lon": lon, "lat": lat, "radius_m": radius_m, "limit": limit},
    )


@router.get("/stops.geojson", summary="All Songdo stops as a GeoJSON FeatureCollection (map export)")
def stops_geojson() -> dict[str, Any]:
    row = fetch_one(
        """
        SELECT jsonb_build_object(
                 'type', 'FeatureCollection',
                 'features', COALESCE(jsonb_agg(jsonb_build_object(
                     'type', 'Feature',
                     'geometry', ST_AsGeoJSON(geom, 6)::jsonb,
                     'properties', jsonb_build_object(
                         'stop_id', stop_id, 'stop_number', stop_number, 'stop_name_ko', stop_name_ko))
                   ORDER BY stop_id), '[]'::jsonb)) AS fc
        FROM mobility.stops
        """
    )
    return row["fc"] if row else {"type": "FeatureCollection", "features": []}


@router.get("/routes", summary="Routes with their stop counts")
def routes() -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT r.route_id, r.route_number, r.route_name_ko, r.route_name_en, r.route_type,
               COUNT(rs.stop_id) FILTER (WHERE rs.direction = 0) AS stops_outbound,
               COUNT(rs.stop_id) FILTER (WHERE rs.direction = 1) AS stops_inbound
        FROM mobility.routes r
        LEFT JOIN mobility.route_stops rs USING (route_id)
        GROUP BY r.route_id
        ORDER BY r.route_number
        """
    )


@router.get("/routes/{route_number}/stops", summary="Ordered stop sequence of a route (DB1-1)")
def route_stops(route_number: str, direction: int = Query(0, ge=0, le=1)) -> list[dict[str, Any]]:
    rows = fetch_all(
        """
        SELECT rs.sequence_order, s.stop_id, s.stop_number, s.stop_name_ko,
               ST_X(s.geom) AS lon, ST_Y(s.geom) AS lat
        FROM mobility.routes r
        JOIN mobility.route_stops rs USING (route_id)
        JOIN mobility.stops s USING (stop_id)
        WHERE r.route_number = %(route_number)s AND rs.direction = %(direction)s
        ORDER BY rs.sequence_order
        """,
        {"route_number": route_number, "direction": direction},
    )
    if not rows:
        raise HTTPException(404, f"No stops for route {route_number!r} direction {direction}")
    return rows


@router.get("/trips/{trip_id}/events", summary="Stop arrivals of one trip with lateness and dwell (DB1-2)")
def trip_events(trip_id: str) -> list[dict[str, Any]]:
    rows = fetch_all(
        """
        SELECT e.sequence_order, e.stop_id, s.stop_name_ko, e.scheduled_arrival, e.observed_arrival,
               e.observed_departure, e.lateness_seconds, e.dwell_seconds, e.boardings, e.alightings
        FROM mobility.trip_stop_events e
        JOIN mobility.stops s USING (stop_id)
        WHERE e.trip_id = %(trip_id)s
        ORDER BY e.sequence_order
        """,
        {"trip_id": trip_id},
    )
    if not rows:
        raise HTTPException(404, f"Unknown trip {trip_id!r}")
    return rows


@router.get("/vehicles/positions", summary="Position of every vehicle as of time T (DB1-3)")
def vehicle_positions(
    as_of: AwareDatetime | None = Query(None, description="ISO-8601 with offset; default = now"),
    max_staleness_s: int | None = Query(None, ge=1, description="Hide vehicles not seen within this many seconds"),
) -> list[dict[str, Any]]:
    as_of_value: datetime | None = as_of
    return fetch_all(
        """
        SELECT * FROM (
            SELECT DISTINCT ON (o.vehicle_id)
                   o.vehicle_id, v.vehicle_type, o.trip_id,
                   ST_X(o.geom) AS lon, ST_Y(o.geom) AS lat,
                   o.speed_kmh::float8 AS speed_kmh, o.heading_deg::float8 AS heading_deg,
                   o.battery_state_of_charge_pct::float8 AS battery_state_of_charge_pct, o.observed_at,
                   ROUND(EXTRACT(EPOCH FROM (COALESCE(%(as_of)s::timestamptz, now()) - o.observed_at)))::int
                       AS staleness_seconds
            FROM mobility.vehicle_observations o
            JOIN mobility.vehicles v USING (vehicle_id)
            WHERE o.observed_at <= COALESCE(%(as_of)s::timestamptz, now())
            ORDER BY o.vehicle_id, o.observed_at DESC
        ) latest
        WHERE %(max_staleness)s::int IS NULL OR staleness_seconds <= %(max_staleness)s::int
        ORDER BY vehicle_id
        """,
        {"as_of": as_of_value, "max_staleness": max_staleness_s},
    )


@router.post("/observation", status_code=202, dependencies=[Depends(require_token)],
             summary="Ingest one GPS observation (validate, dedup, quarantine, audit) (DB1-5)")
def ingest_observation(observation: ObservationIn) -> dict[str, Any]:
    record = observation.model_dump(mode="json")
    with pool.connection() as conn:
        summary = VehicleObservationAdapter(conn, actor="API").ingest([record])
    outcome = "LOADED" if summary.loaded else "QUARANTINED" if summary.quarantined else "DUPLICATE"
    return {"outcome": outcome, **summary.as_dict()}
