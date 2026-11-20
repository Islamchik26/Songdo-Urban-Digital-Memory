"""/api/v1/scenarios — list every task and run one by id (rows, SQL, plan, charts and a headline)."""
from __future__ import annotations

import time
from typing import Any

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.database import pool
from api.scenarios import DOMAINS, SCENARIOS
from api.visuals import describe

router = APIRouter(prefix="/api/v1/scenarios", tags=["scenarios"])

MAX_ROWS = 500


class RunRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)
    explain: bool = Field(False, description="Also return EXPLAIN (ANALYZE, BUFFERS)")


@router.get("", summary="List all scenarios grouped by domain")
def list_scenarios() -> dict[str, Any]:
    return {"domains": DOMAINS, "scenarios": [s.public() for s in SCENARIOS.values()]}


@router.post("/{scenario_id}/run", summary="Run one scenario with validated parameters")
def run_scenario(scenario_id: str, body: RunRequest) -> dict[str, Any]:
    scenario = SCENARIOS.get(scenario_id.upper())
    if scenario is None:
        raise HTTPException(404, f"Unknown scenario {scenario_id!r}")
    if scenario.status != "ready" or not scenario.sql:
        raise HTTPException(501, f"{scenario.id} is planned but not implemented yet")

    unknown = set(body.params) - {p.name for p in scenario.params}
    if unknown:
        raise HTTPException(422, f"Unknown parameter(s): {', '.join(sorted(unknown))}")
    bound: dict[str, Any] = {}
    for p in scenario.params:
        try:
            bound[p.name] = p.coerce(body.params.get(p.name))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    plan: list[str] | None = None
    try:
        with pool.connection() as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            started = time.perf_counter()
            cur = conn.execute(scenario.sql, bound)
            rows = cur.fetchmany(MAX_ROWS)
            truncated = cur.fetchone() is not None
            elapsed_ms = (time.perf_counter() - started) * 1000
            if body.explain:
                plan = [r["QUERY PLAN"] for r in
                        conn.execute("EXPLAIN (ANALYZE, BUFFERS) " + scenario.sql, bound).fetchall()]
    except psycopg.errors.DataError as exc:
        if exc.sqlstate is None:   # raised by the driver, not by the server: a bug on our side, not bad input
            raise
        # the server rejected a value, e.g. a date range whose end is before its start
        raise HTTPException(422, exc.diag.message_primary or "Invalid parameter value") from None

    return {
        "scenario_id": scenario.id,
        "params": bound,
        "sql": scenario.sql.strip(),
        "row_count": len(rows),
        "truncated": truncated,
        "elapsed_ms": round(elapsed_ms, 2),
        "rows": rows,
        "explain": plan,
        **describe(scenario.id, rows, bound),   # "viz": chart specs, "story": headline + key numbers
    }
