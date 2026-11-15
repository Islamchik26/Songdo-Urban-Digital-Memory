"""DB5 Safety & Incident fixtures.

  facilities   real facility NAMES where marked, but every coordinate is a PLACEHOLDER typed by
               hand and not verified (source SRC_CORE_01). Replace with the official list
               (SRC_SAF_01) before presenting locations as fact.
  incidents    all SYNTHETIC (source SRC_SAF_02): about 10 x N incidents between 2026-07-01 and
               2026-10-01, a fifth of them drills, plus six hand-written incidents on the
               service day 2026-10-02.

To keep the seed small, each incident is written once as a compact timeline row in a temporary
table; the database then derives safety.incidents, the status history and the drills from it.
The history rows still pass the state-machine CHECK constraint.

Service-day incidents (status at the end of 2026-10-02)
  INC-20261002-0001  traffic accident near Convensia, 08:05          CLOSED
  INC-20261002-0002  medical emergency at the outlet, 12:20          CLOSED
  INC-20261002-0003  fire at Triple Street, 17:40                    ON_SCENE   (active, 300 m zone)
  INC-20261002-0004  road obstruction near Central Park stn, 18:10   REPORTED   (active, 100 m zone)
  INC-20261002-0005  hazmat leak near Techno Park, 21:15             DISPATCHED (active, 500 m zone)
  INC-20261002-0006  false fire report, 15:30                        CANCELLED
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

from fixtures.common import BBOX, KST, SERVICE_DAY, Raw, insert, point, q

SRC = "SRC_SAF_02"

# (facility_id, name_ko, name_en, type, responder, lon, lat)  -- coordinates are placeholders
FACILITIES = [
    ("FAC_SONGDO_FIRE", "송도소방서", "Songdo Fire Station", "FIRE_STATION", "FIRE", 126.6610, 37.3925),
    ("FAC_SINSONG_119", "신송119안전센터", "Sinsong 119 Safety Center", "SAFETY_CENTER_119", "FIRE", 126.6420, 37.3830),
    ("FAC_TECHNO_119", "테크노파크119안전센터 (가상)", "Techno Park 119 Safety Center (fictional)",
     "SAFETY_CENTER_119", "FIRE", 126.6530, 37.3790),
    ("FAC_POLICE_SONGDO1", "송도1지구대", "Songdo Police Box 1", "POLICE_BOX", "POLICE", 126.6480, 37.3930),
    ("FAC_POLICE_SONGDO2", "송도2지구대", "Songdo Police Box 2", "POLICE_BOX", "POLICE", 126.6600, 37.3785),
]

# type -> (responder, weight, on-scene minutes (min, max))
TYPES = {
    "MEDICAL": ("FIRE", 38, (10, 35)),
    "TRAFFIC_ACCIDENT": ("POLICE", 26, (20, 60)),
    "ROAD_OBSTRUCTION": ("POLICE", 14, (10, 30)),
    "FIRE": ("FIRE", 12, (30, 120)),
    "FLOOD": ("FIRE", 6, (40, 150)),
    "HAZMAT": ("FIRE", 4, (60, 180)),
}
SEVERITY_WEIGHTS = {"LOW": 30, "MEDIUM": 42, "HIGH": 22, "CRITICAL": 6}
SLA = {"CRITICAL": 300, "HIGH": 420, "MEDIUM": 600, "LOW": 900}
HOTSPOTS = ["PLC_CONVENSIA", "PLC_TRIPLE_STREET", "PLC_STN_INU", "PLC_STN_CENTRAL_PARK",
            "PLC_STN_TECHNO_PARK", "PLC_HYUNDAI_OUTLET"]
PERIOD_START = datetime(2026, 7, 1, tzinfo=KST)
PERIOD_DAYS = 93            # up to and including 2026-10-01

COLUMNS = ["incident_id", "type_code", "title", "severity", "lon", "lat", "facility_id", "is_drill", "radius_m",
           "reported_at", "t_dispatch", "t_onscene", "t_resolved", "t_closed", "t_cancel"]


def _metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    dx = (b[0] - a[0]) * 111_320 * math.cos(math.radians(37.39))
    dy = (b[1] - a[1]) * 110_540
    return math.hypot(dx, dy)


def _nearest_facility(xy: tuple[float, float], responder: str) -> tuple[str, float]:
    return min(((f[0], _metres(xy, (f[5], f[6]))) for f in FACILITIES if f[4] == responder), key=lambda c: c[1])


def _clamp(lon: float, lat: float) -> tuple[float, float]:
    return (round(min(max(lon, BBOX[0] + 1e-4), BBOX[2] - 1e-4), 6),
            round(min(max(lat, BBOX[1] + 1e-4), BBOX[3] - 1e-4), 6))


def safety_section(rng: random.Random, n: int, place_xy: dict[str, tuple[float, float]],
                   stop_xy: list[tuple[float, float]]) -> list[str]:
    type_names = list(TYPES)
    type_weights = [TYPES[t][1] for t in type_names]
    severities = list(SEVERITY_WEIGHTS)
    severity_weights = list(SEVERITY_WEIGHTS.values())

    rows: list[list[object]] = []
    for i in range(10 * n):
        type_code = rng.choices(type_names, type_weights)[0]
        severity = rng.choices(severities, severity_weights)[0]
        responder, _w, scene = TYPES[type_code]
        # 55 % of incidents concentrate around six hotspots, the rest happen near a random bus stop
        if rng.random() < 0.55:
            cx, cy = place_xy[rng.choice(HOTSPOTS)]
            lon, lat = _clamp(cx + rng.gauss(0, 0.00045), cy + rng.gauss(0, 0.00036))
        else:
            sx, sy = rng.choice(stop_xy)
            lon, lat = _clamp(sx + rng.gauss(0, 0.0004), sy + rng.gauss(0, 0.0003))
        facility, distance = _nearest_facility((lon, lat), responder)

        day = rng.randrange(PERIOD_DAYS)
        # more incidents in the daytime and evening than at night
        hour = rng.choices(range(24), [1, 1, 1, 1, 1, 2, 4, 7, 8, 6, 5, 5, 6, 5, 5, 5, 6, 8, 9, 8, 6, 4, 3, 2])[0]
        reported = PERIOD_START + timedelta(days=day, hours=hour, minutes=rng.randrange(60), seconds=rng.randrange(60))
        is_drill = rng.random() < 0.20

        urgency = {"CRITICAL": 0.6, "HIGH": 0.8, "MEDIUM": 1.0, "LOW": 1.3}[severity]
        t_dispatch = round(rng.uniform(35, 150) * urgency)
        travel = distance / (rng.uniform(28, 42) / 3.6) + rng.uniform(40, 160) * urgency
        t_onscene = t_dispatch + round(travel)
        t_resolved = t_onscene + rng.randint(scene[0] * 60, scene[1] * 60)
        t_closed = t_resolved + rng.randint(600, 2400)
        t_cancel = None
        roll = rng.random()
        if not is_drill and roll < 0.015:            # withdrawn before any unit was sent
            t_cancel, t_dispatch, t_onscene, t_resolved, t_closed, facility = rng.randint(30, 200), None, None, None, None, None
        elif not is_drill and roll < 0.03:           # cancelled on the way
            t_cancel, t_onscene, t_resolved, t_closed = t_dispatch + rng.randint(20, 150), None, None, None
        rows.append([f"INC-{reported:%Y%m%d}-{i:05d}", type_code, None, severity, lon, lat, facility, is_drill, None,
                     reported, t_dispatch, t_onscene, t_resolved, t_closed, t_cancel])

    def at(h: int, m: int) -> datetime:
        return SERVICE_DAY + timedelta(hours=h, minutes=m)

    def near(place: str, dlon: float = 0.0, dlat: float = 0.0) -> tuple[float, float]:
        return _clamp(place_xy[place][0] + dlon, place_xy[place][1] + dlat)

    curated = [
        ("0001", "TRAFFIC_ACCIDENT", "컨벤시아 앞 교차로 2중 추돌", "MEDIUM", near("PLC_CONVENSIA", 0.0035, 0.0002),
         "FAC_POLICE_SONGDO1", None, at(8, 5), 120, 540, 2700, 3900, None),
        ("0002", "MEDICAL", "아울렛 매장 내 응급환자", "HIGH", near("PLC_HYUNDAI_OUTLET"),
         "FAC_TECHNO_119", None, at(12, 20), 60, 330, 1500, 2700, None),
        ("0003", "FIRE", "트리플스트리트 음식점 화재", "HIGH", near("PLC_TRIPLE_STREET"),
         "FAC_TECHNO_119", 300, at(17, 40), 90, 510, None, None, None),
        ("0004", "ROAD_OBSTRUCTION", "센트럴파크역 인근 낙하물", "LOW", near("PLC_STN_CENTRAL_PARK", 0.0004, -0.0003),
         None, 100, at(18, 10), None, None, None, None, None),
        ("0005", "HAZMAT", "테크노파크 연구동 화학물질 누출", "CRITICAL", near("PLC_STN_TECHNO_PARK", -0.0006, 0.0005),
         "FAC_TECHNO_119", 500, at(21, 15), 45, None, None, None, None),
        ("0006", "FIRE", "오인 화재 신고 (연기 오인)", "MEDIUM", near("PLC_CENTRAL_PARK"),
         None, None, at(15, 30), None, None, None, None, 240),
    ]
    for num, type_code, title, severity, (lon, lat), facility, radius, reported, td, to, tr, tc, tx in curated:
        rows.append([f"INC-20261002-{num}", type_code, title, severity, lon, lat, facility, False, radius,
                     reported, td, to, tr, tc, tx])

    facility_rows = [[fid, ko, en, ftype, responder, point(lon, lat), "SRC_CORE_01"]
                     for fid, ko, en, ftype, responder, lon, lat in FACILITIES]

    lines = ["", "-- ---- safety.facilities (names partly real; ALL coordinates are unverified placeholders) ----"]
    lines += insert("safety.facilities",
                    ["facility_id", "facility_name_ko", "facility_name_en", "facility_type", "responder", "geom",
                     "source_id"], facility_rows)
    lines += ["", "-- ---- safety incidents (SYNTHETIC): compact timelines, expanded by the database below ----",
              "CREATE TEMP TABLE IF NOT EXISTS _seed_incidents (",
              "    incident_id VARCHAR(32) PRIMARY KEY, type_code VARCHAR(24), title VARCHAR(255), severity VARCHAR(8),",
              "    lon DOUBLE PRECISION, lat DOUBLE PRECISION, facility_id VARCHAR(32), is_drill BOOLEAN, radius_m INTEGER,",
              "    reported_at TIMESTAMPTZ, t_dispatch INTEGER, t_onscene INTEGER, t_resolved INTEGER, t_closed INTEGER,",
              "    t_cancel INTEGER);",
              "TRUNCATE _seed_incidents;"]
    for i in range(0, len(rows), 1000):
        values = ",\n".join("  (" + ", ".join(v if isinstance(v, Raw) else q(v) for v in row) + ")"
                            for row in rows[i:i + 1000])
        lines.append(f"INSERT INTO _seed_incidents ({', '.join(COLUMNS)}) VALUES\n{values};")
    lines.append(f"""
