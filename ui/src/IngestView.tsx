// Ingestion playground: send good and bad records through the real write path and watch what happens.
import { useCallback, useEffect, useMemo, useState } from "react";
import { getActivity, kstText, loadToken, nowKstIso, postJson, saveToken } from "./api";
import type { Activity, PostResult, Row } from "./api";

type Kind = "text" | "number" | "select" | "datetime";
interface Field { name: string; label: string; kind: Kind; options?: string[]; optional?: boolean }
type Values = Record<string, string>;
interface Preset { label: string; expect: string; values: () => Values }
interface FormSpec {
  id: string;
  domain: string;
  title: string;
  explain: string;
  path: (v: Values) => string;
  fields: Field[];
  body: (v: Values) => unknown;
  presets: Preset[];
}

const num = (v: string) => (v.trim() === "" ? null : Number(v));
const opt = (v: string) => (v.trim() === "" ? null : v);
const stamp = () => nowKstIso().slice(0, 19) + "+09:00";
const suffix = () => String(Date.now()).slice(-6);

const FORMS: FormSpec[] = [
  {
    id: "gps",
    domain: "DB1 Mobility",
    title: "Vehicle GPS observation",
    explain: "One telemetry packet, as sent by the smart-mobility node. POST /api/v1/mobility/observation",
    path: () => "/api/v1/mobility/observation",
    fields: [
      { name: "vehicle_id", label: "Vehicle", kind: "text" },
      { name: "lon", label: "Longitude", kind: "number" },
      { name: "lat", label: "Latitude", kind: "number" },
      { name: "speed_kmh", label: "Speed km/h", kind: "number" },
      { name: "heading_deg", label: "Heading °", kind: "number", optional: true },
      { name: "battery_state_of_charge_pct", label: "Battery %", kind: "number", optional: true },
      { name: "observed_at", label: "Observed at", kind: "datetime" },
    ],
    body: (v) => ({
      vehicle_id: v.vehicle_id, lon: num(v.lon), lat: num(v.lat), speed_kmh: num(v.speed_kmh),
      heading_deg: num(v.heading_deg), battery_state_of_charge_pct: num(v.battery_state_of_charge_pct),
      observed_at: v.observed_at,
    }),
    presets: [
      { label: "Valid packet", expect: "LOADED",
        values: () => ({ vehicle_id: "VEH-AV1-01", lon: "126.6435", lat: "37.3880", speed_kmh: "18.5",
          heading_deg: "45", battery_state_of_charge_pct: "82", observed_at: stamp() }) },
      { label: "GPS glitch (Seoul)", expect: "QUARANTINED · OUT_OF_BOUNDS",
        values: () => ({ vehicle_id: "VEH-AV1-01", lon: "126.9780", lat: "37.5665", speed_kmh: "20",
          heading_deg: "", battery_state_of_charge_pct: "", observed_at: stamp() }) },
      { label: "Unknown vehicle", expect: "QUARANTINED · FK_VIOLATION",
        values: () => ({ vehicle_id: "VEH-GHOST-99", lon: "126.6435", lat: "37.3880", speed_kmh: "12",
          heading_deg: "", battery_state_of_charge_pct: "", observed_at: stamp() }) },
      { label: "Speed 400 km/h", expect: "REJECTED by API (422)",
        values: () => ({ vehicle_id: "VEH-AV1-01", lon: "126.6435", lat: "37.3880", speed_kmh: "400",
          heading_deg: "", battery_state_of_charge_pct: "", observed_at: stamp() }) },
    ],
  },
  {
    id: "telemetry",
    domain: "DB2 Building",
    title: "Building sensor reading",
    explain: "One device reading. Values outside the metric's valid range are kept but flagged SUSPECT. " +
      "POST /api/v1/building/telemetry",
    path: () => "/api/v1/building/telemetry",
    fields: [
      { name: "device_id", label: "Device", kind: "text" },
      { name: "metric", label: "Metric", kind: "select", options: ["TEMPERATURE_C", "CO2_PPM", "POWER_KW"] },
      { name: "value", label: "Value", kind: "number" },
      { name: "observed_at", label: "Observed at", kind: "datetime" },
    ],
    body: (v) => ({ device_id: v.device_id, metric: v.metric, value: num(v.value), observed_at: v.observed_at }),
    presets: [
      { label: "Valid reading", expect: "LOADED",
        values: () => ({ device_id: "DEV-GT-TMP-01", metric: "TEMPERATURE_C", value: "22.4", observed_at: stamp() }) },
      { label: "Implausible 95 °C", expect: "LOADED, flagged SUSPECT",
        values: () => ({ device_id: "DEV-GT-TMP-01", metric: "TEMPERATURE_C", value: "95", observed_at: stamp() }) },
      { label: "Unknown device", expect: "QUARANTINED · FK_VIOLATION",
        values: () => ({ device_id: "DEV-NOPE-01", metric: "TEMPERATURE_C", value: "21", observed_at: stamp() }) },
      { label: "No timezone", expect: "REJECTED by API (422)",
        values: () => ({ device_id: "DEV-GT-TMP-01", metric: "TEMPERATURE_C", value: "21", observed_at: "2026-10-02 10:00" }) },
    ],
  },
  {
    id: "air",
    domain: "DB4 Environment",
    title: "Air-quality reading (AirKorea format)",
    explain: "Hourly reading in AirKorea's own format. '-' is the station's fault marker. After loading, the hourly " +
      "rollup and alert rules are re-evaluated. POST /api/v1/environment/measurements",
    path: () => "/api/v1/environment/measurements",
    fields: [
      { name: "station_name", label: "Station", kind: "select", options: ["송도", "아암", "동막", "동춘", "인천 신항"] },
      { name: "type_code", label: "Pollutant", kind: "select", options: ["PM25", "PM10", "O3", "NO2", "CO", "SO2"] },
      { name: "value", label: "Value (or -)", kind: "text" },
      { name: "data_time", label: "Data time (KST)", kind: "text" },
    ],
    body: (v) => [{ station_name: v.station_name, type_code: v.type_code,
      value: v.value.trim() === "-" ? "-" : num(v.value), data_time: v.data_time }],
    presets: [
      { label: "Valid hour", expect: "LOADED",
        values: () => ({ station_name: "송도", type_code: "PM25", value: "18", data_time: "2026-10-03 09:00" }) },
      { label: "Very bad PM2.5", expect: "LOADED + new alert",
        values: () => ({ station_name: "동춘", type_code: "PM25", value: "88", data_time: "2026-10-03 10:00" }) },
      { label: "Fault marker '-'", expect: "QUARANTINED · FAULT_READING",
        values: () => ({ station_name: "송도", type_code: "PM10", value: "-", data_time: "2026-10-03 09:00" }) },
      { label: "AirKorea 24:00", expect: "LOADED as next day 00:00",
        values: () => ({ station_name: "아암", type_code: "O3", value: "0.021", data_time: "2026-10-03 24:00" }) },
    ],
  },
  {
    id: "incident",
    domain: "DB5 Safety",
    title: "Report an incident",
    explain: "A new incident starts in REPORTED. POST /api/v1/safety/incidents",
    path: () => "/api/v1/safety/incidents",
    fields: [
      { name: "incident_id", label: "Incident ID", kind: "text" },
      { name: "type_code", label: "Type", kind: "select",
        options: ["TRAFFIC_ACCIDENT", "FIRE", "MEDICAL", "HAZMAT", "FLOOD", "ROAD_OBSTRUCTION"] },
      { name: "title", label: "Title", kind: "text" },
      { name: "severity", label: "Severity", kind: "select", options: ["LOW", "MEDIUM", "HIGH", "CRITICAL"] },
      { name: "lon", label: "Longitude", kind: "number" },
      { name: "lat", label: "Latitude", kind: "number" },
      { name: "reported_at", label: "Reported at", kind: "datetime" },
      { name: "restriction_radius_m", label: "Restriction m", kind: "number", optional: true },
    ],
    body: (v) => ({ incident_id: v.incident_id, type_code: v.type_code, title: v.title, severity: v.severity,
      lon: num(v.lon), lat: num(v.lat), reported_at: v.reported_at, restriction_radius_m: num(v.restriction_radius_m) }),
    presets: [
      { label: "New accident", expect: "LOADED",
        values: () => ({ incident_id: `INC-DEMO-${suffix()}`, type_code: "TRAFFIC_ACCIDENT", title: "센트럴파크 교차로 접촉 사고",
          severity: "MEDIUM", lon: "126.6390", lat: "37.3925", reported_at: stamp(), restriction_radius_m: "150" }) },
      { label: "Outside Songdo", expect: "QUARANTINED · OUT_OF_BOUNDS",
        values: () => ({ incident_id: `INC-DEMO-${suffix()}`, type_code: "FIRE", title: "부평역 화재",
          severity: "HIGH", lon: "126.7240", lat: "37.4895", reported_at: stamp(), restriction_radius_m: "" }) },
    ],
  },
  {
    id: "transition",
    domain: "DB5 Safety",
    title: "Move an incident to its next status",
    explain: "The state machine allows REPORTED→DISPATCHED→ON_SCENE→RESOLVED→CLOSED (or CANCELLED early). " +
      "Illegal steps are refused by the database. POST /api/v1/safety/incidents/{id}/transition",
    path: (v) => `/api/v1/safety/incidents/${encodeURIComponent(v.incident_id)}/transition`,
    fields: [
      { name: "incident_id", label: "Incident ID", kind: "text" },
      { name: "to_status", label: "To status", kind: "select",
        options: ["DISPATCHED", "ON_SCENE", "RESOLVED", "CLOSED", "CANCELLED"] },
      { name: "facility_id", label: "Facility", kind: "text", optional: true },
      { name: "note", label: "Note", kind: "text", optional: true },
    ],
    body: (v) => ({ to_status: v.to_status, facility_id: opt(v.facility_id), note: opt(v.note), actor: "UI" }),
    presets: [
      { label: "Dispatch (use ID from a new report)", expect: "200 + history",
        values: () => ({ incident_id: "", to_status: "DISPATCHED", facility_id: "FAC_POLICE_SONGDO1", note: "unit 12" }) },
      { label: "Illegal: closed → dispatched", expect: "REJECTED by database (409)",
        values: () => ({ incident_id: "INC-20261002-0001", to_status: "DISPATCHED", facility_id: "", note: "" }) },
    ],
  },
];

