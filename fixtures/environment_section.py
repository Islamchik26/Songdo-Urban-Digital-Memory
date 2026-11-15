"""DB4 Environment fixtures.

  real       environment.stations — fixtures/songdo_air_stations.csv, the Songdo subset of the
             AirKorea station list (source SRC_ENV_02)
  synthetic  environment.measurements for the service day 2026-10-02 (source SRC_ENV_SYN),
             shaped like a micro-sensor feed (several readings per hour) so the hourly
             rollup has something to aggregate

Real AirKorea readings are NOT in the seed. They enter through the pipeline with
scripts/ingest_airkorea.py (live API, or --from-fixture to replay the committed sample).

Deliberate test cases on 2026-10-02
  송도        PM2.5 episode 13:00-16:00, peaks above 75 (VERY_BAD), then clears  -> alert RESOLVED
  아암        PM2.5 climbs from 20:00 and is still above 75 at midnight          -> alert stays ACTIVE
  동막        packets of 10:00-11:00 arrive three hours late (link outage backfill)
  인천 신항   five impossible CO readings (99 ppm), stored as SUSPECT and left out of the rollup
"""
from __future__ import annotations

import csv
import math
import random
from datetime import timedelta
from pathlib import Path

from fixtures.common import SERVICE_DAY, insert, point

ROOT = Path(__file__).resolve().parent.parent
SRC = "SRC_ENV_SYN"
STEPS = (1, 2, 5, 10, 15, 30, 60)


def _bump(hour: float, centre: float, width: float) -> float:
    return math.exp(-((hour - centre) / width) ** 2)


def environment_section(rng: random.Random, n: int) -> list[str]:
    with (ROOT / "fixtures" / "songdo_air_stations.csv").open(encoding="utf-8", newline="") as fh:
        stations = list(csv.DictReader(fh))

    station_rows = [[f"AK_{s['stationName']}", s["stationName"], s["addr"], s["mangName"], s["item"],
                     int(s["year"]) if s["year"].isdigit() else None,
                     point(float(s["dmY"]), float(s["dmX"])), "SRC_ENV_02"]     # dmY = longitude, dmX = latitude
                    for s in stations]

    # readings every `step` minutes for 24 h; about 10 x N rows in total
    ideal = len(stations) * 6 * 1440 / (10 * n)
    step = min(STEPS, key=lambda s: abs(s - ideal))
    rows = []
    for s in stations:
        name = s["stationName"]
        sid = f"AK_{name}"
        base_pm = rng.uniform(16, 26)
        roadside = 1.35 if s["mangName"] == "도로변대기" else 1.0
        for k in range(1440 // step):
            t = SERVICE_DAY + timedelta(minutes=k * step)
            hour = k * step / 60
            rush = _bump(hour, 8.2, 1.3) + _bump(hour, 18.5, 1.5)

            pm25 = base_pm + 6 * rush + rng.gauss(0, 1.8)
            if name == "송도":
                pm25 += 78 * _bump(hour, 14.5, 1.15)             # afternoon episode that clears
            if name == "아암" and hour >= 19.5:
                pm25 += 72 * min(1.0, (hour - 19.5) / 2.0)       # evening episode still on at midnight
            pm25 = max(1.0, pm25)
            values = {
                "PM25": round(pm25),
                "PM10": round(max(2.0, pm25 * 1.55 + rng.gauss(4, 3))),
                "O3": round(max(0.001, 0.018 + 0.034 * _bump(hour, 15, 3.5) + rng.gauss(0, 0.003)), 3),
                "NO2": round(max(0.001, (0.012 + 0.022 * rush) * roadside + rng.gauss(0, 0.002)), 3),
                "CO": round(max(0.1, (0.35 + 0.25 * rush) * roadside + rng.gauss(0, 0.03)), 1),
                "SO2": round(max(0.001, 0.003 + rng.gauss(0, 0.0005)), 3),
            }
            late = name == "동막" and 10 <= hour < 11
            received = t + (timedelta(hours=3, seconds=rng.randint(0, 120)) if late
                            else timedelta(seconds=rng.randint(20, 60)))
            for code, value in values.items():
                flag = "GOOD"
                if name == "인천 신항" and code == "CO" and 6 <= hour < 6 + 5 * step / 60:
                    value, flag = 99.0, "SUSPECT"
                rows.append([sid, code, value, flag, t, received, SRC])

    day_start, day_end = SERVICE_DAY, SERVICE_DAY + timedelta(days=1)
    lines = ["", "-- ---- environment.stations (REAL: Songdo subset of the AirKorea station list) ----"]
    lines += insert("environment.stations",
                    ["station_id", "station_name_ko", "address", "network", "measured_items",
                     "established_year", "geom", "source_id"], station_rows,
                    "(station_id) DO UPDATE SET address = EXCLUDED.address, network = EXCLUDED.network, "
                    "measured_items = EXCLUDED.measured_items, established_year = EXCLUDED.established_year, "
                    "geom = EXCLUDED.geom")
    lines += ["", f"-- ---- environment.measurements (SYNTHETIC, every {step} min on the service day) ----"]
    lines += insert("environment.measurements",
                    ["station_id", "type_code", "measured_value", "quality_flag", "observed_at", "received_at",
                     "source_id"], rows)
    lines += ["", "-- rollup and alerts are computed by the database, not by this generator",
              f"SELECT environment.refresh_hourly_summaries('{day_start.isoformat()}', '{day_end.isoformat()}');",
              f"SELECT environment.evaluate_alerts('{day_start.isoformat()}', '{day_end.isoformat()}');"]
    print(f"  environment: {len(station_rows)} stations, {len(rows)} measurements (every {step} min)")
    return lines
