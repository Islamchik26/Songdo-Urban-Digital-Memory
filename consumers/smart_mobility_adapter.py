"""Smart-mobility telemetry adapter: a ROS-style node that streams vehicle GPS to the platform.

    python -m consumers.smart_mobility_adapter                       20 s drive of VEH-AV1-01 on route AV-1
    python -m consumers.smart_mobility_adapter --duration 60 --rate 10
    python -m consumers.smart_mobility_adapter --send-delay 0.5 --queue-size 10   slow uplink -> queue overflows
    python -m consumers.smart_mobility_adapter --dry-run             build packets, send nothing

How it mirrors a ROS node
    timer callback (publisher)  ->  topic with QoS KEEP_LAST(depth)  ->  subscriber (uplink)
    TelemetryPublisher             BoundedTelemetryQueue                TelemetryUplink

  * The publisher never blocks. When the uplink is slower than the sensor, the queue drops the
    OLDEST packet: for a live position the newest fix is worth more than a stale one.
  * The uplink POSTs each packet to /api/v1/mobility/observation. Network errors and 5xx are
    retried with exponential backoff; 4xx (the packet itself is wrong) is never retried.
  * A retried packet is byte-identical, so the server's SHA-256 deduplication makes delivery
    idempotent: at-least-once sending, exactly-once storage.

The vehicle drives along the real stop sequence of the route (read from the API) and stamps
packets with the current time. With --faults (default) the stream also contains what real
telemetry contains: a GPS glitch outside Songdo (the server quarantines it) and a re-sent
packet (the server reports DUPLICATE).

This writes real rows for "now" into mobility.vehicle_observations. The fixture day
(2026-10-02) is not touched; `python scripts/setup_db.py --reset` removes everything again.
"""
from __future__ import annotations

import argparse
import math
import sys
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx

from consumers.api_client import KST, api_client, get_json, ingest_headers

OBSERVATION_PATH = "/api/v1/mobility/observation"
GLITCH_LON, GLITCH_LAT = 126.9780, 37.5665     # Seoul City Hall: a valid coordinate, but not in Songdo


# ---------------------------------------------------------------------------
# Topic: bounded queue, drop-oldest
# ---------------------------------------------------------------------------
class BoundedTelemetryQueue:
    """Thread-safe queue with a fixed depth. put() never blocks; a full queue drops its oldest item."""

    def __init__(self, depth: int) -> None:
        if depth < 1:
            raise ValueError("depth must be at least 1")
        self.depth = depth
        self.dropped = 0
        self.high_water = 0
        self._items: deque[dict[str, Any]] = deque()
        self._ready = threading.Condition()
        self._closed = False

    def put(self, item: dict[str, Any]) -> bool:
        """Add an item. Returns False when an older item had to be dropped to make room."""
        with self._ready:
            overflow = len(self._items) >= self.depth
            if overflow:
                self._items.popleft()
                self.dropped += 1
            self._items.append(item)
            self.high_water = max(self.high_water, len(self._items))
            self._ready.notify()
            return not overflow

    def get(self, timeout: float = 0.2) -> dict[str, Any] | None:
        """Next item, or None when nothing arrived within the timeout."""
        with self._ready:
            if not self._items and not self._closed:
                self._ready.wait(timeout)
            return self._items.popleft() if self._items else None

    def close(self) -> None:
        with self._ready:
            self._closed = True
            self._ready.notify_all()

    @property
    def drained(self) -> bool:
        with self._ready:
            return self._closed and not self._items

    def __len__(self) -> int:
        with self._ready:
            return len(self._items)


# ---------------------------------------------------------------------------
# Vehicle motion along the route
# ---------------------------------------------------------------------------
def _metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distance between two (lon, lat) points; equirectangular is accurate enough over a few km."""
    mean_lat = math.radians((a[1] + b[1]) / 2)
    dx = math.radians(b[0] - a[0]) * math.cos(mean_lat) * 6_371_000
    dy = math.radians(b[1] - a[1]) * 6_371_000
    return math.hypot(dx, dy)


def _heading(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Compass bearing from a to b in degrees, 0 = north, [0, 360)."""
    mean_lat = math.radians((a[1] + b[1]) / 2)
    bearing = math.degrees(math.atan2((b[0] - a[0]) * math.cos(mean_lat), b[1] - a[1])) % 360
    return 0.0 if bearing >= 359.95 else round(bearing, 1)


