"""FastAPI application root. Interactive docs: http://127.0.0.1:8000/docs"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.config import ROOT
from api.database import close_pool, fetch_one, open_pool
from api.routers import core_router, scenario_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    open_pool()
    yield
    close_pool()


app = FastAPI(
    title="Songdo Urban Digital Memory",
    version="0.1.0",
    description="Spatio-temporal data services for Songdo: ingestion (write path) and "
                "indexed query endpoints (read path) over PostgreSQL + PostGIS.",
    lifespan=lifespan,
)

app.include_router(core_router.router)
app.include_router(scenario_router.router)
# Domain routers are added here as each database is delivered:
#   mobility_router, building_router, tourism_router, environment_router, safety_router


@app.get("/health", tags=["system"], summary="Database and PostGIS availability")
def health() -> Any:
    try:
        row = fetch_one(
            """SELECT current_database() AS database,
                      current_setting('server_version') AS postgres,
                      PostGIS_Lib_Version() AS postgis"""
        )
    except Exception as exc:  # database down, PostGIS missing, wrong credentials...
        return JSONResponse(status_code=503, content={"status": "down", "detail": str(exc).strip()})
    return {"status": "ok", **(row or {})}


# Serve the built UI (ui/dist) from the same process when it exists.
_ui_dist = ROOT / "ui" / "dist"
if _ui_dist.is_dir():
    app.mount("/", StaticFiles(directory=_ui_dist, html=True), name="ui")
