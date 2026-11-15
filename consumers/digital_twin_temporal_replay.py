"""Digital-twin temporal replay: rebuild the state of the city at any past moment.

    python -m consumers.digital_twin_temporal_replay                              08:00-08:30, every 5 min
    python -m consumers.digital_twin_temporal_replay --start 2026-10-02T17:30+09:00 --end 2026-10-02T18:30+09:00 --step 600
    python -m consumers.digital_twin_temporal_replay --at 2026-10-02T08:20+09:00  one moment, every vehicle
    python -m consumers.digital_twin_temporal_replay --vehicle VEH-8-01           follow one vehicle

    from consumers.digital_twin_temporal_replay import vehicle_state_as_of, city_state_as_of, replay
    vehicle_state_as_of("2026-10-02 08:20+09")      # list of vehicles

Nothing is stored as "current state". Every frame is recomputed from history:
  vehicles   last GPS observation at or before T          (GET /api/v1/mobility/vehicles/positions, DB1-3)
  incidents  status each incident had at T, from its log  (GET /api/v1/safety/incidents/active, DB5-3)
So the same question asked next year returns the same answer.

A vehicle whose last observation is older than `max_staleness_s` (default 120 s) is reported
as not in service instead of being drawn at a stale position.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import httpx

from consumers.api_client import KST, api_client, get_json, parse_time
from consumers.smart_mobility_adapter import _metres

DEFAULT_START = "2026-10-02T08:00:00+09:00"
DEFAULT_END = "2026-10-02T08:30:00+09:00"
MAX_FRAMES = 500


def vehicle_state_as_of(t: str | datetime, max_staleness_s: int = 120,
                        client: httpx.Client | None = None) -> list[dict[str, Any]]:
    """Every vehicle's last known state at time t, with `in_service` derived from its staleness."""
    if client is None:
        with api_client() as own:
            return vehicle_state_as_of(t, max_staleness_s, own)
    rows = get_json(client, "/api/v1/mobility/vehicles/positions", as_of=parse_time(t).isoformat())
    for row in rows:
        row["in_service"] = row["staleness_seconds"] <= max_staleness_s
    return rows


def city_state_as_of(t: str | datetime, max_staleness_s: int = 120,
                     client: httpx.Client | None = None) -> dict[str, Any]:
    """One frame of the twin: vehicles and active incidents at time t."""
    if client is None:
        with api_client() as own:
            return city_state_as_of(t, max_staleness_s, own)
    moment = parse_time(t)
    vehicles = vehicle_state_as_of(moment, max_staleness_s, client)
    incidents = get_json(client, "/api/v1/safety/incidents/active", as_of=moment.isoformat())
    moving = [v for v in vehicles if v["in_service"]]
    return {
        "as_of": moment.astimezone(KST).isoformat(),
        "vehicles": vehicles,
        "active_incidents": incidents,
        "summary": {
            "vehicles_known": len(vehicles),
            "vehicles_in_service": len(moving),
            "avg_speed_kmh": round(sum(v["speed_kmh"] for v in moving) / len(moving), 1) if moving else None,
            "active_incidents": len(incidents),
        },
    }


def replay(start: str | datetime, end: str | datetime, step_s: int = 300, max_staleness_s: int = 120,
           client: httpx.Client | None = None) -> Iterator[dict[str, Any]]:
    """Yield one frame every step_s seconds from start to end (both included)."""
    first, last = parse_time(start), parse_time(end)
    if step_s <= 0:
        raise ValueError("step_s must be positive")
    if last < first:
        raise ValueError("end must not be before start")
    if (last - first).total_seconds() / step_s + 1 > MAX_FRAMES:
        raise ValueError(f"more than {MAX_FRAMES} frames; use a larger step")
    if client is None:
        with api_client() as own:
            yield from replay(first, last, step_s, max_staleness_s, own)
        return
    moment = first
    while moment <= last:
        yield city_state_as_of(moment, max_staleness_s, client)
        moment += timedelta(seconds=step_s)


def _print_frames(frames: Iterator[dict[str, Any]], vehicle_id: str | None) -> None:
    if vehicle_id:
        print(f"{'time (KST)':<10} {'lon':>11} {'lat':>10} {'km/h':>6} {'moved m':>8}  trip")
    else:
        print(f"{'time (KST)':<10} {'in service':>10} {'avg km/h':>9} {'incidents':>9}  active incidents")
    previous: tuple[float, float] | None = None
    for frame in frames:
        clock = frame["as_of"][11:19]
        if not vehicle_id:
            s = frame["summary"]
            titles = "; ".join(f"{i['title']} [{i['status']}]" for i in frame["active_incidents"])
            print(f"{clock:<10} {s['vehicles_in_service']:>4} of {s['vehicles_known']:<3} "
                  f"{s['avg_speed_kmh'] if s['avg_speed_kmh'] is not None else '-':>9} {s['active_incidents']:>9}  {titles}")
            continue
        match = [v for v in frame["vehicles"] if v["vehicle_id"] == vehicle_id]
        if not match:
            print(f"{clock:<10} no observation yet")
        elif not match[0]["in_service"]:
            print(f"{clock:<10} not in service (last seen {match[0]['staleness_seconds']} s earlier)")
            previous = None
        else:
            v = match[0]
            here = (v["lon"], v["lat"])
            moved = f"{_metres(previous, here):8.0f}" if previous else f"{'':>8}"
            print(f"{clock:<10} {v['lon']:>11.6f} {v['lat']:>10.6f} {v['speed_kmh']:>6} {moved}  {v['trip_id'] or '-'}")
            previous = here


def _print_moment(frame: dict[str, Any]) -> None:
    print(f"city state as of {frame['as_of']}")
    for v in frame["vehicles"]:
        status = "in service" if v["in_service"] else f"idle, last seen {v['staleness_seconds']} s earlier"
        print(f"  {v['vehicle_id']:<11} {v['vehicle_type']:<19} ({v['lon']:.6f}, {v['lat']:.6f}) "
              f"{v['speed_kmh']:>5} km/h  {status}")
    for i in frame["active_incidents"]:
        print(f"  incident {i['incident_id']} {i['severity']:<8} {i['status']:<10} {i['title']}")
    print(f"  {frame['summary']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--step", type=int, default=300, help="seconds between frames")
    parser.add_argument("--at", help="print one moment in full instead of a replay")
    parser.add_argument("--vehicle", help="follow one vehicle through the replay")
    parser.add_argument("--max-staleness", type=int, default=120, help="seconds before a vehicle counts as idle")
    args = parser.parse_args()
    try:
        with api_client() as client:
            if args.at:
                _print_moment(city_state_as_of(args.at, args.max_staleness, client))
            else:
                _print_frames(replay(args.start, args.end, args.step, args.max_staleness, client), args.vehicle)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
