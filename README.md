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
| Web console (`ui/`): tasks, city map with time slider and live mode, ingestion playground, verification | Done |
| DB1 Mobility (`sql/02`): schema, real stop data, fixtures, 5 scenarios, `/api/v1/mobility/*` | Done |
| DB2 Smart Building (`sql/03`): schema, fixtures, 5 scenarios, `/api/v1/building/*` | Done |
| DB3 Tourism (`sql/04`): schema, fixtures, 5 scenarios, `/api/v1/tourism/*` | Done |
| DB4 Environment (`sql/05`): schema, real AirKorea stations, fixtures, 5 scenarios, `/api/v1/environment/*` | Done |
| DB5 Safety & Incident (`sql/06`): schema, state machine, fixtures, 6 scenarios, `/api/v1/safety/*` | Done |
| Cross-domain federation (`sql/07`): 3 views, 3 materialized views with a logged refresh policy, `/api/v1/cross/*` | Done |
| Consumers: telemetry node, VIP building client, digital-twin replay (`consumers/`) | Done |
| Evidence kit (`scripts/evidence.py` → `outputs/evidence/`): clean build, restore, acceptance, performance, API, browser | Done |
| Live deployment (Neon + Render, see `DEPLOY.md`) | Done |
| LaTeX handbook (`main.tex` → `main.pdf`, XeLaTeX) | Done |

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

**4b. (Optional) Ingest the real bus stop file.** The seed already contains the 513 Songdo
stops (`fixtures/songdo_stops.csv`, extracted from the official file). To run the full
pipeline on the original download, put the CSV in `data/raw/` (see `data/raw/README.md`) and:

```bash
python scripts/ingest_bus_stops.py
```

Of 9,657 Incheon rows it loads 513 Songdo stops and quarantines 9,144 (8,935 outside the
bbox, 209 copies published by a second bus system). A second run changes nothing.

**4c. (Optional) Ingest real AirKorea readings.** The seed contains the 5 real Songdo stations
and generated readings for 2026-10-02. To add real readings through the pipeline:

```bash
python scripts/ingest_airkorea.py --from-fixture   # no key: replays fixtures/airkorea_sample.json
python scripts/ingest_airkorea.py                  # live API: needs AIRKOREA_API_KEY in .env
```

It quarantines the Incheon stations outside the Songdo box and any `-` fault readings, then
refreshes the hourly rollup and the alerts. Note: real, clean readings resolve the alert that the
seed leaves ACTIVE at 아암 station, which is the correct behaviour.

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

The console has five pages:

| Page | What it does |
|---|---|
| **Tasks** | All 36 tasks. Pick one, change its parameters, run it: rows, the SQL that ran, the query plan, and a map for results with coordinates. |
| **City map** | Stops, routes, stations, attractions, buildings, safety facilities. A time slider rebuilds the city at any moment from history: vehicles, incidents in their status at that time, PM2.5 per station, air alerts. **Live** mode refreshes every 2 s. |
| **Ingest** | Forms for a new place, GPS, building sensor, device relocation, a new attraction, air reading, incident report and status change, with good and bad presets. Shows the outcome (loaded, quarantined, duplicate, rejected) and the batch, quarantine and audit rows written. |
| **Edit** | Pick a place, attraction, event or device, load its current values, change any fields and save, or delete / cancel / retire it. Changed fields show their old value; the record's audit history is listed below. |
| **Verify** | 28 live checks (schemas, rows per domain, views, materialized view freshness, ledger triggers, indexes, constraints, quarantine, provenance, backup) and a button that runs the test suite. |

Links can open a page in a given state: `#/tasks?task=DB5-2&run=1&view=map`, `#/map?t=18:20`,
`#/map?live=1`, `#/edit?entity=event&id=EVT_SKYLINE_PHOTO`. The map background comes from OpenStreetMap and needs internet; the data layers
work without it. The write forms and the test button send the token typed in the X-API-Token box
(default `songdo-dev-token`, the value in `.env.example`); change it there if your `.env` sets another.

**7. Downstream consumers.** Three programs that use the platform only through its API, never
through the database. Each one uses the server on `SONGDO_API_URL` (default
`http://127.0.0.1:8000`); if none is running, it starts the API inside its own process.

