"""/api/v1/safety — facilities, incidents, status transitions, hazard zones, response analytics."""
from __future__ import annotations

from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, Field

from adapters.safety_adapter import IncidentsAdapter
from api.database import fetch_all, fetch_one, pool
from api.routers.core_router import require_token

router = APIRouter(prefix="/api/v1/safety", tags=["safety"])

# Status of every incident as of time T, rebuilt from the append-only history.
# Active = reported, dispatched or on scene at that moment.
ACTIVE_AS_OF_SQL = """
    SELECT i.incident_id, i.title, i.type_code, t.name_ko AS type_name, i.severity, st.status,
           st.changed_at AS status_since, i.reported_at, i.assigned_facility_id, i.restriction_radius_m,
           ST_X(i.geom) AS lon, ST_Y(i.geom) AS lat, i.geom
    FROM safety.incidents i
    JOIN safety.incident_types t USING (type_code)
    JOIN LATERAL (
        SELECT h.to_status AS status, h.changed_at
        FROM safety.incident_status_history h
        WHERE h.incident_id = i.incident_id AND h.changed_at <= COALESCE(%(as_of)s::timestamptz, now())
        ORDER BY h.changed_at DESC, h.history_id DESC
        LIMIT 1
    ) st ON TRUE
    WHERE i.reported_at <= COALESCE(%(as_of)s::timestamptz, now())
      AND i.reported_at >  COALESCE(%(as_of)s::timestamptz, now()) - INTERVAL '7 days'
      AND NOT i.is_drill
      AND st.status IN ('REPORTED', 'DISPATCHED', 'ON_SCENE')
"""


class IncidentIn(BaseModel):
    incident_id: str = Field(min_length=1, max_length=32, examples=["INC-20261003-0001"])
    type_code: str = Field(pattern="^(FIRE|MEDICAL|HAZMAT|FLOOD|TRAFFIC_ACCIDENT|ROAD_OBSTRUCTION)$",
                           examples=["TRAFFIC_ACCIDENT"])
    title: str = Field(min_length=1, max_length=255, examples=["교차로 접촉 사고"])
    severity: str = Field(pattern="^(CRITICAL|HIGH|MEDIUM|LOW)$", examples=["MEDIUM"])
    lon: float = Field(ge=-180, le=180, examples=[126.6445])
    lat: float = Field(ge=-90, le=90, examples=[37.3885])
    reported_at: AwareDatetime = Field(examples=["2026-10-03T09:00:00+09:00"])
    restriction_radius_m: int | None = Field(None, ge=0, le=2000)


class TransitionIn(BaseModel):
    to_status: str = Field(pattern="^(DISPATCHED|ON_SCENE|RESOLVED|CLOSED|CANCELLED)$", examples=["DISPATCHED"])
    changed_at: AwareDatetime | None = Field(None, description="default = now")
    facility_id: str | None = Field(None, max_length=32, examples=["FAC_POLICE_SONGDO1"])
    actor: str = Field("API", min_length=1, max_length=64)
    note: str | None = Field(None, max_length=500)


@router.get("/facilities", summary="Fire stations, 119 centers and police boxes (DB5-1)")
def facilities() -> list[dict[str, Any]]:
    return fetch_all(
        """SELECT facility_id, facility_name_ko, facility_name_en, facility_type, responder,
                  ST_X(geom) AS lon, ST_Y(geom) AS lat
           FROM safety.facilities ORDER BY facility_type, facility_name_ko""")


@router.get("/incidents/active", summary="Incidents that were active at time T (rebuilt from history)")
def active_incidents(as_of: AwareDatetime | None = Query(None, description="default = now")) -> list[dict[str, Any]]:
    return fetch_all(
        f"""SELECT incident_id, title, type_code, type_name, severity, status, status_since, reported_at,
                   assigned_facility_id, restriction_radius_m, lon, lat
            FROM ({ACTIVE_AS_OF_SQL}) a ORDER BY reported_at""", {"as_of": as_of})


@router.get("/incidents/{incident_id}", summary="One incident with its full status history (DB5-3)")
def incident(incident_id: str) -> dict[str, Any]:
    row = fetch_one(
        """SELECT i.incident_id, i.title, i.type_code, i.severity, sl.sla_target_seconds, i.current_status,
                  i.reported_at, i.assigned_facility_id, i.restriction_radius_m, i.is_drill,
                  ST_X(i.geom) AS lon, ST_Y(i.geom) AS lat
           FROM safety.incidents i JOIN safety.severity_levels sl USING (severity)
           WHERE i.incident_id = %(id)s""", {"id": incident_id})
    if row is None:
        raise HTTPException(404, f"Unknown incident {incident_id!r}")
    history = fetch_all(
        """SELECT from_status, to_status, changed_at, actor, facility_id, note
           FROM safety.incident_status_history WHERE incident_id = %(id)s
           ORDER BY changed_at, history_id""", {"id": incident_id})
    return {"incident": row, "history": history}


