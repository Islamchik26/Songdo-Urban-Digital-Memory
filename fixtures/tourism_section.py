"""DB3 Tourism fixtures.

Venue names and locations are real Songdo landmarks. Everything else is a
PLACEHOLDER generated for the course: opening hours, accessibility and parking
flags, admission fees, events (all fictional), and every visitor number —
including the "official" monthly totals, which stand in for SRC_TOU_02 until a
real statistics file is ingested.

Deliberate test cases
  EVT_TRIBOWL_CONCERT   runs 19:00-21:00 but the venue closes at 18:00
  EVT_PARK_MARKET       cancelled (kept in the table, excluded from analysis)
  EVT_MOONLIGHT_FEST    big evening festival when no bus service is scheduled
  ATT_HANOK_VILLAGE     two open intervals per day (lunch and dinner)
  ATT_TRIBOWL           closed on Mondays (no row for day_of_week = 1)
"""
from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta

from fixtures.common import KST, SERVICE_DAY, insert, point

SRC = "SRC_TOU_SYN"
ALL_DAYS = range(7)
TUE_SUN = (0, 2, 3, 4, 5, 6)

# (id, name_ko, name_en, category, address, wheelchair, parking, spaces, fee, place_id or (lon, lat),
#  hours: list of (days, opens, closes), typical visitors per open hour)
ATTRACTIONS = [
    ("ATT_CENTRAL_PARK", "송도 센트럴파크", "Songdo Central Park", "PARK", "인천광역시 연수구 컨벤시아대로 160",
     True, True, 2500, 0, "PLC_CENTRAL_PARK", [(ALL_DAYS, "00:00", "24:00")], 260),
    ("ATT_CONVENSIA", "송도컨벤시아", "Songdo Convensia", "CONVENTION", "인천광역시 연수구 센트럴로 123",
     True, True, 1800, 0, "PLC_CONVENSIA", [(ALL_DAYS, "09:00", "18:00")], 180),
    ("ATT_TRIBOWL", "트라이보울", "Tri-Bowl", "CULTURE", "인천광역시 연수구 인천타워대로 250",
     True, False, None, 0, "PLC_TRIBOWL", [(TUE_SUN, "10:00", "18:00")], 45),
    ("ATT_G_TOWER_OBS", "G타워 전망대", "G-Tower Observatory", "LANDMARK", "인천광역시 연수구 아트센터대로 175",
     True, True, 400, 0, "PLC_G_TOWER", [((1, 2, 3, 4, 5), "10:00", "20:00"), ((0, 6), "13:00", "18:00")], 60),
    ("ATT_TRIPLE_STREET", "트리플스트리트", "Triple Street", "SHOPPING", "인천광역시 연수구 송도과학로16번길 33-4",
     True, True, 3000, 0, "PLC_TRIPLE_STREET", [(ALL_DAYS, "10:30", "22:00")], 420),
    ("ATT_HYUNDAI_OUTLET", "현대프리미엄아울렛 송도점", "Hyundai Premium Outlets Songdo", "SHOPPING",
     "인천광역시 연수구 송도국제대로 123", True, True, 3200, 0, "PLC_HYUNDAI_OUTLET",
     [((1, 2, 3, 4), "10:30", "21:00"), ((0, 5, 6), "10:30", "21:30")], 520),
    ("ATT_HAEDOJI_PARK", "해돋이공원", "Haedoji Park", "PARK", None,
     True, True, 350, 0, "PLC_HAEDOJI_PARK", [(ALL_DAYS, "00:00", "24:00")], 70),
    ("ATT_MOONLIGHT_PARK", "송도달빛축제공원", "Songdo Moonlight Festival Park", "PARK", None,
     False, True, 1200, 0, "PLC_MOONLIGHT_PARK", [(ALL_DAYS, "00:00", "24:00")], 55),
    ("ATT_HANOK_VILLAGE", "송도 한옥마을", "Songdo Hanok Village", "CULTURE", None,
     False, False, None, 0, (126.6425, 37.3915),
     [(ALL_DAYS, "11:00", "15:00"), (ALL_DAYS, "17:00", "21:00")], 90),
]

# (id, attraction, name_ko, name_en, type, start, end, daily start, daily end, attendance/day, cancelled)
EVENTS = [
    ("EVT_SMARTCITY_EXPO", "ATT_CONVENSIA", "송도 스마트시티 엑스포 2026 (가상)", "Songdo Smart City Expo 2026 (fictional)",
     "CONFERENCE", date(2026, 10, 1), date(2026, 10, 3), "09:00", "18:00", 3500, False),
    ("EVT_BIO_FORUM", "ATT_CONVENSIA", "바이오 혁신 포럼 (가상)", "Bio Innovation Forum (fictional)",
     "CONFERENCE", date(2026, 10, 2), date(2026, 10, 2), "13:00", "17:00", 800, False),
    ("EVT_MOONLIGHT_FEST", "ATT_MOONLIGHT_PARK", "달빛 가을 음악축제 (가상)", "Moonlight Autumn Music Festival (fictional)",
     "FESTIVAL", date(2026, 10, 2), date(2026, 10, 4), "17:00", "22:00", 12000, False),
    ("EVT_TRIBOWL_CONCERT", "ATT_TRIBOWL", "트라이보울 저녁 콘서트 (가상)", "Tri-Bowl Evening Concert (fictional)",
     "CONCERT", date(2026, 10, 3), date(2026, 10, 3), "19:00", "21:00", 400, False),
    ("EVT_PARK_MARKET", "ATT_CENTRAL_PARK", "센트럴파크 플리마켓 (가상)", "Central Park Flea Market (fictional)",
     "FESTIVAL", date(2026, 10, 2), date(2026, 10, 2), "10:00", "16:00", 2000, True),
    ("EVT_OUTLET_SALE", "ATT_HYUNDAI_OUTLET", "가을 정기 세일 (가상)", "Autumn Sale (fictional)",
     "EXHIBITION", date(2026, 9, 25), date(2026, 10, 5), "10:30", "21:00", 5000, False),
    ("EVT_SKYLINE_PHOTO", "ATT_G_TOWER_OBS", "송도 스카이라인 사진전 (가상)", "Songdo Skyline Photo Exhibition (fictional)",
     "EXHIBITION", date(2026, 9, 10), date(2026, 9, 12), "10:00", "18:00", 300, False),
]

