"""Deterministic fixture generator -> fixtures/seed_data.sql

    python fixtures/seed_fixtures.py [N]

N (default 1000) scales the high-volume telemetry: about 10 x N vehicle GPS rows.

Deterministic means: same N -> byte-identical seed_data.sql. No wall-clock
values and no unseeded randomness may be written into the file.

What is real and what is generated
  real       core.source_registry (source_registry.csv), mobility.stops
             (fixtures/songdo_stops.csv, the Songdo subset of the official file)
  curated    core.places (hand-typed landmark coordinates, approximate)
  synthetic  routes, route patterns, vehicles, trips, stop events, GPS telemetry;
             building interiors, devices, telemetry, maintenance (fixtures/building_section.py)
"""
from __future__ import annotations

import csv
import math
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fixtures.building_section import building_section  # noqa: E402
from fixtures.common import BBOX, SERVICE_DAY, insert, point  # noqa: E402
OUT = ROOT / "fixtures" / "seed_data.sql"
SEED = 20261002

# Curated Songdo landmarks. Coordinates are approximate centroids typed by hand
# (source SRC_CORE_01) — replace with geocoded official values when verified.
# (place_id, name_ko, name_en, category, road_address, lon, lat)
PLACES = [
    ("PLC_CENTRAL_PARK", "송도 센트럴파크", "Songdo Central Park", "PARK", "인천광역시 연수구 컨벤시아대로 160", 126.6390, 37.3925),
    ("PLC_CONVENSIA", "송도컨벤시아", "Songdo Convensia", "CONVENTION", "인천광역시 연수구 센트럴로 123", 126.6445, 37.3885),
    ("PLC_TRIBOWL", "트라이보울", "Tri-Bowl", "CULTURE", "인천광역시 연수구 인천타워대로 250", 126.6367, 37.3963),
    ("PLC_G_TOWER", "G타워", "G-Tower", "LANDMARK", "인천광역시 연수구 아트센터대로 175", 126.6336, 37.3962),
    ("PLC_POSCO_TOWER", "포스코타워-송도", "POSCO Tower Songdo", "LANDMARK", "인천광역시 연수구 컨벤시아대로 165", 126.6440, 37.3893),
    ("PLC_INU", "인천대학교 송도캠퍼스", "Incheon National University", "UNIVERSITY", "인천광역시 연수구 아카데미로 119", 126.6328, 37.3751),
    ("PLC_YONSEI_IC", "연세대학교 국제캠퍼스", "Yonsei University International Campus", "UNIVERSITY", "인천광역시 연수구 송도과학로 85", 126.6690, 37.3817),
    ("PLC_IGC", "인천글로벌캠퍼스", "Incheon Global Campus", "UNIVERSITY", "인천광역시 연수구 송도문화로 119", 126.6672, 37.3763),
    ("PLC_TRIPLE_STREET", "트리플스트리트", "Triple Street", "SHOPPING", "인천광역시 연수구 송도과학로16번길 33-4", 126.6600, 37.3797),
    ("PLC_HYUNDAI_OUTLET", "현대프리미엄아울렛 송도점", "Hyundai Premium Outlets Songdo", "SHOPPING", "인천광역시 연수구 송도국제대로 123", 126.6578, 37.3817),
    ("PLC_STN_CENTRAL_PARK", "센트럴파크역", "Central Park Station", "SUBWAY_STATION", None, 126.6346, 37.3934),
    ("PLC_STN_INU", "인천대입구역", "Incheon National University Station", "SUBWAY_STATION", None, 126.6394, 37.3863),
    ("PLC_STN_TECHNO_PARK", "테크노파크역", "Techno Park Station", "SUBWAY_STATION", None, 126.6564, 37.3822),
    ("PLC_HAEDOJI_PARK", "해돋이공원", "Haedoji Park", "PARK", None, 126.6528, 37.3944),
    ("PLC_MOONLIGHT_PARK", "송도달빛축제공원", "Songdo Moonlight Festival Park", "PARK", None, 126.6347, 37.4067),
]
PLACE_XY = {p[0]: (p[5], p[6]) for p in PLACES}

