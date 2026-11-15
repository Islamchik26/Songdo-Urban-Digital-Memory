"""Ingest real AirKorea data through the full pipeline.

    python scripts/ingest_airkorea.py                  live API (needs AIRKOREA_API_KEY in .env)
    python scripts/ingest_airkorea.py --export-fixture live API, and also rewrite the committed fixtures
    python scripts/ingest_airkorea.py --from-fixture   no key needed: replays fixtures/airkorea_sample.json

What it does
  1. station list for Incheon -> AirStationsAdapter (stations outside the Songdo bbox are quarantined)
  2. last ~24 hourly readings of every Songdo station -> AirMeasurementsAdapter
     ('-' fault markers are quarantined; a revised value replaces the earlier one)
  3. refreshes the hourly rollup and evaluates alert rules for the ingested period
Running it twice changes nothing (SHA-256 deduplication), except for hours AirKorea has revised.

Services (data.go.kr, provider 한국환경공단):
  측정소정보    MsrstnInfoInqireSvc/getMsrstnList
  대기오염정보  ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

import httpx
import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.base_adapter import validate_coordinate  # noqa: E402
from adapters.environment_adapter import (  # noqa: E402
    AirMeasurementsAdapter, AirStationsAdapter, explode_airkorea_item, parse_airkorea_time)
from api.config import settings  # noqa: E402

BASE = "http://apis.data.go.kr/B552584"
STATION_FIXTURE = ROOT / "fixtures" / "songdo_air_stations.csv"
SAMPLE_FIXTURE = ROOT / "fixtures" / "airkorea_sample.json"
RAW_DIR = ROOT / "data" / "raw"
STATION_COLUMNS = ["stationName", "addr", "dmX", "dmY", "mangName", "item", "year"]


def call(path: str, key: str, **params: object) -> list[dict]:
    """One API call. The key is never written to logs or error messages."""
    try:
        response = httpx.get(f"{BASE}/{path}", timeout=30,
                             params={"serviceKey": key, "returnType": "json", "numOfRows": 200, "pageNo": 1, **params})
        body = response.json()["response"]
    except Exception as exc:  # network error, or an XML error page instead of JSON
        raise SystemExit(f"AirKorea call {path} failed: {type(exc).__name__}. "
                         "A new key can take about an hour to become active.") from None
    if body["header"]["resultCode"] != "00":
        raise SystemExit(f"AirKorea call {path} rejected: {body['header']}")
    return body["body"]["items"]


def in_songdo(station: dict) -> bool:
    return validate_coordinate(station.get("dmY"), station.get("dmX")).ok


def fetch_live() -> tuple[list[dict], dict[str, list[dict]]]:
    raw_key = os.getenv("AIRKOREA_API_KEY", "").strip().strip("\"'")
    if not raw_key:
        raise SystemExit("AIRKOREA_API_KEY is not set in .env (or use --from-fixture)")
    key = unquote(raw_key)   # works for both the plain and the percent-encoded form of the key
    stations = call("MsrstnInfoInqireSvc/getMsrstnList", key, addr="인천")
    readings = {s["stationName"]: call("ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty", key,
                                       stationName=s["stationName"], dataTerm="DAILY", ver="1.3")
                for s in stations if in_songdo(s)}
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
    (RAW_DIR / f"airkorea_{stamp}.json").write_text(
        json.dumps({"stations": stations, "readings": readings}, ensure_ascii=False, indent=1), encoding="utf-8")
    return stations, readings


def load_fixture() -> tuple[list[dict], dict[str, list[dict]]]:
    if not SAMPLE_FIXTURE.exists():
        raise SystemExit(f"{SAMPLE_FIXTURE.relative_to(ROOT)} not found")
    data = json.loads(SAMPLE_FIXTURE.read_text(encoding="utf-8"))
    return data["stations"], data["readings"]


def export_fixtures(stations: list[dict], readings: dict[str, list[dict]]) -> None:
    songdo = sorted((s for s in stations if in_songdo(s)), key=lambda s: s["stationName"])
    with STATION_FIXTURE.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(STATION_COLUMNS)
        writer.writerows([[s.get(c) for c in STATION_COLUMNS] for s in songdo])
    SAMPLE_FIXTURE.write_text(json.dumps({"stations": stations, "readings": readings}, ensure_ascii=False, indent=1),
                              encoding="utf-8", newline="\n")
    print(f"  wrote {STATION_FIXTURE.relative_to(ROOT)} ({len(songdo)} stations) "
          f"and {SAMPLE_FIXTURE.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-fixture", action="store_true", help="replay the committed sample; no key needed")
    parser.add_argument("--export-fixture", action="store_true", help="rewrite the committed fixtures from the live API")
    parser.add_argument("--no-ingest", action="store_true", help="fetch/export only; do not touch the database")
    args = parser.parse_args()

    stations, readings = load_fixture() if args.from_fixture else fetch_live()
    hours = sum(len(v) for v in readings.values())
    print(f"AirKorea: {len(stations)} Incheon stations, {len(readings)} in Songdo, {hours} station-hours")
    if args.export_fixture:
        export_fixtures(stations, readings)
    if args.no_ingest:
        return

    records = [r for name, items in readings.items() for item in items for r in explode_airkorea_item(item, name)]
    times = [t for t in (parse_airkorea_time(r["data_time"]) for r in records) if t]
    with psycopg.connect(settings.conninfo()) as conn:
        s = AirStationsAdapter(conn, actor="ingest_airkorea").ingest(stations)
        print(f"  stations      batch {s.batch_id}: loaded {s.loaded}, quarantined {s.quarantined}, duplicates {s.duplicates}")
        m = AirMeasurementsAdapter(conn, actor="ingest_airkorea").ingest(records)
        print(f"  measurements  batch {m.batch_id}: loaded {m.loaded}, quarantined {m.quarantined}, duplicates {m.duplicates}")
        conn.execute("SELECT * FROM cross_domain.refresh_materialized_views()")   # refresh policy
        if times:
            start, end = min(times), max(times) + timedelta(hours=1)
            rolled = conn.execute("SELECT environment.refresh_hourly_summaries(%s, %s)", (start, end)).fetchone()[0]
            alerts = conn.execute("SELECT environment.evaluate_alerts(%s, %s)", (start, end)).fetchone()[0]
            print(f"  rollup        {rolled} hourly summaries refreshed, {alerts} new alerts "
                  f"({start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} KST)")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    main()
