"""Shared HTTP access for the downstream consumers.

Consumers never open a database connection. They only use the public API, the
same way an external robot, building system or digital twin would.

    with api_client() as client:
        client.get("/health")

Target: SONGDO_API_URL (default http://127.0.0.1:8000). If no server answers
there, the API is started inside this process instead, so the consumers also
work in a notebook or a test run without `uvicorn` running.
"""
from __future__ import annotations

import atexit
import os
import sys
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DEFAULT_URL = "http://127.0.0.1:8000"
KST = timezone(timedelta(hours=9), "KST")


def api_url() -> str:
    return os.getenv("SONGDO_API_URL", DEFAULT_URL).rstrip("/")


def ingest_headers() -> dict[str, str]:
    """Header required by the write endpoints."""
    return {"X-API-Token": os.getenv("INGEST_API_TOKEN", "songdo-dev-token")}


def parse_time(value: str | datetime) -> datetime:
    """ISO-8601 text or datetime -> aware datetime. A time without offset is read as Korean time."""
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(value.strip())
    return moment if moment.tzinfo else moment.replace(tzinfo=KST)


def _server_is_up(url: str) -> bool:
    try:
        return httpx.get(f"{url}/health", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


@contextmanager
def api_client(base_url: str | None = None, in_process: bool | None = None) -> Iterator[httpx.Client]:
    """Yield an httpx client for the API.

    in_process=None  use the running server if there is one, else start the app in this process
    in_process=True  always in this process;  False  always over the network
    """
    url = (base_url or api_url()).rstrip("/")
    if in_process is None:
        in_process = not _server_is_up(url)
        if in_process:
            print(f"[api_client] no server at {url}; running the API in this process", file=sys.stderr)
    if not in_process:
        with httpx.Client(base_url=url, timeout=10) as client:
            yield client
        return
    yield _in_process_client()


_local_client: httpx.Client | None = None


def _in_process_client() -> httpx.Client:
    """One in-process API per Python process: its connection pool can be opened only once."""
    global _local_client
    if _local_client is None:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from fastapi.testclient import TestClient
        from api.main import app
        context = TestClient(app)
        _local_client = context.__enter__()      # runs startup, i.e. opens the connection pool
        atexit.register(context.__exit__, None, None, None)
    return _local_client


def get_json(client: httpx.Client, path: str, **params: object) -> object:
    """GET that raises a readable error instead of returning an error body."""
    response = client.get(path, params={k: v for k, v in params.items() if v is not None})
    if response.status_code >= 400:
        raise RuntimeError(f"GET {path} -> HTTP {response.status_code}: {response.text[:200]}")
    return response.json()