SAMPLE_MONTH = date(2026, 9, 1)                      # a full month of hourly samples
OFFICIAL_MONTHS = [date(2026, m, 1) for m in range(4, 10)]
SEASON = {4: 0.95, 5: 1.15, 6: 1.0, 7: 0.9, 8: 0.92, 9: 1.0}   # relative to September


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def tourism_section(rng: random.Random, place_xy: dict[str, tuple[float, float]]) -> list[str]:
    attraction_rows, hours_rows, sample_rows, aggregate_rows = [], [], [], []

    hours_by_attraction: dict[str, dict[int, list[tuple[int, int]]]] = {}
    for aid, ko, en, cat, addr, wheel, parking, spaces, fee, where, hours, _typical in ATTRACTIONS:
        place_id = where if isinstance(where, str) else None
        lon, lat = place_xy[where] if place_id else where
        attraction_rows.append([aid, ko, en, cat, addr, wheel, parking, spaces, fee, place_id, point(lon, lat), SRC])
        by_day: dict[int, list[tuple[int, int]]] = {}
        for days, opens, closes in hours:
            for dow in days:
                hours_rows.append([aid, dow, opens, closes])
                by_day.setdefault(dow, []).append((_minutes(opens), _minutes(closes)))
        hours_by_attraction[aid] = by_day

    event_rows = [[eid, aid, ko, en, etype, start.isoformat(), end.isoformat(), t0, t1, attendance, cancelled, SRC]
                  for eid, aid, ko, en, etype, start, end, t0, t1, attendance, cancelled in EVENTS]

    # hourly samples: all of September 2026 plus the service day (2026-10-02)
    hours_list = [datetime(2026, 9, 1, tzinfo=KST) + timedelta(hours=h) for h in range(30 * 24)]
    hours_list += [SERVICE_DAY + timedelta(hours=h) for h in range(24)]
    for aid, *_mid, _hours, typical in ATTRACTIONS:
        coverage = rng.uniform(0.28, 0.42)           # the sensor sees only part of all visitors
        september_total = 0
        for hour in hours_list:
            dow = (hour.weekday() + 1) % 7           # Python Monday=0 -> SQL Sunday=0
            minute = hour.hour * 60
            is_open = any(a <= minute < b for a, b in hours_by_attraction[aid].get(dow, []))
            if is_open:
                daylight = max(0.08, math.sin(math.pi * (hour.hour - 6) / 15)) if 6 <= hour.hour <= 21 else 0.05
                weekend = 1.45 if dow in (0, 6) else 1.0
                count = max(0, round(typical * coverage * daylight * weekend * rng.uniform(0.75, 1.25)))
            else:
                count = 0
            sample_rows.append([aid, hour, count, SRC, hour + timedelta(hours=1, seconds=rng.randint(5, 90))])
            if hour.month == 9:
                september_total += count
        september_official = round(september_total / coverage / 100) * 100
        for month in OFFICIAL_MONTHS:
            total = september_official if month.month == 9 else \
                round(september_official * SEASON[month.month] * rng.uniform(0.95, 1.05) / 100) * 100
            aggregate_rows.append([aid, month.isoformat(), total, SRC])

    lines = ["", "-- ---- tourism (venue names real; hours, events and visitor numbers are PLACEHOLDERS) ----"]
    lines += insert("tourism.attractions",
                    ["attraction_id", "attraction_name_ko", "attraction_name_en", "category", "road_address",
                     "is_wheelchair_accessible", "has_parking", "parking_spaces", "admission_fee_krw",
                     "place_id", "geom", "source_id"], attraction_rows)
    lines += insert("tourism.operating_hours", ["attraction_id", "day_of_week", "opens_at", "closes_at"], hours_rows)
    lines += insert("tourism.events",
                    ["event_id", "attraction_id", "event_name_ko", "event_name_en", "event_type", "start_date",
                     "end_date", "daily_start_time", "daily_end_time", "expected_attendance", "is_cancelled",
                     "source_id"], event_rows)
    lines += insert("tourism.aggregate_visits", ["attraction_id", "year_month", "visitor_count", "source_id"],
                    aggregate_rows)
    lines += insert("tourism.sample_visits",
                    ["attraction_id", "hour_start", "visitor_count", "source_id", "received_at"], sample_rows)
    print(f"  tourism: {len(attraction_rows)} attractions, {len(hours_rows)} opening intervals, "
          f"{len(event_rows)} events, {len(aggregate_rows)} monthly totals, {len(sample_rows)} hourly samples")
    return lines
