"""Ingest the official national bus stop file through the full pipeline.

    python scripts/ingest_bus_stops.py                  ingest every Incheon row
    python scripts/ingest_bus_stops.py --export-fixture also rewrite fixtures/songdo_stops.csv
    python scripts/ingest_bus_stops.py --export-fixture --no-ingest   fixture only, no database

Input: data/raw/국토교통부_전국 버스정류장 위치정보_*.csv (CP949, as downloaded).

Only rows with 도시명 = 인천광역시 are sent to the adapter. The adapter then
  - loads 인천BIS stops inside the Songdo bbox into mobility.stops,
  - quarantines the rest of Incheon as OUT_OF_BOUNDS,
  - quarantines 경기BIS copies of Songdo stops as DUPLICATE_AUTHORITY.
Running it twice changes nothing (SHA-256 deduplication).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import sys
import time
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.base_adapter import validate_coordinate  # noqa: E402
from adapters.mobility_adapter import StopsAdapter  # noqa: E402
from api.config import settings  # noqa: E402

FIXTURE = ROOT / "fixtures" / "songdo_stops.csv"
CITY = "인천광역시"


def read_rows(path: Path) -> tuple[list[dict[str, str]], str, str]:
    """Decode the raw file without altering it. Returns (rows, encoding, sha256)."""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp949"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SystemExit(f"{path.name}: neither UTF-8 nor CP949")
    return list(csv.DictReader(io.StringIO(text))), encoding, hashlib.sha256(raw).hexdigest()


def export_fixture(rows: list[dict[str, str]]) -> int:
    """Songdo subset (인천BIS only) as a small UTF-8 CSV that is committed to the repo."""
    keep = [r for r in rows
            if r["관리도시명"] == StopsAdapter.authority and validate_coordinate(r["경도"], r["위도"]).ok]
    keep.sort(key=lambda r: r["정류장번호"])
    with FIXTURE.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["stop_id", "stop_number", "stop_name_ko", "lon", "lat", "managing_bis", "collected_on"])
        for r in keep:
            writer.writerow([r["정류장번호"], r["모바일단축번호"], r["정류장명"], r["경도"], r["위도"],
                             r["관리도시명"], r["정보수집일"]])
    return len(keep)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--export-fixture", action="store_true")
    parser.add_argument("--no-ingest", action="store_true", help="only read the file (use with --export-fixture)")
    parser.add_argument("--file", type=Path, help="explicit path to the raw CSV")
    args = parser.parse_args()

    path = args.file
    if path is None:
        found = sorted((ROOT / "data" / "raw").glob("*버스정류장 위치정보*.csv"))
        if not found:
            raise SystemExit("No bus stop file in data/raw/ (see data/raw/README.md)")
        path = found[-1]

    rows, encoding, sha = read_rows(path)
    incheon = [r for r in rows if r.get("도시명") == CITY]
    print(f"{path.name}\n  encoding {encoding}, sha256 {sha}\n  {len(rows)} rows, {len(incheon)} in {CITY}")

    if args.export_fixture:
        print(f"  wrote {FIXTURE.relative_to(ROOT)} ({export_fixture(incheon)} Songdo stops)")

    if args.no_ingest:
        return
    started = time.perf_counter()
    with psycopg.connect(settings.conninfo()) as conn:
        summary = StopsAdapter(conn, actor="ingest_bus_stops").ingest(incheon)
        conn.execute("SELECT * FROM cross_domain.refresh_materialized_views()")   # refresh policy
    print(f"  batch {summary.batch_id}: loaded {summary.loaded}, quarantined {summary.quarantined}, "
          f"duplicates {summary.duplicates}  ({time.perf_counter() - started:.1f} s)")


if __name__ == "__main__":
    main()
