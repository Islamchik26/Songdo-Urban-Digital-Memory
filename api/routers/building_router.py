"""/api/v1/building — buildings, facility state, device history, heartbeat check, telemetry ingestion."""
from __future__ import annotations

from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, Field

from adapters.building_adapter import BuildingTelemetryAdapter
from api.database import fetch_all, fetch_one, pool
from api.routers.core_router import require_token

router = APIRouter(prefix="/api/v1/building", tags=["building"])


class TelemetryIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=48, examples=["DEV-GT-TMP-01"])
    metric: str = Field(min_length=1, max_length=24, examples=["TEMPERATURE_C"])
    value: float = Field(examples=[22.4], allow_inf_nan=False)
    observed_at: AwareDatetime = Field(examples=["2026-10-02T10:32:00+09:00"])


class RelocationIn(BaseModel):
    space_id: str = Field(min_length=1, max_length=48, examples=["SP-GT-F12-R2"])
    relocated_at: AwareDatetime = Field(examples=["2026-10-02T11:00:00+09:00"])


@router.get("/buildings", summary="Buildings with device counts")
def buildings() -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT b.building_id, b.building_name_ko, b.building_name_en, b.building_use, b.road_address,
               b.floors_above, b.floors_below, b.place_id, ST_X(b.geom) AS lon, ST_Y(b.geom) AS lat,
               COUNT(a.device_id) AS devices_installed
        FROM building.buildings b
        LEFT JOIN building.spaces sp USING (building_id)
        LEFT JOIN building.device_space_assignments a ON a.space_id = sp.space_id AND a.unassigned_at IS NULL
        GROUP BY b.building_id
        ORDER BY b.building_id
        """
    )


@router.get("/buildings/{building_id}/state",
            summary="Facility state for VIP: latest readings, power, maintenance (DB2-5)")
def building_state(
    building_id: str,
    as_of: AwareDatetime | None = Query(None, description="ISO-8601 with offset; default = now"),
) -> dict[str, Any]:
    building = fetch_one(
        """SELECT building_id, building_name_ko, building_name_en, building_use,
                  ST_X(geom) AS lon, ST_Y(geom) AS lat
           FROM building.buildings WHERE building_id = %(id)s""", {"id": building_id})
    if building is None:
        raise HTTPException(404, f"Unknown building {building_id!r}")
    params = {"id": building_id, "as_of": as_of}
    devices = fetch_all(
        """
        SELECT d.device_id, d.device_type, d.current_status, sp.space_name, sp.floor_number,
               l.metric, l.value::float8 AS value, mt.unit, l.observed_at,
               ROUND(EXTRACT(EPOCH FROM (COALESCE(%(as_of)s::timestamptz, now()) - l.observed_at)) / 60)::int
                   AS age_minutes
        FROM building.spaces sp
        JOIN building.device_space_assignments a ON a.space_id = sp.space_id AND a.unassigned_at IS NULL
        JOIN building.devices d USING (device_id)
        LEFT JOIN LATERAL (
            SELECT t.metric, t.value, t.observed_at
            FROM building.telemetry t
            WHERE t.device_id = d.device_id AND t.quality_flag = 'GOOD'
              AND t.observed_at <= COALESCE(%(as_of)s::timestamptz, now())
            ORDER BY t.observed_at DESC
            LIMIT 1
        ) l ON TRUE
        LEFT JOIN building.metric_types mt ON mt.metric_code = l.metric
        WHERE sp.building_id = %(id)s
        ORDER BY sp.floor_number, sp.space_name, d.device_id
        """, params)
    maintenance = fetch_all(
        """
        SELECT m.action_id, m.device_id, m.action_type, m.description, m.technician,
               m.started_at, m.completed_at, (m.completed_at IS NULL) AS is_open
        FROM building.maintenance_actions m
        JOIN building.device_space_assignments a ON a.device_id = m.device_id AND a.unassigned_at IS NULL
        JOIN building.spaces sp ON sp.space_id = a.space_id
        WHERE sp.building_id = %(id)s
        ORDER BY m.started_at DESC
        """, params)
    temps = [d["value"] for d in devices if d["device_type"] == "TEMP_SENSOR" and d["value"] is not None]
    power = [d["value"] for d in devices if d["device_type"] == "POWER_METER" and d["value"] is not None]
    return {
        "building": building,
        "summary": {
            "avg_indoor_temperature_c": round(sum(temps) / len(temps), 2) if temps else None,
            "power_kw": round(sum(power), 1) if power else None,
            "devices": len(devices),
            "devices_not_active": sum(1 for d in devices if d["current_status"] != "ACTIVE"),
            "open_maintenance_actions": sum(1 for m in maintenance if m["is_open"]),
        },
        "devices": devices,
        "maintenance": maintenance,
    }


@router.get("/devices/silent", summary="Devices with no telemetry in the last N minutes (DB2-4)")
def silent_devices(
    as_of: AwareDatetime | None = Query(None, description="ISO-8601 with offset; default = now"),
    silent_minutes: int = Query(15, ge=1, le=1440),
) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT d.device_id, d.device_type, d.current_status, b.building_id, sp.space_name,
               last.observed_at AS last_seen
        FROM building.devices d
        LEFT JOIN building.device_space_assignments a ON a.device_id = d.device_id AND a.unassigned_at IS NULL
        LEFT JOIN building.spaces sp ON sp.space_id = a.space_id
        LEFT JOIN building.buildings b ON b.building_id = sp.building_id
        LEFT JOIN LATERAL (
            SELECT MAX(t.observed_at) AS observed_at FROM building.telemetry t
            WHERE t.device_id = d.device_id AND t.observed_at <= COALESCE(%(as_of)s::timestamptz, now())
        ) last ON TRUE
        WHERE d.current_status <> 'RETIRED'
          AND NOT EXISTS (
              SELECT 1 FROM building.telemetry t
              WHERE t.device_id = d.device_id
                AND t.observed_at >  COALESCE(%(as_of)s::timestamptz, now()) - make_interval(mins => %(minutes)s)
                AND t.observed_at <= COALESCE(%(as_of)s::timestamptz, now()))
        ORDER BY last.observed_at NULLS FIRST, d.device_id
        """,
        {"as_of": as_of, "minutes": silent_minutes},
    )