-- Bulk load: the per-row triggers are switched off and the history is written set-based instead.
ALTER TABLE safety.incidents DISABLE TRIGGER USER;
INSERT INTO safety.incidents
    (incident_id, type_code, title, severity, current_status, reported_at, assigned_facility_id,
     restriction_radius_m, is_drill, geom, source_id)
SELECT s.incident_id, s.type_code,
       COALESCE(s.title, CASE WHEN s.is_drill THEN '[훈련] ' ELSE '' END || t.name_ko || ' ' || RIGHT(s.incident_id, 5)),
       s.severity,
       CASE WHEN s.t_cancel   IS NOT NULL THEN 'CANCELLED'
            WHEN s.t_closed   IS NOT NULL THEN 'CLOSED'
            WHEN s.t_resolved IS NOT NULL THEN 'RESOLVED'
            WHEN s.t_onscene  IS NOT NULL THEN 'ON_SCENE'
            WHEN s.t_dispatch IS NOT NULL THEN 'DISPATCHED'
            ELSE 'REPORTED' END,
       s.reported_at, s.facility_id, COALESCE(s.radius_m, t.default_restriction_radius_m), s.is_drill,
       ST_SetSRID(ST_MakePoint(s.lon, s.lat), 4326), '{SRC}'
FROM _seed_incidents s
JOIN safety.incident_types t USING (type_code)
ON CONFLICT (incident_id) DO NOTHING;
ALTER TABLE safety.incidents ENABLE TRIGGER USER;

