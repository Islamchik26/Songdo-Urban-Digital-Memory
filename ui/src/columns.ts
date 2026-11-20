// Readable table headers and cells for task results. The SQL column name stays available as the
// header's tooltip, so people who read the SQL can still match a column to the query.

const NAMES: Record<string, string> = {
  lon: "Longitude",
  lat: "Latitude",
  geom: "Geometry",
  dist_m: "Distance (m)",
  name_ko: "Name (Korean)",
  name_en: "Name (English)",
  stop_name_ko: "Stop",
  stop_name: "Stop",
  nearby_stop_name: "Nearby stop",
  nearest_stop: "Nearest stop",
  nearest_stop_name: "Nearest stop",
  from_stop: "From stop",
  event_name_ko: "Event",
  active_event_name: "Event",
  attraction_name_ko: "Attraction",
  attraction_name: "Attraction",
  facility_name_ko: "Facility",
  building_name_ko: "Building",
  station_name_ko: "Station",
  nearest_station: "Nearest station",
  nearest_env_station: "Nearest air station",
  space_name: "Room / space",
  type_code: "Pollutant",
  pollutant_type: "Pollutant",
  sla_target_seconds: "Response target (s)",
  sla_met_pct: "Within target (%)",
  avg_response_s: "Average response (s)",
  median_response_s: "Median response (s)",
  p90_response_s: "90% answered within (s)",
  worst_monthly_p90_s: "Worst month, 90% within (s)",
  battery_state_of_charge_pct: "Battery (%)",
  speed_kmh: "Speed (km/h)",
  heading_deg: "Heading (°)",
  is_wheelchair_accessible: "Wheelchair access",
  has_parking: "Parking",
  admission_fee_krw: "Admission (KRW)",
  expected_attendance: "Expected visitors / day",
  attendees_per_seat: "Visitors per seat",
  verification_level: "Verification level (1–4)",
  payload: "Original record",
  payload_snapshot: "Record snapshot",
  reason_code: "Reason",
  reason_detail: "Details",
  zone_geojson: "Zone (GeoJSON)",
  headway_status: "Headway check",
  transit_status: "Transit check",
  schedule_check: "Schedule check",
  reconciliation_status: "Check result",
  status_at_t: "Status at that time",
  current_status: "Current status",
  is_current: "Current",
  is_drill: "Drill",
  bad_above: "'Bad' above",
  very_bad_above: "'Very bad' above",
  bad_threshold: "'Bad' limit",
  very_bad_threshold: "'Very bad' limit",
  avg_value: "Hourly average",
  min_value: "Lowest",
  max_value: "Highest",
  sample_count: "Samples",
  staleness_seconds: "Age of the fix (s)",
  silent_minutes: "Silent for (min)",
  last_seen: "Last reading",
  open_actions: "Open maintenance",
  last_maintenance: "Last maintenance",
  metres_from_previous: "From previous stop (m)",
  sequence_order: "#",
};

const WORDS: Record<string, string> = { id: "ID", sla: "SLA", pm: "PM", geojson: "GeoJSON", env: "air", mv: "view", ko: "", en: "" };

const UNITS: [RegExp, string][] = [
  [/_seconds$|_s$/, " (s)"],
  [/_minutes$|_min$/, " (min)"],
  [/_ms$/, " (ms)"],
  [/_meters$|_m$/, " (m)"],
  [/_m2$/, " (m²)"],
  [/_pct$/, " (%)"],
  [/_krw$/, " (KRW)"],
  [/_kmh$/, " (km/h)"],
];

/** "distance_to_stop_meters" -> "Distance to stop (m)"; known columns use the list above. */
export function columnLabel(name: string): string {
  if (NAMES[name]) return NAMES[name];
  let unit = "";
  let base = name;
  for (const [re, u] of UNITS) {
    if (re.test(base)) { unit = u; base = base.replace(re, ""); break; }
  }
  const lang = name.endsWith("_ko") ? " (Korean)" : name.endsWith("_en") ? " (English)" : "";
  const words = base.split("_").map((w) => WORDS[w] ?? w).filter(Boolean);
  const text = words.join(" ").replace(/\bat$/, "at");
  return (text.charAt(0).toUpperCase() + text.slice(1)) + lang + unit;
}

const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;

/** Timestamps in Korean time, booleans as Yes / No, objects as compact JSON. */
export function cellText(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "object") return JSON.stringify(value);
  if (typeof value === "string" && ISO.test(value)) {
    const d = new Date(value);
    if (!Number.isNaN(d.getTime())) {
      return d.toLocaleString("sv-SE", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", second: d.getSeconds() ? "2-digit" : undefined }).replace("T", " ");
    }
  }
  return String(value);
}
