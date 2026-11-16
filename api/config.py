"""Central settings, read once from .env / environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    db_host: str = os.getenv("DB_HOST", "127.0.0.1")
    db_port: int = int(os.getenv("DB_PORT", "5432"))
    db_name: str = os.getenv("DB_NAME", "songdo_urban_memory")
    db_user: str = os.getenv("DB_USER", "songdo_admin")
    db_password: str = os.getenv("DB_PASSWORD", "")
    ingest_api_token: str = os.getenv("INGEST_API_TOKEN", "songdo-dev-token")
    min_lon: float = float(os.getenv("SONGDO_MIN_LON", "126.600"))
    max_lon: float = float(os.getenv("SONGDO_MAX_LON", "126.685"))
    min_lat: float = float(os.getenv("SONGDO_MIN_LAT", "37.350"))
    max_lat: float = float(os.getenv("SONGDO_MAX_LAT", "37.425"))

    # A full URL (e.g. from Neon: postgresql://user:pass@host/db?sslmode=require) overrides DB_*.
    database_url: str = os.getenv("DATABASE_URL", "")

    def conninfo(self, dbname: str | None = None) -> str:
        if self.database_url:
            extra = {"dbname": dbname} if dbname else {}
            return make_conninfo(self.database_url, connect_timeout=10, **extra)
        return make_conninfo(
            host=self.db_host,
            port=self.db_port,
            dbname=dbname or self.db_name,
            user=self.db_user,
            password=self.db_password,
            connect_timeout=5,
        )


settings = Settings()
