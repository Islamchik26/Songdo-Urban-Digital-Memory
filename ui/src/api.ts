// Typed access to the FastAPI backend. Every page goes through these helpers.

export interface Param {
  name: string;
  label: string;
  type: "float" | "int" | "str" | "datetime" | "date" | "time";
  default: string | number | null;
  min: number | null;
  max: number | null;
}

export interface Scenario {
  id: string;
  domain: string;
  title: string;
  summary: string;
  status: "planned" | "ready";
  sql: string | null;
  params: Param[];
}

export interface Registry {
  domains: Record<string, string>;
  scenarios: Scenario[];
}

export type Row = Record<string, unknown>;

export interface RunResult {
  scenario_id: string;
  params: Record<string, unknown>;
  sql: string;
  row_count: number;
  truncated: boolean;
  elapsed_ms: number;
  rows: Row[];
  explain: string[] | null;
  viz?: ChartSpec[] | null;     // how to draw the rows (api/visuals.py)
  story?: Story | null;         // plain-language headline and key numbers computed from the rows
}

export interface Story { headline: string; stats: { label: string; value: string }[] }

export interface Measure { column: string; label: string; unit?: string; unit_column?: string; divide?: number; max?: number }
interface StatusSpec { column: string; map: Record<string, string>; labels?: Record<string, string> }
/** One chart, as declared in api/visuals.py. Which keys apply depends on `type`. */
export interface ChartSpec {
  type: "line" | "columns" | "bars" | "paired_bars" | "timeline" | "stepper" | "tree" | "meters" | string;
  title: string;
  x?: { column: string; label: string; format?: "time" };
  y?: Measure;
  value?: Measure | string;
  band?: { min: string; max: string; label?: string };
  thresholds?: { column: string; label: string; role?: string }[];
  markers?: { column: string; map: Record<string, string>; label: string };
  zero_line?: boolean;
  label?: string | string[];
  sub?: string;
  sub_prefix?: string;
  limit?: number;
  null_label?: string;
  reference?: { column?: string; param?: string; value?: number; label: string };
  marker?: { column: string; label: string };
  status?: StatusSpec;
  unit?: string;
  unit_column?: string;
  a?: { column: string; label: string };
  b?: { column: string; label: string };
  start?: string;
  end?: string;
  open_end_param?: string;
  current?: string;
  duration?: { column: string; unit: string } | string;
  time?: string;
  state?: string;               // stepper: the column holding each step's state
  detail?: string;
  actor?: string;
  path?: string;
  count?: { column: string; label: string };
  kind?: string;
  age?: { column: string; unit: string };
}

export interface Health {
  status: "ok" | "down";
  database?: string;
  postgres?: string;
  postgis?: string;
  detail?: string;
}

// ---- map ------------------------------------------------------------------
export interface Point {
  lon: number;
  lat: number;
  name: string;
}
export interface Stop extends Point { stop_id: string; stop_number: string }
export interface RoutePattern {
  route_number: string;
  name: string;
  route_type: string;
  direction: number;
  path: [number, number][];
  stop_names: string[];
}
export interface Station extends Point { station_id: string; network: string; address: string }
export interface Attraction extends Point {
  attraction_id: string; name_en: string | null; category: string;
  is_wheelchair_accessible: boolean; has_parking: boolean;
}
export interface Building extends Point {
  building_id: string; name_en: string | null; building_use: string; floors_above: number | null;
}
export interface Facility extends Point {
  facility_id: string; name_en: string | null; facility_type: string; responder: string;
}
export interface MapLayers {
  stops: Stop[];
  routes: RoutePattern[];
  stations: Station[];
  attractions: Attraction[];
  buildings: Building[];
  facilities: Facility[];
  bbox: { min_lon: number; min_lat: number; max_lon: number; max_lat: number };
  time_range: { first_observation: string | null; last_observation: string | null; fixture_day_end: string | null };
}

export interface Vehicle {
  vehicle_id: string; vehicle_type: string; trip_id: string | null; route_number: string | null;
  lon: number; lat: number; speed_kmh: number; heading_deg: number | null; battery_pct: number | null;
  observed_at: string; staleness_seconds: number;
}
export interface ActiveIncident {
  incident_id: string; title: string; type_code: string; type_name: string; severity: string; status: string;
  status_since: string; reported_at: string; assigned_facility_id: string | null;
  restriction_radius_m: number | null; lon: number; lat: number;
}
export interface AirState {
  station_id: string; name: string; pm25: number | null; observed_at: string | null;
  age_minutes: number | null; grade: "NORMAL" | "BAD" | "VERY_BAD" | "NO_DATA";
}
export interface AirAlert {
  alert_id: number; station: string; type_code: string; severity: string; peak_value: number;
  started_hour: string; alert_message: string;
}
export interface Frame {
  as_of: string;
  vehicles: Vehicle[];
  incidents: ActiveIncident[];
  air: AirState[];
  alerts: AirAlert[];
  elapsed_ms: number;
}

