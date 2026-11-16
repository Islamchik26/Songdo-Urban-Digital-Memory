"""Build the database from a clean clone: migrations in order, then seed data.

    python scripts/setup_db.py            apply sql/*.sql in order + load fixtures/seed_data.sql
    python scripts/setup_db.py --reset    drop all project schemas first (destroys data)
    python scripts/setup_db.py --no-seed  migrations only

Works the same on Windows / macOS / Linux; psql is not required.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.config import settings  # noqa: E402

SCHEMAS = ["cross_domain", "safety", "environment", "tourism", "building", "mobility", "core"]


def run_file(conn: psycopg.Connection, path: Path) -> None:
    started = time.perf_counter()
    try:
        conn.execute(path.read_text(encoding="utf-8"))  # no params -> multi-statement script is allowed
        conn.commit()
    except psycopg.Error as exc:
        conn.rollback()
        print(f"  FAILED  {path.relative_to(ROOT)}\n          {str(exc).strip()}")
        raise SystemExit(1) from None
    print(f"  ok      {path.relative_to(ROOT)}  ({(time.perf_counter() - started) * 1000:.0f} ms)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="drop all project schemas first")
    parser.add_argument("--no-seed", action="store_true", help="skip fixtures/seed_data.sql")
    args = parser.parse_args()

    target = conninfo_to_dict(settings.conninfo())   # the real target, also when DATABASE_URL is set
    print(f"Database {target.get('dbname')} on {target.get('host')}:{target.get('port', 5432)} as {target.get('user')}")
    try:
        conn = psycopg.connect(settings.conninfo())
    except psycopg.OperationalError as exc:
        raise SystemExit(f"Cannot connect: {str(exc).strip()}\nCheck DB_* values in .env") from None

    with conn:
        if args.reset:
            for schema in SCHEMAS:
                conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            conn.commit()
            print("  reset   dropped schemas: " + ", ".join(SCHEMAS))

        migrations = sorted((ROOT / "sql").glob("[0-9][0-9]_*.sql"))
        if not migrations:
            raise SystemExit("No migration files found in sql/")
        for path in migrations:
            run_file(conn, path)

        seed = ROOT / "fixtures" / "seed_data.sql"
        if not args.no_seed:
            if seed.exists():
                run_file(conn, seed)
            else:
                print("  skip    fixtures/seed_data.sql not found (run: python fixtures/seed_fixtures.py)")

        row = conn.execute(
            """SELECT PostGIS_Lib_Version(),
                      (SELECT count(*) FROM core.source_registry),
                      (SELECT count(*) FROM core.places)"""
        ).fetchone()
        print(f"Done. PostGIS {row[0]}, {row[1]} sources, {row[2]} places")


if __name__ == "__main__":
    main()
