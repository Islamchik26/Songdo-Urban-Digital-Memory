"""Shared ingestion pipeline: validate -> deduplicate (SHA-256) -> stage -> load -> audit.

Every domain adapter subclasses BaseAdapter and only implements `load()` (the
INSERT into its own domain table) and, when needed, `validate()`.

Rejected rows are never dropped: they go to core.quarantine_records with a
reason code. Re-sending the same payload is a no-op (idempotent).
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import psycopg
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from api.config import settings


# --------------------------------------------------------------------------- #
# Pure helpers (no database needed)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason_code: str | None = None
    detail: str = ""

    def __str__(self) -> str:  # readable in notebooks / logs
        return "OK" if self.ok else f"{self.reason_code}: {self.detail}"


VALID = ValidationResult(True)


def _in_bbox(lon: float, lat: float) -> bool:
    return (settings.min_lon <= lon <= settings.max_lon
            and settings.min_lat <= lat <= settings.max_lat)


def validate_coordinate(lon: Any, lat: Any) -> ValidationResult:
    """Check a WGS84 coordinate against the Songdo Teaching Bounding Box."""
    if lon is None or lat is None or lon == "" or lat == "":
        return ValidationResult(False, "MISSING_COORDINATE", "lon/lat is missing")
    try:
        lon_f, lat_f = float(lon), float(lat)
    except (TypeError, ValueError):
        return ValidationResult(False, "MALFORMED_COORDINATE", f"not numeric: lon={lon!r} lat={lat!r}")
    if not (math.isfinite(lon_f) and math.isfinite(lat_f)):
        return ValidationResult(False, "MALFORMED_COORDINATE", f"not finite: lon={lon!r} lat={lat!r}")
    if _in_bbox(lon_f, lat_f):
        return VALID
    if _in_bbox(lat_f, lon_f):
        return ValidationResult(
            False, "COORDINATE_SWAPPED",
            f"lon={lon_f} lat={lat_f} is inside Songdo only when swapped")
    return ValidationResult(False, "OUT_OF_BOUNDS", f"lon={lon_f} lat={lat_f} is outside the Songdo bbox")


def canonical_json(payload: Any) -> str:
    """Stable text form: sorted keys, no whitespace, Korean kept as UTF-8."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def calculate_payload_hash(payload: Any) -> str:
    """SHA-256 of the canonical JSON. Key order does not change the hash."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def parse_timestamp(value: Any) -> datetime | None:
    """ISO-8601 string -> aware datetime. Naive values are rejected (None)."""
    if isinstance(value, datetime):
        return value if value.tzinfo else None
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #
@dataclass
class IngestSummary:
    batch_id: int | None = None
    received: int = 0
    loaded: int = 0
    duplicates: int = 0
    quarantined: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class BaseAdapter:
    source_id: str = ""        # FK to core.source_registry
    domain_schema: str = ""    # e.g. "mobility"
    target_table: str = ""     # e.g. "stops"
    required_fields: tuple[str, ...] = ()
    lon_field: str | None = "lon"   # set both to None for records without coordinates
    lat_field: str | None = "lat"

    def __init__(self, conn: psycopg.Connection, actor: str = "SYSTEM") -> None:
        self.conn = conn
        self.actor = actor

    # ---- hooks for subclasses ------------------------------------------------
    def validate(self, record: dict[str, Any]) -> ValidationResult:
        missing = [f for f in self.required_fields if record.get(f) in (None, "")]
        if missing:
            return ValidationResult(False, "MISSING_FIELD", f"missing: {', '.join(missing)}")
        if self.lon_field and self.lat_field:
            return validate_coordinate(record.get(self.lon_field), record.get(self.lat_field))
        return VALID

    def observed_at(self, record: dict[str, Any], received_at: datetime) -> datetime:
        return parse_timestamp(record.get("observed_at")) or received_at

    def load(self, cur: psycopg.Cursor, record: dict[str, Any]) -> tuple[str, str]:
        """Write one validated record to the domain table.

        Returns (entity_id, action) where action is 'INSERT' or 'UPDATE'.
        """
        raise NotImplementedError

    # ---- pipeline ------------------------------------------------------------
    def ingest(self, records: Iterable[Any]) -> IngestSummary:
        summary = IngestSummary()
        # conn.transaction() is a real transaction, or a savepoint when the caller
        # already opened one (the tests use that to roll everything back).
        with self.conn.transaction(), self.conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                """INSERT INTO core.ingestion_batches (source_id, domain_schema, target_table, adapter_name)
                   VALUES (%s, %s, %s, %s) RETURNING batch_id""",
                (self.source_id, self.domain_schema, self.target_table, type(self).__name__),
            )
            summary.batch_id = cur.fetchone()[0]

            for raw in records:
                summary.received += 1
                received_at = datetime.now(timezone.utc)
                record = raw if isinstance(raw, dict) else {"_raw": str(raw)}
                sha = calculate_payload_hash(record)

                if not isinstance(raw, dict):
                    self._quarantine(cur, summary, record, sha, received_at,
                                     "SCHEMA_VIOLATION", "payload is not a JSON object")
                    continue

                cur.execute(
                    """SELECT 1 FROM core.staging_records
                       WHERE source_id = %s AND target_table = %s AND payload_sha256 = %s LIMIT 1""",
                    (self.source_id, self.target_table, sha),
                )
                if cur.fetchone():
                    summary.duplicates += 1
                    continue

                check = self.validate(record)
                if not check.ok:
                    self._quarantine(cur, summary, record, sha, received_at,
                                     check.reason_code or "OTHER", check.detail)
                    continue

                observed_at = self.observed_at(record, received_at)
                cur.execute(
                    """INSERT INTO core.staging_records
                           (batch_id, source_id, domain_schema, target_table, payload, payload_sha256,
                            observed_at, received_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (batch_id, payload_sha256) DO NOTHING
                       RETURNING staging_id""",
                    (summary.batch_id, self.source_id, self.domain_schema, self.target_table,
                     Jsonb(record), sha, observed_at, received_at),
                )
                staged = cur.fetchone()
                if staged is None:
                    summary.duplicates += 1
                    continue
                staging_id = staged[0]

                try:
                    with self.conn.transaction():  # savepoint: one bad row cannot abort the batch
                        entity_id, action = self.load(cur, record)
                        cur.execute(
                            "SELECT core.log_event(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                            (self.domain_schema, self.target_table, entity_id, action, observed_at,
                             received_at, self.source_id, Jsonb(record), self.actor),
                        )
                        cur.execute(
                            "UPDATE core.staging_records SET status = 'LOADED', entity_id = %s WHERE staging_id = %s",
                            (entity_id, staging_id),
                        )
                    summary.loaded += 1
                except psycopg.Error as exc:
                    cur.execute("UPDATE core.staging_records SET status = 'REJECTED' WHERE staging_id = %s",
                                (staging_id,))
                    reason = "FK_VIOLATION" if exc.sqlstate == "23503" else "SCHEMA_VIOLATION"
                    detail = (exc.diag.message_primary or str(exc)) if exc.diag else str(exc)
                    self._quarantine(cur, summary, record, sha, received_at, reason, detail)

            cur.execute(
                """UPDATE core.ingestion_batches
                   SET status = 'COMPLETED', finished_at = clock_timestamp(),
                       rows_received = %s, rows_loaded = %s, rows_duplicate = %s, rows_quarantined = %s
                   WHERE batch_id = %s""",
                (summary.received, summary.loaded, summary.duplicates, summary.quarantined, summary.batch_id),
            )
        return summary

    def _quarantine(self, cur: psycopg.Cursor, summary: IngestSummary, record: dict[str, Any],
                    sha: str, received_at: datetime, reason_code: str, detail: str) -> None:
        cur.execute(
            """INSERT INTO core.quarantine_records
                   (batch_id, source_id, domain_schema, target_table, reason_code, reason_detail,
                    payload, payload_sha256, received_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (source_id, payload_sha256, reason_code) DO NOTHING
               RETURNING quarantine_id""",
            (summary.batch_id, self.source_id, self.domain_schema, self.target_table,
             reason_code, detail, Jsonb(record), sha, received_at),
        )
        if cur.fetchone() is None:
            summary.duplicates += 1   # the same bad payload was already quarantined
        else:
            summary.quarantined += 1