class RouteDriver:
    """Moves a vehicle back and forth along a polyline of stops at a cruise speed."""

    def __init__(self, waypoints: list[tuple[float, float]], cruise_kmh: float = 28.0) -> None:
        if len(waypoints) < 2:
            raise ValueError("a route needs at least two stops")
        self.waypoints = waypoints
        self.cruise_kmh = cruise_kmh
        self.lengths = [_metres(a, b) for a, b in zip(waypoints, waypoints[1:])]
        self.total = sum(self.lengths)

    def state_at(self, elapsed_s: float) -> dict[str, float]:
        """Position, speed and heading after driving for elapsed_s seconds."""
        travelled = elapsed_s * self.cruise_kmh / 3.6
        lap, offset = divmod(travelled, self.total)
        forward = int(lap) % 2 == 0
        if not forward:
            offset = self.total - offset
        for index, length in enumerate(self.lengths):
            if offset <= length or index == len(self.lengths) - 1:
                a, b = self.waypoints[index], self.waypoints[index + 1]
                share = min(max(offset / length, 0.0), 1.0) if length else 0.0
                # slow down near a stop, as a real vehicle does
                near_stop = min(share, 1 - share) * length < 25
                start, end = (a, b) if forward else (b, a)
                return {"lon": round(a[0] + (b[0] - a[0]) * share, 6),
                        "lat": round(a[1] + (b[1] - a[1]) * share, 6),
                        "speed_kmh": round(self.cruise_kmh * (0.4 if near_stop else 1.0), 1),
                        "heading_deg": _heading(start, end)}
            offset -= length
        raise AssertionError("unreachable")


# ---------------------------------------------------------------------------
# Publisher and uplink
# ---------------------------------------------------------------------------
class TelemetryPublisher:
    """Builds one observation packet per tick (the ROS timer callback)."""

    def __init__(self, vehicle_id: str, driver: RouteDriver, faults: bool = True,
                 start_battery_pct: float = 86.0) -> None:
        self.vehicle_id = vehicle_id
        self.driver = driver
        self.faults = faults
        self.start_battery_pct = start_battery_pct
        self.sequence = 0
        self._last: dict[str, Any] | None = None

    def next_packets(self, elapsed_s: float, now: datetime) -> list[dict[str, Any]]:
        """Packets for this tick: normally one; two when the previous packet is re-sent."""
        self.sequence += 1
        packet: dict[str, Any] = {
            "vehicle_id": self.vehicle_id,
            **self.driver.state_at(elapsed_s),
            "battery_state_of_charge_pct": round(max(self.start_battery_pct - elapsed_s * 0.004, 5.0), 2),
            "observed_at": now.astimezone(KST).isoformat(timespec="milliseconds"),
        }
        packets = [packet]
        if self.faults and self.sequence % 25 == 0:         # GPS glitch: position jumps out of the city
            packet["lon"], packet["lat"] = GLITCH_LON, GLITCH_LAT
        elif self.faults and self.sequence % 40 == 0 and self._last is not None:
            packets.insert(0, dict(self._last))             # radio re-sends the previous packet
        self._last = packet
        return packets