@router.get("/hazard-zones", summary="Active restriction zones as GeoJSON for path planners (DB5-5)")
def hazard_zones(
    as_of: AwareDatetime | None = Query(None, description="default = now"),
    lon: float | None = Query(None, ge=-180, le=180, description="optional: test this point against the zones"),
    lat: float | None = Query(None, ge=-90, le=90),
) -> dict[str, Any]:
    if (lon is None) != (lat is None):
        raise HTTPException(422, "lon and lat must be given together")
    rows = fetch_all(
        f"""
        SELECT a.incident_id, a.title, a.type_code, a.severity, a.status, a.restriction_radius_m, a.lon, a.lat,
               ST_AsGeoJSON(ST_Buffer(a.geom::geography, a.restriction_radius_m, 16)::geometry, 6)::jsonb AS zone,
               CASE WHEN %(lon)s::float8 IS NULL THEN NULL
                    ELSE ST_DWithin(a.geom::geography,
                                    ST_SetSRID(ST_MakePoint(%(lon)s::float8, %(lat)s::float8), 4326)::geography,
                                    a.restriction_radius_m) END AS contains_point
        FROM ({ACTIVE_AS_OF_SQL}) a
        WHERE a.restriction_radius_m > 0
        ORDER BY a.reported_at
        """, {"as_of": as_of, "lon": lon, "lat": lat})
    features = [{"type": "Feature", "geometry": r["zone"],
                 "properties": {k: r[k] for k in ("incident_id", "title", "type_code", "severity", "status",
                                                  "restriction_radius_m", "lon", "lat")}}
                for r in rows]
    body: dict[str, Any] = {"type": "FeatureCollection", "features": features}
    if lon is not None:
        body["point"] = {"lon": lon, "lat": lat,
                         "blocked_by": [r["incident_id"] for r in rows if r["contains_point"]]}
    return body


@router.get("/analytics/response-times", summary="Response time by facility (DB5-4)")
def response_times(
    from_time: AwareDatetime = Query(..., alias="from"),
    to_time: AwareDatetime = Query(..., alias="to"),
    include_drills: bool = Query(False),
) -> list[dict[str, Any]]:
    if to_time <= from_time:
        raise HTTPException(422, "'to' must be after 'from'")
    return fetch_all(
        """
        SELECT f.facility_id, f.facility_name_ko, f.facility_type, COUNT(*) AS incidents,
               ROUND(AVG(r.response_s))::int AS avg_response_s,
               ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY r.response_s))::int AS median_response_s,
               ROUND(PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY r.response_s))::int AS p90_response_s,
               ROUND(100.0 * COUNT(*) FILTER (WHERE r.response_s <= r.sla_s) / COUNT(*), 1)::float8 AS sla_met_pct
        FROM (
            SELECT i.assigned_facility_id, sl.sla_target_seconds AS sla_s,
                   EXTRACT(EPOCH FROM h.changed_at - i.reported_at)::int AS response_s
            FROM safety.incidents i
            JOIN safety.incident_status_history h ON h.incident_id = i.incident_id AND h.to_status = 'ON_SCENE'
            JOIN safety.severity_levels sl USING (severity)
            WHERE i.reported_at >= %(from)s AND i.reported_at < %(to)s
              AND (%(drills)s OR NOT i.is_drill)
        ) r
        JOIN safety.facilities f ON f.facility_id = r.assigned_facility_id
        GROUP BY f.facility_id
        ORDER BY avg_response_s
        """, {"from": from_time, "to": to_time, "drills": include_drills})


@router.post("/incidents", status_code=202, dependencies=[Depends(require_token)],
             summary="Report an incident (validate, dedup, quarantine, audit) (DB5-2)")
def report_incident(report: IncidentIn) -> dict[str, Any]:
    with pool.connection() as conn:
        summary = IncidentsAdapter(conn, actor="API").ingest([report.model_dump(mode="json")])
    outcome = "LOADED" if summary.loaded else "QUARANTINED" if summary.quarantined else "DUPLICATE"
    return {"outcome": outcome, **summary.as_dict()}


@router.post("/incidents/{incident_id}/transition", dependencies=[Depends(require_token)],
             summary="Move an incident to its next status; illegal steps are rejected (DB5-3)")
def transition(incident_id: str, body: TransitionIn) -> dict[str, Any]:
    try:
        with pool.connection() as conn:
            conn.execute("SELECT safety.transition_incident(%s, %s, %s, %s, %s, %s)",
                         (incident_id, body.to_status, body.changed_at, body.actor, body.facility_id, body.note))
    except psycopg.errors.NoDataFound:
        raise HTTPException(404, f"Unknown incident {incident_id!r}") from None
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(404, "Unknown facility") from None
    except psycopg.errors.CheckViolation as exc:
        raise HTTPException(409, exc.diag.message_primary or "Transition rejected") from None
    return incident(incident_id)