INSERT INTO safety.incident_status_history (incident_id, from_status, to_status, changed_at, actor, facility_id)
SELECT step.incident_id, step.from_status, step.to_status, step.changed_at, 'SEED', step.facility_id
FROM _seed_incidents s
CROSS JOIN LATERAL (VALUES
    (s.incident_id, NULL,         'REPORTED',   s.reported_at,                                         NULL),
    (s.incident_id, 'REPORTED',   'DISPATCHED', s.reported_at + make_interval(secs => s.t_dispatch),   s.facility_id),
    (s.incident_id, 'DISPATCHED', 'ON_SCENE',   s.reported_at + make_interval(secs => s.t_onscene),    NULL),
    (s.incident_id, 'ON_SCENE',   'RESOLVED',   s.reported_at + make_interval(secs => s.t_resolved),   NULL),
    (s.incident_id, 'RESOLVED',   'CLOSED',     s.reported_at + make_interval(secs => s.t_closed),     NULL),
    (s.incident_id, CASE WHEN s.t_dispatch IS NULL THEN 'REPORTED' ELSE 'DISPATCHED' END,
                                  'CANCELLED',  s.reported_at + make_interval(secs => s.t_cancel),     NULL)
) AS step (incident_id, from_status, to_status, changed_at, facility_id)
WHERE step.changed_at IS NOT NULL
ON CONFLICT (incident_id, to_status) DO NOTHING;

INSERT INTO safety.response_drills (incident_id, facility_id, held_at, target_seconds, response_seconds)
SELECT s.incident_id, s.facility_id, s.reported_at, sl.sla_target_seconds, s.t_onscene
FROM _seed_incidents s
JOIN safety.severity_levels sl USING (severity)
WHERE s.is_drill AND s.t_onscene IS NOT NULL
ON CONFLICT (incident_id) DO NOTHING;

DROP TABLE _seed_incidents;""")
    drills = sum(1 for r in rows if r[7])
    print(f"  safety: {len(facility_rows)} facilities, {len(rows)} incidents ({drills} drills, "
          f"{sum(1 for r in rows if r[14] is not None)} cancelled)")
    return lines
