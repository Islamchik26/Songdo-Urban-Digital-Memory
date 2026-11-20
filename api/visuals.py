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
  counts       rows grouped by one column and counted (optionally counting distinct values)
  cards        one card per row: a main value, a few labelled details and a status badge
  paired_bars also takes "series" (2-3 measures); timeline also takes mode "date" or "clock" (with a marker);
  stepper also takes "gap" (a distance between steps) and "scroll" for long sequences
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
    if s >= 48 * 3600:
        return f"{s / 86400:.1f} days"
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


# --------------------------------------------------------------------------- step 2: the other 26 tasks
SEVERITY = {"column": "severity", "map": {"CRITICAL": "critical", "HIGH": "serious", "MEDIUM": "warning", "LOW": "good"},
            "labels": {"CRITICAL": "Critical", "HIGH": "High", "MEDIUM": "Medium", "LOW": "Low"}}
PROVENANCE = {"OFFICIAL_OPEN_DATA": "Official open data", "CURATED_MANUAL": "Typed in by the team",
              "SYNTHETIC_FIXTURE": "Generated (synthetic)"}
REASONS = {"OUT_OF_BOUNDS": "Outside Songdo", "COORDINATE_SWAPPED": "Latitude / longitude swapped",
           "SCHEMA_VIOLATION": "Invalid or missing field", "FK_VIOLATION": "Unknown related record",
           "FAULT_READING": "Sensor fault marker", "DUPLICATE_AUTHORITY": "Published by a second system",
           "MISSING_FIELD": "Missing field", "MISSING_COORDINATE": "No coordinate",
           "MALFORMED_COORDINATE": "Unreadable coordinate"}
RECONCILE = {"column": "reconciliation_status",
             "map": {"CONFIRMED": "good", "REVIEW": "warning", "UNLINKED": "serious"},
             "labels": {"CONFIRMED": "Confirmed", "REVIEW": "Address differs, check", "UNLINKED": "No place linked"}}
GRADE = {"column": "grade", "map": {"NORMAL": "good", "BAD": "warning", "VERY_BAD": "critical", "NOT_GRADED": "neutral"},
         "labels": {"NORMAL": "Normal", "BAD": "Bad", "VERY_BAD": "Very bad", "NOT_GRADED": "Not graded"}}
RESPONSE = [
    {"type": "bars", "title": "Average response time (report to arrival on scene)",
     "label": ["facility_name_ko"],
     "value": {"column": "avg_response_s", "label": "Average", "unit": "min", "divide": 60}},
    {"type": "bars", "title": "Share of incidents answered within the target time",
     "label": ["facility_name_ko"],
     "value": {"column": "sla_met_pct", "label": "Within target", "unit": "%", "max": 100}},
]