interface Sent { id: number; form: string; path: string; payload: unknown; result: PostResult; at: string }

function outcome(r: PostResult): { label: string; tone: "ok" | "warn" | "bad" | "info" } {
  const body = (r.body ?? {}) as Record<string, unknown>;
  if (r.status === 0) return { label: "API NOT REACHABLE", tone: "bad" };
  if (r.status === 401) return { label: "UNAUTHORIZED (token)", tone: "bad" };
  if (r.status === 422) return { label: "REJECTED BY API (422)", tone: "bad" };
  if (r.status === 409) return { label: "REJECTED BY DATABASE (409)", tone: "bad" };
  if (r.status === 404) return { label: "NOT FOUND (404)", tone: "bad" };
  if (!r.ok) return { label: `HTTP ${r.status}`, tone: "bad" };
  const o = typeof body.outcome === "string" ? body.outcome
    : Number(body.loaded) > 0 ? "LOADED" : Number(body.quarantined) > 0 ? "QUARANTINED"
    : Number(body.duplicates) > 0 ? "DUPLICATE" : "OK";
  const extra = Number(body.new_alerts) > 0 ? ` + ${body.new_alerts} new alert` : "";
  return { label: o + extra, tone: o === "LOADED" || o === "OK" ? "ok" : o === "QUARANTINED" ? "warn" : "info" };
}

