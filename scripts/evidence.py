"""Build the reproducibility and engineering evidence kit in outputs/evidence/.

    python scripts/evidence.py                    everything (about 3-5 minutes)
    python scripts/evidence.py --no-browser       skip the headless-browser checks and screenshots
    python scripts/evidence.py --keep             keep the two evidence databases afterwards

Everything runs against a DISPOSABLE database built from the repository, never against your
working database:

  1  environment      engine, extension, tool and package versions, git commit
  2  clean build      CREATE DATABASE songdo_evidence; psql -v ON_ERROR_STOP=1 -f sql/00..07 + seed
  3  backup/restore   pg_dump -Fc -> pg_restore --exit-on-error into songdo_evidence_restore,
                      then compare row counts, schema objects, task results and triggers
  4  tests            python -m unittest tests/test_platform.py against the clean database
  5  acceptance       one executed proof per scenario (scripts/evidence_acceptance.py)
  6  performance      EXPLAIN (ANALYZE, BUFFERS) + timings for every task; index on/off comparison
  7  API              contract (422/401/409, idempotency, concurrent duplicates), latency
  8  browser          every task opened in headless Chrome through the UI, rows read from the DOM
  9  report           outputs/evidence/README.md + verification_summary.csv

Requires psql, pg_dump and pg_restore on PATH (they come with PostgreSQL).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

EVIDENCE_DB = "songdo_evidence"
RESTORE_DB = "songdo_evidence_restore"
# Point every project module (api.config.settings) at the disposable database BEFORE importing it.
os.environ["DB_NAME"] = EVIDENCE_DB

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import httpx  # noqa: E402
import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from api.config import settings  # noqa: E402
from api.scenarios import SCENARIOS  # noqa: E402

OUT = ROOT / "outputs" / "evidence"
BACKUP = ROOT / "outputs" / "songdo_evidence.backup"
SCHEMAS = ["core", "mobility", "building", "tourism", "environment", "safety", "cross_domain"]
MIGRATIONS = sorted((ROOT / "sql").glob("[0-9][0-9]_*.sql"))
SEED = ROOT / "fixtures" / "seed_data.sql"

REPORT: dict[str, Any] = {}       # collected results for README.md


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def say(message: str) -> None:
    print(message, flush=True)


def write_csv(name: str, rows: list[dict[str, Any]]) -> None:
    path = OUT / name
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def pg_env() -> dict[str, str]:
    return {**os.environ, "PGPASSWORD": settings.db_password, "PGHOST": settings.db_host,
            "PGPORT": str(settings.db_port), "PGUSER": settings.db_user, "PGCLIENTENCODING": "UTF8"}


def tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise SystemExit(f"{name} not found on PATH (add PostgreSQL's bin folder, see docs/Setup_Guide_Short.docx)")
    return path


def connect(dbname: str = EVIDENCE_DB, **kw: Any) -> psycopg.Connection:
    return psycopg.connect(settings.conninfo(dbname), **kw)


def recreate_database(name: str) -> None:
    with connect("postgres", autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{name}" ENCODING \'UTF8\' TEMPLATE template0')


def drop_database(name: str) -> None:
    with connect("postgres", autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def run_task(conn: psycopg.Connection, scenario_id: str) -> list[dict[str, Any]]:
    s = SCENARIOS[scenario_id]
    params = {p.name: p.coerce(p.default) for p in s.params}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(s.sql, params)
        return cur.fetchall()


def digest(rows: Any) -> str:
    return hashlib.sha256(json.dumps(rows, default=str, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 1 environment
# ---------------------------------------------------------------------------
def step_environment() -> None:
    say("1/9 environment")

    def cmd(*args: str) -> str:
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=20).stdout.strip().splitlines()[0]
        except (OSError, IndexError, subprocess.SubprocessError):
            return "not found"

    with connect("postgres") as conn:
        server = conn.execute("SELECT version()").fetchone()[0]
    with connect(EVIDENCE_DB) as conn:
        postgis = conn.execute("SELECT PostGIS_Full_Version()").fetchone()[0]
    packages = {p: metadata.version(p) for p in
                ("fastapi", "uvicorn", "pydantic", "psycopg", "psycopg-pool", "python-dotenv", "httpx")}
    ui = json.loads((ROOT / "ui" / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "ui" / "package-lock.json").read_text(encoding="utf-8")).get("packages", {})
    ui_versions = {name: lock.get(f"node_modules/{name}", {}).get("version", spec)
                   for name, spec in {**ui.get("dependencies", {}), **ui.get("devDependencies", {})}.items()}
    commit = cmd("git", "-C", str(ROOT), "rev-parse", "--short", "HEAD")
    # the evidence folder itself is being rewritten by this run, so it does not count as a change
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", ".", ":!outputs/evidence"],
                           capture_output=True, text=True).stdout
    env = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "git_commit": commit + (" (with uncommitted changes)" if dirty.strip() else ""),
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "python": platform.python_version(),
        "postgresql_server": server,
        "postgis": postgis,
        "psql": cmd(tool("psql"), "--version"),
        "pg_dump": cmd(tool("pg_dump"), "--version"),
        "pg_restore": cmd(tool("pg_restore"), "--version"),
        "node": cmd(shutil.which("node") or "node", "--version"),
        "python_packages": packages,
        "ui_packages": ui_versions,
        "database": f"{EVIDENCE_DB} (disposable, built by this script)",
    }
    lines = []
    for key, value in env.items():
        if isinstance(value, dict):
            lines.append(f"{key}:")
            lines += [f"  {k} {v}" for k, v in value.items()]
        else:
            lines.append(f"{key}: {value}")
    (OUT / "environment.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    REPORT["environment"] = env


# ---------------------------------------------------------------------------
# 2 clean build with psql ON_ERROR_STOP
# ---------------------------------------------------------------------------
def inventory(conn: psycopg.Connection) -> dict[str, Any]:
    """Row count of every table plus counts of every kind of schema object."""
    tables = conn.execute(
        """SELECT table_schema || '.' || table_name FROM information_schema.tables
           WHERE table_schema = ANY(%s) AND table_type = 'BASE TABLE' ORDER BY 1""", (SCHEMAS,)).fetchall()
    rows = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for (t,) in tables}  # names from catalog
    objects = conn.execute(
        """SELECT
             (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = ANY(%(s)s) AND c.relkind = 'r') AS tables,
             (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = ANY(%(s)s) AND c.relkind = 'v') AS views,
             (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = ANY(%(s)s) AND c.relkind = 'm') AS materialized_views,
             (SELECT count(*) FROM pg_indexes WHERE schemaname = ANY(%(s)s)) AS indexes,
             (SELECT count(*) FROM pg_indexes WHERE schemaname = ANY(%(s)s) AND indexdef ILIKE '%%using gist%%') AS gist_indexes,
             (SELECT count(*) FROM pg_constraint k JOIN pg_namespace n ON n.oid = k.connamespace
               WHERE n.nspname = ANY(%(s)s) AND k.contype = 'p') AS primary_keys,
             (SELECT count(*) FROM pg_constraint k JOIN pg_namespace n ON n.oid = k.connamespace
               WHERE n.nspname = ANY(%(s)s) AND k.contype = 'f') AS foreign_keys,
             (SELECT count(*) FROM pg_constraint k JOIN pg_namespace n ON n.oid = k.connamespace
               WHERE n.nspname = ANY(%(s)s) AND k.contype = 'u') AS unique_constraints,
             (SELECT count(*) FROM pg_constraint k JOIN pg_namespace n ON n.oid = k.connamespace
               WHERE n.nspname = ANY(%(s)s) AND k.contype = 'c') AS check_constraints,
             (SELECT count(*) FROM pg_constraint k JOIN pg_namespace n ON n.oid = k.connamespace
               WHERE n.nspname = ANY(%(s)s) AND k.contype = 'x') AS exclusion_constraints,
             (SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
               JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = ANY(%(s)s) AND NOT t.tgisinternal) AS triggers,
             (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
               WHERE n.nspname = ANY(%(s)s)) AS functions,
             (SELECT count(*) FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
               JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = ANY(%(s)s) AND a.attgenerated = 's') AS generated_columns""",
        {"s": SCHEMAS}).fetchone()
    names = ["tables", "views", "materialized_views", "indexes", "gist_indexes", "primary_keys", "foreign_keys",
             "unique_constraints", "check_constraints", "exclusion_constraints", "triggers", "functions",
             "generated_columns"]
    return {"rows": rows, "objects": dict(zip(names, objects))}


def step_clean_build() -> None:
    say("2/9 clean build: CREATE DATABASE + psql -v ON_ERROR_STOP=1")
    recreate_database(EVIDENCE_DB)
    log = [f"# Clean build of {EVIDENCE_DB} from the repository", f"# {datetime.now().astimezone():%Y-%m-%d %H:%M:%S %z}",
           f"# command per file: psql -X -q -v ON_ERROR_STOP=1 -d {EVIDENCE_DB} -f <file>", ""]
    steps = []
    total = time.perf_counter()
    for path in [*MIGRATIONS, SEED]:
        started = time.perf_counter()
        proc = subprocess.run([tool("psql"), "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", EVIDENCE_DB, "-f", str(path)],
                              env=pg_env(), capture_output=True, text=True, encoding="utf-8", errors="replace")
        ms = (time.perf_counter() - started) * 1000
        rel = path.relative_to(ROOT).as_posix()
        steps.append({"file": rel, "exit_code": proc.returncode, "ms": round(ms),
                      "bytes": path.stat().st_size, "stderr_lines": len(proc.stderr.strip().splitlines())})
        log.append(f"{'ok  ' if proc.returncode == 0 else 'FAIL'} {rel:<36} exit {proc.returncode}  {ms:7.0f} ms")
        for line in (proc.stdout + proc.stderr).strip().splitlines()[:20]:
            log.append(f"      | {line}")
        if proc.returncode != 0:
            (OUT / "clean_build.log").write_text("\n".join(log) + "\n", encoding="utf-8")
            raise SystemExit(f"clean build failed at {rel}; see outputs/evidence/clean_build.log")
    with connect() as conn:
        inv = inventory(conn)
    log += ["", f"total {(time.perf_counter() - total):.1f} s", "", "schema objects:"]
    log += [f"  {k:<22} {v}" for k, v in inv["objects"].items()]
    log += ["", "rows per table:"] + [f"  {t:<46} {n:>8,}" for t, n in inv["rows"].items()]
    (OUT / "clean_build.log").write_text("\n".join(log) + "\n", encoding="utf-8")
    write_csv("schema_inventory.csv",
              [{"kind": "object", "name": k, "count": v} for k, v in inv["objects"].items()] +
              [{"kind": "table_rows", "name": t, "count": n} for t, n in inv["rows"].items()])
    REPORT["build"] = {"steps": steps, "inventory": inv, "seconds": round(time.perf_counter() - total, 1)}


# ---------------------------------------------------------------------------
# 3 backup and restore
# ---------------------------------------------------------------------------
TASKS_TO_COMPARE = ["DB1-3", "DB2-5", "DB3-4", "DB4-4", "DB5-4", "DB5-5", "X-1", "X-3"]


def step_backup_restore() -> None:
    say("3/9 backup (pg_dump -Fc) and restore (pg_restore --exit-on-error)")
    BACKUP.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    dump = subprocess.run([tool("pg_dump"), "-d", EVIDENCE_DB, "-Fc", "-f", str(BACKUP)],
                          env=pg_env(), capture_output=True, text=True, encoding="utf-8", errors="replace")
    dump_s = time.perf_counter() - started
    if dump.returncode:
        raise SystemExit(f"pg_dump failed: {dump.stderr}")
    recreate_database(RESTORE_DB)
    started = time.perf_counter()
    restore = subprocess.run([tool("pg_restore"), "--exit-on-error", "-d", RESTORE_DB, str(BACKUP)],
                             env=pg_env(), capture_output=True, text=True, encoding="utf-8", errors="replace")
    restore_s = time.perf_counter() - started
    if restore.returncode:
        raise SystemExit(f"pg_restore failed: {restore.stderr}")

    with connect(EVIDENCE_DB) as a, connect(RESTORE_DB) as b:
        inv_a, inv_b = inventory(a), inventory(b)
        rows = []
        for name in inv_a["objects"]:
            rows.append({"item": f"objects.{name}", "original": inv_a["objects"][name],
                         "restored": inv_b["objects"].get(name), "match": inv_a["objects"][name] == inv_b["objects"].get(name)})
        for table, n in inv_a["rows"].items():
            rows.append({"item": f"rows.{table}", "original": n, "restored": inv_b["rows"].get(table),
                         "match": n == inv_b["rows"].get(table)})
        for task_id in TASKS_TO_COMPARE:
            da, db = digest(run_task(a, task_id)), digest(run_task(b, task_id))
            rows.append({"item": f"task.{task_id} result sha256", "original": da, "restored": db, "match": da == db})
        # behaviour survives the restore: the ledger still refuses updates, materialized views refresh
        def ledger_behaviour(conn: psycopg.Connection) -> str:
            conn.execute("""SELECT core.log_event('core', 'places', 'EVD_RESTORE', 'INSERT', now(), now(),
                                                'SRC_CORE_01', '{}'::jsonb)""")
            try:
                with conn.transaction():
                    conn.execute("UPDATE core.event_audit_log SET actor = 'x' WHERE entity_id = 'EVD_RESTORE'")
                result = "UPDATE accepted (trigger missing)"
            except psycopg.Error as exc:
                result = f"UPDATE rejected ({exc.sqlstate})"
            conn.rollback()
            return result
        la, lb = ledger_behaviour(a), ledger_behaviour(b)
        rows.append({"item": "behaviour.audit ledger append-only", "original": la, "restored": lb,
                     "match": la == lb == "UPDATE rejected (23001)"})
        refreshed = b.execute("SELECT count(*) FROM cross_domain.refresh_materialized_views()").fetchone()[0]
        b.commit()
        rows.append({"item": "behaviour.materialized views refresh", "original": 3, "restored": refreshed,
                     "match": refreshed == 3})
        try:
            with b.transaction():
                b.execute("""INSERT INTO building.device_space_assignments (device_id, space_id, assigned_at)
                             VALUES ('DEV-GT-TMP-01', 'SP-GT-F12-R2', '2026-10-01T00:00:00+09:00')""")
            excl = "overlap accepted"
        except psycopg.Error as exc:
            excl = f"overlap rejected ({exc.sqlstate})"
        b.rollback()
        rows.append({"item": "behaviour.exclusion constraint", "original": "overlap rejected (23P01)",
                     "restored": excl, "match": excl == "overlap rejected (23P01)"})
    write_csv("backup_restore.csv", rows)
    matched = sum(1 for r in rows if r["match"])
    log = [f"pg_dump -Fc -d {EVIDENCE_DB} -f outputs/{BACKUP.name}   exit {dump.returncode}  {dump_s:.1f} s  "
           f"{BACKUP.stat().st_size / 1e6:.1f} MB",
           f"pg_restore --exit-on-error -d {RESTORE_DB}            exit {restore.returncode}  {restore_s:.1f} s",
           f"comparison: {matched} of {len(rows)} items identical (see backup_restore.csv)"]
    (OUT / "backup_restore.log").write_text("\n".join(log) + "\n", encoding="utf-8")
    REPORT["restore"] = {"matched": matched, "total": len(rows), "dump_s": round(dump_s, 1),
                         "restore_s": round(restore_s, 1), "size_mb": round(BACKUP.stat().st_size / 1e6, 1),
                         "mismatches": [r["item"] for r in rows if not r["match"]]}


# ---------------------------------------------------------------------------
# 4 tests
# ---------------------------------------------------------------------------
def step_tests() -> None:
    say("4/9 test suite")
    started = time.perf_counter()
    proc = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest", "-v", "tests/test_platform.py"],
                          cwd=ROOT, env={**os.environ, "DB_NAME": EVIDENCE_DB}, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600)
    output = (proc.stdout + proc.stderr).strip()
    header = [f"command: python -m unittest -v tests/test_platform.py", f"database: {EVIDENCE_DB} (clean build)",
              f"exit code: {proc.returncode}", f"wall time: {time.perf_counter() - started:.1f} s", ""]
    (OUT / "test_results.txt").write_text("\n".join(header) + output + "\n", encoding="utf-8")
    ran = re.search(r"Ran (\d+) tests? in ([\d.]+)s", output)
    REPORT["tests"] = {"passed": proc.returncode == 0, "count": int(ran.group(1)) if ran else 0,
                       "seconds": float(ran.group(2)) if ran else None, "last_line": output.splitlines()[-1]}


# ---------------------------------------------------------------------------
# 5 acceptance
# ---------------------------------------------------------------------------
def step_acceptance() -> None:
    say("5/9 acceptance checks")
    from evidence_acceptance import run_all
    results = run_all(include_commits=True)
    write_csv("acceptance_matrix.csv", [{"id": r.id, "scenario": r.scenario, "required_acceptance": r.criterion,
                                         "status": r.status, "evidence": r.evidence, "ms": r.ms} for r in results])
    REPORT["acceptance"] = results


# ---------------------------------------------------------------------------
# 6 performance
# ---------------------------------------------------------------------------
INDEX_RE = re.compile(r"(?:Index(?: Only)? Scan(?: Backward)? using|Bitmap Index Scan on) (\w+)")
SEQ_RE = re.compile(r"Seq Scan on (\S+)")

COMPARISONS = [
    ("Stops within 1000 m (starter notebook, module 19)", "mobility.stops, 513 rows",
     """SELECT stop_id FROM mobility.stops
        WHERE ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint(126.646, 37.388), 4326)::geography, 1000)"""),
    ("GPS points within 150 m of a point", "mobility.vehicle_observations",
     """SELECT count(*) FROM mobility.vehicle_observations
        WHERE ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint(126.6435, 37.3880), 4326)::geography, 150)"""),
    ("Each vehicle's last position at T (point-in-time replay)", "mobility.vehicle_observations",
     """SELECT v.vehicle_id, o.observed_at FROM mobility.vehicles v
        CROSS JOIN LATERAL (SELECT observed_at FROM mobility.vehicle_observations x
                            WHERE x.vehicle_id = v.vehicle_id AND x.observed_at <= '2026-10-02T08:20:00+09:00'
                            ORDER BY x.observed_at DESC LIMIT 1) o"""),
    ("Audit history of one entity", "core.event_audit_log",
     """SELECT * FROM core.event_audit_log WHERE domain_schema = 'safety' AND table_name = 'incidents'
        AND entity_id = 'INC-20261002-0003' ORDER BY audit_id"""),
    ("Status history of one incident", "safety.incident_status_history",
     """SELECT * FROM safety.incident_status_history WHERE incident_id = 'INC-20261002-0003' ORDER BY changed_at"""),
]


def _time(conn: psycopg.Connection, sql: str, params: Any, runs: int) -> list[float]:
    times = []
    for _ in range(runs):
        started = time.perf_counter()
        conn.execute(sql, params).fetchall()
        times.append((time.perf_counter() - started) * 1000)
    return times


def _explain(conn: psycopg.Connection, sql: str, params: Any) -> list[str]:
    return [r[0] for r in conn.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql, params).fetchall()]


SCALE_ROWS = 1_000_000
SCALE_QUERIES = [
    ("Radius search: GPS fixes within 150 m of a point",
     """SELECT count(*) FROM bench.vehicle_observations
        WHERE ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint(126.6435, 37.3880), 4326)::geography, 150)"""),
    ("Point-in-time replay: last fix of 50 vehicles at T",
     """SELECT v, o.observed_at FROM generate_series(0, 49) v
        CROSS JOIN LATERAL (SELECT observed_at FROM bench.vehicle_observations x
                            WHERE x.vehicle_id = v AND x.observed_at <= '2026-10-02T12:00:00+09:00'
                            ORDER BY x.observed_at DESC LIMIT 1) o"""),
    ("Time window: fixes in a 5-minute interval",
     """SELECT count(*) FROM bench.vehicle_observations
        WHERE observed_at >= '2026-10-02T12:00:00+09:00' AND observed_at < '2026-10-02T12:05:00+09:00'"""),
]


def scale_benchmark(conn: psycopg.Connection, plans: list[str]) -> list[dict[str, Any]]:
    """Same table shape and indexes as mobility.vehicle_observations, 1,000,000 synthetic rows
    (500 vehicles x 2,000 fixes, 30 s apart, random positions in the Songdo box, fixed seed)."""
    started = time.perf_counter()
    conn.execute("DROP SCHEMA IF EXISTS bench CASCADE; CREATE SCHEMA bench")
    conn.execute("CREATE TABLE bench.vehicle_observations (vehicle_id int NOT NULL, observed_at timestamptz NOT NULL, "
                 "geom public.geometry(Point, 4326) NOT NULL)")
    conn.execute("SELECT setseed(0.2026)")
    conn.execute(f"""INSERT INTO bench.vehicle_observations
                     SELECT g % 500, '2026-10-02T06:00:00+09:00'::timestamptz + (g / 500) * interval '30 seconds',
                            ST_SetSRID(ST_MakePoint(126.600 + random() * 0.085, 37.350 + random() * 0.075), 4326)
                     FROM generate_series(0, {SCALE_ROWS - 1}) g""")
    conn.execute("CREATE INDEX ON bench.vehicle_observations USING GIST ((geom::public.geography))")
    conn.execute("CREATE UNIQUE INDEX ON bench.vehicle_observations (vehicle_id, observed_at)")
    conn.execute("CREATE INDEX ON bench.vehicle_observations (observed_at)")
    conn.execute("ANALYZE bench.vehicle_observations")
    conn.commit()
    build_s = time.perf_counter() - started
    results = []
    plans += [f"## Scale benchmark · {SCALE_ROWS:,} synthetic GPS rows", "",
              f"Table and indexes built in {build_s:.1f} s. Same column types and index definitions as "
              "mobility.vehicle_observations.", ""]
    for title, sql in SCALE_QUERIES:
        conn.execute(sql).fetchall()
        with_idx = statistics.median(_time(conn, sql, None, 5))
        plan_idx = _explain(conn, sql, None)
        conn.execute("SET enable_indexscan = off; SET enable_bitmapscan = off; SET enable_indexonlyscan = off")
        without = statistics.median(_time(conn, sql, None, 3))
        plan_seq = _explain(conn, sql, None)
        conn.execute("RESET enable_indexscan; RESET enable_bitmapscan; RESET enable_indexonlyscan")
        conn.rollback()
        text = "\n".join(plan_idx)
        results.append({"query": title, "rows_in_table": SCALE_ROWS,
                        "index": " ".join(sorted(set(INDEX_RE.findall(text)))) or "none",
                        "median_ms_with_index": round(with_idx, 2), "median_ms_without_index": round(without, 1),
                        "speedup": round(without / with_idx) if with_idx else None})
        plans += [f"### {title}", "", "With indexes:", "", "```", text, "```", "", "Indexes disabled:", "", "```",
                  "\n".join(plan_seq), "```", ""]
    conn.execute("DROP SCHEMA bench CASCADE")
    conn.commit()
    REPORT["scale_build_s"] = round(build_s, 1)
    return results


def step_performance() -> None:
    say("6/9 performance: EXPLAIN (ANALYZE, BUFFERS) for every task, index on/off comparison")
    rows, plans = [], ["# Query plans of every task", "",
                       f"EXPLAIN (ANALYZE, BUFFERS) on `{EVIDENCE_DB}`, default parameters, after one warm-up run.", ""]
    # prepare_threshold=None: psycopg would otherwise prepare a query after 5 runs and keep reusing the plan
    # made while indexes were enabled, so the "indexes disabled" timings would silently still use them.
    with connect(prepare_threshold=None) as conn:
        conn.execute("ANALYZE")
        conn.commit()
        for s in SCENARIOS.values():
            if s.status != "ready" or not s.sql:
                continue
            params = {p.name: p.coerce(p.default) for p in s.params}
            n_rows = len(conn.execute(s.sql, params).fetchall())      # warm-up
            times = sorted(_time(conn, s.sql, params, 7))
            plan = _explain(conn, s.sql, params)
            conn.rollback()
            text = "\n".join(plan)
            exec_ms = re.search(r"Execution Time: ([\d.]+) ms", text)
            plan_ms = re.search(r"Planning Time: ([\d.]+) ms", text)
            rows.append({"task": s.id, "title": s.title, "rows": n_rows,
                         "median_ms": round(statistics.median(times), 2), "max_ms": round(times[-1], 2),
                         "planning_ms": float(plan_ms.group(1)) if plan_ms else None,
                         "execution_ms": float(exec_ms.group(1)) if exec_ms else None,
                         "indexes_used": " ".join(sorted(set(INDEX_RE.findall(text)))),
                         "seq_scans": " ".join(sorted(set(SEQ_RE.findall(text))))})
            plans += [f"## {s.id} · {s.title}", "", f"Parameters: `{json.dumps(params, default=str, ensure_ascii=False)}`",
                      "", "```", text, "```", ""]

        comparison = []
        for title, table, sql in COMPARISONS:
            conn.execute(sql).fetchall()
            chosen = _explain(conn, sql, None)
            with_idx = statistics.median(_time(conn, sql, None, 15))
            conn.execute("SET enable_indexscan = off; SET enable_bitmapscan = off; SET enable_indexonlyscan = off")
            without = statistics.median(_time(conn, sql, None, 15))
            forced_seq = _explain(conn, sql, None)
            conn.execute("RESET enable_indexscan; RESET enable_bitmapscan; RESET enable_indexonlyscan")
            conn.execute("SET enable_seqscan = off")
            forced_idx = _explain(conn, sql, None)
            conn.execute("RESET enable_seqscan")
            conn.rollback()
            chosen_text = "\n".join(chosen)
            comparison.append({
                "query": title, "table": table,
                "planner_choice": "index" if INDEX_RE.search(chosen_text) else "sequential scan",
                "indexes": " ".join(sorted(set(INDEX_RE.findall(chosen_text + "\n".join(forced_idx))))),
                "median_ms_planner_choice": round(with_idx, 3), "median_ms_indexes_disabled": round(without, 3),
                "speedup": round(without / with_idx, 1) if with_idx else None,
            })
            plans += [f"## Comparison · {title}", "", "Planner's choice:", "", "```", chosen_text, "```", "",
                      "Indexes disabled (`enable_indexscan/bitmapscan/indexonlyscan = off`):", "", "```",
                      "\n".join(forced_seq), "```", "",
                      "Sequential scan disabled (shows the index plan is available):", "", "```",
                      "\n".join(forced_idx), "```", ""]
        scale = scale_benchmark(conn, plans)
    write_csv("performance.csv", rows)
    write_csv("index_comparison.csv", comparison)
    write_csv("scale_benchmark.csv", scale)
    REPORT["scale"] = scale
    (OUT / "explain_plans.md").write_text("\n".join(plans) + "\n", encoding="utf-8")
    REPORT["performance"] = rows
    REPORT["comparison"] = comparison


# ---------------------------------------------------------------------------
# 7 API (and 8 browser) against a real server on the clean database
# ---------------------------------------------------------------------------
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self) -> None:
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "Server":
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "api.main:app", "--port", str(self.port), "--log-level", "warning"],
            cwd=ROOT, env={**os.environ, "DB_NAME": EVIDENCE_DB},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                if httpx.get(f"{self.url}/health", timeout=1).status_code == 200:
                    return self
            except httpx.HTTPError:
                time.sleep(0.5)
        raise SystemExit("API server did not start")

    def __exit__(self, *exc: Any) -> None:
        if self.proc:
            self.proc.terminate()
            self.proc.wait(10)


def _count(sql: str, params: Any = None) -> int:
    with connect() as conn:
        return conn.execute(sql, params).fetchone()[0]


def step_api(server: Server) -> None:
    say("7/9 API contract, concurrency and latency")
    token = {"X-API-Token": settings.ingest_api_token}
    obs = "/api/v1/mobility/observation"
    base = {"vehicle_id": "VEH-AV1-04", "lon": 126.6435, "lat": 37.3880, "speed_kmh": 18.5,
            "observed_at": "2026-10-10T09:00:00+09:00"}
    stored = ("SELECT count(*) FROM mobility.vehicle_observations WHERE vehicle_id = %s AND observed_at = %s")
    cases: list[dict[str, Any]] = []
    client = httpx.Client(base_url=server.url, timeout=30)

    def case(name: str, expected: str, method: str, path: str, body: Any = None, headers: dict | None = None,
             check: Any = None) -> httpx.Response:
        r = client.request(method, path, json=body, headers=headers or {})
        try:
            data = r.json()
        except ValueError:
            data = None
        outcome = data.get("outcome") if isinstance(data, dict) and "outcome" in data else ""
        actual = f"{r.status_code} {outcome}".strip()
        db_note, db_ok = "", True
        if check:
            db_ok, db_note = check()
        cases.append({"case": name, "request": f"{method} {path}", "expected": expected, "actual": actual,
                      "database_check": db_note, "status": "PASS" if actual.startswith(expected) and db_ok else "FAIL"})
        return r

    one_row = lambda vid, at: (lambda: ((n := _count(stored, (vid, at))) == 1, f"{n} stored row(s)"))  # noqa: E731
    no_row = lambda vid, at: (lambda: ((n := _count(stored, (vid, at))) == 0, f"{n} stored row(s)"))   # noqa: E731

    case("valid observation", "202 LOADED", "POST", obs, base, token, one_row("VEH-AV1-04", base["observed_at"]))
    for i in range(1, 4):
        case(f"retry {i} of the same packet", "202 DUPLICATE", "POST", obs, base, token,
             one_row("VEH-AV1-04", base["observed_at"]))
    case("no token", "401", "POST", obs, base, None)
    case("wrong token", "401", "POST", obs, base, {"X-API-Token": "wrong"})
    case("speed 400 km/h", "422", "POST", obs, {**base, "speed_kmh": 400, "observed_at": "2026-10-10T09:01:00+09:00"},
         token, no_row("VEH-AV1-04", "2026-10-10T09:01:00+09:00"))
    case("swapped coordinates (lat 126.64)", "422", "POST", obs,
         {**base, "lon": 37.388, "lat": 126.6435, "observed_at": "2026-10-10T09:02:00+09:00"}, token,
         no_row("VEH-AV1-04", "2026-10-10T09:02:00+09:00"))
    case("time without offset", "422", "POST", obs, {**base, "observed_at": "2026-10-10 09:03"}, token)
    case("malformed time", "422", "POST", obs, {**base, "observed_at": "yesterday"}, token)
    case("missing field (speed)", "422", "POST", obs, {k: v for k, v in base.items() if k != "speed_kmh"}, token)
    case("GPS fix outside Songdo", "202 QUARANTINED", "POST", obs,
         {**base, "lon": 126.978, "lat": 37.5665, "observed_at": "2026-10-10T09:04:00+09:00"}, token,
         lambda: ((n := _count("SELECT count(*) FROM core.quarantine_records WHERE reason_code = 'OUT_OF_BOUNDS' "
                               "AND payload->>'observed_at' = '2026-10-10T09:04:00+09:00'")) == 1,
                  f"{n} OUT_OF_BOUNDS quarantine row(s)"))
    case("unknown vehicle", "202 QUARANTINED", "POST", obs,
         {**base, "vehicle_id": "VEH-GHOST", "observed_at": "2026-10-10T09:05:00+09:00"}, token,
         lambda: ((n := _count("SELECT count(*) FROM core.quarantine_records WHERE reason_code = 'FK_VIOLATION' "
                               "AND payload->>'vehicle_id' = 'VEH-GHOST'")) == 1, f"{n} FK_VIOLATION quarantine row(s)"))
    report = {"incident_id": "INC-EVD-API-1", "type_code": "TRAFFIC_ACCIDENT", "title": "API 증거 사고",
              "severity": "MEDIUM", "lon": 126.639, "lat": 37.3925, "reported_at": "2026-10-10T09:00:00+09:00"}
    case("invalid severity", "422", "POST", "/api/v1/safety/incidents", {**report, "severity": "URGENT"}, token)
    case("report incident", "202 LOADED", "POST", "/api/v1/safety/incidents", report, token)
    case("REPORTED -> RESOLVED (skips states)", "409", "POST", "/api/v1/safety/incidents/INC-EVD-API-1/transition",
         {"to_status": "RESOLVED", "changed_at": "2026-10-10T09:05:00+09:00"}, token)
    case("REPORTED -> DISPATCHED", "200", "POST", "/api/v1/safety/incidents/INC-EVD-API-1/transition",
         {"to_status": "DISPATCHED", "changed_at": "2026-10-10T09:02:00+09:00", "facility_id": "FAC_POLICE_SONGDO1"},
         token, lambda: ((n := _count("SELECT count(*) FROM safety.incident_status_history "
                                      "WHERE incident_id = 'INC-EVD-API-1'")) == 2, f"{n} history rows"))
    case("CLOSED incident -> DISPATCHED", "409", "POST", "/api/v1/safety/incidents/INC-20261002-0001/transition",
         {"to_status": "DISPATCHED", "facility_id": "FAC_POLICE_SONGDO1"}, token)
    # corrections and retirement of reference data (api/edits.py)
    places = "/api/v1/core/places"
    audit_q = "SELECT count(*) FROM core.event_audit_log WHERE table_name = %s AND entity_id = %s AND action = %s"
    case("add a place to edit", "202", "POST", places,
         [{"place_id": "PLC_EVD_EDIT", "name_ko": "증거 편집 장소", "category": "LANDMARK", "lon": 126.6405, "lat": 37.3905}],
         token)
    case("PATCH place without token", "401", "PATCH", f"{places}/PLC_EVD_EDIT", {"name_en": "x"})
    case("PATCH place: rename", "200 UPDATED", "PATCH", f"{places}/PLC_EVD_EDIT", {"name_en": "Evidence place"}, token,
         lambda: ((n := _count(audit_q, ("places", "PLC_EVD_EDIT", "UPDATE"))) == 1, f"{n} UPDATE audit entry"))
    case("PATCH place: same value again", "200 UNCHANGED", "PATCH", f"{places}/PLC_EVD_EDIT",
         {"name_en": "Evidence place"}, token,
         lambda: ((n := _count(audit_q, ("places", "PLC_EVD_EDIT", "UPDATE"))) == 1, f"still {n} audit entry"))
    case("PATCH place: move outside Songdo", "422", "PATCH", f"{places}/PLC_EVD_EDIT", {"lon": 126.978}, token)
    case("PATCH place: unknown field", "422", "PATCH", f"{places}/PLC_EVD_EDIT", {"population": 5}, token)
    case("PATCH unknown place", "404", "PATCH", f"{places}/PLC_NOPE", {"name_en": "x"}, token)
    case("DELETE place still in use", "409", "DELETE", f"{places}/PLC_CONVENSIA", None, token,
         lambda: ((n := _count("SELECT count(*) FROM core.places WHERE place_id = 'PLC_CONVENSIA'")) == 1,
                  f"{n} row kept"))
    case("DELETE unreferenced place", "200 DELETED", "DELETE", f"{places}/PLC_EVD_EDIT", None, token,
         lambda: ((g := _count("SELECT count(*) FROM core.places WHERE place_id = 'PLC_EVD_EDIT'")) == 0
                  and (a := _count("SELECT count(*) FROM core.event_audit_log WHERE entity_id = 'PLC_EVD_EDIT' "
                                   "AND action = 'DELETE' AND payload_snapshot ? 'deleted_row'")) == 1,
                  f"{g} rows left, {a} DELETE audit entry with the full row"))
    case("PATCH attraction: parking spaces without parking", "409", "PATCH",
         "/api/v1/tourism/attractions/ATT_HANOK_VILLAGE", {"parking_spaces": 10}, token)
    case("DELETE event (cancel, row kept)", "200 CANCELLED", "DELETE", "/api/v1/tourism/events/EVT_SKYLINE_PHOTO",
         None, token, lambda: ((n := _count("SELECT count(*) FROM tourism.events WHERE event_id = 'EVT_SKYLINE_PHOTO' "
                                             "AND is_cancelled")) == 1, f"{n} row kept, is_cancelled"))
    case("DELETE device (retire)", "200 RETIRED", "DELETE", "/api/v1/building/devices/DEV-CV-PWR-01", None, token,
         lambda: ((n := _count("SELECT count(*) FROM building.device_space_assignments WHERE device_id = 'DEV-CV-PWR-01' "
                               "AND unassigned_at IS NULL")) == 0
                  and (h := _count("SELECT count(*) FROM building.device_space_assignments "
                                   "WHERE device_id = 'DEV-CV-PWR-01'")) > 0,
                  f"{n} open locations, {h} history rows kept"))
    case("PATCH retired device", "409", "PATCH", "/api/v1/building/devices/DEV-CV-PWR-01", {"model": "X"}, token)
    case("unknown task", "404", "POST", "/api/v1/scenarios/DB9-9/run", {"params": {}})
    case("task with bad parameter", "422", "POST", "/api/v1/scenarios/DB1-3/run", {"params": {"as_of": "noon"}})
    case("query out of range", "422", "GET", "/api/v1/core/places/nearby?radius_m=-5")
    geo = case("stops GeoJSON", "200", "GET", "/api/v1/mobility/stops.geojson").json()
    inside = all(126.6 <= f["geometry"]["coordinates"][0] <= 126.685 and 37.35 <= f["geometry"]["coordinates"][1] <= 37.425
                 for f in geo["features"])
    cases[-1]["database_check"] = f"{geo['type']}, {len(geo['features'])} Point features, all inside the bbox: {inside}"
    cases[-1]["status"] = "PASS" if geo["type"] == "FeatureCollection" and inside and geo["features"] else "FAIL"
    spec = case("OpenAPI document", "200", "GET", "/openapi.json").json()
    cases[-1]["database_check"] = f"{len(spec['paths'])} paths documented"

    # concurrency: 12 clients send the same NEW packet at the same instant
    packet = {**base, "vehicle_id": "VEH-AV1-05", "observed_at": "2026-10-10T10:00:00+09:00"}
    barrier = threading.Barrier(12)

    def fire(_: int) -> str:
        with httpx.Client(base_url=server.url, timeout=30) as c:
            barrier.wait()
            r = c.post(obs, json=packet, headers=token)
            return r.json().get("outcome", str(r.status_code)) if r.status_code == 202 else str(r.status_code)

    with ThreadPoolExecutor(12) as pool:
        outcomes = list(pool.map(fire, range(12)))
    n = _count(stored, ("VEH-AV1-05", packet["observed_at"]))
    tally = {o: outcomes.count(o) for o in sorted(set(outcomes))}
    cases.append({"case": "12 identical packets sent concurrently", "request": f"POST {obs} x12 (barrier)",
                  "expected": "1 stored row", "actual": json.dumps(tally),
                  "database_check": f"{n} stored row(s)", "status": "PASS" if n == 1 else "FAIL"})
    write_csv("api_contract.csv", cases)

    # latency
    endpoints = [
        ("GET", "/health"),
        ("GET", "/api/v1/mobility/stops/nearby?lon=126.6445&lat=37.3885&radius_m=400"),
        ("GET", "/api/v1/mobility/vehicles/positions?as_of=2026-10-02T08:20:00%2B09:00"),
        ("GET", "/api/v1/building/buildings/BLD_G_TOWER/state?as_of=2026-10-02T10:00:00%2B09:00"),
        ("GET", "/api/v1/tourism/attractions"),
        ("GET", "/api/v1/environment/exposure?lon=126.6548&lat=37.3888&as_of=2026-10-02T14:30:00%2B09:00"),
        ("GET", "/api/v1/safety/incidents/active?as_of=2026-10-02T18:30:00%2B09:00"),
        ("GET", "/api/v1/cross/incident-impact"),
        ("GET", "/api/v1/system/map/frame?as_of=2026-10-02T08:10:00%2B09:00"),
        ("POST", "/api/v1/scenarios/DB1-3/run"),
        ("POST", "/api/v1/scenarios/X-5/run"),
        ("POST", obs),
    ]
    latency = []
    for method, path in endpoints:
        samples = []
        for i in range(31):
            body = None
            if path == obs:
                body = {**base, "vehicle_id": "VEH-AV1-06", "observed_at": f"2026-10-10T11:{i // 60:02d}:{i % 60:02d}+09:00"}
            elif method == "POST":
                body = {"params": {}, "explain": False}
            started = time.perf_counter()
            r = client.request(method, path, json=body, headers=token if path == obs else {})
            samples.append((time.perf_counter() - started) * 1000)
            if r.status_code >= 400:
                raise SystemExit(f"latency probe {path} returned {r.status_code}")
        samples = sorted(samples[1:])            # first call warms caches
        latency.append({"endpoint": f"{method} {path.split('?')[0]}", "requests": len(samples),
                        "p50_ms": round(statistics.median(samples), 1),
                        "p95_ms": round(samples[int(len(samples) * 0.95) - 1], 1), "max_ms": round(samples[-1], 1)})
    write_csv("api_latency.csv", latency)
    client.close()
    REPORT["api"] = {"cases": cases, "latency": latency}


def find_browser() -> str | None:
    candidates = [shutil.which(n) for n in ("chrome", "google-chrome", "chromium", "msedge")] + [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    return next((c for c in candidates if c and Path(c).exists()), None)


def step_browser(server: Server) -> None:
    say("8/9 browser: every task through the web console (headless)")
    browser = find_browser()
    if not browser or not (ROOT / "ui" / "dist" / "index.html").exists():
        REPORT["browser"] = {"skipped": "no Chrome/Edge found" if not browser else "ui/dist missing (npm run build)"}
        say("    skipped: " + REPORT["browser"]["skipped"])
        return
    shots = OUT / "screenshots"
    shots.mkdir(exist_ok=True)
    profile_root = ROOT / "outputs" / ".browser-profiles"

    def chrome(profile: str, url: str, *mode: str) -> str:
        # one profile folder per call: two Chrome processes on one profile would hand off and exit silently
        proc = subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                               f"--user-data-dir={profile_root / profile}", "--window-size=1440,900",
                               "--virtual-time-budget=12000", *mode, url],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        return proc.stdout

    def visit(item: tuple[int, str]) -> dict[str, Any]:
        _, task_id = item
        url = f"{server.url}/#/tasks?task={task_id}&run=1"
        dom = chrome(f"{task_id}-dom", url, "--dump-dom")
        rows = re.search(r'class="stats">(\d+) rows?', dom)
        error = re.search(r'<p class="error">([^<]*)', dom)
        shot = shots / f"task_{task_id}.png"
        chrome(f"{task_id}-shot", url, f"--screenshot={shot}")
        api = httpx.post(f"{server.url}/api/v1/scenarios/{task_id}/run", json={"params": {}, "explain": False},
                         timeout=60).json()
        ui_rows = int(rows.group(1)) if rows else None
        return {"task": task_id, "url": f"#/tasks?task={task_id}&run=1",
                "ui_rows": ui_rows, "api_rows": api.get("row_count"),
                "ui_error": error.group(1) if error else "",
                "status": "PASS" if ui_rows is not None and ui_rows == api.get("row_count") and not error else "FAIL",
                "screenshot": f"screenshots/{shot.name}"}

    ids = [s.id for s in SCENARIOS.values() if s.status == "ready"]
    with ThreadPoolExecutor(4) as pool:
        coverage = list(pool.map(visit, enumerate(ids)))
    pages = [("map_0810", "#/map?t=08:10"), ("map_1820", "#/map?t=18:20"), ("ingest", "#/ingest"),
             ("verify", "#/verify"), ("task_result_map", "#/tasks?task=DB5-2&run=1&view=map"),
             ("edit", "#/edit?entity=event&id=EVT_TRIBOWL_CONCERT")]
    for name, frag in pages:
        chrome(f"page-{name}", f"{server.url}/{frag}", f"--screenshot={shots / f'page_{name}.png'}")
    shutil.rmtree(profile_root, ignore_errors=True)
    write_csv("ui_task_coverage.csv", coverage)
    REPORT["browser"] = {"coverage": coverage, "pages": [f"screenshots/page_{n}.png" for n, _ in pages],
                         "browser": Path(browser).stem}


# ---------------------------------------------------------------------------
# 9 report
# ---------------------------------------------------------------------------
def md_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    def clean(v: Any) -> str:
        return str(v if v is not None else "—").replace("|", "\\|").replace("\n", " ")
    return ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)] + \
           ["| " + " | ".join(clean(v) for v in r) + " |" for r in rows]


def step_report(server: Server | None) -> None:
    say("9/9 report")
    env, build, restore = REPORT["environment"], REPORT["build"], REPORT["restore"]
    tests, acceptance = REPORT["tests"], REPORT["acceptance"]
    perf, comparison, api = REPORT["performance"], REPORT["comparison"], REPORT["api"]
    browser = REPORT.get("browser", {})
    acc_count = {s: sum(1 for r in acceptance if r.status == s) for s in ("PASS", "LIMITED", "FAIL", "SKIPPED")}
    scenario_rows = [r for r in acceptance if r.id.startswith("DB") and "RACE" not in r.id]
    api_pass = sum(1 for c in api["cases"] if c["status"] == "PASS")
    gist_tasks = [p for p in perf if "geog" in p["indexes_used"] or "geom" in p["indexes_used"]]
    cov = browser.get("coverage", [])
    cov_pass = sum(1 for c in cov if c["status"] == "PASS")

    # verification summary (starter-notebook format, one row per gate)
    verification = [
        ("PostgreSQL", "Connection and version", "PASS", env["postgresql_server"].split(",")[0]),
        ("PostGIS", "Extension", "PASS", (re.search(r'POSTGIS="([^" ]+)', env["postgis"]) or [None, env["postgis"]])[1]),
        ("Boot / DDL / seed", "psql ON_ERROR_STOP on a new database",
         "PASS" if all(s["exit_code"] == 0 for s in build["steps"]) else "FAIL",
         f"{len(build['steps'])} files, exit 0, {build['seconds']} s"),
        ("Schema", "Objects created", "PASS",
         ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in build["inventory"]["objects"].items()
                   if k in ("tables", "views", "materialized_views", "triggers", "exclusion_constraints"))),
        ("Quarantine", "Bad input stored with a reason, API path included",
         "PASS" if all(r.status == "PASS" for r in acceptance if r.id in ("CORE-1", "CORE-2")) else "FAIL",
         "CORE-1, CORE-2, api_contract.csv"),
        ("GiST", "Spatial index used", "PASS" if gist_tasks else "FAIL",
         f"{len(gist_tasks)} tasks use a GiST index; see index_comparison.csv"),
        ("API", "Contract, idempotency, concurrency", "PASS" if api_pass == len(api["cases"]) else "FAIL",
         f"{api_pass}/{len(api['cases'])} cases"),
        ("Tests", "unittest on the clean database", "PASS" if tests["passed"] else "FAIL",
         f"{tests['count']} tests, {tests['last_line']}"),
        ("Recovery", "pg_restore and integrity comparison",
         "PASS" if restore["matched"] == restore["total"] else "FAIL", f"{restore['matched']}/{restore['total']} identical"),
        ("Scenarios", "Acceptance proofs", "PASS" if acc_count["FAIL"] == 0 else "FAIL",
         f"{acc_count['PASS']} pass, {acc_count['LIMITED']} limited, {acc_count['FAIL']} fail"),
        ("Web console", "Every task run in a browser", ("PASS" if cov and cov_pass == len(cov) else "FAIL") if cov else "SKIPPED",
         f"{cov_pass}/{len(cov)} tasks" if cov else browser.get("skipped", "")),
        ("Backup", "File", "PASS" if BACKUP.exists() else "FAIL", f"outputs/{BACKUP.name}"),
    ]
    write_csv("verification_summary.csv", [{"Component": a, "Verification": b, "Status": c, "Detail": d}
                                           for a, b, c, d in verification])

    L: list[str] = [
        "# Evidence kit · Songdo Urban Digital Memory", "",
        f"Generated {env['generated_at']} by `python scripts/evidence.py` from commit `{env['git_commit']}`.",
        f"Every number below was produced by that run on a **new, disposable database** (`{EVIDENCE_DB}`) built "
        "from this repository, not on a developer's working database. Rerun the command to reproduce.", "",
        "## Gates", "",
        "The gates of the midterm assessment (\"Verification and staged remediation\"), each with the run that proves it.", "",
    ]
    L += md_table(["Component", "Verification", "Status", "Detail"], [list(v) for v in verification])
    L += ["", "## Remediation items from the midterm feedback", ""]
    by_id = {r.id: r for r in acceptance}
    case = {c["case"]: c for c in api["cases"]}
    remediation = [
        ("P0 Hash-only write", "Staging, domain row and audit in one transaction; invalid input stored with a reason",
         f"{by_id['CORE-2'].status}: {by_id['CORE-2'].evidence}. Concurrent duplicates: "
         f"{case['12 identical packets sent concurrently']['database_check']}."),
        ("P1 HTTP status / timestamp", "Swapped coordinates and malformed time return 422; a valid request persists once",
         f"swapped -> {case['swapped coordinates (lat 126.64)']['actual']}, malformed time -> "
         f"{case['malformed time']['actual']}, no offset -> {case['time without offset']['actual']}; valid -> "
         f"{case['valid observation']['actual']} ({case['valid observation']['database_check']}), 3 retries -> DUPLICATE"),
        ("P1 Assignment history", "Exclusion rule in a versioned migration; overlap 23P01; adjacent succeeds",
         f"sql/03 `ex_assignment_no_overlap`. {by_id['DB2-3'].evidence}. Race: {by_id['DB2-3-RACE'].evidence}"),
        ("P1 Safety progression", "Lock, validate and append atomically; REPORTED -> RESOLVED rejected",
         f"sql/06 `transition_incident` (SELECT ... FOR UPDATE) and guard trigger. {by_id['DB5-3'].evidence}. "
         f"Race: {by_id['DB5-3-RACE'].evidence}"),
        ("P1 Time and spatial paths", "Bind T, stable ties, indexed geography, valid GeoJSON, boundary distances",
         f"{by_id['DB1-3'].evidence}. {by_id['DB1-5'].evidence}. GeoJSON: {case['stops GeoJSON']['database_check']}"),
        ("Federation", "Nearest station by distance; alert only with a distance rule",
         by_id["X-VIEWS"].evidence),
    ]
    L += md_table(["Item", "Required proof", "Evidence from this run"], [list(r) for r in remediation])

    L += ["", "## Scenario acceptance matrix", "",
          "One executed check per scenario, written against the \"Required acceptance\" column of the feedback. "
          "Each check runs in a transaction that is rolled back (the two races commit on the disposable database). "
          "`LIMITED` means the check ran and passed, but part of the criterion needs data the project does not have.", ""]
    L += md_table(["ID", "Scenario", "Required acceptance", "Status", "Evidence"],
                  [[r.id, r.scenario, r.criterion, r.status, r.evidence] for r in acceptance])
    L += ["", f"Totals: {acc_count['PASS']} PASS, {acc_count['LIMITED']} LIMITED, {acc_count['FAIL']} FAIL "
          f"({len(scenario_rows)} of 25 scenarios have a dedicated check; full table in `acceptance_matrix.csv`)."]

    L += ["", "## Clean build", "",
          f"`CREATE DATABASE {EVIDENCE_DB}`, then `psql -X -q -v ON_ERROR_STOP=1 -f` for each file, in order.", ""]
    L += md_table(["File", "Exit", "ms", "Size"], [[s["file"], s["exit_code"], s["ms"], f"{s['bytes'] / 1024:.0f} KB"]
                                                   for s in build["steps"]])
    L += ["", "Objects: " + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in build["inventory"]["objects"].items()) + ".",
          f"Rows: {sum(build['inventory']['rows'].values()):,} in {len(build['inventory']['rows'])} tables "
          "(per table in `schema_inventory.csv`)."]

    L += ["", "## Backup and restore", "",
          f"`pg_dump -Fc` ({restore['size_mb']} MB, {restore['dump_s']} s) then `pg_restore --exit-on-error` into "
          f"`{RESTORE_DB}` ({restore['restore_s']} s). **{restore['matched']} of {restore['total']} comparisons "
          "identical**: every object count, every table's row count, the result hash of 8 tasks, and behaviour "
          "(ledger still append-only, exclusion constraint still enforced, materialized views refresh)."
          + (f" Mismatches: {', '.join(restore['mismatches'])}." if restore["mismatches"] else "")]

    L += ["", "## Tests", "", f"`{tests['count']}` tests on the clean database: {tests['last_line']} "
          f"({tests['seconds']} s). Full verbose output in `test_results.txt`."]

    scale = REPORT["scale"]
    L += ["", "## Performance", "",
          f"### Index versus no index at scale ({SCALE_ROWS:,} GPS rows)", "",
          f"A synthetic copy of the GPS table with the same columns and index definitions, filled with {SCALE_ROWS:,} "
          f"rows (500 vehicles x 2,000 fixes, fixed random seed; built in {REPORT['scale_build_s']} s, then dropped). "
          "Median of 5 runs with indexes, 3 runs with index scans disabled.", ""]
    L += md_table(["Query", "Index used", "ms with index", "ms without", "Speed-up"],
                  [[x["query"], x["index"], x["median_ms_with_index"], x["median_ms_without_index"],
                    f"{x['speedup']}x"] for x in scale])
    L += ["", "### Index versus no index on the fixture data", "",
          "The same comparison on the project's own tables (513 stops, about 20,000 GPS rows, about 50,000 status "
          "history rows). The gains are smaller than at scale because small tables fit in a few memory pages; the "
          "table above shows how the gap grows with volume. Median of 15 runs; full plans in `explain_plans.md`.", ""]
    L += md_table(["Query", "Table", "Planner choice", "Index", "ms (planner)", "ms (no index)", "Speed-up"],
                  [[c["query"], c["table"], c["planner_choice"], c["indexes"], c["median_ms_planner_choice"],
                    c["median_ms_indexes_disabled"], f"{c['speedup']}x"] for c in comparison])
    L += ["", "### Every task", "", "Median of 7 runs after a warm-up, default parameters.", ""]
    L += md_table(["Task", "Rows", "Median ms", "Execution ms", "Indexes used", "Seq scans"],
                  [[p["task"], p["rows"], p["median_ms"], p["execution_ms"], p["indexes_used"] or "—",
                    p["seq_scans"] or "—"] for p in perf])

    L += ["", "## API", "", f"Real HTTP server (uvicorn) on the clean database. {api_pass} of {len(api['cases'])} "
          "contract cases pass.", ""]
    L += md_table(["Case", "Request", "Expected", "Actual", "Database check", "Status"],
                  [[c["case"], c["request"], c["expected"], c["actual"], c["database_check"], c["status"]]
                   for c in api["cases"]])
    L += ["", "Latency over 30 requests per endpoint (after one warm-up), same machine:", ""]
    L += md_table(["Endpoint", "p50 ms", "p95 ms", "max ms"],
                  [[x["endpoint"], x["p50_ms"], x["p95_ms"], x["max_ms"]] for x in api["latency"]])

    L += ["", "## Web console", ""]
    if cov:
        L += [f"Each task was opened in headless {browser['browser']} through its link "
              f"(`#/tasks?task=<id>&run=1`). The row count was read from the rendered page and compared with the API: "
              f"**{cov_pass} of {len(cov)} tasks match**. Screenshots in `screenshots/`.", ""]
        L += md_table(["Task", "Rows in browser", "Rows from API", "Status", "Screenshot"],
                      [[c["task"], c["ui_rows"], c["api_rows"], c["status"], f"[png]({c['screenshot']})"] for c in cov])
        L += ["", "Pages: " + ", ".join(f"[{Path(p).stem}]({p})" for p in browser["pages"])]
    else:
        L += [f"Skipped: {browser.get('skipped', 'not run')}."]

    L += ["", "## Known limitations (stated, not hidden)", "",
          "- Real data: bus stops (MOLIT file) and AirKorea stations and readings. Routes, vehicles, trips, building "
          "interiors, tourism hours, events and visitor numbers, and incidents are generated by "
          "`fixtures/seed_fixtures.py` (seed 20261002) and labelled with SYNTHETIC_FIXTURE sources.",
          *[f"- {r.id}: {r.evidence}" for r in acceptance if r.status == "LIMITED"],
          "- Timings come from one laptop and a small data set (about 20,000 GPS rows, 10,000 incidents); "
          "they show the plan shape and relative cost, not production capacity.",
          "", "## Files", ""]
    files = [
        ("README.md", "this report"), ("verification_summary.csv", "gate checklist (starter notebook format)"),
        ("environment.txt", "versions of every component and the git commit"),
        ("clean_build.log", "psql output of the clean build, object counts, rows per table"),
        ("schema_inventory.csv", "object counts and rows per table"),
        ("acceptance_matrix.csv", "scenario acceptance checks"), ("test_results.txt", "unittest verbose output"),
        ("backup_restore.log", "dump and restore commands"), ("backup_restore.csv", "original vs restored comparison"),
        ("performance.csv", "timing and plan summary of every task"),
        ("index_comparison.csv", "index vs no-index timings on the fixture tables"),
        ("scale_benchmark.csv", "index vs no-index timings on 1,000,000 synthetic GPS rows"), ("explain_plans.md", "full EXPLAIN ANALYZE plans"),
        ("api_contract.csv", "API negative and positive cases"), ("api_latency.csv", "endpoint latency"),
        ("ui_task_coverage.csv", "browser-to-API run of every task"), ("screenshots/", "console screenshots"),
    ]
    L += md_table(["File", "Content"], [[f"`{f}`", d] for f, d in files if (OUT / f.rstrip("/")).exists()])
    (OUT / "README.md").write_text("\n".join(L) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-browser", action="store_true", help="skip headless-browser checks and screenshots")
    parser.add_argument("--keep", action="store_true", help="keep the evidence databases afterwards")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for name in ("psql", "pg_dump", "pg_restore"):
        tool(name)
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*"):
        if old.name != ".gitkeep":
            shutil.rmtree(old) if old.is_dir() else old.unlink()
    started = time.perf_counter()
    try:
        step_clean_build()
        step_environment()
        step_backup_restore()
        step_tests()
        step_acceptance()
        step_performance()
        with Server() as server:
            step_api(server)
            if args.no_browser:
                REPORT["browser"] = {"skipped": "--no-browser"}
            else:
                step_browser(server)
        step_report(None)
    finally:
        if not args.keep:
            drop_database(RESTORE_DB)
            drop_database(EVIDENCE_DB)
    say(f"done in {time.perf_counter() - started:.0f} s -> outputs/evidence/README.md")
    for row in csv.DictReader((OUT / "verification_summary.csv").open(encoding="utf-8")):
        say(f"  {row['Status']:<8} {row['Component']:<18} {row['Detail']}")


if __name__ == "__main__":
    main()