VIZ.update({
    "CORE-1": [
        {"type": "bars", "title": "Distance of each place from the point",
         "label": ["name_ko", "category"],
         "value": {"column": "dist_m", "label": "Distance", "unit": "m"},
         "reference": {"param": "radius_m", "label": "Search radius"}},
    ],
    "CORE-2": [
        {"type": "counts", "title": "Where the data comes from", "group": "provenance", "labels": PROVENANCE,
         "unit": "sources"},
        {"type": "counts", "title": "How well each source is verified (4 = best)", "group": "verification_level",
         "unit": "sources", "labels": {"1": "Level 1", "2": "Level 2", "3": "Level 3", "4": "Level 4"}},
    ],
    "CORE-3": [
        {"type": "counts", "title": "Why records were set aside", "group": "reason_code", "labels": REASONS,
         "unit": "records"},
        {"type": "counts", "title": "Which table they were meant for", "group": "target_table", "unit": "records"},
    ],
    "CORE-4": [
        {"type": "counts", "title": "Which tables were written", "group": "table_name", "unit": "entries"},
        {"type": "counts", "title": "What kind of change", "group": "action", "unit": "entries",
         "labels": {"INSERT": "New record", "UPDATE": "Change", "DELETE": "Deletion", "STATUS_CHANGE": "Status change"}},
    ],
    "DB1-1": [
        {"type": "stepper", "title": "Stops in order, with the walking distance between them",
         "state": "stop_name_ko", "detail": "stop_number", "gap": {"column": "metres_from_previous", "unit": "m"},
         "scroll": True, "note": "Numbers between the stops are the straight-line distance from the previous stop."},
    ],
    "DB1-3": [
        {"type": "bars", "title": "Speed of each vehicle at that moment",
         "label": ["vehicle_id", "trip_id"],
         "value": {"column": "speed_kmh", "label": "Speed", "unit": "km/h"}},
        {"type": "bars", "title": "How old each vehicle's last GPS fix was",
         "label": ["vehicle_id", "vehicle_type"],
         "value": {"column": "staleness_seconds", "label": "Age of the fix", "unit": "s"}},
    ],
    "DB1-5": [
        {"type": "bars", "title": "Stops near the shuttle's latest GPS fixes",
         "label": ["stop_name_ko", "observed_at"], "time_columns": ["observed_at"],
         "value": {"column": "distance_m", "label": "Distance", "unit": "m"},
         "reference": {"param": "radius_m", "label": "Search radius"}},
    ],
    "DB2-1": [
        {"type": "counts", "title": "Result of the check", "group": "reconciliation_status", "status": RECONCILE,
         "unit": "buildings"},
        {"type": "bars", "title": "Distance between each building and its nearest place",
         "label": ["building_name_ko", "building_id"],
         "value": {"column": "distance_m", "label": "Distance", "unit": "m"},
         "reference": {"param": "max_distance_m", "label": "Matching limit"}, "status": RECONCILE,
         "null_label": "no place nearby"},
    ],
    "DB2-5": [
        {"type": "cards", "title": "Latest reading of every device in the building",
         "card_title": "device_id", "subtitle": ["space_name", "device_type"],
         "value": {"column": "value", "unit_column": "unit", "label_column": "metric",
                   "label_map": {"TEMPERATURE_C": "Temperature", "CO2_PPM": "CO₂ concentration",
                                 "POWER_KW": "Power draw"}},
         "status": {"column": "current_status", "map": {"ACTIVE": "good", "MAINTENANCE": "warning", "FAULT": "serious"},
                    "labels": {"ACTIVE": "Active", "MAINTENANCE": "Maintenance", "FAULT": "Fault"}},
         "notes": [{"column": "age_minutes", "label": "Reading age", "unit": "min"},
                   {"column": "open_actions", "label": "Open maintenance"},
                   {"column": "last_maintenance", "label": "Last maintenance", "format": "date"}]},
    ],
    "DB3-1": [
        {"type": "cards", "title": "Attractions, their accessibility and nearest stop",
         "card_title": "attraction_name_ko", "subtitle": ["category"],
         "value": {"column": "stop_distance_m", "unit": "m", "label": "to the nearest stop"},
         "notes": [{"column": "nearest_stop", "label": "Nearest stop"},
                   {"column": "is_wheelchair_accessible", "label": "Wheelchair access", "format": "bool"},
                   {"column": "parking_spaces", "label": "Parking spaces"},
                   {"column": "admission_fee_krw", "label": "Admission", "unit": "KRW"}]},
    ],
    "DB3-2": [
        {"type": "timeline", "title": "When each event runs, and whether it fits the venue's opening hours",
         "mode": "date", "label": "event_name_ko", "sub": "venue", "start": "start_date", "end": "end_date",
         "duration": {"column": "days", "unit": "days"},
         "status": {"column": "schedule_check",
                    "map": {"OK": "good", "OUTSIDE_OPERATING_HOURS": "serious", "CANCELLED": "neutral"},
                    "labels": {"OK": "Fits the opening hours", "OUTSIDE_OPERATING_HOURS": "Outside the opening hours",
                               "CANCELLED": "Cancelled"}}},
    ],
    "DB3-3": [
        {"type": "paired_bars", "title": "Published monthly visitors vs visitors counted by sensors",
         "label": "attraction_name_ko", "unit": "visitors",
         "series": [{"column": "monthly_total", "label": "Published monthly total"},
                    {"column": "sample_total", "label": "Counted by sensors (part of all visitors)"}],
         "note_column": "sample_coverage_pct", "note_label": "sensors saw", "note_unit": "%"},
    ],
    "DB3-4": [
        {"type": "timeline", "title": "Opening hours on that day, and the time you asked about",
         "mode": "clock", "label": "attraction_name_ko", "sub": "category", "start": "opens_at", "end": "closes_at",
         "marker_param": "at_time", "marker_label": "Time asked",
         "duration": {"column": "distance_m", "unit": "m away"}},
    ],
    "DB4-1": [
        {"type": "cards", "title": "Limits per pollutant at this station",
         "card_title": "type_code", "pretty_title": True, "subtitle": ["station_name_ko", "network"],
         "value": {"column": "bad_above", "unit_column": "unit", "label": "'bad' above"},
         "notes": [{"column": "very_bad_above", "label": "'Very bad' above"},
                   {"column": "unit", "label": "Unit"},
                   {"column": "station_provenance", "label": "Station data", "labels": PROVENANCE}]},
    ],
    "DB4-2": [
        {"type": "meters", "title": "Latest reading of each pollutant against its limits",
         "label": "type_code", "value": "value", "unit_column": "unit",
         "thresholds": [{"column": "bad_threshold", "label": "Bad"},
                        {"column": "very_bad_threshold", "label": "Very bad"}],
         "age": {"column": "age_minutes", "unit": "min old"}, "status": GRADE},
    ],
    "DB4-3": [
        {"type": "paired_bars", "title": "Problems found per station and pollutant",
         "label": "station_name_ko", "sub": "type_code", "unit": "readings",
         "series": [{"column": "late_arrivals", "label": "Arrived late"},
                    {"column": "faults_quarantined", "label": "Fault, set aside"},
                    {"column": "corrections_applied", "label": "Corrected later"}],
         "note_column": "readings", "note_label": "readings stored", "note_unit": ""},
    ],
    "DB5-1": [
        {"type": "paired_bars", "title": "Work done by each facility",
         "label": "facility_name_ko", "sub": "facility_type", "unit": "",
         "series": [{"column": "incidents_handled", "label": "Incidents handled"},
                    {"column": "drills_held", "label": "Drills held"}]},
    ],
    "DB5-2": [
        {"type": "cards", "title": "New incidents, most urgent first",
         "card_title": "title", "subtitle": ["incident_type", "assigned_facility"],
         "value": {"column": "sla_target_seconds", "divide": 60, "unit": "min", "label": "to reach the scene"},
         "status": SEVERITY,
         "notes": [{"column": "current_status", "label": "Status"},
                   {"column": "reported_at", "label": "Reported", "format": "time"},
                   {"column": "facility_distance_m", "label": "Facility distance", "unit": "m"}]},
    ],
    "DB5-4B": [
        {"type": "bars", "title": "Incidents in each hotspot",
         "label": ["nearest_place", "most_common_type"],
         "value": {"column": "incidents", "label": "Incidents", "unit": "incidents"}},
    ],
    "DB5-5": [
        {"type": "cards", "title": "Areas to avoid at that moment",
         "card_title": "title", "subtitle": ["incident_type", "status_at_t"],
         "value": {"column": "restriction_radius_m", "unit": "m", "label": "closed-off radius"},
         "status": SEVERITY,
         "notes": [{"column": "stops_in_zone", "label": "Bus stops inside"},
                   {"column": "vehicles_in_zone", "label": "Vehicles inside"}]},
    ],
    "X-1": [
        {"type": "bars", "title": "Events at attractions near the stop",
         "label": ["active_event_name", "attraction_name"],
         "value": {"column": "expected_attendance", "label": "Expected visitors per day", "unit": "people"}},
    ],
    "X-2": [
        {"type": "bars", "title": "Outdoor air at the station nearest to the building",
         "label": ["pollutant_type", "nearest_env_station"], "distinct": ["pollutant_type"],
         "value": {"column": "outdoor_value", "label": "Latest reading", "unit": "µg/m³"}},
        {"type": "counts", "title": "Devices that get this outdoor context", "group": "device_type",
         "distinct": "device_id", "unit": "devices"},
    ],
    "X-3": [
        {"type": "counts", "title": "Bus stops within 600 m of each open incident", "group": "title",
         "distinct": "nearby_stop_name", "unit": "stops"},
    ],
    "X-4": [
        {"type": "cards", "title": "What is around each place",
         "card_title": "name_ko", "subtitle": ["category"],
         "value": {"column": "nearest_stop_m", "unit": "m", "label": "to the nearest bus stop"},
         "notes": [{"column": "nearest_stop_name", "label": "Nearest stop"},
                   {"column": "stops_within_500m", "label": "Stops within 500 m"},
                   {"column": "nearest_env_station", "label": "Air station"},
                   {"column": "nearest_env_station_m", "label": "Air station distance", "unit": "m"},
                   {"column": "nearest_fire_unit", "label": "Fire unit"},
                   {"column": "nearest_fire_unit_m", "label": "Fire unit distance", "unit": "m"},
                   {"column": "nearest_police_unit", "label": "Police unit"},
                   {"column": "nearest_police_unit_m", "label": "Police unit distance", "unit": "m"}]},
    ],
    "X-5": [
        {**RESPONSE[0], "marker": {"column": "worst_monthly_p90_s", "label": "Worst month: 9 in 10 answered within"}},
        RESPONSE[1],
    ],
    "X-6": [
        {"type": "cards", "title": "How fresh each precomputed view is",
         "card_title": "view_name", "subtitle": [],
         "value": {"column": "age_minutes", "format": "minutes", "label": "since the last refresh"},
         "level": {"column": "age_minutes", "good_below": 60, "warning_below": 1440,
                   "labels": {"good": "Fresh", "warning": "Hours old", "serious": "Over a day old"}},
         "notes": [{"column": "row_count", "label": "Rows"},
                   {"column": "duration_ms", "label": "Refresh took", "unit": "ms"},
                   {"column": "refreshed_at", "label": "Refreshed", "format": "datetime"}]},
    ],
})