function MiniTable({ rows, columns }: { rows: Row[]; columns: [string, string][] }) {
  if (rows.length === 0) return <p className="muted small">Nothing yet.</p>;
  return (
    <div className="table-wrap short">
      <table>
        <thead><tr>{columns.map(([, label]) => <th key={label}>{label}</th>)}</tr></thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map(([key]) => {
                const v = row[key];
                const text = v === null || v === undefined ? "—"
                  : key.endsWith("_at") ? kstText(String(v))
                  : typeof v === "object" ? JSON.stringify(v) : String(v);
                return <td key={key} title={text}>{text}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function IngestView() {
  const [formId, setFormId] = useState(FORMS[0].id);
  const form = useMemo(() => FORMS.find((f) => f.id === formId)!, [formId]);
  const [values, setValues] = useState<Values>(() => FORMS[0].presets[0].values());
  const [token, setToken] = useState(loadToken);
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState<Sent[]>([]);
  const [activity, setActivity] = useState<Activity | null>(null);
  const [lastIncident, setLastIncident] = useState("");

  const refresh = useCallback(() => {
    getActivity(12).then(setActivity).catch(() => undefined);
  }, []);
  useEffect(refresh, [refresh]);

  function pickForm(id: string) {
    const f = FORMS.find((x) => x.id === id)!;
    setFormId(id);
    const v = f.presets[0].values();
    if (id === "transition" && lastIncident) v.incident_id = lastIncident;
    setValues(v);
  }

  function applyPreset(p: Preset) {
    const v = p.values();
    if (form.id === "transition" && v.incident_id === "" && lastIncident) v.incident_id = lastIncident;
    setValues(v);
  }

  async function send() {
    setSending(true);
    const payload = form.body(values);
    const path = form.path(values);
    const result = await postJson(path, payload, token);
    if (form.id === "incident" && result.ok) setLastIncident(values.incident_id);
    setSent((s) => [{ id: Date.now(), form: form.title, path, payload, result, at: new Date().toLocaleTimeString() }, ...s].slice(0, 20));
    setSending(false);
    refresh();
  }

  const payload = form.body(values);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2 className="title">Ingestion playground</h2>
          <p className="summary">
            Every record goes through the same pipeline as the batch scripts: request schema check, SHA-256
            deduplication, coordinate and field validation, quarantine with a reason code, then an append-only audit
            entry. Send a preset twice to see <b>DUPLICATE</b>.
          </p>
        </div>
        <label className="token">
          X-API-Token
          <input type="password" value={token} onChange={(e) => { setToken(e.target.value); saveToken(e.target.value); }} />
        </label>
      </div>

      <div className="ingest-grid">
        <section className="card">
          <div className="form-tabs">
            {FORMS.map((f) => (
              <button key={f.id} className={f.id === formId ? "on" : ""} onClick={() => pickForm(f.id)}>
                <small>{f.domain}</small>{f.title}
              </button>
            ))}
          </div>
          <p className="muted small">{form.explain}</p>

          <div className="presets">
            {form.presets.map((p) => (
              <button key={p.label} onClick={() => applyPreset(p)} title={`Expected: ${p.expect}`}>
                {p.label}<small>{p.expect}</small>
              </button>
            ))}
          </div>

          <form className="params" onSubmit={(e) => { e.preventDefault(); send(); }}>
            {form.fields.map((f) => (
              <label key={f.name}>
                <span>{f.label}{f.optional && <small> (optional)</small>}</span>
                {f.kind === "select" ? (
                  <select value={values[f.name] ?? ""} onChange={(e) => setValues({ ...values, [f.name]: e.target.value })}>
                    {f.options!.map((o) => <option key={o}>{o}</option>)}
                  </select>
                ) : (
                  <input
                    className={f.kind === "datetime" ? "wide" : ""}
                    value={values[f.name] ?? ""}
                    inputMode={f.kind === "number" ? "decimal" : "text"}
                    onChange={(e) => setValues({ ...values, [f.name]: e.target.value })}
                  />
                )}
              </label>
            ))}
            <button className="run" type="submit" disabled={sending}>{sending ? "Sending…" : "Send"}</button>
          </form>

          <details>
            <summary>Request body</summary>
            <pre>{`POST ${form.path(values)}\n${JSON.stringify(payload, null, 2)}`}</pre>
          </details>
        </section>

        <section className="card">
          <h3>Responses</h3>
          {sent.length === 0 && <p className="muted small">Pick a preset and press Send.</p>}
          {sent.map((s) => {
            const o = outcome(s.result);
            return (
              <details key={s.id} className="response" open={s === sent[0]}>
                <summary>
                  <span className={`pill ${o.tone}`}>{o.label}</span> {s.form} <span className="muted">· {s.at} · {s.result.elapsed_ms} ms</span>
                </summary>
                <pre>{JSON.stringify(s.result.body, null, 2)}</pre>
              </details>
            );
          })}
        </section>
      </div>

      {activity && (
        <div className="activity">
          <div className="activity-head">
            <h3>What the database recorded</h3>
            <span className="muted small">
              {activity.totals.batches.toLocaleString()} batches · {activity.totals.quarantined.toLocaleString()} quarantined ·{" "}
              {activity.totals.audit_entries.toLocaleString()} audit entries
            </span>
            <button className="ghost" onClick={refresh}>Refresh</button>
          </div>
          <h4>Ingestion batches (core.ingestion_batches)</h4>
          <MiniTable rows={activity.batches} columns={[["batch_id", "batch"], ["adapter_name", "adapter"],
            ["target_table", "table"], ["rows_received", "recv"], ["rows_loaded", "loaded"], ["rows_duplicate", "dup"],
            ["rows_quarantined", "quar."], ["started_at", "started (KST)"]]} />
          <h4>Quarantine (core.quarantine_records): never dropped, always with a reason</h4>
          <MiniTable rows={activity.quarantine} columns={[["quarantine_id", "id"], ["target_table", "table"],
            ["reason_code", "reason"], ["reason_detail", "detail"], ["payload", "payload"], ["recorded_at", "recorded (KST)"]]} />
          <h4>Audit ledger (core.event_audit_log): append-only, three timestamps</h4>
          <MiniTable rows={activity.audit} columns={[["audit_id", "id"], ["table_name", "table"], ["entity_id", "entity"],
            ["action", "action"], ["actor", "actor"], ["observed_at", "observed"], ["received_at", "received"],
            ["recorded_at", "recorded"]]} />
        </div>
      )}
    </div>
  );
}
