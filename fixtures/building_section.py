"""DB2 Smart Building fixtures (all SYNTHETIC except the landmark names/addresses).

Buildings reuse the curated landmarks of core.places; their interiors, devices,
telemetry and maintenance are generated. Deliberate test cases are built in:

  reconciliation   Convensia's address is written in a different style; the IGC
                   centroid is ~220 m from its place; the testbed has no place at all
  relocation       DEV-GT-TMP-04 and DEV-CV-TMP-02 moved once, DEV-PT-TMP-03 twice
  heartbeat        DEV-GT-TMP-06 silent from 08:40 (FAULT, repair open)
                   DEV-PT-CO2-01 silent from 09:10 but still marked ACTIVE
                   DEV-TB-PWR-01 silent from 09:30 (MAINTENANCE, firmware update open)
                   DEV-TS-TMP-03 never reported; DEV-CV-TMP-03 is RETIRED
  quality          DEV-GC-TMP-02 sends three impossible readings (flagged SUSPECT)
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from fixtures.common import KST, SERVICE_DAY, insert, point

SRC = "SRC_BLD_SYN"
START = SERVICE_DAY + timedelta(hours=6)
END = SERVICE_DAY + timedelta(hours=10, minutes=30)

# (building_id, code, name_ko, name_en, use, road_address, floors_above, floors_below,
#  place_id, (dlon, dlat) offset from the place or absolute (lon, lat), modelled floors, source)
BUILDINGS = [
    ("BLD_G_TOWER", "GT", "G타워", "G-Tower", "OFFICE", "인천광역시 연수구 아트센터대로 175", 33, 2,
     "PLC_G_TOWER", (0.00025, -0.00015), [1, 3, 12, 29], "SRC_CORE_01"),
    ("BLD_POSCO_TOWER", "PT", "포스코타워-송도", "POSCO Tower Songdo", "OFFICE", "인천광역시 연수구 컨벤시아대로 165", 68, 3,
     "PLC_POSCO_TOWER", (-0.0002, 0.0002), [1, 10, 36, 65], "SRC_CORE_01"),
    ("BLD_CONVENSIA", "CV", "송도컨벤시아", "Songdo Convensia", "CONVENTION", "인천 연수구 센트럴로 123", 4, 1,
     "PLC_CONVENSIA", (0.0003, 0.0001), [1, 2, 4], "SRC_CORE_01"),
    ("BLD_TRIBOWL", "TB", "트라이보울", "Tri-Bowl", "CULTURE", "인천광역시 연수구 인천타워대로 250", 3, 1,
     "PLC_TRIBOWL", (-0.0001, -0.0002), [1, 2, 3], "SRC_CORE_01"),
    ("BLD_IGC", "GC", "인천글로벌캠퍼스 멀티컴플렉스", "Incheon Global Campus Multi-Complex", "EDUCATION",
     "인천광역시 연수구 송도문화로 119", 5, 1, "PLC_IGC", (0.0025, 0.0), [1, 2, 3], "SRC_CORE_01"),
    ("BLD_TESTBED", "TS", "송도 스마트빌딩 테스트베드 (가상)", "Songdo Smart Building Testbed (fictional)", "TESTBED",
     None, 6, 1, None, (126.6500, 37.3850), [1, 2, 5], SRC),
]

DEVICE_KINDS = {  # type: (abbreviation, manufacturer, model, protocol, metric)
    "TEMP_SENSOR": ("TMP", "Songdo Sensing", "SS-T100", "MQTT", "TEMPERATURE_C"),
    "CO2_SENSOR": ("CO2", "Songdo Sensing", "SS-C200", "MQTT", "CO2_PPM"),
    "POWER_METER": ("PWR", "Incheon Metering", "IM-P3", "MODBUS", "POWER_KW"),
    "HVAC_CONTROLLER": ("HVC", "Yeonsu Controls", "YC-AHU8", "BACNET", "TEMPERATURE_C"),
}

SILENT_FROM = {  # device -> last moment it reported
    "DEV-GT-TMP-06": SERVICE_DAY + timedelta(hours=8, minutes=40),
    "DEV-PT-CO2-01": SERVICE_DAY + timedelta(hours=9, minutes=10),
    "DEV-TB-PWR-01": SERVICE_DAY + timedelta(hours=9, minutes=30),
    "DEV-CV-TMP-03": SERVICE_DAY + timedelta(hours=7),
}
NEVER_REPORTED = "DEV-TS-TMP-03"
STATUS = {"DEV-GT-TMP-06": "FAULT", "DEV-TB-PWR-01": "MAINTENANCE", "DEV-CV-TMP-03": "RETIRED"}
SUSPECT_DEVICE = "DEV-GC-TMP-02"
# device -> list of (space it was in before, moved out at); the last entry leads to the current space
RELOCATIONS = {
    "DEV-GT-TMP-04": [("SP-GT-F03-R1", datetime(2026, 9, 15, 14, 0, tzinfo=KST))],
    "DEV-CV-TMP-02": [("SP-CV-F02-R2", datetime(2026, 9, 22, 10, 30, tzinfo=KST))],
    "DEV-PT-TMP-03": [("SP-PT-F36-R1", datetime(2026, 9, 8, 9, 0, tzinfo=KST)),
                      ("SP-PT-F65-R2", datetime(2026, 9, 25, 16, 0, tzinfo=KST))],
}


def building_section(rng: random.Random, n: int, place_xy: dict[str, tuple[float, float]]) -> list[str]:
    building_rows, space_rows, device_rows, assignment_rows = [], [], [], []
    devices = []  # (device_id, type, building_id, floors_above, space_id)

    for bid, code, ko, en, use, addr, above, below, place_id, xy, floors, source in BUILDINGS:
        lon, lat = (place_xy[place_id][0] + xy[0], place_xy[place_id][1] + xy[1]) if place_id else xy
        building_rows.append([bid, ko, en, use, addr, above, below, place_id, point(lon, lat), source])
        counters: dict[str, int] = {}

        def add_device(dtype: str, space_id: str) -> None:
            abbr = DEVICE_KINDS[dtype][0]
            counters[abbr] = counters.get(abbr, 0) + 1
            devices.append((f"DEV-{code}-{abbr}-{counters[abbr]:02d}", dtype, bid, above, space_id))

        basement = f"SP-{code}-B01"
        plant = f"SP-{code}-B01-PLANT"
        space_rows.append([basement, bid, None, "FLOOR", "B1", -1, None])
        space_rows.append([plant, bid, basement, "PLANT_ROOM", "기계실", -1, 180.0])
        add_device("POWER_METER", plant)
        add_device("HVAC_CONTROLLER", plant)

        for floor in floors:
            fid = f"SP-{code}-F{floor:02d}"
            space_rows.append([fid, bid, None, "FLOOR", f"{floor}F", floor, None])
            rooms = ([("R1", "LOBBY", "로비", 320.0), ("R2", "ROOM", "관리실", 45.0)] if floor == 1 else
                     [("R1", "ROOM", f"{floor}01호", 85.0), ("R2", "ROOM", f"{floor}02호", 62.5)])
            for i, (suffix, stype, name, area) in enumerate(rooms):
                sid = f"{fid}-{suffix}"
                space_rows.append([sid, bid, fid, stype, name, floor, area])
                add_device("TEMP_SENSOR", sid)
                if floor != 1 and i == 0:
                    add_device("CO2_SENSOR", sid)

    known_spaces = {r[0] for r in space_rows}
    known_devices = {d[0] for d in devices}
    for special in [*SILENT_FROM, NEVER_REPORTED, SUSPECT_DEVICE, *RELOCATIONS]:
        assert special in known_devices, special

    for idx, (dev, dtype, _bid, _above, space_id) in enumerate(devices):
        abbr, maker, model, protocol, _metric = DEVICE_KINDS[dtype]
        installed = datetime(2026, 8, 3, 9, 0, tzinfo=KST) + timedelta(days=idx % 20)
        device_rows.append([dev, dtype, maker, model, f"{model}-{idx + 1:05d}", protocol,
                            installed.date().isoformat(), STATUS.get(dev, "ACTIVE"), SRC])
        start = installed
        for old_space, moved_at in RELOCATIONS.get(dev, []):
            assert old_space in known_spaces and old_space != space_id, old_space
            assignment_rows.append([dev, old_space, start, moved_at])
            start = moved_at
        assignment_rows.append([dev, space_id, start, None])

    # telemetry: one reading per device every `step` minutes, 06:00-10:30
    reporting = [d for d in devices if d[0] != NEVER_REPORTED]
    span_min = (END - START).total_seconds() / 60
    step = int(max(1, min(15, round(len(reporting) * span_min / (10 * n)))))
    telemetry_rows = []
    for dev, dtype, _bid, above, _space in reporting:
        metric = DEVICE_KINDS[dtype][4]
        room_base = rng.uniform(20.5, 23.0)
        co2_peak = rng.uniform(600, 950)
        t, k = START, 0
        while t <= min(END, SILENT_FROM.get(dev, END)):
            ramp = min(1.0, (t - START).total_seconds() / 10800)      # 0 at 06:00 -> 1 at 09:00
            if dtype == "TEMP_SENSOR":
                value = room_base + 0.8 * ramp + rng.gauss(0, 0.15)
            elif dtype == "HVAC_CONTROLLER":
                value = 17.5 - 3.0 * ramp + rng.gauss(0, 0.3)             # supply air gets colder as load rises
            elif dtype == "CO2_SENSOR":
                value = 430 + (co2_peak - 430) * ramp + rng.gauss(0, 12)
            else:
                value = above * 12.0 * (0.45 + 0.55 * ramp) + rng.gauss(0, above * 0.3)
            flag = "GOOD"
            if dev == SUSPECT_DEVICE and k in (40, 41, 42):
                value, flag = 85.0 + k, "SUSPECT"                           # impossible room temperature
            telemetry_rows.append([dev, metric, round(value, 3), flag, t,
                                   t + timedelta(seconds=rng.randint(1, 4))])
            t += timedelta(minutes=step)
            k += 1

    maintenance_rows = [
        ["DEV-GT-TMP-06", "REPAIR", "No heartbeat since 08:40; sensor board to be replaced", "김민수",
         SERVICE_DAY + timedelta(hours=9, minutes=20), None],
        ["DEV-TB-PWR-01", "FIRMWARE_UPDATE", "Meter offline during firmware update", "이서연",
         SERVICE_DAY + timedelta(hours=9, minutes=30), None],
        ["DEV-CV-TMP-03", "REPLACEMENT", "Sensor retired; replacement unit ordered", "박지훈",
         SERVICE_DAY + timedelta(hours=7, minutes=5), SERVICE_DAY + timedelta(hours=7, minutes=40)],
    ]
    technicians = ["김민수", "이서연", "박지훈"]
    for idx, (dev, *_rest) in enumerate(devices):
        if idx % 6 == 0:
            begin = datetime(2026, 9, 10, 10, 0, tzinfo=KST) + timedelta(days=idx % 9, minutes=idx)
            maintenance_rows.append([dev, "CALIBRATION", "Scheduled calibration against reference instrument",
                                     technicians[idx % 3], begin, begin + timedelta(minutes=35)])

    lines = ["", "-- ---- building: buildings / spaces / devices / assignments (SYNTHETIC interiors) ----"]
    lines += insert("building.buildings",
                    ["building_id", "building_name_ko", "building_name_en", "building_use", "road_address",
                     "floors_above", "floors_below", "place_id", "geom", "source_id"], building_rows)
    lines += insert("building.spaces",
                    ["space_id", "building_id", "parent_space_id", "space_type", "space_name", "floor_number",
                     "area_m2"], space_rows)
    lines += insert("building.devices",
                    ["device_id", "device_type", "manufacturer", "model", "serial_number", "protocol",
                     "installed_on", "current_status", "source_id"], device_rows)
    # no natural unique key besides the exclusion constraint, which ON CONFLICT DO NOTHING also honours
    lines += insert("building.device_space_assignments",
                    ["device_id", "space_id", "assigned_at", "unassigned_at"], assignment_rows)
    lines += insert("building.maintenance_actions",
                    ["device_id", "action_type", "description", "technician", "started_at", "completed_at"],
                    maintenance_rows)
    lines += ["", f"-- ---- building.telemetry (SYNTHETIC, every {step} min) ----"]
    lines += insert("building.telemetry",
                    ["device_id", "metric", "value", "quality_flag", "observed_at", "received_at"], telemetry_rows)
    print(f"  building: {len(building_rows)} buildings, {len(space_rows)} spaces, {len(device_rows)} devices, "
          f"{len(assignment_rows)} assignments, {len(maintenance_rows)} maintenance actions, "
          f"{len(telemetry_rows)} telemetry rows (every {step} min)")
    return lines