def _count(rows: list[dict], column: str) -> dict[Any, int]:
    out: dict[Any, int] = {}
    for r in rows:
        out[r[column]] = out.get(r[column], 0) + 1
    return out


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _core_1(rows, params):
    near = rows[0]
    return story(
        f"{plural(len(rows), 'place')} lie within {params['radius_m']:,.0f} m of the point; the closest is "
        f"{near['name_ko']} ({near['name_en'] or near['category']}) at {near['dist_m']:,.0f} m.",
        stat("Places found", str(len(rows))), stat("Closest", near["name_ko"]),
        stat("Closest distance", f"{near['dist_m']:,.0f} m"),
        stat("Categories", str(len({r['category'] for r in rows}))))


def _core_2(rows, params):
    by = _count(rows, "provenance")
    official = by.get("OFFICIAL_OPEN_DATA", 0)
    synthetic = by.get("SYNTHETIC_FIXTURE", 0)
    best = sum(r["verification_level"] == 4 for r in rows)
    return story(
        f"The platform knows {plural(len(rows), 'data source')}: {official} official open-data sets, "
        f"{synthetic} generated by the team for testing, and {by.get('CURATED_MANUAL', 0)} typed in by hand. "
        f"{best} reach the highest verification level (4).",
        stat("Sources", str(len(rows))), stat("Official", str(official)), stat("Generated", str(synthetic)),
        stat("Verification level 4", str(best)))


