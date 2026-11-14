"""/api/v1/tourism — attractions, open-now search, events, visitor numbers, attraction ingestion."""
from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, Field

from adapters.tourism_adapter import AttractionsAdapter
from api.database import fetch_all, fetch_one, pool
from api.routers.core_router import require_token

router = APIRouter(prefix="/api/v1/tourism", tags=["tourism"])


class AttractionIn(BaseModel):
    attraction_id: str = Field(min_length=1, max_length=32, examples=["ATT_DEMO_01"])
    attraction_name_ko: str = Field(min_length=1, max_length=255, examples=["데모 전시관"])
    attraction_name_en: str | None = Field(None, max_length=255)
    category: str = Field(pattern="^(PARK|CULTURE|CONVENTION|SHOPPING|LANDMARK)$", examples=["CULTURE"])
    road_address: str | None = Field(None, max_length=255)
    is_wheelchair_accessible: bool
    has_parking: bool
    parking_spaces: int | None = Field(None, gt=0)
    admission_fee_krw: int = Field(0, ge=0)
    lon: float = Field(ge=-180, le=180, examples=[126.6400])
    lat: float = Field(ge=-90, le=90, examples=[37.3900])


@router.get("/attractions", summary="Attractions with accessibility metadata (DB3-1)")
def attractions(
    wheelchair_accessible: bool | None = Query(None),
    has_parking: bool | None = Query(None),
) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT attraction_id, attraction_name_ko, attraction_name_en, category, road_address,
               is_wheelchair_accessible, has_parking, parking_spaces, admission_fee_krw,
               ST_X(geom) AS lon, ST_Y(geom) AS lat
        FROM tourism.attractions
        WHERE (%(wheelchair)s::boolean IS NULL OR is_wheelchair_accessible = %(wheelchair)s::boolean)
          AND (%(parking)s::boolean IS NULL OR has_parking = %(parking)s::boolean)
        ORDER BY attraction_name_ko
        """,
        {"wheelchair": wheelchair_accessible, "parking": has_parking},
    )


@router.get("/attractions/open", summary="Attractions open at a given moment within a radius (DB3-4)")
def open_attractions(
    lon: float = Query(126.6445, ge=-180, le=180),
    lat: float = Query(37.3885, ge=-90, le=90),
    radius_m: float = Query(1000, gt=0, le=20000),
    at: AwareDatetime | None = Query(None, description="ISO-8601 with offset; default = now"),
) -> list[dict[str, Any]]:
    # weekday and clock time are evaluated in Korean local time, whatever offset the caller sent
    return fetch_all(
        """
        WITH moment AS (
            SELECT (COALESCE(%(at)s::timestamptz, now()) AT TIME ZONE 'Asia/Seoul') AS local_ts
        )
        SELECT a.attraction_id, a.attraction_name_ko, a.category,
               TO_CHAR(h.opens_at, 'HH24:MI') AS opens_at, TO_CHAR(h.closes_at, 'HH24:MI') AS closes_at,
               a.is_wheelchair_accessible, a.has_parking,
               ROUND(ST_Distance(a.geom::geography,
                     ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography)::numeric, 1)::float8 AS dist_m
        FROM moment m
        JOIN tourism.operating_hours h
          ON h.day_of_week = EXTRACT(DOW FROM m.local_ts)::int
         AND h.opens_at <= m.local_ts::time AND h.closes_at > m.local_ts::time
        JOIN tourism.attractions a USING (attraction_id)
        WHERE ST_DWithin(a.geom::geography,
                         ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography, %(radius_m)s)
        ORDER BY dist_m
        """,
        {"lon": lon, "lat": lat, "radius_m": radius_m, "at": at},
    )


@router.get("/events", summary="Events overlapping a date range (DB3-2)")
def events(
    from_date: date = Query(date(2026, 9, 1)),
    to_date: date = Query(date(2026, 10, 31)),
    include_cancelled: bool = Query(False),
) -> list[dict[str, Any]]:
    if to_date < from_date:
        raise HTTPException(422, "to_date must not be before from_date")
    return fetch_all(
        """
        SELECT e.event_id, e.event_name_ko, e.event_name_en, e.event_type, a.attraction_id,
               a.attraction_name_ko AS venue, e.start_date, e.end_date, e.daily_start_time, e.daily_end_time,
               e.expected_attendance, e.is_cancelled
        FROM tourism.events e
        JOIN tourism.attractions a USING (attraction_id)
        WHERE daterange(e.start_date, e.end_date, '[]') && daterange(%(from)s::date, %(to)s::date, '[]')
          AND (%(cancelled)s OR NOT e.is_cancelled)
        ORDER BY e.start_date, e.event_id
        """,
        {"from": from_date, "to": to_date, "cancelled": include_cancelled},
    )


@router.get("/attractions/{attraction_id}/visits",
            summary="Official monthly totals next to the hourly-sample totals (DB3-3)")
def visits(attraction_id: str) -> dict[str, Any]:
    attraction = fetch_one(
        "SELECT attraction_id, attraction_name_ko FROM tourism.attractions WHERE attraction_id = %(id)s",
        {"id": attraction_id})
    if attraction is None:
        raise HTTPException(404, f"Unknown attraction {attraction_id!r}")
    monthly = fetch_all(
        """SELECT o.year_month, o.visitor_count, o.source_id, r.provenance
           FROM tourism.aggregate_visits o JOIN core.source_registry r USING (source_id)
           WHERE o.attraction_id = %(id)s ORDER BY o.year_month""", {"id": attraction_id})
    samples = fetch_all(
        """SELECT date_trunc('month', v.hour_start AT TIME ZONE 'Asia/Seoul')::date AS year_month,
                  SUM(v.visitor_count) AS sample_total, COUNT(*) AS sample_hours
           FROM tourism.sample_visits v
           WHERE v.attraction_id = %(id)s GROUP BY 1 ORDER BY 1""", {"id": attraction_id})
    return {"attraction": attraction, "official_monthly": monthly, "hourly_sample_totals": samples}


@router.post("/attractions", status_code=202, dependencies=[Depends(require_token)],
             summary="Ingest attractions through the full pipeline (DB3-1)")
def ingest_attractions(items: Annotated[list[AttractionIn], Body(min_length=1, max_length=500)]) -> dict[str, Any]:
    with pool.connection() as conn:
        summary = AttractionsAdapter(conn, actor="API").ingest([i.model_dump() for i in items])
    return summary.as_dict()
