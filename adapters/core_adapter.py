"""Adapter for core.places — also the reference example for domain adapters."""
from __future__ import annotations

from typing import Any

import psycopg

from adapters.base_adapter import BaseAdapter


class PlacesAdapter(BaseAdapter):
    source_id = "SRC_CORE_01"
    domain_schema = "core"
    target_table = "places"
    required_fields = ("place_id", "name_ko", "category")

    def load(self, cur: psycopg.Cursor, record: dict[str, Any]) -> tuple[str, str]:
        cur.execute(
            """INSERT INTO core.places (place_id, name_ko, name_en, category, road_address, geom, provenance)
               VALUES (%(place_id)s, %(name_ko)s, %(name_en)s, %(category)s, %(road_address)s,
                       ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326), %(provenance)s)
               ON CONFLICT (place_id) DO UPDATE
                   SET name_ko = EXCLUDED.name_ko, name_en = EXCLUDED.name_en,
                       category = EXCLUDED.category, road_address = EXCLUDED.road_address,
                       geom = EXCLUDED.geom, provenance = EXCLUDED.provenance
               RETURNING place_id, (xmax = 0) AS inserted""",
            {
                "place_id": record["place_id"],
                "name_ko": record["name_ko"],
                "name_en": record.get("name_en"),
                "category": record["category"],
                "road_address": record.get("road_address"),
                "lon": float(record["lon"]),
                "lat": float(record["lat"]),
                "provenance": record.get("source_id") or self.source_id,
            },
        )
        place_id, inserted = cur.fetchone()
        return place_id, "INSERT" if inserted else "UPDATE"
