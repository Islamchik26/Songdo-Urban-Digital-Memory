# Songdo Urban Digital Memory

Database Management and Practice (Inha University) — a spatio-temporal data platform for
Songdo on PostgreSQL + PostGIS, with a FastAPI service and a web console that can run every
project task.

## What works today

| Area | Status |
|---|---|
| Core schema (`sql/00`, `sql/01`): source registry, places, staging, quarantine, audit ledger | Done |
| Ingestion pipeline: validate → SHA-256 dedup → quarantine → load → audit | Done (core places) |
| API: `/health`, `/api/v1/core/*`, `/api/v1/scenarios` | Done |
| Web console: pick a task, set parameters, run it, see rows + SQL + query plan | Done (shell) |
| DB1–DB5 domain schemas and their 25 scenarios | Registered as *planned* |
| Cross-domain views, consumers, LaTeX handbook | Not started |

## Setup (native PostgreSQL, no Docker)

You need PostgreSQL 16+ with PostGIS 3.4+, Python 3.10+, and Node 20+ (for the UI only).

**1. Install PostGIS.** Windows: Start → *Application Stack Builder* → Spatial Extensions →
PostGIS bundle. macOS: `brew install postgresql@16 postgis`.

**2. Create the role and database** (once, in pgAdmin's Query Tool as `postgres`):

```sql
CREATE ROLE songdo_admin LOGIN SUPERUSER PASSWORD 'songdo_local';
CREATE DATABASE songdo_urban_memory OWNER songdo_admin;
```

`songdo_admin` is a superuser because it enables PostGIS and creates roles, the same as in
the handbook's Docker setup. This is for local development only.

**3. Configure and install.**

```bash
cp .env.example .env          # then set DB_PORT to your server's port (5432 or 5433)
python -m venv .venv
.venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

**4. Build the database.**

```bash
python fixtures/seed_fixtures.py 1000   # regenerates fixtures/seed_data.sql (deterministic)
python scripts/setup_db.py              # runs sql/*.sql in order, then loads the seed data
```

Use `python scripts/setup_db.py --reset` to drop every project schema and rebuild from zero.

**5. Run.**

```bash
uvicorn api.main:app --port 8000 --reload     # API docs: http://127.0.0.1:8000/docs
python -m unittest tests/test_platform.py     # test suite
```

**6. Web console.**

```bash
cd ui
npm install
npm run dev        # http://localhost:5173 (needs the API running on :8000)
```

`npm run build` writes `ui/dist`; when that folder exists, FastAPI serves the console at
`http://127.0.0.1:8000/` so the whole demo runs from one process.

## How to deliver a scenario

1. Add tables to your domain file (`sql/02_db1_mobility.sql` … `sql/06_db5_safety.sql`).
2. Add fixture rows in `fixtures/seed_fixtures.py` (draw randomness only from the seeded `rng`).
3. Subclass `BaseAdapter` (see `adapters/core_adapter.py`) for ingestion.
4. In `api/scenarios.py`, give the scenario its `sql` and `params` and set `status="ready"`.
   It then appears as runnable in the console and in `/docs` with no UI work.
5. Add tests to `tests/test_platform.py`.

## Things to know

- **Radius queries need the geography index.** `ST_DWithin(geom::geography, …)` only uses a
  GiST index built on the same expression: `USING GIST ((geom::geography))`. Every table with
  a `geom` column needs both indexes (see `core.places`).
- **Schema-qualify PostGIS inside SQL functions** (`public.ST_Covers`), otherwise `pg_restore`
  fails, because it runs with an empty `search_path`.
- **Scenario SQL is read-only.** It runs in a `READ ONLY` transaction with bound parameters.
- **Write endpoints need a token**: header `X-API-Token` (value of `INGEST_API_TOKEN` in `.env`).
- **Local stack differs from the handbook** (PostgreSQL 18 / PostGIS 3.7 here, 16.3 / 3.4 in the
  handbook). Avoid features newer than PostgreSQL 16. `compose.yaml` and `Dockerfile` follow the
  handbook but have not been run yet.
- Landmark coordinates in the seed data are approximate and typed by hand (`SRC_CORE_01`).
  Verification levels in `source_registry.csv` are copied from the handbook and still need to
  be re-verified by the team.

## Backup and restore

```bash
pg_dump -h localhost -p 5433 -U songdo_admin -d songdo_urban_memory -Fc -f outputs/songdo.backup
createdb -h localhost -p 5433 -U songdo_admin songdo_restore_test
pg_restore -h localhost -p 5433 -U songdo_admin -d songdo_restore_test --exit-on-error outputs/songdo.backup
```
