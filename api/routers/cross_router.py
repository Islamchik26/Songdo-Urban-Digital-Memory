"""/api/v1/cross — cross-domain federation views and materialized views."""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.database import fetch_all, pool
from api.routers.core_router import require_token

router = APIRouter(prefix="/api/v1/cross", tags=["cross-domain"])


@router.get("/attractions-near-stops", summary="Attractions and events within 500 m of stops (mobility x tourism)")
def attractions_near_stops(
    stop_number: str | None = Query(None, max_length=16, description="limit to one stop"),
    on_date: date | None = Query(None, description="only events running on this date"),
    limit: int = Query(200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT stop_id, stop_name, stop_number, attraction_id, attraction_name,
               distance_meters::float8 AS distance_meters, event_id, active_event_name, expected_attendance,
               event_start_date, event_end_date
        FROM cross_domain.v_attractions_events_near_stops
        WHERE (%(stop)s::varchar IS NULL OR stop_number = %(stop)s::varchar)
          AND (%(on)s::date IS NULL OR %(on)s::date BETWEEN event_start_date AND event_end_date)
        ORDER BY distance_meters, stop_id, event_start_date
        LIMIT %(limit)s
        """, {"stop": stop_number, "on": on_date, "limit": limit})


@router.get("/building-context/{building_id}",
            summary="Devices of a building with outdoor PM readings (building x environment)")
def building_context(building_id: str) -> list[dict[str, Any]]:
    rows = fetch_all(
        """
        SELECT building_id, building_name_ko, device_id, device_type, current_status, nearest_env_station,
               distance_to_station_meters::float8 AS distance_to_station_meters, pollutant_type,
               outdoor_value::float8 AS outdoor_value, reading_time
        FROM cross_domain.v_building_devices_with_environmental_context
        WHERE building_id = %(id)s
        ORDER BY device_id, pollutant_type
        """, {"id": building_id})
    if not rows:
        raise HTTPException(404, f"Unknown building {building_id!r} (or it has no devices)")
    return rows


@router.get("/incident-impact", summary="Open incidents, affected stops and local air alerts (safety x mobility x environment)")
def incident_impact() -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT incident_id, title, incident_type, severity, current_status, stop_id, nearby_stop_name,
               distance_to_stop_meters::float8 AS distance_to_stop_meters, nearest_env_station, active_env_alert
        FROM cross_domain.v_incidents_mobility_environmental_impact
        ORDER BY incident_id, distance_to_stop_meters
        """)


@router.get("/place-context", summary="Precomputed context card per place (materialized view)")
def place_context(category: str | None = Query(None, max_length=64)) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT place_id, name_ko, name_en, category, lon, lat, nearest_stop_id, nearest_stop_name,
               nearest_stop_m::float8 AS nearest_stop_m, stops_within_500m, attractions_within_500m,
               nearest_env_station, nearest_env_station_m::float8 AS nearest_env_station_m,
               nearest_fire_unit, nearest_fire_unit_m::float8 AS nearest_fire_unit_m,
               nearest_police_unit, nearest_police_unit_m::float8 AS nearest_police_unit_m
        FROM cross_domain.mv_place_context
        WHERE %(category)s::varchar IS NULL OR category = %(category)s::varchar
        ORDER BY name_ko
        """, {"category": category})


@router.get("/facility-response-stats", summary="Monthly response statistics per facility (materialized view)")
def facility_response_stats(include_drills: bool = Query(False)) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT facility_id, facility_name_ko, facility_type, month, is_drill, incidents, avg_response_s,
               median_response_s, p90_response_s,
               ROUND(100.0 * sla_met / incidents, 1)::float8 AS sla_met_pct
        FROM cross_domain.mv_facility_response_stats
        WHERE %(drills)s OR NOT is_drill
        ORDER BY month, facility_name_ko, is_drill
        """, {"drills": include_drills})


@router.get("/freshness", summary="When each materialized view was last refreshed")
def freshness() -> list[dict[str, Any]]:
    return fetch_all(
        """SELECT view_name, refreshed_at, row_count, duration_ms::float8 AS duration_ms, age_minutes
           FROM cross_domain.v_materialized_view_freshness ORDER BY view_name""")


@router.post("/refresh", dependencies=[Depends(require_token)],
             summary="Refresh all materialized views (CONCURRENTLY: readers are not blocked)")
def refresh() -> list[dict[str, Any]]:
    with pool.connection() as conn:
        return conn.execute(
            """SELECT view_name, row_count, duration_ms::float8 AS duration_ms
               FROM cross_domain.refresh_materialized_views()""").fetchall()