def _core_3(rows, params):
    by = _count(rows, "reason_code")
    top, n = max(by.items(), key=lambda kv: kv[1])
    return story(
        f"The latest {plural(len(rows), 'record')} that failed a check were kept aside, never deleted. "
        f"The most common reason: \"{REASONS.get(top, top)}\" ({n} records).",
        stat("Records shown", str(len(rows))), stat("Different reasons", str(len(by))),
        stat("Most common", REASONS.get(top, top)),
        stat("Tables affected", str(len({r['target_table'] for r in rows}))))


def _core_4(rows, params):
    by = _count(rows, "table_name")
    top, n = max(by.items(), key=lambda kv: kv[1])
    newest = rows[0]
    return story(
        f"The latest {plural(len(rows), 'change')} in the audit log, newest first. Most went to {top} ({n}). "
        f"The newest: {newest['action'].lower()} of {newest['table_name']} {newest['entity_id']}. "
        f"Entries can be added but never changed or deleted.",
        stat("Entries shown", str(len(rows))), stat("Tables", str(len(by))),
        stat("Newest", f"{newest['table_name']} {newest['action'].lower()}"),
        stat("Busiest table", top))


def _db1_1(rows, params):
    total = sum(r["metres_from_previous"] or 0 for r in rows)
    longest = max(rows, key=lambda r: r["metres_from_previous"] or 0)
    return story(
        f"Route {params['route_number']} (direction {params['direction']}) serves {plural(len(rows), 'stop')}, "
        f"from {rows[0]['stop_name_ko']} to {rows[-1]['stop_name_ko']}, about {total / 1000:.1f} km. "
        f"The longest hop is {longest['metres_from_previous']:,} m, into {longest['stop_name_ko']}.",
        stat("Stops", str(len(rows))), stat("Length", f"{total / 1000:.1f} km"),
        stat("Average gap", f"{total / max(1, len(rows) - 1):,.0f} m"),
        stat("Longest gap", f"{longest['metres_from_previous']:,} m"))


