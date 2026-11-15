"""DB5 Safety adapters: incoming incident reports and status transitions."""
from __future__ import annotations

from typing import Any

import psycopg

from adapters.base_adapter import VALID, BaseAdapter, ValidationResult, parse_timestamp

SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
STATUSES = ("REPORTED", "DISPATCHED", "ON_SCENE", "RESOLVED", "CLOSED", "CANCELLED")


class IncidentsAdapter(BaseAdapter):
    """Emergency reports: {incident_id, type_code, title, severity, lon, lat, reported_at}.

    Triage happens here: the restriction radius defaults to the incident type's value,
    and the response-time target follows from the severity (safety.severity_levels).
    A new incident always starts as REPORTED; the database trigger writes its first history row.
    """
    source_id = "SRC_SAF_02"
    domain_schema = "safety"
    target_table = "incidents"
    required_fields = ("incident_id", "type_code", "title", "severity", "reported_at")

    def validate(self, record: dict[str, Any]) -> ValidationResult:
        base = super().validate(record)
        if not base.ok:
            return base
        if str(record["severity"]).upper() not in SEVERITIES:
            return ValidationResult(False, "SCHEMA_VIOLATION", f"unknown severity {record['severity']!r}")
        if parse_timestamp(record["reported_at"]) is None:
            return ValidationResult(False, "SCHEMA_VIOLATION", "reported_at must be ISO-8601 with a timezone offset")
        return VALID

    def observed_at(self, record: dict[str, Any], received_at):
        return parse_timestamp(record["reported_at"]) or received_at

    def load(self, cur: psycopg.Cursor, record: dict[str, Any]) -> tuple[str, str]:
        # A repeated report for the same incident id (a second caller) only refreshes the
        # descriptive fields; it never resets the status.
        cur.execute(
            """INSERT INTO safety.incidents
                   (incident_id, type_code, title, severity, reported_at, restriction_radius_m, is_drill,
                    geom, source_id)
               SELECT %(id)s::varchar, t.type_code, %(title)s::varchar, %(severity)s::varchar,
                      %(reported_at)s::timestamptz,
                      COALESCE(%(radius)s::integer, t.default_restriction_radius_m), %(drill)s::boolean,
                      ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326), %(source)s::varchar
               FROM (SELECT %(type)s::varchar AS type_code) wanted
               LEFT JOIN safety.incident_types t USING (type_code)
               ON CONFLICT (incident_id) DO UPDATE
                   SET title = EXCLUDED.title, severity = EXCLUDED.severity
               RETURNING incident_id, (xmax = 0) AS inserted""",
            {
                "id": record["incident_id"],
                "type": str(record["type_code"]).upper(),
                "title": record["title"],
                "severity": str(record["severity"]).upper(),
                "reported_at": parse_timestamp(record["reported_at"]),
                "radius": record.get("restriction_radius_m"),
                "drill": bool(record.get("is_drill", False)),
                "lon": float(record["lon"]),
                "lat": float(record["lat"]),
                "source": self.source_id,
            },
        )
        incident_id, inserted = cur.fetchone()
        return incident_id, "INSERT" if inserted else "UPDATE"


class IncidentTransitionsAdapter(BaseAdapter):
    """Status changes from the dispatch stream: {incident_id, to_status, changed_at[, facility_id, note]}.

    The database function safety.transition_incident() enforces the state machine. A step that is
    not allowed (e.g. REPORTED -> RESOLVED), an unknown incident or an out-of-order timestamp is
    rejected by the database and lands in quarantine instead of corrupting the history.
    """
    source_id = "SRC_SAF_02"
    domain_schema = "safety"
    target_table = "incident_status_history"
    required_fields = ("incident_id", "to_status", "changed_at")
    lon_field = None
    lat_field = None

    def validate(self, record: dict[str, Any]) -> ValidationResult:
        base = super().validate(record)
        if not base.ok:
            return base
        if str(record["to_status"]).upper() not in STATUSES:
            return ValidationResult(False, "SCHEMA_VIOLATION", f"unknown status {record['to_status']!r}")
        if parse_timestamp(record["changed_at"]) is None:
            return ValidationResult(False, "SCHEMA_VIOLATION", "changed_at must be ISO-8601 with a timezone offset")
        return VALID

    def observed_at(self, record: dict[str, Any], received_at):
        return parse_timestamp(record["changed_at"]) or received_at

    def load(self, cur: psycopg.Cursor, record: dict[str, Any]) -> tuple[str, str]:
        to_status = str(record["to_status"]).upper()
        cur.execute(
            "SELECT safety.transition_incident(%s, %s, %s, %s, %s, %s)",
            (record["incident_id"], to_status, parse_timestamp(record["changed_at"]), self.actor,
             record.get("facility_id"), record.get("note")),
        )
        return record["incident_id"], "UPDATE"