# Synthetic route patterns: a corridor through landmark waypoints, snapped to real stops.
# (route_id, route_number, name_ko, name_en, type, waypoints, headway_min, speed_kmh, vehicle_type, capacity)
ROUTES = [
    ("RT_8", "8", "8번 (합성 노선: 인천대-연세대)", "Route 8 (synthetic pattern: INU - Yonsei)", "TRUNK",
     ["PLC_INU", "PLC_STN_INU", "PLC_CONVENSIA", "PLC_STN_TECHNO_PARK", "PLC_YONSEI_IC"], 15, 22.0, "BUS", 60),
    ("RT_SDA", "SD-A", "송도순환 A (합성 노선)", "Songdo Loop A (synthetic pattern)", "BRANCH",
     ["PLC_MOONLIGHT_PARK", "PLC_STN_CENTRAL_PARK", "PLC_CENTRAL_PARK", "PLC_CONVENSIA", "PLC_HAEDOJI_PARK"],
     15, 20.0, "EV_BUS", 45),
    ("RT_AV1", "AV-1", "자율주행 셔틀 1 (합성 노선)", "Autonomous Shuttle 1 (synthetic pattern)", "AUTONOMOUS_SHUTTLE",
     ["PLC_STN_INU", "PLC_CONVENSIA", "PLC_HAEDOJI_PARK"], 10, 15.0, "AUTONOMOUS_SHUTTLE", 12),
]
FIRST_DEPARTURE = timedelta(hours=6, minutes=30)
LAST_DEPARTURE = timedelta(hours=10)
PEAK = (timedelta(hours=7, minutes=30), timedelta(hours=9))
LAYOVER = timedelta(minutes=12)
MAX_LATENESS_S = 540


# --------------------------------------------------------------------------- geometry helpers
def to_xy(lon: float, lat: float) -> tuple[float, float]:
    """Local metres (equirectangular around Songdo) — fine for corridor geometry."""
    return (lon - 126.64) * 111_320 * math.cos(math.radians(37.39)), (lat - 37.39) * 110_540


def dist_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    ax, ay = to_xy(*a)
    bx, by = to_xy(*b)
    return math.hypot(bx - ax, by - ay)


def bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    ax, ay = to_xy(*a)
    bx, by = to_xy(*b)
    return math.degrees(math.atan2(bx - ax, by - ay)) % 360


# --------------------------------------------------------------------------- core
def core_section() -> list[str]:
    lines = ["-- ---- core.source_registry (from source_registry.csv) ----"]
    with (ROOT / "source_registry.csv").open(encoding="utf-8", newline="") as fh:
        rows = [[r["source_id"], r["title_ko"], r["title_en"], r["provider"], r["source_url"],
                 int(r["verification_level"]), r["provenance"], r["fallback_strategy"], r["notes"]]
                for r in csv.DictReader(fh)]
    cols = ["source_id", "title_ko", "title_en", "provider", "source_url", "verification_level",
            "provenance", "fallback_strategy", "notes"]
    lines += insert("core.source_registry", cols, rows,
                    "(source_id) DO UPDATE SET " + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols[1:]))
    lines += ["", "-- ---- core.places (curated landmarks) ----"]
    lines += insert(
        "core.places", ["place_id", "name_ko", "name_en", "category", "road_address", "geom", "provenance"],
        [[pid, ko, en, cat, addr, point(lon, lat), "SRC_CORE_01"] for pid, ko, en, cat, addr, lon, lat in PLACES],
        "(place_id) DO UPDATE SET name_ko = EXCLUDED.name_ko, name_en = EXCLUDED.name_en, "
        "category = EXCLUDED.category, road_address = EXCLUDED.road_address, geom = EXCLUDED.geom")
    return lines


# --------------------------------------------------------------------------- mobility
def load_stops() -> list[dict]:
    with (ROOT / "fixtures" / "songdo_stops.csv").open(encoding="utf-8", newline="") as fh:
        stops = list(csv.DictReader(fh))
    for s in stops:
        s["xy"] = (float(s["lon"]), float(s["lat"]))
    return stops


def build_pattern(stops: list[dict], waypoints: list[tuple[float, float]], exclude: set[str]) -> list[dict]:
    """Stops within 250 m of the waypoint polyline, in travel order, at least 300 m apart."""
    pattern: list[dict] = []
    used = set(exclude)
    for a, b in zip(waypoints, waypoints[1:]):
        ax, ay = to_xy(*a)
        bx, by = to_xy(*b)
        seg2 = (bx - ax) ** 2 + (by - ay) ** 2
        candidates = []
        for s in stops:
            if s["stop_id"] in used:
                continue
            px, py = to_xy(*s["xy"])
            t = ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / seg2
            if not 0 <= t <= 1:
                continue
            off = math.hypot(px - (ax + t * (bx - ax)), py - (ay + t * (by - ay)))
            if off <= 250:
                candidates.append((t, off, s["stop_id"], s))
        for _, _, _, s in sorted(candidates, key=lambda c: (c[0], c[1], c[2])):
            if pattern and dist_m(pattern[-1]["xy"], s["xy"]) < 300:
                continue
            pattern.append(s)
            used.add(s["stop_id"])
    return pattern


