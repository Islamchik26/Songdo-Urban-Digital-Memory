// Edit page: pick an existing record, see its current values and history, correct it or retire it.
// Writes go to PATCH / DELETE on the domain routes (api/edits.py); every change is audited.
import { useCallback, useEffect, useMemo, useState } from "react";
import { getJson, kstText, loadToken, saveToken, sendJson } from "./api";
import type { PostResult } from "./api";

type EntityId = "place" | "attraction" | "event" | "device";
type Kind = "text" | "number" | "bool" | "select" | "date" | "time";
interface FieldSpec { name: string; label: string; kind: Kind; options?: string[] }
interface EntitySpec {
  id: EntityId;
  title: string;
  domain: string;
  path: string;                 // PATCH / DELETE base path
  deleteLabel: string;
  deleteRule: string;           // what DELETE does for this kind of record
  fields: FieldSpec[];
  examples: { id: string; note: string }[];
}
interface RecordItem { id: string; name: string; detail: string }
interface AuditRow { audit_id: number; action: string; actor: string; recorded_at: string; payload_snapshot: unknown }
interface Loaded {
  entity: EntityId;
  entity_id: string;
  nullable: string[];
  record: Record<string, unknown>;
  audit: AuditRow[];
}
interface Sent { id: number; label: string; request: string; payload: unknown; result: PostResult; at: string }

const ENTITIES: EntitySpec[] = [
  {
    id: "place", title: "Places", domain: "Core", path: "/api/v1/core/places/",
    deleteLabel: "Delete place",
    deleteRule: "Removed only when no building or attraction refers to it (otherwise 409). The full row stays in the audit log.",
    fields: [
      { name: "name_ko", label: "Name (Korean)", kind: "text" },
      { name: "name_en", label: "Name (English)", kind: "text" },
      { name: "category", label: "Category", kind: "text" },
      { name: "road_address", label: "Road address", kind: "text" },
      { name: "lon", label: "Longitude", kind: "number" },
      { name: "lat", label: "Latitude", kind: "number" },
    ],
    examples: [
      { id: "PLC_INU", note: "not used by other tables: can be edited and deleted" },
      { id: "PLC_CONVENSIA", note: "used by a building and an attraction: delete is refused" },
    ],
  },
  {
    id: "attraction", title: "Attractions", domain: "DB3 Tourism", path: "/api/v1/tourism/attractions/",
    deleteLabel: "Delete attraction",
    deleteRule: "Removed only when no events, opening hours or visitor counts refer to it (otherwise 409).",
    fields: [
      { name: "attraction_name_ko", label: "Name (Korean)", kind: "text" },
      { name: "attraction_name_en", label: "Name (English)", kind: "text" },
      { name: "category", label: "Category", kind: "select", options: ["PARK", "CULTURE", "CONVENTION", "SHOPPING", "LANDMARK"] },
      { name: "road_address", label: "Road address", kind: "text" },
      { name: "is_wheelchair_accessible", label: "Wheelchair access", kind: "bool" },
      { name: "has_parking", label: "Parking", kind: "bool" },
      { name: "parking_spaces", label: "Parking spaces", kind: "number" },
      { name: "admission_fee_krw", label: "Fee (KRW)", kind: "number" },
      { name: "lon", label: "Longitude", kind: "number" },
      { name: "lat", label: "Latitude", kind: "number" },
    ],
    examples: [
      { id: "ATT_G_TOWER_OBS", note: "try a new admission fee" },
      { id: "ATT_HANOK_VILLAGE", note: "no parking: setting parking spaces is refused by the database" },
    ],
  },
  {
    id: "event", title: "Events", domain: "DB3 Tourism", path: "/api/v1/tourism/events/",
    deleteLabel: "Cancel event",
    deleteRule: "Soft delete: the event is marked cancelled and kept; tasks and views leave it out.",
    fields: [
      { name: "event_name_ko", label: "Name (Korean)", kind: "text" },
      { name: "event_name_en", label: "Name (English)", kind: "text" },
      { name: "event_type", label: "Type", kind: "select", options: ["CONFERENCE", "FESTIVAL", "EXHIBITION", "CONCERT"] },
      { name: "start_date", label: "Start date", kind: "date" },
      { name: "end_date", label: "End date", kind: "date" },
      { name: "daily_start_time", label: "Opens", kind: "time" },
      { name: "daily_end_time", label: "Closes", kind: "time" },
      { name: "expected_attendance", label: "Visitors per day", kind: "number" },
    ],
    examples: [
      { id: "EVT_SKYLINE_PHOTO", note: "safe to edit or cancel in a demo" },
      { id: "EVT_TRIBOWL_CONCERT", note: "set an end date before the start: refused by the database" },
    ],
  },
  {
    id: "device", title: "Devices", domain: "DB2 Building", path: "/api/v1/building/devices/",
    deleteLabel: "Retire device",
    deleteRule: "Retire: status RETIRED and its current room interval closed now. Where it was in the past stays unchanged; a retired device cannot be edited or moved.",
    fields: [
      { name: "current_status", label: "Status", kind: "select", options: ["ACTIVE", "FAULT", "MAINTENANCE"] },
      { name: "manufacturer", label: "Manufacturer", kind: "text" },
      { name: "model", label: "Model", kind: "text" },
      { name: "serial_number", label: "Serial number", kind: "text" },
      { name: "protocol", label: "Protocol", kind: "select", options: ["BACNET", "MODBUS", "MQTT"] },
    ],
    examples: [
      { id: "DEV-CV-PWR-01", note: "safe to change status or retire in a demo" },
      { id: "DEV-CV-TMP-03", note: "already retired: editing is refused" },
    ],
  },
];

