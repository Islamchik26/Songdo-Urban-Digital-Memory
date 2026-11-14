"""Helpers shared by every fixture section: SQL literals, multi-row INSERTs, the service day."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
SERVICE_DAY = datetime(2026, 10, 2, tzinfo=KST)   # all generated activity happens on this morning
BBOX = (126.600, 37.350, 126.685, 37.425)


class Raw(str):
    """SQL expression that must not be quoted."""


def q(value: object) -> str:
    """SQL literal."""
    if value is None or value == "":
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, datetime):
        return "'" + value.isoformat(timespec="seconds") + "'"
    return "'" + str(value).replace("'", "''") + "'"


def point(lon: float, lat: float) -> Raw:
    return Raw(f"ST_SetSRID(ST_MakePoint({lon:.6f}, {lat:.6f}), 4326)")


def insert(table: str, columns: list[str], rows: list[list[object]], conflict: str = "DO NOTHING",
           chunk: int = 500) -> list[str]:
    out = []
    head = f"INSERT INTO {table} ({', '.join(columns)}) VALUES"
    for i in range(0, len(rows), chunk):
        values = ",\n".join("  (" + ", ".join(v if isinstance(v, Raw) else q(v) for v in row) + ")"
                            for row in rows[i:i + chunk])
        out.append(f"{head}\n{values}\nON CONFLICT {conflict};")
    return out
