"""VIP building client: indoor state of a building next to the outdoor air around it.

    python -m consumers.vip_building_env_client                       G-Tower at 10:00 on the fixture day
    python -m consumers.vip_building_env_client BLD_CONVENSIA --as-of 2026-10-02T09:30:00+09:00
    python -m consumers.vip_building_env_client --json

    from consumers.vip_building_env_client import get_context
    get_context()                                   # dict

One call answers what a building operator asks before opening the fresh-air dampers:
how is the air inside, how is the air outside, and how much can each number be trusted
(reading age, distance to the station, devices that stopped reporting).

Sources: GET /api/v1/building/buildings/{id}/state  (DB2-5)
         GET /api/v1/environment/exposure           (DB4-5, nearest station to the building)
The ventilation advice is a simple rule written for this demo, not an engineering standard.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Any

import httpx

from consumers.api_client import api_client, get_json, parse_time

DEFAULT_BUILDING = "BLD_G_TOWER"
DEFAULT_AS_OF = "2026-10-02T10:00:00+09:00"    # building telemetry in the fixture covers 06:00-10:30
CO2_HIGH_PPM = 1000                            # demo threshold for "stale indoor air"
MAX_READING_AGE_MIN = 90                       # older outdoor readings are reported as stale
MAX_DEVICE_SILENCE_MIN = 30                    # indoor devices report every few minutes
FAR_STATION_M = 3000


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _ventilation_advice(co2: float | None, outdoor: dict[str, Any], stale: bool) -> tuple[str, str]:
    grades = {code: r["grade"] for code, r in outdoor.items() if code in ("PM10", "PM25")}
    if not grades or stale:
        return "UNKNOWN", "No current outdoor PM reading; use the building's own sensors."
    dirty = [code for code, grade in grades.items() if grade != "NORMAL"]
    if dirty and co2 is not None and co2 >= CO2_HIGH_PPM:
        return "FILTERED_INTAKE", (f"Indoor CO2 is high ({co2:.0f} ppm) and outdoor {'/'.join(dirty)} is elevated: "
                                   "ventilate through filters only.")
    if dirty:
        return "RECIRCULATE", f"Outdoor {'/'.join(dirty)} is elevated: keep outdoor air intake low."
    if co2 is not None and co2 >= CO2_HIGH_PPM:
        return "INCREASE_FRESH_AIR", f"Indoor CO2 is high ({co2:.0f} ppm) and outdoor air is clean: increase intake."
    return "NORMAL", "Indoor and outdoor air are both within normal range."


def get_context(building_id: str = DEFAULT_BUILDING, as_of: str | datetime | None = DEFAULT_AS_OF,
                client: httpx.Client | None = None) -> dict[str, Any]:
    """Indoor/outdoor context of one building as of a moment (None = now)."""
    if client is None:
        with api_client() as own:
            return get_context(building_id, as_of, own)

    moment = parse_time(as_of).isoformat() if as_of is not None else None
    state = get_json(client, f"/api/v1/building/buildings/{building_id}/state", as_of=moment)
    building = state["building"]
    exposure = get_json(client, "/api/v1/environment/exposure",
                        lon=building["lon"], lat=building["lat"], as_of=moment)

    by_metric: dict[str, list[float]] = {}
    for device in state["devices"]:
        if device["value"] is not None and device["device_type"] != "HVAC_CONTROLLER":
            by_metric.setdefault(device["metric"], []).append(device["value"])
    co2 = _mean(by_metric.get("CO2_PPM", []))
    oldest_indoor = max((d["age_minutes"] for d in state["devices"] if d["age_minutes"] is not None), default=None)

    outdoor = {r["type_code"]: {"value": r["value"], "unit": r["unit"], "grade": r["grade"],
                                "observed_at": r["observed_at"], "age_minutes": r["age_minutes"]}
               for r in exposure["readings"]}
    station = exposure["nearest_station"]
    oldest_outdoor = max((r["age_minutes"] for r in outdoor.values()), default=None)
    stale = oldest_outdoor is None or oldest_outdoor > MAX_READING_AGE_MIN

    caveats = []
    if stale:
        caveats.append("outdoor readings are missing or older than "
                       f"{MAX_READING_AGE_MIN} minutes")
    if station["distance_m"] > FAR_STATION_M:
        caveats.append(f"nearest station is {station['distance_m'] / 1000:.1f} km away")
    silent = [d["device_id"] for d in state["devices"]
              if d["value"] is None or d["age_minutes"] > MAX_DEVICE_SILENCE_MIN]
    if silent:
        caveats.append(f"no reading in the last {MAX_DEVICE_SILENCE_MIN} minutes from {', '.join(silent)}")
    action, reason = _ventilation_advice(co2, outdoor, stale)

    return {
        "as_of": moment or "now",
        "building": building,
        "indoor": {
            "avg_temperature_c": state["summary"]["avg_indoor_temperature_c"],
            "avg_co2_ppm": co2,
            "power_kw": state["summary"]["power_kw"],
            "devices": state["summary"]["devices"],
            "devices_not_active": state["summary"]["devices_not_active"],
            "open_maintenance_actions": state["summary"]["open_maintenance_actions"],
            "oldest_reading_age_minutes": oldest_indoor,
        },
        "outdoor": {
            "station": station["station_name_ko"],
            "station_distance_m": station["distance_m"],
            "oldest_reading_age_minutes": oldest_outdoor,
            "readings": outdoor,
        },
        "ventilation": {"action": action, "reason": reason},
        "caveats": caveats,
    }


def format_context(ctx: dict[str, Any]) -> str:
    b, i, o = ctx["building"], ctx["indoor"], ctx["outdoor"]
    lines = [
        f"{b['building_name_ko']} ({b['building_name_en']}, {b['building_id']})  as of {ctx['as_of']}",
        f"  indoor   {i['avg_temperature_c']} °C, CO2 {i['avg_co2_ppm']} ppm, power {i['power_kw']} kW",
        f"           {i['devices']} devices, {i['devices_not_active']} not active, "
        f"{i['open_maintenance_actions']} open maintenance",
        f"  outdoor  station {o['station']}, {o['station_distance_m']:.0f} m away",
    ]
    for code in ("PM25", "PM10", "O3", "NO2"):
        r = o["readings"].get(code)
        if r:
            lines.append(f"           {code:<5}{r['value']:>7} {r['unit']:<6} {r['grade']:<9} {r['age_minutes']} min old")
    lines.append(f"  advice   {ctx['ventilation']['action']}: {ctx['ventilation']['reason']}")
    lines += [f"  caveat   {c}" for c in ctx["caveats"]]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("building_id", nargs="?", default=DEFAULT_BUILDING)
    parser.add_argument("--as-of", default=DEFAULT_AS_OF, help="ISO time; 'now' for the current time")
    parser.add_argument("--json", action="store_true", help="print the raw dictionary")
    args = parser.parse_args()
    try:
        ctx = get_context(args.building_id, None if args.as_of == "now" else args.as_of)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    print(json.dumps(ctx, ensure_ascii=False, indent=2) if args.json else format_context(ctx))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