def _db1_3(rows, params):
    moving = [r for r in rows if (r["speed_kmh"] or 0) > 1]
    stale = [r for r in rows if (r["staleness_seconds"] or 0) > 120]
    return story(
        f"At {hhmm(params['as_of'])} on {str(params['as_of'])[:10]}, {plural(len(rows), 'vehicle')} had reported a "
        f"position; {len(moving)} were moving. "
        + (f"{plural(len(stale), 'vehicle')} had not reported for over 2 minutes, so their position is uncertain."
           if stale else "Every position was less than 2 minutes old."),
        stat("Vehicles", str(len(rows))), stat("Moving", str(len(moving))),
        stat("Average speed", f"{sum(r['speed_kmh'] or 0 for r in rows) / len(rows):.0f} km/h"),
        stat("Older than 2 min", str(len(stale))))


def _db1_5(rows, params):
    fixes = {r["observation_id"] for r in rows}
    closest = min(rows, key=lambda r: r["distance_m"])
    return story(
        f"For the last {plural(len(fixes), 'GPS fix', 'GPS fixes')} of {params['vehicle_id']}, the closest stop was "
        f"{closest['stop_name_ko']} at {closest['distance_m']:,.0f} m. Only stops within {params['radius_m']:,.0f} m "
        f"are listed (up to 3 per fix).",
        stat("GPS fixes", str(len(fixes))), stat("Stops found", str(len({r['stop_id'] for r in rows}))),
        stat("Closest stop", closest["stop_name_ko"]), stat("Closest distance", f"{closest['distance_m']:,.0f} m"))


def _db2_1(rows, params):
    by = _count(rows, "reconciliation_status")
    review = [r["building_name_ko"] for r in rows if r["reconciliation_status"] == "REVIEW"]
    return story(
        f"Of {plural(len(rows), 'building')}, {by.get('CONFIRMED', 0)} match their place exactly. "
        + (f"{', '.join(review)} {'has' if len(review) == 1 else 'have'} a different address text and should be checked. "
           if review else "")
        + (f"{by.get('UNLINKED', 0)} {'is' if by.get('UNLINKED', 0) == 1 else 'are'} not linked to any place yet."
           if by.get("UNLINKED") else ""),
        stat("Buildings", str(len(rows))), stat("Confirmed", str(by.get("CONFIRMED", 0))),
        stat("To check", str(by.get("REVIEW", 0))), stat("Not linked", str(by.get("UNLINKED", 0))))


def _db2_5(rows, params):
    stale = [r for r in rows if (r["age_minutes"] or 0) > 30]
    not_ok = [r for r in rows if r["current_status"] != "ACTIVE"]
    open_actions = sum(r["open_actions"] or 0 for r in rows)
    return story(
        f"{params['building_id']} at {hhmm(params['as_of'])}: {plural(len(rows), 'device')} reporting. "
        + (f"{', '.join(r['device_id'] for r in not_ok)} {'is' if len(not_ok) == 1 else 'are'} not active. " if not_ok else "All devices are active. ")
        + (f"{plural(len(stale), 'reading')} {'is' if len(stale) == 1 else 'are'} older than 30 minutes. " if stale else "")
        + (f"{plural(open_actions, 'maintenance job')} still open." if open_actions else "No maintenance open."),
        stat("Devices", str(len(rows))), stat("Not active", str(len(not_ok))),
        stat("Readings over 30 min old", str(len(stale))), stat("Open maintenance", str(open_actions)))


def _db3_1(rows, params):
    wheel = sum(bool(r["is_wheelchair_accessible"]) for r in rows)
    free = sum((r["admission_fee_krw"] or 0) == 0 for r in rows)
    near = min(rows, key=lambda r: r["stop_distance_m"])
    return story(
        f"{plural(len(rows), 'attraction')}: {wheel} accessible by wheelchair, {free} free to enter. "
        f"{near['attraction_name_ko']} is the easiest to reach by bus, {near['stop_distance_m']:,.0f} m from "
        f"{near['nearest_stop']}.",
        stat("Attractions", str(len(rows))), stat("Wheelchair accessible", str(wheel)),
        stat("Free entry", str(free)), stat("With parking", str(sum(bool(r["has_parking"]) for r in rows))))


