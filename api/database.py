"""Psycopg 3 connection pool and bound-parameter SQL helpers.

Every query goes through these helpers with parameters bound by the driver
(%(name)s placeholders). SQL text is never built from user input.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from api.config import settings

Params = Mapping[str, Any] | Sequence[Any] | None

pool = ConnectionPool(
    conninfo=settings.conninfo(),
    min_size=1,
    max_size=8,
    kwargs={"row_factory": dict_row},
    open=False,  # opened by the FastAPI lifespan (or open_pool() in scripts)
)


def open_pool() -> None:
    if pool.closed:
        pool.open(wait=False)


def close_pool() -> None:
    if not pool.closed:
        pool.close()


def fetch_all(sql: str, params: Params = None) -> list[dict[str, Any]]:
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


def fetch_one(sql: str, params: Params = None) -> dict[str, Any] | None:
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchone()