const asText = (v: unknown): string => (v === null || v === undefined ? "" : String(v));
/** A dropdown always shows the record's real value, even one it does not offer (e.g. RETIRED). */
const withCurrent = (options: string[], value: string): string[] =>
  value === "" || options.includes(value) ? options : [value, ...options];

/** Form text -> JSON value. Empty means null (clears an optional field). */
function toValue(f: FieldSpec, raw: string): unknown {
  if (raw.trim() === "") return null;
  if (f.kind === "number") return Number(raw);
  if (f.kind === "bool") return raw === "true";
  if (f.kind === "time" && /^\d\d:\d\d$/.test(raw)) return `${raw}:00`;
  return raw;
}

function outcome(r: PostResult): { label: string; tone: "ok" | "warn" | "bad" | "info" } {
  const body = (r.body ?? {}) as Record<string, unknown>;
  if (r.status === 0) return { label: "API NOT REACHABLE", tone: "bad" };
  if (r.status === 401) return { label: "UNAUTHORIZED (token)", tone: "bad" };
  if (r.status === 422) return { label: "REJECTED BY API (422)", tone: "bad" };
  if (r.status === 409) return { label: "REJECTED BY DATABASE (409)", tone: "bad" };
  if (r.status === 404) return { label: "NOT FOUND (404)", tone: "bad" };
  if (!r.ok) return { label: `HTTP ${r.status}`, tone: "bad" };
  const o = typeof body.outcome === "string" ? body.outcome : "OK";
  return { label: o, tone: ["UPDATED", "DELETED", "CANCELLED", "RETIRED"].includes(o) ? "ok" : "info" };
}

function hashParams(): URLSearchParams {
  return new URLSearchParams(location.hash.split("?")[1] ?? "");
}