def _db3_2(rows, params):
    outside = [r for r in rows if r["schedule_check"] == "OUTSIDE_OPERATING_HOURS"]
    cancelled = [r for r in rows if r["schedule_check"] == "CANCELLED"]
    return story(
        f"{plural(len(rows), 'event')} between {params['from_date']} and {params['to_date']}. "
        + (f"{', '.join(r['event_name_ko'] for r in outside)} {'runs' if len(outside) == 1 else 'run'} outside "
           f"the venue's opening hours." if outside else "All of them fit their venue's opening hours.")
        + (f" {', '.join(r['event_name_ko'] for r in cancelled)} {'is' if len(cancelled) == 1 else 'are'} cancelled."
           if cancelled else ""),
        stat("Events", str(len(rows))), stat("Outside opening hours", str(len(outside))),
        stat("Longest", f"{max(r['days'] for r in rows)} days"),
        stat("Visitors per day (sum)", num(sum(r["expected_attendance"] for r in rows))))


def _db3_3(rows, params):
    cov = [r["sample_coverage_pct"] for r in rows if r["sample_coverage_pct"] is not None]
    biggest = max(rows, key=lambda r: r["monthly_total"] or 0)
    return story(
        f"In {str(params['month'])[:7]} the sensors counted {min(cov):.0f}–{max(cov):.0f}% of the visitors in the "
        f"published monthly totals, so the samples show the pattern, not the full number. "
        f"{biggest['attraction_name_ko']} was the busiest, with {num(biggest['monthly_total'])} visitors.",
        stat("Venues", str(len(rows))),
        stat("Published visitors", num(sum(r["monthly_total"] or 0 for r in rows))),
        stat("Counted by sensors", num(sum(r["sample_total"] or 0 for r in rows))),
        stat("Sensor coverage", f"{min(cov):.0f}–{max(cov):.0f}%"))


DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def _db3_4(rows, params):
    near = min(rows, key=lambda r: r["distance_m"])
    day = DAYS[int(params["day_of_week"]) % 7]
    return story(
        f"On a {day} at {str(params['at_time'])[:5]}, {plural(len(rows), 'attraction')} within "
        f"{params['radius_m']:,.0f} m of stop {params['stop_number']} ({rows[0]['from_stop']}) "
        f"{'is' if len(rows) == 1 else 'are'} open. The nearest is {near['attraction_name_ko']}, "
        f"{near['distance_m']:,.0f} m away, open {near['opens_at']}–{near['closes_at']}.",
        stat("Open now", str(len(rows))), stat("Nearest", near["attraction_name_ko"]),
        stat("Nearest distance", f"{near['distance_m']:,.0f} m"),
        stat("Wheelchair accessible", str(sum(bool(r["is_wheelchair_accessible"]) for r in rows))))


def _db4_1(rows, params):
    r0 = rows[0]
    pm = next((r for r in rows if r["type_code"] == "PM25"), None)
    return story(
        f"{r0['station_name_ko']} is an official AirKorea station ({r0['network']}, since {r0['established_year']}) "
        f"measuring {plural(len(rows), 'pollutant')}, each with its own unit and limits"
        + (f"; for example PM2.5 counts as 'bad' above {pm['bad_above']:g} and 'very bad' above "
           f"{pm['very_bad_above']:g} {pm['unit']}." if pm else "."),
        stat("Pollutants", str(len(rows))), stat("Network", r0["network"]),
        stat("Since", str(r0["established_year"])),
        stat("Source", PROVENANCE.get(r0["station_provenance"], r0["station_provenance"])))


def _db4_2(rows, params):
    worse = [r for r in rows if r["grade"] in ("BAD", "VERY_BAD")]
    oldest = max(r["age_minutes"] or 0 for r in rows)
    return story(
        f"At {params['station']} station, {hhmm(params['as_of'])}: "
        + (f"{', '.join(pollutant(r['type_code']) for r in worse)} {'is' if len(worse) == 1 else 'are'} above the 'bad' limit. "
           if worse else f"all {len(rows)} pollutants are within the normal range. ")
        + ("All readings are current." if oldest == 0 else f"The oldest reading is {oldest} minutes old."),
        stat("Pollutants", str(len(rows))), stat("Above limit", str(len(worse))),
        stat("Flagged suspect", str(sum(r["quality_flag"] != "GOOD" for r in rows))),
        stat("Oldest reading", f"{oldest} min"))


