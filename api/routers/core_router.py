"""/api/v1/core — nearby places, source registry, quarantine, place ingestion."""
from __future__ import annotations

import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from adapters.core_adapter import PlacesAdapter
from api import edits
from api.config import settings
from api.database import fetch_all, pool
from api.edits import run_edit

router = APIRouter(prefix="/api/v1/core", tags=["core"])


def require_token(x_api_token: str | None = Header(None)) -> None:
    """Write endpoints need the shared ingestion token in the X-API-Token header."""
    if not x_api_token or not secrets.compare_digest(x_api_token, settings.ingest_api_token):
        raise HTTPException(401, "Missing or invalid X-API-Token header")


class PlaceIn(BaseModel):
    place_id: str = Field(min_length=1, max_length=64, examples=["PLC_DEMO_01"])
    name_ko: str = Field(min_length=1, max_length=255, examples=["송도 데모 장소"])
    name_en: str | None = Field(None, max_length=255, examples=["Songdo demo place"])
    category: str = Field(min_length=1, max_length=64, examples=["LANDMARK"])
    road_address: str | None = Field(None, max_length=255)
    lon: float = Field(ge=-180, le=180, examples=[126.6400])
    lat: float = Field(ge=-90, le=90, examples=[37.3900])
    source_id: str = Field("SRC_CORE_01", max_length=32)


@router.get("/places/nearby", summary="Places within a radius (metres) of a point")
def places_nearby(
    lon: float = Query(126.6548, ge=-180, le=180),
    lat: float = Query(37.3888, ge=-90, le=90),
    radius_m: float = Query(1000, gt=0, le=20000),
    limit: int = Query(50, ge=1, le=500),
) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT place_id, name_ko, name_en, category, road_address,
               ST_X(geom) AS lon, ST_Y(geom) AS lat,
               ROUND(ST_Distance(geom::geography,
                     ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography)::numeric, 1)::float8 AS dist_m
        FROM core.places
        WHERE ST_DWithin(geom::geography,
                         ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography, %(radius_m)s)
        ORDER BY dist_m
        LIMIT %(limit)s
        """,
        {"lon": lon, "lat": lat, "radius_m": radius_m, "limit": limit},
    )


@router.get("/sources", summary="Master source registry")
def sources() -> list[dict[str, Any]]:
    return fetch_all("SELECT * FROM core.source_registry ORDER BY source_id")


@router.get("/quarantine", summary="Latest quarantined records")
def quarantine(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    return fetch_all(
        """SELECT quarantine_id, batch_id, source_id, domain_schema, target_table, reason_code,
                  reason_detail, payload, received_at, recorded_at
           FROM core.quarantine_records ORDER BY recorded_at DESC LIMIT %(limit)s""",
        {"limit": limit},
    )


@router.post("/places", status_code=202, dependencies=[Depends(require_token)],
             summary="Ingest places through the full pipeline (validate, dedup, quarantine, audit)")
def ingest_places(places: Annotated[list[PlaceIn], Body(min_length=1, max_length=1000)]) -> dict[str, Any]:
    with pool.connection() as conn:
        summary = PlacesAdapter(conn, actor="API").ingest([p.model_dump() for p in places])
    return summary.as_dict()


# --------------------------------------------------------------------------- corrections
class PlacePatch(BaseModel):
    """Only the fields sent are changed. Send null to clear an optional field."""
    model_config = ConfigDict(extra="forbid")
    name_ko: str | None = Field(None, min_length=1, max_length=255)
    name_en: str | None = Field(None, max_length=255)
    category: str | None = Field(None, min_length=1, max_length=64)
    road_address: str | None = Field(None, max_length=255)
    lon: float | None = Field(None, ge=-180, le=180)
    lat: float | None = Field(None, ge=-90, le=90)


@router.patch("/places/{place_id}", dependencies=[Depends(require_token)],
              summary="Correct a place: changed fields are written with an audit entry (old and new values)")
def patch_place(place_id: str, body: PlacePatch) -> dict[str, Any]:
    return run_edit(edits.update, edits.PLACE, place_id, body.model_dump(mode="json", exclude_unset=True))


@router.delete("/places/{place_id}", dependencies=[Depends(require_token)],
               summary="Delete a place; refused (409) while a building or attraction refers to it")
def delete_place(place_id: str) -> dict[str, Any]:
    return run_edit(edits.delete, edits.PLACE, place_id)