```bash
python -m consumers.smart_mobility_adapter            # ROS-style node: streams GPS of a shuttle for 20 s
python -m consumers.vip_building_env_client           # indoor state of G-Tower + outdoor air around it
python -m consumers.digital_twin_temporal_replay      # city state every 5 min, 08:00-08:30 on the fixture day
```

Add `--help` to any of them for the options. The telemetry node writes real rows stamped with the
current time (the fixture day is not touched); `python scripts/setup_db.py --reset` removes them.

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
- **Corrections and deletes** (`api/edits.py`): `PATCH` / `DELETE` on `/api/v1/core/places/{id}`,
  `/api/v1/tourism/attractions/{id}`, `/api/v1/tourism/events/{id}` and `/api/v1/building/devices/{id}`.
  Each change is audited with old and new values. A place or attraction is deleted only when nothing refers
  to it (otherwise 409); deleting an event cancels it and deleting a device retires it, so history is kept.
  History tables (GPS, telemetry, measurements) are never edited in place.
- **Local stack differs from the handbook** (PostgreSQL 18 / PostGIS 3.7 here, 16.3 / 3.4 in the
  handbook). Avoid features newer than PostgreSQL 16. `compose.yaml` and `Dockerfile` follow the
  handbook but have not been run yet.
- **What is real in DB1:** stop IDs, names and coordinates (source `SRC_MOB_03`). Route patterns,
  trips, arrivals and GPS telemetry are generated (`SRC_MOB_SYN`): the "Route 8" pattern is a
  corridor snapped to real stops, not the real Route 8.
- Landmark coordinates in the seed data are approximate and typed by hand (`SRC_CORE_01`).
  Verification levels in `source_registry.csv` are copied from the handbook and still need to
  be re-verified by the team.

## Evidence kit

```bash
python scripts/evidence.py              # about 3-5 minutes; --no-browser skips the Chrome checks
```

The script builds a new, disposable database `songdo_evidence` from this repository (`psql -v
ON_ERROR_STOP=1` for `sql/00`–`07` and the seed), then proves the platform on it and writes the
results to `outputs/evidence/` (start with its `README.md`):

- **clean build** log and schema inventory, **versions** of every component and the git commit;
- **backup and restore**: `pg_dump -Fc` → `pg_restore --exit-on-error`, then rows, objects, task
  results and constraint behaviour compared between the two databases;
- the **test suite** on the clean database;
- an **acceptance matrix**: one executed check per scenario, written against the midterm
  feedback's "Required acceptance" column, plus two concurrency races;
- **performance**: `EXPLAIN (ANALYZE, BUFFERS)` and timings of every task, index versus no-index;
- **API** contract cases (422 / 401 / 409, retries, 12 concurrent identical requests) and latency;
- the **web console**: every task opened in headless Chrome, rows read from the page and compared
  with the API, plus screenshots.

Your working database is never touched; the two evidence databases are dropped at the end
(`--keep` keeps them). The backup file goes to `outputs/` and is not committed.

## ER diagrams

```bash
python scripts/erd.py              # reads the live catalog -> docs/erd/ (Markdown, Mermaid, PDF)
```

[`docs/erd/README.md`](docs/erd/README.md) has a schema map, one crow's-foot ER diagram per schema
(every table, column, type, PK/FK/unique key and cardinality), the lineage of the cross-domain views,
and a table of every key, exclusion constraint and trigger. GitHub renders the diagrams directly; the
PDFs are used by the handbook. Rerun the script after any schema change. Rendering the PDFs needs
Chrome or Edge and internet access (mermaid.js from a CDN); `--no-render` skips them.

## Handbook

`main.tex` is the project handbook: architecture, sources, every scenario, API, consumers,
console, evidence, performance, recovery, deployment, limitations and defense questions. It
compiles with **XeLaTeX** (on Overleaf: Menu → Compiler → XeLaTeX) and uses only TeX Live
packages. To include the screenshots, upload `outputs/evidence/screenshots/` with the same
folder structure; without them the figures show a placeholder. Compile twice and commit
`main.pdf` next to it.

## Backup and restore

```bash
pg_dump -h localhost -p 5433 -U songdo_admin -d songdo_urban_memory -Fc -f outputs/songdo.backup
createdb -h localhost -p 5433 -U songdo_admin songdo_restore_test
pg_restore -h localhost -p 5433 -U songdo_admin -d songdo_restore_test --exit-on-error outputs/songdo.backup
```