def _db4_3(rows, params):
    late = sum(r["late_arrivals"] or 0 for r in rows)
    faults = sum(r["faults_quarantined"] or 0 for r in rows)
    fixes = sum(r["corrections_applied"] or 0 for r in rows)
    stored = sum(r["readings"] or 0 for r in rows)
    return story(
        f"Between {hhmm(params['from_time'])} on {str(params['from_time'])[:10]} and {hhmm(params['to_time'])} on "
        f"{str(params['to_time'])[:10]}: {plural(late, 'reading')} arrived more than {params['late_minutes']} minutes "
        f"late, {plural(faults, 'fault marker')} {'was' if faults == 1 else 'were'} set aside and "
        f"{plural(fixes, 'value')} {'was' if fixes == 1 else 'were'} corrected afterwards, out of "
        f"{num(stored)} readings stored.",
        stat("Readings stored", num(stored)), stat("Late", num(late)), stat("Faults set aside", num(faults)),
        stat("Corrected", num(fixes)))


def _db5_1(rows, params):
    busiest = max(rows, key=lambda r: r["incidents_handled"])
    return story(
        f"{plural(len(rows), 'facility', 'facilities')} serve Songdo. {busiest['facility_name_ko']} handled the most "
        f"incidents ({num(busiest['incidents_handled'])}). Their coordinates are typed in by the team and still "
        f"need checking against the official list.",
        stat("Facilities", str(len(rows))),
        stat("Incidents handled", num(sum(r["incidents_handled"] for r in rows))),
        stat("Drills held", num(sum(r["drills_held"] for r in rows))),
        stat("Busiest", busiest["facility_name_ko"]))


def _db5_2(rows, params):
    by = _count(rows, "severity")
    first = rows[0]
    open_ = [r for r in rows if r["current_status"] not in ("CLOSED", "RESOLVED", "CANCELLED")]
    return story(
        f"{plural(len(rows), 'incident')} reported between {hhmm(params['from_time'])} on {str(params['from_time'])[:10]} "
        f"and {hhmm(params['to_time'])} on {str(params['to_time'])[:10]}; {len(open_)} still open. The most urgent: "
        f"{first['title']} ({first['severity'].lower()}, responders should arrive within "
        f"{duration(first['sla_target_seconds'])}).",
        stat("Incidents", str(len(rows))), stat("Still open", str(len(open_))),
        stat("Critical", str(by.get("CRITICAL", 0))), stat("High", str(by.get("HIGH", 0))))


def _db5_4b(rows, params):
    top = rows[0]
    return story(
        f"Incidents cluster in {plural(len(rows), 'hotspot')}. The largest, {num(top['incidents'])} incidents (mostly "
        f"{top['most_common_type'].lower()}), is around {top['nearest_place']}.",
        stat("Hotspots", str(len(rows))), stat("Largest", num(top["incidents"])),
        stat("Incidents in hotspots", num(sum(r["incidents"] for r in rows))),
        stat("Largest near", top["nearest_place"]))


def _db5_5(rows, params):
    stops = sum(r["stops_in_zone"] or 0 for r in rows)
    vehicles = sum(r["vehicles_in_zone"] or 0 for r in rows)
    big = max(rows, key=lambda r: r["restriction_radius_m"])
    return story(
        f"At {hhmm(params['as_of'])}, {plural(len(rows), 'area')} {'is' if len(rows) == 1 else 'are'} closed off; "
        f"the largest is a {big['restriction_radius_m']} m zone around {big['title']}. "
        f"Inside the zones: {plural(stops, 'bus stop')} and {plural(vehicles, 'vehicle')}; route planners should "
        f"steer around them.",
        stat("Closed areas", str(len(rows))), stat("Largest radius", f"{big['restriction_radius_m']} m"),
        stat("Stops inside", str(stops)), stat("Vehicles inside", str(vehicles)))


def _x_1(rows, params):
    events = {r["active_event_name"] for r in rows if r["active_event_name"]}
    places = {r["attraction_name"] for r in rows}
    return story(
        f"Within 500 m of stop {params['stop_number']} ({rows[0]['stop_name']}): {plural(len(places), 'attraction')} "
        f"with {plural(len(events), 'event')} on {params['on_date']}, expecting "
        f"{num(sum(r['expected_attendance'] or 0 for r in rows))} visitors a day.",
        stat("Attractions", str(len(places))), stat("Events", str(len(events))),
        stat("Expected visitors", num(sum(r["expected_attendance"] or 0 for r in rows))),
        stat("Closest", f"{min(r['distance_meters'] for r in rows):,.0f} m"))