// ---- ingestion / verification ------------------------------------------------
export interface Activity {
  batches: Row[];
  quarantine: Row[];
  audit: Row[];
  totals: { batches: number; quarantined: number; audit_entries: number };
}
export interface Check {
  component: string;
  check: string;
  status: "PASS" | "WARN" | "FAIL";
  detail: string;
}
export interface Verification {
  summary: Record<"PASS" | "WARN" | "FAIL", number>;
  checks: Check[];
}
export interface TestRun {
  passed: boolean;
  summary: string;
  elapsed_s: number;
  output_tail: string[];
}

export interface PostResult {
  status: number;
  ok: boolean;
  body: unknown;
  elapsed_ms: number;
}

// ---- helpers ---------------------------------------------------------------
async function readError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    return JSON.stringify(body.detail ?? body);
  } catch {
    return `${res.status} ${res.statusText}`;
  }
}

export async function getJson<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
  const query = params
    ? "?" + new URLSearchParams(
        Object.entries(params).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => [k, String(v)]),
      ).toString()
    : "";
  const res = await fetch(path + (query === "?" ? "" : query));
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

/** POST that never throws for HTTP errors: the ingestion page shows rejected requests too. */
export async function postJson(path: string, body: unknown, token?: string): Promise<PostResult> {
  return sendJson("POST", path, body, token);
}

/** POST / PATCH / DELETE with an optional JSON body. Never throws: network errors come back as status 0. */
export async function sendJson(method: "POST" | "PATCH" | "DELETE", path: string, body: unknown,
                               token?: string): Promise<PostResult> {
  const started = performance.now();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["X-API-Token"] = token;
  try {
    const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    let parsed: unknown = null;
    try {
      parsed = await res.json();
    } catch {
      parsed = null;
    }
    return { status: res.status, ok: res.ok, body: parsed, elapsed_ms: Math.round(performance.now() - started) };
  } catch (e) {
    return { status: 0, ok: false, body: { detail: (e as Error).message }, elapsed_ms: 0 };
  }
}

export async function getHealth(): Promise<Health> {
  try {
    const res = await fetch("/health");
    return await res.json();
  } catch {
    return { status: "down", detail: "API is not reachable" };
  }
}

export const getRegistry = () => getJson<Registry>("/api/v1/scenarios");
export const getMapLayers = () => getJson<MapLayers>("/api/v1/system/map/layers");
export const getFrame = (asOf?: string, maxStaleness = 120) =>
  getJson<Frame>("/api/v1/system/map/frame", { as_of: asOf, max_staleness_s: maxStaleness });
export const getActivity = (limit = 15) => getJson<Activity>("/api/v1/system/activity", { limit });
export const getVerification = () => getJson<Verification>("/api/v1/system/verification");

export async function runScenario(id: string, params: Record<string, string>, explain: boolean): Promise<RunResult> {
  const res = await fetch(`/api/v1/scenarios/${id}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ params, explain }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

// ---- token kept in the browser (local dev convenience only) -------------------
const TOKEN_KEY = "songdo.ingestToken";
export function loadToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "songdo-dev-token";
  } catch {
    return "songdo-dev-token";
  }
}
export function saveToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* private mode: keep it in memory only */
  }
}

// ---- time ------------------------------------------------------------------
/** "2026-10-02", 495 -> "2026-10-02T08:15:00+09:00" (Korean time, as stored in the data). */
export function kstIso(date: string, minuteOfDay: number): string {
  const h = String(Math.floor(minuteOfDay / 60)).padStart(2, "0");
  const m = String(minuteOfDay % 60).padStart(2, "0");
  return `${date}T${h}:${m}:00+09:00`;
}

/** Any ISO timestamp -> "2026-10-02 08:15:03" in Korean time. */
export function kstText(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const k = new Date(d.getTime() + 9 * 3600_000);
  return k.toISOString().slice(0, 19).replace("T", " ");
}

export function nowKstIso(): string {
  const k = new Date(Date.now() + 9 * 3600_000).toISOString().slice(0, 19);
  return `${k}+09:00`;
}