@router.get("/devices/{device_id}/history", summary="Where a device has been installed over time (DB2-3)")
def device_history(device_id: str) -> list[dict[str, Any]]:
    rows = fetch_all(
        """
        SELECT a.assignment_id, sp.building_id, sp.space_id, sp.space_name, sp.floor_number,
               a.assigned_at, a.unassigned_at, (a.unassigned_at IS NULL) AS is_current
        FROM building.device_space_assignments a
        JOIN building.spaces sp USING (space_id)
        WHERE a.device_id = %(id)s
        ORDER BY a.assigned_at
        """, {"id": device_id})
    if not rows:
        raise HTTPException(404, f"Unknown device {device_id!r}")
    return rows


@router.post("/devices/{device_id}/relocate", dependencies=[Depends(require_token)],
             summary="Move a device to another space, keeping the history (DB2-3)")
def relocate_device(device_id: str, body: RelocationIn) -> dict[str, Any]:
    try:
        with pool.connection() as conn:
            row = conn.execute(
                "SELECT building.relocate_device(%(device)s, %(space)s, %(at)s, 'API') AS assignment_id",
                {"device": device_id, "space": body.space_id, "at": body.relocated_at}).fetchone()
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(404, "Unknown device or space") from None
    except (psycopg.errors.CheckViolation, psycopg.errors.ExclusionViolation) as exc:
        raise HTTPException(409, exc.diag.message_primary or "Relocation rejected") from None
    return {"device_id": device_id, "space_id": body.space_id, "assignment_id": row["assignment_id"]}


@router.post("/telemetry", status_code=202, dependencies=[Depends(require_token)],
             summary="Ingest one device reading (validate, dedup, quarantine, audit)")
def ingest_telemetry(reading: TelemetryIn) -> dict[str, Any]:
    with pool.connection() as conn:
        summary = BuildingTelemetryAdapter(conn, actor="API").ingest([reading.model_dump(mode="json")])
    outcome = "LOADED" if summary.loaded else "QUARANTINED" if summary.quarantined else "DUPLICATE"
    return {"outcome": outcome, **summary.as_dict()}
