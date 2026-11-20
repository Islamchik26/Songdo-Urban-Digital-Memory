"""How each task's result is shown to a person: a plain-language headline, key numbers, and charts.

  VIZ[task]    declarative chart specs (JSON) that the console draws; no task-specific frontend code
  STORY[task]  a function (rows, params) -> {"headline": str, "stats": [{"label", "value"}]} computed from
               the very rows the task returned, so the sentence always agrees with the table below it

describe() is called by the scenario router after the SQL ran. A failing story never fails the task:
the run still returns its rows, just without a headline.

Chart types (ui/src/charts.tsx):
  line         x over time or categories, one series, optional min-max band, threshold lines, alert markers
  columns      vertical bars over the same categories as a line chart
  bars         horizontal bars per row, optional status colour and reference line (value, param or column)
  paired_bars  two measures of the same unit per row (e.g. visitors vs seats), with a status badge
  timeline     intervals from start to end (open end = the as-of time), one lane per row
  stepper      status changes in order, with the time spent in each state
  tree         indented hierarchy from a "A > B > C" path column, with a count per node
  meters       one meter per row against its own thresholds, coloured by status
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")

# status roles map to the fixed status palette (good / warning / serious / critical) + neutral
VIZ: dict[str, list[dict[str, Any]]] = {
    "DB1-2": [
        {"type": "line", "title": "Lateness at each stop",
         "x": {"column": "stop_name_ko", "label": "Stop (in order)"},
         "y": {"column": "lateness_seconds", "label": "Lateness", "unit": "min", "divide": 60},
         "zero_line": True},
        {"type": "columns", "title": "Passengers on board after each stop",
         "x": {"column": "stop_name_ko", "label": "Stop (in order)"},
         "y": {"column": "passengers_on_board", "label": "Passengers", "unit": "people"}},
    ],
    "DB1-4": [
        {"type": "bars", "title": "Actual time since the previous bus, largest deviations first",
         "label": ["stop_name_ko", "trip_id"], "limit": 15,
         "value": {"column": "headway_seconds", "label": "Headway", "unit": "min", "divide": 60},
         "reference": {"column": "scheduled_headway_seconds", "label": "Scheduled headway"},
         "status": {"column": "headway_status",
                    "map": {"OK": "good", "BUNCHED": "warning", "GAP": "critical"},
                    "labels": {"OK": "On schedule", "BUNCHED": "Bunched (too close)", "GAP": "Gap (too far apart)"}}},
    ],
    "DB2-2": [
        {"type": "tree", "title": "Floors, rooms and the devices installed in them",
         "path": "path", "count": {"column": "devices", "label": "devices"}, "detail": "installed",
         "kind": "space_type"},
    ],
    "DB2-3": [
        {"type": "timeline", "title": "Which room the device was in, and for how long",
         "label": "space_name", "sub": "floor_number", "sub_prefix": "Floor ",
         "start": "assigned_at", "end": "unassigned_at", "open_end_param": "as_of",
         "current": "is_current", "duration": {"column": "days_in_space", "unit": "days"}},
    ],
    "DB2-4": [
        {"type": "bars", "title": "Minutes since each device's last reading",
         "label": ["device_id", "space_name"],
         "value": {"column": "silent_minutes", "label": "Silent for", "unit": "min"},
         "reference": {"param": "silent_minutes", "label": "Alarm threshold"},
         "null_label": "never reported",
         "status": {"column": "finding",
                    "map": {"NEVER_REPORTED": "critical", "UNDETECTED_FAULT": "serious", "KNOWN_ISSUE": "warning"},
                    "labels": {"NEVER_REPORTED": "Never reported", "UNDETECTED_FAULT": "Fault not yet flagged",
                               "KNOWN_ISSUE": "Already flagged"}}},
    ],
    "DB3-5": [
        {"type": "paired_bars", "title": "Expected visitors vs bus seats arriving before the start",
         "label": "event_name_ko", "sub": "venue", "unit": "people",
         "a": {"column": "expected_attendance", "label": "Expected visitors"},
         "b": {"column": "seat_capacity", "label": "Bus seats arriving"},
         "status": {"column": "transit_status",
                    "map": {"OK": "good", "OVER_CAPACITY": "serious", "NO_SERVICE": "critical"},
                    "labels": {"OK": "Enough seats", "OVER_CAPACITY": "Not enough seats",
                               "NO_SERVICE": "No bus nearby"}}},
    ],
    "DB4-4": [
        {"type": "line", "title": "Hourly average over the day",
         "x": {"column": "hour_start", "label": "Hour (KST)", "format": "time"},
         "y": {"column": "avg_value", "label": "Hourly average", "unit_column": "unit"},
         "band": {"min": "min_value", "max": "max_value", "label": "Lowest to highest reading in the hour"},
         "thresholds": [{"column": "bad_threshold", "label": "Bad", "role": "warning"},
                        {"column": "very_bad_threshold", "label": "Very bad", "role": "critical"}],
         "markers": {"column": "alert_severity", "map": {"BAD": "warning", "VERY_BAD": "critical"},
                     "label": "Hour inside an alert episode"}},
    ],
    "DB4-5": [
        {"type": "meters", "title": "Latest reading of each pollutant against its limits",
         "label": "type_code", "value": "value", "unit_column": "unit",
         "thresholds": [{"column": "bad_threshold", "label": "Bad"},
                        {"column": "very_bad_threshold", "label": "Very bad"}],
         "age": {"column": "age_minutes", "unit": "min old"},
         "status": {"column": "grade", "map": {"NORMAL": "good", "BAD": "warning", "VERY_BAD": "critical"},
                    "labels": {"NORMAL": "Normal", "BAD": "Bad", "VERY_BAD": "Very bad"}}},
    ],
    "DB5-3": [
        {"type": "stepper", "title": "Life of the incident, step by step",
         "state": "to_status", "time": "changed_at", "duration": "seconds_in_previous_state",
         "detail": "dispatched_facility", "actor": "actor"},
    ],
    "DB5-4": [
        {"type": "bars", "title": "Average response time (report to arrival on scene)",
         "label": ["facility_name_ko"],
         "value": {"column": "avg_response_s", "label": "Average", "unit": "min", "divide": 60},
         "marker": {"column": "p90_response_s", "label": "9 in 10 answered within"}},
        {"type": "bars", "title": "Share of incidents answered within the target time",
         "label": ["facility_name_ko"],
         "value": {"column": "sla_met_pct", "label": "Within target", "unit": "%", "max": 100}},
    ],
}


# --------------------------------------------------------------------------- formatting helpers
def _t(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(KST)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).astimezone(KST)
        except ValueError:
            return None
    return None


def hhmm(value: Any) -> str:
    t = _t(value)
    return t.strftime("%H:%M") if t else "?"


def minutes(seconds: float | None, digits: int = 1) -> str:
    if seconds is None:
        return "?"
    m = seconds / 60
    return f"{m:.{digits}f} min" if abs(m) < 10 or digits == 0 else f"{m:.0f} min"


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    s = int(round(seconds))
    if s < 3600:
        return f"{s // 60} min" if s % 60 == 0 or s >= 600 else f"{s // 60} min {s % 60} s"
    h, rest = divmod(s, 3600)
    return f"{h} h {rest // 60} min"


def num(v: float | None, digits: int = 0) -> str:
    if v is None:
        return "?"
    return f"{v:,.{digits}f}"


def pollutant(code: str) -> str:
    return {"PM25": "PM2.5", "O3": "O₃", "NO2": "NO₂", "SO2": "SO₂"}.get(code, code)


def sig(v: float) -> str:
    """Three significant digits: 93.1, 26.7, 0.0874 (ppm values stay readable)."""
    return f"{v:.3g}" if abs(v) < 1000 else f"{v:,.0f}"


def stat(label: str, value: str) -> dict[str, str]:
    return {"label": label, "value": value}


def story(headline: str, *stats: dict[str, str]) -> dict[str, Any]:
    return {"headline": headline, "stats": list(stats)}


# --------------------------------------------------------------------------- stories
def _db1_2(rows: list[dict], params: dict) -> dict:
    worst = max(rows, key=lambda r: r["lateness_seconds"] or 0)
    last = rows[-1]
    peak = max(rows, key=lambda r: r["passengers_on_board"] or 0)
    late = last["lateness_seconds"] or 0
    behind = ("the delay grew all the way there" if worst is last else
              f"it was furthest behind at {worst['stop_name_ko']} ({minutes(worst['lateness_seconds'])} late)")
    return story(
        f"Trip {params['trip_id']} reached its last stop, {last['stop_name_ko']}, "
        f"{minutes(abs(late))} {'late' if late >= 0 else 'early'}; {behind}. "
        f"The bus was fullest after {peak['stop_name_ko']}, with {peak['passengers_on_board']} people on board.",
        stat("Stops served", str(len(rows))),
        stat("Worst delay", minutes(worst["lateness_seconds"])),
        stat("Average stop time", f"{sum(r['dwell_seconds'] or 0 for r in rows) / len(rows):.0f} s"),
        stat("Most people on board", str(peak["passengers_on_board"])))


def _db1_4(rows: list[dict], params: dict) -> dict:
    gaps = sum(r["headway_status"] == "GAP" for r in rows)
    bunched = sum(r["headway_status"] == "BUNCHED" for r in rows)
    w = rows[0]           # the task orders by the largest deviation from the schedule
    return story(
        f"On route {params['route_number']} between {hhmm(params['from_time'])} and {hhmm(params['to_time'])}, "
        f"{gaps} arrivals came after a gap and {bunched} came bunched behind the previous bus. "
        f"The worst: at {w['stop_name_ko']} a bus came {minutes(w['headway_seconds'], 0)} after the previous one, "
        f"where the timetable says {minutes(w['scheduled_headway_seconds'], 0)}.",
        stat("Headways checked", str(len(rows))),
        stat("Gaps", str(gaps)),
        stat("Bunched", str(bunched)),
        stat("On schedule", str(len(rows) - gaps - bunched)))


def _db2_2(rows: list[dict], params: dict) -> dict:
    floors = sum(r["depth"] == 1 for r in rows)
    rooms = sum(r["depth"] > 1 for r in rows)
    devices = sum(r["devices"] or 0 for r in rows)
    empty = sum(r["depth"] > 1 and not r["devices"] for r in rows)
    return story(
        f"{params['building_id']} has {floors} floors with {rooms} rooms below them, and {devices} devices "
        f"installed; " + ("every room has at least one device." if not empty else
                          f"{empty} room{'s have' if empty != 1 else ' has'} no device yet."),
        stat("Floors", str(floors)), stat("Rooms", str(rooms)), stat("Installed devices", str(devices)))


def _db2_3(rows: list[dict], params: dict) -> dict:
    current = next((r for r in rows if r["is_current"]), rows[-1])
    moves = len(rows) - 1
    return story(
        f"{params['device_id']} has been in {len(rows)} rooms: it was moved {moves} time{'s' if moves != 1 else ''}. "
        f"At {hhmm(params['as_of'])} on {str(params['as_of'])[:10]} it was in {current['space_name']} "
        f"(floor {current['floor_number']}), where it has been for {current['days_in_space']} days.",
        stat("Rooms so far", str(len(rows))),
        stat("Moves", str(moves)),
        stat("Current room", f"{current['space_name']}"),
        stat("Longest stay", f"{max(r['days_in_space'] or 0 for r in rows)} days"))


def _db2_4(rows: list[dict], params: dict) -> dict:
    never = [r for r in rows if r["finding"] == "NEVER_REPORTED"]
    undetected = [r for r in rows if r["finding"] == "UNDETECTED_FAULT"]
    timed = [r for r in rows if r["silent_minutes"] is not None]
    longest = max(timed, key=lambda r: r["silent_minutes"]) if timed else None
    parts = [f"At {hhmm(params['as_of'])}, {len(rows)} device{'s have' if len(rows) != 1 else ' has'} been silent "
             f"for {params['silent_minutes']} minutes or more."]
    if undetected:
        parts.append(f"{len(undetected)} still report{'s' if len(undetected) == 1 else ''} ACTIVE, so the fault has "
                     f"not been noticed yet ({', '.join(r['device_id'] for r in undetected)}).")
    if never:
        parts.append(f"{len(never)} never sent any reading.")
    return story(" ".join(parts),
                 stat("Silent devices", str(len(rows))),
                 stat("Fault not yet flagged", str(len(undetected))),
                 stat("Never reported", str(len(never))),
                 stat("Longest silence", f"{longest['silent_minutes']} min" if longest else "—"))


def _db3_5(rows: list[dict], params: dict) -> dict:
    short = [r for r in rows if r["transit_status"] != "OK"]
    ratio = [r for r in rows if r["attendees_per_seat"] is not None]
    worst = max(ratio, key=lambda r: r["attendees_per_seat"]) if ratio else None
    nobus = [r for r in rows if r["transit_status"] == "NO_SERVICE"]
    text = (f"On {params['event_date']}, {len(short)} of {len(rows)} events expect more visitors than the bus seats "
            f"arriving within {int(params['radius_m'])} m in the {params['window_min']} minutes before they start.")
    if worst:
        text += (f" {worst['event_name_ko']} is the tightest: about {worst['attendees_per_seat']} visitors "
                 f"for every seat.")
    if nobus:
        text += f" {nobus[0]['event_name_ko']} has no bus stopping nearby at all."
    return story(text,
                 stat("Events", str(len(rows))),
                 stat("Short of seats", str(len(short))),
                 stat("Expected visitors", num(sum(r["expected_attendance"] or 0 for r in rows))),
                 stat("Bus seats arriving", num(sum(r["seat_capacity"] or 0 for r in rows))))


def _db4_4(rows: list[dict], params: dict) -> dict:
    unit = rows[0]["unit"]
    peak = max(rows, key=lambda r: r["avg_value"])
    bad = rows[0]["bad_threshold"]
    over = [r for r in rows if bad is not None and r["avg_value"] > bad]
    alerts = [r for r in rows if r["alert_severity"]]
    avg = sum(r["avg_value"] for r in rows) / len(rows)
    text = (f"{pollutant(params['type_code'])} at {params['station']} station on {params['day']}: the hourly average peaked "
            f"at {sig(peak['avg_value'])} {unit} at {hhmm(peak['hour_start'])}.")
    text += (f" {len(over)} hour{'s were' if len(over) != 1 else ' was'} above the 'bad' limit of {bad:g} {unit}."
             if over else f" Every hour stayed below the 'bad' limit of {bad:g} {unit}." if bad is not None else "")
    return story(text,
                 stat("Hours measured", str(len(rows))),
                 stat("Day average", f"{sig(avg)} {unit}"),
                 stat("Peak hour", f"{sig(peak['avg_value'])} {unit} at {hhmm(peak['hour_start'])}"),
                 stat("Hours in an alert", str(len(alerts))))


def _db4_5(rows: list[dict], params: dict) -> dict:
    station = rows[0]["nearest_station"]
    worse = [r for r in rows if r["grade"] != "NORMAL"]
    oldest = max(r["age_minutes"] or 0 for r in rows)
    text = (f"The nearest monitoring station is {station}, {rows[0]['distance_m']:,.0f} m away. ")
    text += (f"{', '.join(pollutant(r['type_code']) for r in worse)} {'is' if len(worse) == 1 else 'are'} "
             f"above the 'bad' limit."
             if worse else f"All {len(rows)} pollutants are within the normal range.")
    text += (" All readings are current." if oldest == 0 else
             f" All readings are at most {oldest} minute{'s' if oldest != 1 else ''} old.")
    return story(text,
                 stat("Nearest station", station),
                 stat("Distance", f"{rows[0]['distance_m']:,.0f} m"),
                 stat("Pollutants above limit", str(len(worse))),
                 stat("Oldest reading", f"{oldest} min"))


def _db5_3(rows: list[dict], params: dict) -> dict:
    first, last = rows[0], rows[-1]
    on_scene = next((r for r in rows if r["to_status"] == "ON_SCENE"), None)
    total = last["seconds_since_report"]
    text = (f"{params['incident_id']} was reported at {hhmm(first['changed_at'])}; its latest status, "
            f"{last['to_status']}, came {duration(total)} after the report.")
    if on_scene:
        text += f" Responders were on scene {duration(on_scene['seconds_since_report'])} after the report."
    return story(text,
                 stat("Status changes", str(len(rows) - 1)),
                 stat("Current status", last["to_status"]),
                 stat("Report to on scene", duration(on_scene["seconds_since_report"]) if on_scene else "—"),
                 stat("Report to latest status", duration(total)))


def _db5_4(rows: list[dict], params: dict) -> dict:
    fastest = min(rows, key=lambda r: r["avg_response_s"])
    slowest = max(rows, key=lambda r: r["avg_response_s"])
    worst_sla = min(rows, key=lambda r: r["sla_met_pct"])
    return story(
        f"From {params['from_date']} to {params['to_date']}, {fastest['facility_name_ko']} answered fastest "
        f"(on average {minutes(fastest['avg_response_s'])}), {slowest['facility_name_ko']} slowest "
        f"({minutes(slowest['avg_response_s'])}). {worst_sla['facility_name_ko']} met the target time least often: "
        f"{worst_sla['sla_met_pct']}% of incidents.",
        stat("Facilities", str(len(rows))),
        stat("Incidents", num(sum(r["incidents"] for r in rows))),
        stat("Fastest average", minutes(fastest["avg_response_s"])),
        stat("Lowest within target", f"{worst_sla['sla_met_pct']}%"))


STORY: dict[str, Callable[[list[dict], dict], dict]] = {
    "DB1-2": _db1_2, "DB1-4": _db1_4, "DB2-2": _db2_2, "DB2-3": _db2_3, "DB2-4": _db2_4,
    "DB3-5": _db3_5, "DB4-4": _db4_4, "DB4-5": _db4_5, "DB5-3": _db5_3, "DB5-4": _db5_4,
}


def describe(task_id: str, rows: list[dict], params: dict) -> dict[str, Any]:
    """Charts and headline for one run; empty when the task has none or returned no rows."""
    out: dict[str, Any] = {"viz": VIZ.get(task_id), "story": None}
    fn = STORY.get(task_id)
    if fn and rows:
        try:
            out["story"] = fn(rows, params)
        except Exception:      # a headline must never break the task itself
            log.exception("story for %s failed", task_id)
    return out