def mobility_section(rng: random.Random, n: int) -> list[str]:
    stops = load_stops()
    lines = ["", "-- ---- mobility.stops (REAL: Songdo subset of the official MOLIT file) ----"]
    lines += insert(
        "mobility.stops",
        ["stop_id", "stop_number", "stop_name_ko", "geom", "managing_bis", "collected_on", "source_id"],
        [[s["stop_id"], s["stop_number"], s["stop_name_ko"], point(*s["xy"]), s["managing_bis"],
          s["collected_on"], "SRC_MOB_03"] for s in stops],
        "(stop_id) DO UPDATE SET stop_number = EXCLUDED.stop_number, stop_name_ko = EXCLUDED.stop_name_ko, "
        "geom = EXCLUDED.geom, managing_bis = EXCLUDED.managing_bis, collected_on = EXCLUDED.collected_on")

    route_rows, pattern_rows, vehicle_rows, trip_rows, event_rows = [], [], [], [], []
    timelines = []  # (vehicle_id, vehicle_type, trip_id, [(time, (lon, lat))...])

    for route_id, number, name_ko, name_en, rtype, wp_ids, headway, speed, vtype, capacity in ROUTES:
        route_rows.append([route_id, number, name_ko, name_en, rtype, "SRC_MOB_SYN"])
        waypoints = [PLACE_XY[w] for w in wp_ids]
        outbound = build_pattern(stops, waypoints, set())
        inbound = build_pattern(stops, waypoints[::-1], {s["stop_id"] for s in outbound})  # opposite-side poles
        patterns = {0: outbound, 1: inbound}
        for direction, pattern in patterns.items():
            if len(pattern) < 4:
                raise SystemExit(f"{route_id} direction {direction}: only {len(pattern)} stops found")
            pattern_rows += [[route_id, direction, i, s["stop_id"]] for i, s in enumerate(pattern, 1)]

        fleet: list[tuple[str, datetime]] = []  # (vehicle_id, free_from)
        departure = FIRST_DEPARTURE
        while departure <= LAST_DEPARTURE:
            for direction, pattern in patterns.items():
                start = SERVICE_DAY + departure
                # schedule
                sched = [start]
                for prev, cur in zip(pattern, pattern[1:]):
                    travel = dist_m(prev["xy"], cur["xy"]) / (speed / 3.6)
                    sched.append(sched[-1] + timedelta(seconds=round(travel + 25)))
                end = sched[-1] + timedelta(seconds=60)
                # first free vehicle of this route, or a new one
                for idx, (vid, free_from) in enumerate(fleet):
                    if free_from <= start:
                        fleet[idx] = (vid, end + LAYOVER)
                        break
                else:
                    vid = f"VEH-{route_id[3:]}-{len(fleet) + 1:02d}"
                    fleet.append((vid, end + LAYOVER))
                    plate = f"인천{70 + len(vehicle_rows) // 100}자{1000 + len(vehicle_rows) * 37 % 9000:04d}"
                    vehicle_rows.append([vid, plate, vtype, capacity, True, "SRC_MOB_SYN"])

                trip_id = f"T{start:%Y%m%d}-{number}-{direction}-{start:%H%M}"
                trip_rows.append([trip_id, route_id, direction, vid, start.date().isoformat(), start, end,
                                  "COMPLETED", "SRC_MOB_SYN"])
                # observed run: lateness drifts, more during the peak
                in_peak = PEAK[0] <= departure < PEAK[1]
                late = rng.gauss(15, 25)
                knots, prev_departure, load = [], None, 0
                for seq, (stop, planned) in enumerate(zip(pattern, sched), 1):
                    late += rng.gauss(14 if in_peak else 2, 45 if in_peak else 20)
                    late = max(-60.0, min(float(MAX_LATENESS_S), late))
                    arrive = planned + timedelta(seconds=round(late))
                    if prev_departure is not None and arrive < prev_departure + timedelta(seconds=20):
                        arrive = prev_departure + timedelta(seconds=20)
                    leave = arrive + timedelta(seconds=rng.randint(12, 55 if in_peak else 35))
                    last = seq == len(pattern)
                    alightings = load if last else rng.randint(0, min(load, 6 if in_peak else 3))
                    boardings = 0 if last else rng.randint(0, min(capacity - load + alightings, 9 if in_peak else 4))
                    load += boardings - alightings
                    event_rows.append([trip_id, seq, stop["stop_id"], planned, arrive, leave, boardings, alightings])
                    knots += [(arrive, stop["xy"]), (leave, stop["xy"])]
                    prev_departure = leave
                timelines.append((vid, vtype, trip_id, knots))
            departure += timedelta(minutes=headway)

    # GPS telemetry: sample every `interval` seconds along each trip's timeline
    total_s = sum((k[-1][0] - k[0][0]).total_seconds() for *_, k in timelines)
    interval = int(max(5, min(120, round(total_s / (10 * n)))))
    battery: dict[str, float] = {}
    obs_rows = []
    for vid, vtype, trip_id, knots in sorted(timelines, key=lambda t: (t[3][0][0], t[0])):
        t, i = knots[0][0], 0
        while t <= knots[-1][0]:
            while i < len(knots) - 2 and knots[i + 1][0] <= t:
                i += 1
            (t0, a), (t1, b) = knots[i], knots[i + 1]
            span = (t1 - t0).total_seconds()
            f = 0.0 if span == 0 else (t - t0).total_seconds() / span
            lon = a[0] + f * (b[0] - a[0]) + rng.gauss(0, 0.00003)
            lat = a[1] + f * (b[1] - a[1]) + rng.gauss(0, 0.00003)
            lon = min(max(lon, BBOX[0] + 1e-5), BBOX[2] - 1e-5)
            lat = min(max(lat, BBOX[1] + 1e-5), BBOX[3] - 1e-5)
            moving = a != b and span > 0
            speed = max(0.0, dist_m(a, b) / span * 3.6 + rng.gauss(0, 2)) if moving else 0.0
            heading = round(bearing(a, b), 1) % 360 if moving else None
            soc = None
            if vtype != "BUS":
                battery[vid] = max(20.0, battery.get(vid, 96.0) - (0.004 * interval if moving else 0.001 * interval))
                soc = round(battery[vid], 2)
            obs_rows.append([vid, trip_id, point(lon, lat), round(speed, 1), heading, soc, t,
                             t + timedelta(seconds=rng.randint(1, 3)), "SRC_MOB_SYN"])
            t += timedelta(seconds=interval)

    lines += ["", "-- ---- mobility routes / patterns / fleet / trips (SYNTHETIC) ----"]
    lines += insert("mobility.routes",
                    ["route_id", "route_number", "route_name_ko", "route_name_en", "route_type", "source_id"], route_rows)
    lines += insert("mobility.route_stops", ["route_id", "direction", "sequence_order", "stop_id"], pattern_rows)
    lines += insert("mobility.vehicles",
                    ["vehicle_id", "plate_number", "vehicle_type", "capacity", "is_active", "source_id"], vehicle_rows)
    lines += insert("mobility.trips",
                    ["trip_id", "route_id", "direction", "vehicle_id", "service_date", "scheduled_departure",
                     "scheduled_arrival", "status", "source_id"], trip_rows)
    lines += insert("mobility.trip_stop_events",
                    ["trip_id", "sequence_order", "stop_id", "scheduled_arrival", "observed_arrival",
                     "observed_departure", "boardings", "alightings"], event_rows)
    lines += ["", f"-- ---- mobility.vehicle_observations (SYNTHETIC, every {interval} s) ----"]
    lines += insert("mobility.vehicle_observations",
                    ["vehicle_id", "trip_id", "geom", "speed_kmh", "heading_deg", "battery_state_of_charge_pct",
                     "observed_at", "received_at", "source_id"], obs_rows)
    print(f"  mobility: {len(stops)} stops, {len(route_rows)} routes, {len(pattern_rows)} route stops, "
          f"{len(vehicle_rows)} vehicles, {len(trip_rows)} trips, {len(event_rows)} stop events, "
          f"{len(obs_rows)} GPS rows (every {interval} s)")
    return lines


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    rng = random.Random(SEED)  # every domain section must draw from this generator only

    lines = [
        "-- GENERATED by fixtures/seed_fixtures.py — do not edit by hand.",
        f"-- Deterministic: seed={SEED}, N={n}. Safe to load more than once.",
        "SET client_encoding = 'UTF8';",
        "",
    ]
    lines += core_section()
    lines += mobility_section(rng, n)
    lines += building_section(rng, n, PLACE_XY)
    # Next domain sections (tourism, environment, safety) are appended here.

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1024:.0f} kB, N={n})")


if __name__ == "__main__":
    main()