export default function EditView() {
  const initial = hashParams();
  const [entityId, setEntityId] = useState<EntityId>(
    (ENTITIES.find((e) => e.id === initial.get("entity"))?.id ?? "place") as EntityId);
  const spec = useMemo(() => ENTITIES.find((e) => e.id === entityId)!, [entityId]);
  const [records, setRecords] = useState<RecordItem[]>([]);
  const [recordId, setRecordId] = useState(initial.get("id") ?? "");
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [loadError, setLoadError] = useState("");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [token, setToken] = useState(loadToken);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<Sent[]>([]);

  const loadList = useCallback(() => {
    getJson<RecordItem[]>(`/api/v1/system/records/${entityId}`).then(setRecords).catch(() => setRecords([]));
  }, [entityId]);
  useEffect(loadList, [loadList]);

  const load = useCallback(async (id: string, entity: EntityId = entityId) => {
    const target = id.trim();
    if (!target) return;
    setLoadError("");
    try {
      const data = await getJson<Loaded>(`/api/v1/system/records/${entity}/${encodeURIComponent(target)}`);
      const s = ENTITIES.find((e) => e.id === entity)!;
      setLoaded(data);
      setDraft(Object.fromEntries(s.fields.map((f) => [f.name, asText(data.record[f.name])])));
      location.hash = `#/edit?entity=${entity}&id=${encodeURIComponent(target)}`;
    } catch (e) {
      setLoaded(null);
      setLoadError((e as Error).message);
    }
  }, [entityId]);

  // a link like #/edit?entity=event&id=EVT_SKYLINE_PHOTO opens that record directly
  useEffect(() => {
    if (initial.get("id")) load(initial.get("id")!, entityId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function pickEntity(id: EntityId) {
    setEntityId(id);
    setRecordId("");
    setLoaded(null);
    setLoadError("");
    location.hash = `#/edit?entity=${id}`;
  }

  const current = loaded && loaded.entity === entityId ? loaded : null;
  const changed = current
    ? spec.fields.filter((f) => (draft[f.name] ?? "") !== asText(current.record[f.name]))
    : [];
  const retired = entityId === "device" && current?.record.current_status === "RETIRED";
  const cancelled = entityId === "event" && current?.record.is_cancelled === true;

  async function send(method: "PATCH" | "DELETE") {
    if (!current) return;
    const path = `${spec.path}${encodeURIComponent(current.entity_id)}`;
    const payload = method === "PATCH"
      ? Object.fromEntries(changed.map((f) => [f.name, toValue(f, draft[f.name] ?? "")]))
      : undefined;
    if (method === "DELETE" && !window.confirm(`${spec.deleteLabel} ${current.entity_id}?\n\n${spec.deleteRule}`)) return;
    setBusy(true);
    const result = await sendJson(method, path, payload, token);
    setSent((s) => [{ id: Date.now(), label: `${method === "PATCH" ? "Edit" : spec.deleteLabel} ${current.entity_id}`,
      request: `${method} ${path}`, payload, result, at: new Date().toLocaleTimeString() }, ...s].slice(0, 20));
    setBusy(false);
    const deleted = result.ok && (result.body as Record<string, unknown>)?.outcome === "DELETED";
    if (deleted) {
      setLoaded(null);
      setRecordId("");
      location.hash = `#/edit?entity=${entityId}`;
    } else {
      await load(current.entity_id);
    }
    loadList();
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2 className="title">Edit and retire records</h2>
          <p className="summary">
            Correct or remove existing reference data. Every change is written in one transaction together with an
            audit entry holding the old and new values. A place or attraction is deleted only when nothing refers to
            it; an event is cancelled and a device retired instead, so history is kept. Measurements and GPS history
            are never edited here: a correction is sent again through <a href="#/ingest">Ingest</a>.
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
            {ENTITIES.map((e) => (
              <button key={e.id} className={e.id === entityId ? "on" : ""} onClick={() => pickEntity(e.id)}>
                <small>{e.domain}</small>{e.title}
              </button>
            ))}
          </div>

          <form className="params" onSubmit={(e) => { e.preventDefault(); load(recordId); }}>
            <label>
              <span>Record ({records.length} available)</span>
              <input className="wide" list="edit-records" value={recordId} placeholder="type or pick an ID"
                     onChange={(e) => {
                       setRecordId(e.target.value);
                       if (records.some((r) => r.id === e.target.value)) load(e.target.value);
                     }} />
              <datalist id="edit-records">
                {records.map((r) => <option key={r.id} value={r.id}>{`${r.name} · ${r.detail}`}</option>)}
              </datalist>
            </label>
            <button className="run" type="submit" disabled={!recordId.trim()}>Load current values</button>
          </form>

          <div className="presets">
            {spec.examples.map((x) => (
              <button key={x.id} onClick={() => { setRecordId(x.id); load(x.id); }} title={x.note}>
                {x.id}<small>{x.note}</small>
              </button>
            ))}
          </div>

          {loadError && <p className="error">{loadError}</p>}
          {!current && !loadError && <p className="muted small">Pick a record to see and change its values.</p>}

          {current && (
            <>
              <h3 className="edit-title">
                {current.entity_id}
                {retired && <span className="pill warn">RETIRED</span>}
                {cancelled && <span className="pill warn">CANCELLED</span>}
              </h3>
              {retired && <p className="muted small">A retired device is kept for history and cannot be edited or moved.</p>}
              <form className="params edit-form" onSubmit={(e) => { e.preventDefault(); send("PATCH"); }}>
                {spec.fields.map((f) => {
                  const value = draft[f.name] ?? "";
                  const isChanged = changed.includes(f);
                  const set = (v: string) => setDraft({ ...draft, [f.name]: v });
                  const optional = current.nullable.includes(f.name);
                  return (
                    <label key={f.name} className={isChanged ? "changed" : ""}>
                      <span>{f.label}{optional && <small> (optional)</small>}</span>
                      {f.kind === "select" || f.kind === "bool" ? (
                        <select value={value} disabled={retired} onChange={(e) => set(e.target.value)}>
                          {withCurrent(f.kind === "bool" ? ["true", "false"] : f.options!, value)
                            .map((o) => <option key={o}>{o}</option>)}
                        </select>
                      ) : (
                        <input value={value} disabled={retired}
                               type={f.kind === "date" ? "date" : f.kind === "time" ? "time" : "text"}
                               step={f.kind === "time" ? 1 : undefined}
                               inputMode={f.kind === "number" ? "decimal" : "text"}
                               onChange={(e) => set(e.target.value)} />
                      )}
                      {isChanged && <small className="was">was: {asText(current.record[f.name]) || "empty"}</small>}
                    </label>
                  );
                })}
              </form>
              <div className="edit-actions">
                <button className="run" disabled={busy || retired || changed.length === 0} onClick={() => send("PATCH")}>
                  {changed.length ? `Save ${changed.length} change${changed.length > 1 ? "s" : ""}` : "No changes"}
                </button>
                <button className="ghost" disabled={busy || changed.length === 0}
                        onClick={() => setDraft(Object.fromEntries(spec.fields.map((f) => [f.name, asText(current.record[f.name])])))}>
                  Undo changes
                </button>
                <button className="danger" disabled={busy || retired || cancelled} onClick={() => send("DELETE")}>
                  {retired ? "Already retired" : cancelled ? "Already cancelled" : spec.deleteLabel}
                </button>
              </div>
              <p className="muted small">{spec.deleteRule}</p>

              <h4>History of this record (core.event_audit_log)</h4>
              {current.audit.length === 0 ? <p className="muted small">No changes recorded yet.</p> : (
                <div className="table-wrap short">
                  <table>
                    <thead><tr><th>id</th><th>action</th><th>actor</th><th>recorded (KST)</th><th>change</th></tr></thead>
                    <tbody>
                      {current.audit.map((a) => {
                        const text = JSON.stringify(a.payload_snapshot);
                        return (
                          <tr key={a.audit_id}>
                            <td>{a.audit_id}</td><td>{a.action}</td><td>{a.actor}</td>
                            <td>{kstText(a.recorded_at)}</td><td title={text}>{text}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </section>

        <section className="card">
          <h3>Responses</h3>
          {sent.length === 0 && <p className="muted small">Change a value and press Save, or use the delete button.</p>}
          {sent.map((s) => {
            const o = outcome(s.result);
            return (
              <details key={s.id} className="response" open={s === sent[0]}>
                <summary>
                  <span className={`pill ${o.tone}`}>{o.label}</span> {s.label}{" "}
                  <span className="muted">· {s.at} · {s.result.elapsed_ms} ms</span>
                </summary>
                <pre>{s.request + (s.payload === undefined ? "" : `\n${JSON.stringify(s.payload, null, 2)}`)}
                  {"\n\n"}{JSON.stringify(s.result.body, null, 2)}</pre>
              </details>
            );
          })}
        </section>
      </div>
    </div>
  );
}