def _x_2(rows, params):
    devices = {r["device_id"] for r in rows}
    station = rows[0]["nearest_env_station"]
    vals = {r["pollutant_type"]: r["outdoor_value"] for r in rows}
    return story(
        f"The {plural(len(devices), 'device')} in {rows[0]['building_name_ko']} take their outdoor air from {station} "
        f"station, {rows[0]['distance_to_station_meters']:,.0f} m away: "
        + ", ".join(f"{pollutant(k)} {v:g} µg/m³" for k, v in sorted(vals.items())) + ".",
        stat("Devices", str(len(devices))), stat("Air station", station),
        stat("Distance", f"{rows[0]['distance_to_station_meters']:,.0f} m"),
        *[stat(pollutant(k), f"{v:g} µg/m³") for k, v in sorted(vals.items())][:1])


def _x_3(rows, params):
    incidents = {r["incident_id"]: r for r in rows}
    alerts = {r["incident_id"] for r in rows if r["active_env_alert"]}
    return story(
        f"{plural(len(incidents), 'open incident')} {'has' if len(incidents) == 1 else 'have'} bus stops within 600 m "
        f"({len(rows)} incident–stop pairs shown). "
        + (f"{len(alerts)} also {'has' if len(alerts) == 1 else 'have'} an air alert at the nearest station."
           if alerts else "None has an air alert at its nearest station."),
        stat("Open incidents", str(len(incidents))), stat("Stops affected", str(len({r['nearby_stop_name'] for r in rows}))),
        stat("With air alert", str(len(alerts))),
        stat("Closest stop", f"{min(r['distance_to_stop_meters'] for r in rows):,.0f} m"))


def _x_4(rows, params):
    far = max(rows, key=lambda r: r["nearest_stop_m"])
    best = max(rows, key=lambda r: r["stops_within_500m"])
    return story(
        f"Context cards for {plural(len(rows), 'place')}, precomputed so they load instantly. "
        f"{best['name_ko']} has the most bus stops nearby ({best['stops_within_500m']} within 500 m); "
        f"{far['name_ko']} is the furthest from a stop ({far['nearest_stop_m']:,.0f} m).",
        stat("Places", str(len(rows))),
        stat("Average to nearest stop", f"{sum(r['nearest_stop_m'] for r in rows) / len(rows):,.0f} m"),
        stat("Best connected", best["name_ko"]), stat("Least connected", far["name_ko"]))


def _x_5(rows, params):
    s = _db5_4(rows, params)
    s["headline"] += " These figures come from a precomputed view, so they load instantly."
    return s


def _x_6(rows, params):
    oldest = max(rows, key=lambda r: r["age_minutes"])
    return story(
        f"{plural(len(rows), 'precomputed view')}; the oldest, {oldest['view_name']}, was refreshed "
        f"{duration(oldest['age_minutes'] * 60)} ago. "
        + ("Run a refresh before relying on them." if oldest["age_minutes"] > 1440 else "They are reasonably fresh."),
        stat("Views", str(len(rows))), stat("Oldest", duration(oldest["age_minutes"] * 60)),
        stat("Rows in total", num(sum(r["row_count"] for r in rows))),
        stat("Slowest refresh", f"{max(r['duration_ms'] for r in rows):,.0f} ms"))


STORY.update({
    "CORE-1": _core_1, "CORE-2": _core_2, "CORE-3": _core_3, "CORE-4": _core_4,
    "DB1-1": _db1_1, "DB1-3": _db1_3, "DB1-5": _db1_5, "DB2-1": _db2_1, "DB2-5": _db2_5,
    "DB3-1": _db3_1, "DB3-2": _db3_2, "DB3-3": _db3_3, "DB3-4": _db3_4,
    "DB4-1": _db4_1, "DB4-2": _db4_2, "DB4-3": _db4_3,
    "DB5-1": _db5_1, "DB5-2": _db5_2, "DB5-4B": _db5_4b, "DB5-5": _db5_5,
    "X-1": _x_1, "X-2": _x_2, "X-3": _x_3, "X-4": _x_4, "X-5": _x_5, "X-6": _x_6,
})


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