@dataclass
class UplinkStats:
    published: int = 0
    outcomes: Counter = field(default_factory=Counter)      # LOADED / QUARANTINED / DUPLICATE
    rejected: int = 0                                       # 4xx: server refused the packet
    failed: int = 0                                         # gave up after retries
    retries: int = 0
    dropped: int = 0                                        # queue overflow (oldest dropped)
    queue_high_water: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    @property
    def delivered(self) -> int:
        return sum(self.outcomes.values())

    def as_dict(self) -> dict[str, Any]:
        lat = sorted(self.latencies_ms)
        return {
            "published": self.published, "delivered": self.delivered,
            "loaded": self.outcomes["LOADED"], "quarantined": self.outcomes["QUARANTINED"],
            "duplicates": self.outcomes["DUPLICATE"], "rejected": self.rejected, "failed": self.failed,
            "retries": self.retries, "dropped_by_queue": self.dropped,
            "queue_high_water": self.queue_high_water,
            "latency_ms_median": round(lat[len(lat) // 2], 1) if lat else None,
            "latency_ms_p95": round(lat[int(len(lat) * 0.95) - 1], 1) if lat else None,
        }


class TelemetryUplink:
    """Takes packets from the queue and POSTs them, with bounded retries."""

    def __init__(self, client: httpx.Client, queue: BoundedTelemetryQueue, stats: UplinkStats,
                 max_retries: int = 3, backoff_s: float = 0.2, send_delay_s: float = 0.0) -> None:
        self.client = client
        self.queue = queue
        self.stats = stats
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self.send_delay_s = send_delay_s
        self.headers = ingest_headers()

    def send(self, packet: dict[str, Any]) -> str:
        """Deliver one packet. Returns LOADED, QUARANTINED, DUPLICATE, REJECTED or FAILED."""
        for attempt in range(self.max_retries + 1):
            if attempt:
                self.stats.retries += 1
                time.sleep(self.backoff_s * 2 ** (attempt - 1))
            started = time.perf_counter()
            try:
                response = self.client.post(OBSERVATION_PATH, json=packet, headers=self.headers)
            except httpx.HTTPError:
                continue                                    # network problem: retry
            if response.status_code >= 500:
                continue                                    # server problem: retry
            if response.status_code >= 400:
                self.stats.rejected += 1                    # our packet is wrong: retrying cannot help
                return "REJECTED"
            self.stats.latencies_ms.append((time.perf_counter() - started) * 1000)
            outcome = response.json().get("outcome", "LOADED")
            self.stats.outcomes[outcome] += 1
            return outcome
        self.stats.failed += 1
        return "FAILED"

    def run(self) -> None:
        """Subscriber loop: runs until the queue is closed and empty."""
        while not self.queue.drained:
            packet = self.queue.get()
            if packet is None:
                continue
            if self.send_delay_s:
                time.sleep(self.send_delay_s)               # simulated slow uplink
            self.send(packet)


def run_node(client: httpx.Client, vehicle_id: str, waypoints: list[tuple[float, float]], *,
             duration_s: float = 20.0, rate_hz: float = 5.0, queue_depth: int = 50, faults: bool = True,
             send_delay_s: float = 0.0, dry_run: bool = False, verbose: bool = True) -> UplinkStats:
    """Drive for duration_s, publishing at rate_hz. Returns the delivery statistics."""
    queue = BoundedTelemetryQueue(queue_depth)
    stats = UplinkStats()
    publisher = TelemetryPublisher(vehicle_id, RouteDriver(waypoints), faults=faults)
    uplink = TelemetryUplink(client, queue, stats, send_delay_s=send_delay_s)
    worker = threading.Thread(target=uplink.run, name="uplink", daemon=True)
    if not dry_run:
        worker.start()

    period = 1.0 / rate_hz
    started = time.perf_counter()
    wall_start = datetime.now(KST)
    tick = 0
    while tick * period < duration_s:
        elapsed = tick * period
        for packet in publisher.next_packets(elapsed, wall_start + timedelta(seconds=elapsed)):
            stats.published += 1
            if dry_run:
                if verbose and stats.published <= 3:
                    print("  packet", packet)
            else:
                queue.put(packet)
        tick += 1
        if verbose and tick % max(int(rate_hz * 5), 1) == 0:
            print(f"  t={elapsed:5.1f}s  published {stats.published:4d}  delivered {stats.delivered:4d}  "
                  f"in queue {len(queue):3d}  dropped {queue.dropped}")
        time.sleep(max(started + tick * period - time.perf_counter(), 0))

    queue.close()
    if not dry_run:
        worker.join(timeout=60)
    stats.dropped, stats.queue_high_water = queue.dropped, queue.high_water
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vehicle", default="VEH-AV1-01")
    parser.add_argument("--route", default="AV-1", help="route number whose stops the vehicle follows")
    parser.add_argument("--duration", type=float, default=20.0, help="seconds to drive")
    parser.add_argument("--rate", type=float, default=5.0, help="packets per second")
    parser.add_argument("--queue-size", type=int, default=50, help="queue depth (KEEP_LAST)")
    parser.add_argument("--send-delay", type=float, default=0.0, help="extra seconds per send (slow uplink)")
    parser.add_argument("--no-faults", action="store_true", help="do not inject GPS glitches and re-sends")
    parser.add_argument("--dry-run", action="store_true", help="build packets but send nothing")
    args = parser.parse_args()
    if args.rate <= 0 or args.duration <= 0:
        parser.error("--rate and --duration must be positive")

    with api_client() as client:
        stops = get_json(client, f"/api/v1/mobility/routes/{args.route}/stops", direction=0)
        waypoints = [(s["lon"], s["lat"]) for s in stops]
        print(f"{args.vehicle} on route {args.route}: {len(stops)} stops, "
              f"{stops[0]['stop_name_ko']} -> {stops[-1]['stop_name_ko']}")
        print(f"publishing {args.rate:g} Hz for {args.duration:g} s, queue depth {args.queue_size}"
              f"{', DRY RUN' if args.dry_run else ''}")
        stats = run_node(client, args.vehicle, waypoints, duration_s=args.duration, rate_hz=args.rate,
                         queue_depth=args.queue_size, faults=not args.no_faults,
                         send_delay_s=args.send_delay, dry_run=args.dry_run)
        print("summary")
        for key, value in stats.as_dict().items():
            print(f"  {key:<20} {value}")
        if not args.dry_run and stats.outcomes["LOADED"]:
            latest = [v for v in get_json(client, "/api/v1/mobility/vehicles/positions", max_staleness_s=120)
                      if v["vehicle_id"] == args.vehicle]
            if latest:
                v = latest[0]
                print(f"read back: {v['vehicle_id']} at ({v['lon']}, {v['lat']}), {v['speed_kmh']} km/h, "
                      f"{v['staleness_seconds']} s ago")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
