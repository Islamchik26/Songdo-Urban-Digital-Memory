"""Corrections (PATCH) and retirement (DELETE) of reference data, each with an audit entry.

The platform is an urban *memory*, so the rules depend on the kind of data:

  reference data   places, attractions   PATCH changes fields; DELETE removes the row only when nothing
                                         refers to it (otherwise 409), and the audit entry keeps a full
                                         snapshot of the deleted row
                   events                DELETE is a soft delete: is_cancelled = TRUE (the row stays)
                   devices               DELETE retires: current_status = RETIRED and the open location
                                         interval is closed now, so the device's history stays intact
  history          GPS, telemetry,       never edited in place: a corrected reading is re-sent and
                   measurements          replaces the value through the ingestion pipeline (DB4-3)
  audit ledger                           UPDATE / DELETE blocked by trigger

Every change runs in one transaction: lock the row (SELECT ... FOR UPDATE), compare, write, then
core.log_event() with the old and new values. A PATCH that changes nothing writes nothing.

The functions take a connection, so tests can call them inside a transaction that is rolled back;
the routers call them through run_edit(), which maps EditError to an HTTP status.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import psycopg
from fastapi import HTTPException
from psycopg import sql
from psycopg.types.json import Jsonb

from adapters.base_adapter import validate_coordinate


class EditError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class Entity:
    label: str
    schema: str
    table: str
    key: str
    editable: tuple[str, ...]          # columns a PATCH may change ("lon"/"lat" move the point)
    nullable: tuple[str, ...]          # editable columns that may be cleared with null
    lineage: str                       # column holding the source_id for the audit entry
    has_geom: bool = False
    references: tuple[tuple[str, str], ...] = ()   # (table, column) that block a hard delete


PLACE = Entity("place", "core", "places", "place_id",
               ("name_ko", "name_en", "category", "road_address", "lon", "lat"),
               ("name_en", "road_address"), "provenance", has_geom=True,
               references=(("building.buildings", "place_id"), ("tourism.attractions", "place_id")))
ATTRACTION = Entity("attraction", "tourism", "attractions", "attraction_id",
                    ("attraction_name_ko", "attraction_name_en", "category", "road_address",
                     "is_wheelchair_accessible", "has_parking", "parking_spaces", "admission_fee_krw", "lon", "lat"),
                    ("attraction_name_en", "road_address", "parking_spaces"), "source_id", has_geom=True,
                    references=(("tourism.events", "attraction_id"), ("tourism.operating_hours", "attraction_id"),
                                ("tourism.aggregate_visits", "attraction_id"), ("tourism.sample_visits", "attraction_id")))
EVENT = Entity("event", "tourism", "events", "event_id",
               ("event_name_ko", "event_name_en", "event_type", "start_date", "end_date",
                "daily_start_time", "daily_end_time", "expected_attendance"),
               ("event_name_en",), "source_id")
DEVICE = Entity("device", "building", "devices", "device_id",
                ("manufacturer", "model", "serial_number", "protocol", "current_status"),
                (), "source_id")


ENTITIES: dict[str, Entity] = {e.label: e for e in (PLACE, ATTRACTION, EVENT, DEVICE)}

# id, a readable name and one detail per record, for the console's record picker
_LISTS = {
    "place": """SELECT place_id AS id, name_ko || COALESCE(' · ' || name_en, '') AS name, category AS detail
                FROM core.places ORDER BY place_id""",
    "attraction": """SELECT attraction_id AS id, attraction_name_ko || COALESCE(' · ' || attraction_name_en, '') AS name,
                            category AS detail
                     FROM tourism.attractions ORDER BY attraction_id""",
    "event": """SELECT event_id AS id, event_name_ko AS name,
                       start_date::text || CASE WHEN is_cancelled THEN ' · CANCELLED' ELSE '' END AS detail
                FROM tourism.events ORDER BY event_id""",
    "device": """SELECT d.device_id AS id, d.device_type || ' · ' || COALESCE(sp.building_id, 'not installed') AS name,
                        d.current_status AS detail
                 FROM building.devices d
                 LEFT JOIN building.device_space_assignments a ON a.device_id = d.device_id AND a.unassigned_at IS NULL
                 LEFT JOIN building.spaces sp ON sp.space_id = a.space_id
                 ORDER BY d.device_id""",
}


def _table(e: Entity) -> sql.Composed:
    return sql.SQL("{}.{}").format(sql.Identifier(e.schema), sql.Identifier(e.table))


def listing(conn: psycopg.Connection, e: Entity) -> list[dict[str, Any]]:
    rows = conn.execute(_LISTS[e.label]).fetchall()
    return [{"id": _get(r, "id", 0), "name": _get(r, "name", 1), "detail": _get(r, "detail", 2)} for r in rows]


def read(conn: psycopg.Connection, e: Entity, entity_id: str) -> dict[str, Any]:
    """Current values of one record and its latest audit entries (what was changed, when, by whom)."""
    record = _lock(conn, e, entity_id, lock=False)
    audit = conn.execute(
        """SELECT audit_id, action, actor, recorded_at, payload_snapshot
           FROM core.event_audit_log WHERE table_name = %s AND entity_id = %s
           ORDER BY audit_id DESC LIMIT 20""", (e.table, entity_id)).fetchall()
    keys = ("audit_id", "action", "actor", "recorded_at", "payload_snapshot")
    return {"entity": e.label, "entity_id": entity_id, "editable": list(e.editable), "nullable": list(e.nullable),
            "record": record,
            "audit": [{k: _get(r, k, i) for i, k in enumerate(keys)} for r in audit]}


def _lock(conn: psycopg.Connection, e: Entity, entity_id: str, lock: bool = True) -> dict[str, Any]:
    """The current row as JSON-like values (dates and times as ISO text), locked for this transaction."""
    point = sql.SQL(", ST_X(t.geom) AS lon, ST_Y(t.geom) AS lat") if e.has_geom else sql.SQL("")
    row = conn.execute(
        sql.SQL("SELECT to_jsonb(t) - 'geom' AS row{point} FROM {table} t WHERE {key} = %s{lock}")
        .format(point=point, table=_table(e), key=sql.Identifier(e.key),
                lock=sql.SQL(" FOR UPDATE OF t") if lock else sql.SQL("")), (entity_id,)).fetchone()
    if row is None:
        raise EditError(404, f"unknown {e.label} {entity_id!r}")
    current = dict(_get(row, "row", 0))
    if e.has_geom:
        current["lon"], current["lat"] = float(_get(row, "lon", 1)), float(_get(row, "lat", 2))
    return current


def _get(row: Any, name: str, index: int) -> Any:
    return row[name] if isinstance(row, dict) else row[index]


def _audit(conn: psycopg.Connection, e: Entity, entity_id: str, action: str, lineage: str | None,
           payload: dict[str, Any], actor: str) -> int:
    row = conn.execute(
        "SELECT core.log_event(%s, %s, %s, %s, clock_timestamp(), clock_timestamp(), %s, %s, %s) AS audit_id",
        (e.schema, e.table, entity_id, action, lineage, Jsonb(payload), actor)).fetchone()
    return _get(row, "audit_id", 0)


def _db_error(exc: psycopg.Error) -> EditError:
    message = (exc.diag.message_primary if exc.diag else None) or str(exc)
    constraint = exc.diag.constraint_name if exc.diag else None
    return EditError(409, f"rejected by the database: {message}" + (f" ({constraint})" if constraint else ""))


def update(conn: psycopg.Connection, e: Entity, entity_id: str, changes: dict[str, Any],
           actor: str = "API") -> dict[str, Any]:
    """PATCH: change the given fields; only real differences are written and audited."""
    unknown = sorted(set(changes) - set(e.editable))
    if unknown:
        raise EditError(422, f"not editable on a {e.label}: {', '.join(unknown)}")
    if not changes:
        raise EditError(422, "no fields to change")
    for name, value in changes.items():
        if value is None and name not in e.nullable:
            raise EditError(422, f"{name} cannot be empty")

    with conn.transaction():
        current = _lock(conn, e, entity_id)
        if e is DEVICE and current["current_status"] == "RETIRED":
            raise EditError(409, f"device {entity_id} is retired; a retired device is kept for history and not edited")
        diff = {k: v for k, v in changes.items() if current.get(k) != v}
        if not diff:
            return {"outcome": "UNCHANGED", "entity": e.label, "entity_id": entity_id, "changed": {}}

        params: dict[str, Any] = {"_id": entity_id}
        sets = []
        for name, value in diff.items():
            if name in ("lon", "lat"):
                continue
            sets.append(sql.SQL("{} = {}").format(sql.Identifier(name), sql.Placeholder(name)))
            params[name] = value
        if "lon" in diff or "lat" in diff:
            params["lon"], params["lat"] = diff.get("lon", current["lon"]), diff.get("lat", current["lat"])
            check = validate_coordinate(params["lon"], params["lat"])
            if not check.ok:
                raise EditError(422, f"{check.reason_code}: {check.detail}")
            sets.append(sql.SQL("geom = ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)"))
        try:
            with conn.transaction():
                conn.execute(sql.SQL("UPDATE {table} SET {sets} WHERE {key} = %(_id)s").format(
                    table=_table(e), sets=sql.SQL(", ").join(sets), key=sql.Identifier(e.key)), params)
        except psycopg.errors.IntegrityError as exc:
            raise _db_error(exc) from None

        before = {k: current.get(k) for k in diff}
        audit_id = _audit(conn, e, entity_id, "UPDATE", current.get(e.lineage),
                          {"operation": "PATCH", "before": before, "after": diff}, actor)
    return {"outcome": "UPDATED", "entity": e.label, "entity_id": entity_id,
            "changed": {k: {"from": before[k], "to": v} for k, v in diff.items()}, "audit_id": audit_id}


def delete(conn: psycopg.Connection, e: Entity, entity_id: str, actor: str = "API") -> dict[str, Any]:
    """DELETE: hard delete when unreferenced (places, attractions), cancel (events), retire (devices)."""
    with conn.transaction():
        current = _lock(conn, e, entity_id)
        lineage = current.get(e.lineage)

        if e is EVENT:
            if current["is_cancelled"]:
                return {"outcome": "ALREADY_CANCELLED", "entity": e.label, "entity_id": entity_id}
            conn.execute("UPDATE tourism.events SET is_cancelled = TRUE WHERE event_id = %s", (entity_id,))
            audit_id = _audit(conn, e, entity_id, "STATUS_CHANGE", lineage,
                              {"operation": "DELETE (cancel)", "before": {"is_cancelled": False},
                               "after": {"is_cancelled": True}}, actor)
            return {"outcome": "CANCELLED", "entity": e.label, "entity_id": entity_id, "audit_id": audit_id,
                    "note": "the event is kept and marked cancelled; queries and views leave it out"}

        if e is DEVICE:
            if current["current_status"] == "RETIRED":
                return {"outcome": "ALREADY_RETIRED", "entity": e.label, "entity_id": entity_id}
            conn.execute("UPDATE building.devices SET current_status = 'RETIRED' WHERE device_id = %s", (entity_id,))
            try:
                with conn.transaction():
                    closed = conn.execute(
                        """UPDATE building.device_space_assignments SET unassigned_at = clock_timestamp()
                           WHERE device_id = %s AND unassigned_at IS NULL
                           RETURNING assignment_id, space_id""", (entity_id,)).fetchall()
            except psycopg.errors.IntegrityError as exc:
                raise _db_error(exc) from None
            closed_list = [{"assignment_id": _get(r, "assignment_id", 0), "space_id": _get(r, "space_id", 1)}
                           for r in closed]
            audit_id = _audit(conn, e, entity_id, "STATUS_CHANGE", lineage,
                              {"operation": "DELETE (retire)", "before": {"current_status": current["current_status"]},
                               "after": {"current_status": "RETIRED"}, "closed_assignments": closed_list}, actor)
            return {"outcome": "RETIRED", "entity": e.label, "entity_id": entity_id, "audit_id": audit_id,
                    "closed_assignments": closed_list,
                    "note": "the device and its location history are kept; it no longer counts as installed"}

        blocking = []
        for table, column in e.references:
            schema, name = table.split(".")
            n = _get(conn.execute(sql.SQL("SELECT count(*) AS n FROM {}.{} WHERE {} = %s").format(
                sql.Identifier(schema), sql.Identifier(name), sql.Identifier(column)), (entity_id,)).fetchone(), "n", 0)
            if n:
                blocking.append(f"{n} row(s) in {table}")
        if blocking:
            raise EditError(409, f"{e.label} {entity_id} is still referenced by {', '.join(blocking)}; "
                                 "it cannot be deleted without losing that history")
        try:
            with conn.transaction():
                conn.execute(sql.SQL("DELETE FROM {table} WHERE {key} = %s").format(
                    table=_table(e), key=sql.Identifier(e.key)), (entity_id,))
        except psycopg.errors.IntegrityError as exc:
            raise _db_error(exc) from None
        audit_id = _audit(conn, e, entity_id, "DELETE", lineage,
                          {"operation": "DELETE", "deleted_row": current}, actor)
        return {"outcome": "DELETED", "entity": e.label, "entity_id": entity_id, "audit_id": audit_id,
                "note": "the full row is kept in the audit ledger"}


def run_edit(fn: Callable[..., Any], *args: Any, actor: str | None = "API") -> Any:
    """Run an edit (or a read) on a pooled connection and turn EditError into HTTP errors.

    Edits are committed when the connection is returned to the pool; reads pass actor=None.
    """
    from api.database import pool   # imported here so tests can use this module without the pool
    try:
        with pool.connection() as conn:
            return fn(conn, *args, actor=actor) if actor else fn(conn, *args)
    except EditError as exc:
        raise HTTPException(exc.status, exc.detail) from None
