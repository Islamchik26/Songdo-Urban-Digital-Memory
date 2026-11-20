// Scenario console: pick any of the registered tasks, fill its parameters, run it, see a headline,
// charts (api/visuals.py), a map when rows have coordinates, the rows, the SQL and the plan.
import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import LeafletMap, { popup } from "./LeafletMap";
import { getRegistry, runScenario } from "./api";
import { Charts, StoryPanel } from "./charts";
import type { Param, Registry, Row, RunResult, Scenario } from "./api";

function cell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function defaults(s: Scenario): Record<string, string> {
  return Object.fromEntries(s.params.map((p) => [p.name, p.default === null ? "" : String(p.default)]));
}

const LON_KEYS = ["lon", "longitude", "stop_lon", "incident_lon"];
const LAT_KEYS = ["lat", "latitude", "stop_lat", "incident_lat"];

/** Rows that carry a coordinate can also be shown on a map. */
function coordinateKeys(rows: Row[]): [string, string] | null {
  if (rows.length === 0) return null;
  const keys = Object.keys(rows[0]);
  const lon = LON_KEYS.find((k) => keys.includes(k));
  const lat = LAT_KEYS.find((k) => keys.includes(k));
  return lon && lat ? [lon, lat] : null;
}

type View = "chart" | "map" | "table";

/** The view a fresh result opens in: the chart when the task has one, else the map, else the table. */
function firstView(r: RunResult, wanted: View | null): View {
  const has = { chart: Boolean(r.viz?.length) && r.rows.length > 0, map: coordinateKeys(r.rows) !== null, table: true };
  if (wanted && has[wanted]) return wanted;
  return has.chart ? "chart" : has.map ? "map" : "table";
}

function placeholder(p: Param): string {
  if (p.type === "datetime") return "2026-10-02T08:00:00+09:00";
  if (p.type === "date") return "2026-10-02";
  if (p.type === "time") return "14:30";
  return p.default === null ? "(empty = all)" : "";
}

function ResultMap({ rows, keys }: { rows: Row[]; keys: [string, string] }) {
  const mapRef = useRef<L.Map | null>(null);
  const groupRef = useRef<L.FeatureGroup | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    groupRef.current?.remove();
    const group = L.featureGroup().addTo(map);
    const [lonKey, latKey] = keys;
    const labelKey = Object.keys(rows[0]).find((k) => /name|title/.test(k)) ?? Object.keys(rows[0])[0];
    for (const row of rows) {
      const lon = Number(row[lonKey]);
      const lat = Number(row[latKey]);
      if (!Number.isFinite(lon) || !Number.isFinite(lat)) continue;
      L.circleMarker([lat, lon], { radius: 7, color: "#ffffff", weight: 2, fillColor: "#ea580c", fillOpacity: 0.9 })
        .bindPopup(popup(cell(row[labelKey]), Object.entries(row).slice(0, 10).map(([k, v]) => [k, cell(v)])))
        .addTo(group);
    }
    groupRef.current = group;
    const bounds = group.getBounds();
    if (bounds.isValid()) map.fitBounds(bounds.pad(0.2), { maxZoom: 16 });
  }, [rows, keys, ready]);

  return (
    <LeafletMap className="leaflet-host result-map" onReady={(m) => { mapRef.current = m; setReady(true); }} />
  );
}

export default function TasksView() {
  const [registry, setRegistry] = useState<Registry | null>(null);
  const [loadError, setLoadError] = useState("");
  const [filter, setFilter] = useState("");
  const [selectedId, setSelectedId] = useState(() => new URLSearchParams(location.hash.split("?")[1]).get("task") ?? "");
  const [values, setValues] = useState<Record<string, string>>({});
  const [explain, setExplain] = useState(true);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<RunResult | null>(null);
  const [runError, setRunError] = useState("");
  const [view, setView] = useState<View>("table");

  useEffect(() => {
    getRegistry()
      .then((r) => {
        setRegistry(r);
        const wanted = r.scenarios.find((s) => s.id === selectedId);
        const first = wanted ?? r.scenarios.find((s) => s.status === "ready") ?? r.scenarios[0];
        if (!first) return;
        // deep link: #/tasks?task=X-4&run=1&view=map opens the task already run
        const query = new URLSearchParams(location.hash.split("?")[1]);
        select(first);
        if (wanted && query.get("run") === "1") run(first, defaults(first), (query.get("view") as View | null) ?? null);
      })
      .catch((e: Error) => setLoadError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selected = useMemo(
    () => registry?.scenarios.find((s) => s.id === selectedId) ?? null,
    [registry, selectedId],
  );

  function select(s: Scenario) {
    setSelectedId(s.id);
    setValues(defaults(s));
    setResult(null);
    setRunError("");
    setView("table");
    history.replaceState(null, "", `#/tasks?task=${s.id}`);
  }

  async function run(task = selected, params = values, wanted: View | null = null) {
    if (!task) return;
    setRunning(true);
    setRunError("");
    try {
      const r = await runScenario(task.id, params, explain);
      setResult(r);
      setView(firstView(r, wanted));
    } catch (e) {
      setResult(null);
      setRunError((e as Error).message);
    } finally {
      setRunning(false);
    }
  }

  const needle = filter.trim().toLowerCase();
  const matches = (s: Scenario) =>
    !needle || `${s.id} ${s.title} ${s.summary}`.toLowerCase().includes(needle);
  const readyCount = registry?.scenarios.filter((s) => s.status === "ready").length ?? 0;
  const columns = result && result.rows.length > 0 ? Object.keys(result.rows[0]) : [];
  const geo = result ? coordinateKeys(result.rows) : null;
  const hasChart = Boolean(result?.viz?.length);

  return (
    <div className="layout">
      <aside className="sidebar">
        <input
          className="search"
          placeholder="Search tasks (e.g. lateness, PM2.5, SLA)"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        {registry && (
          <p className="muted small pad">{readyCount} of {registry.scenarios.length} tasks ready</p>
        )}
        {loadError && <p className="error">Could not load tasks: {loadError}</p>}
        <nav>
          {registry &&
            Object.entries(registry.domains).map(([key, label]) => {
              const list = registry.scenarios.filter((s) => s.domain === key && matches(s));
              if (list.length === 0) return null;
              return (
                <section key={key}>
                  <h2>{label}</h2>
                  <ul>
                    {list.map((s) => (
                      <li key={s.id}>
                        <button
                          className={`task ${s.id === selectedId ? "active" : ""} ${s.status}`}
                          onClick={() => select(s)}
                        >
                          <span className="task-id">{s.id}</span>
                          <span className="task-title">{s.title}</span>
                          {s.status === "planned" && <span className="badge">planned</span>}
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              );
            })}
        </nav>
      </aside>

      <main>
        {!selected && <p className="muted">Select a task on the left.</p>}

        {selected && (
          <>
            <p className="eyebrow">{selected.id}</p>
            <h2 className="title">{selected.title}</h2>
            <p className="summary">{selected.summary}</p>

            {selected.status === "planned" ? (
              <p className="notice">This task is registered but its SQL is not implemented yet.</p>
            ) : (
              <form className="params" onSubmit={(e) => { e.preventDefault(); run(); }}>
                {selected.params.map((p) => (
                  <label key={p.name}>
                    {p.label}
                    <input
                      value={values[p.name] ?? ""}
                      placeholder={placeholder(p)}
                      className={p.type === "datetime" ? "wide" : ""}
                      inputMode={p.type === "float" || p.type === "int" ? "decimal" : "text"}
                      onChange={(e) => setValues({ ...values, [p.name]: e.target.value })}
                    />
                  </label>
                ))}
                <label className="check">
                  <input type="checkbox" checked={explain} onChange={(e) => setExplain(e.target.checked)} />
                  Show query plan
                </label>
                <button className="run" type="submit" disabled={running}>
                  {running ? "Running…" : "Run task"}
                </button>
                <button type="button" className="ghost" onClick={() => setValues(defaults(selected))}>
                  Reset
                </button>
              </form>
            )}

            {runError && <p className="error">{runError}</p>}

            {result && (
              <>
                <div className="result-bar">
                  <p className="stats">
                    {result.row_count} row{result.row_count === 1 ? "" : "s"}
                    {result.truncated && " (truncated)"} · {result.elapsed_ms} ms
                  </p>
                  {(hasChart || geo) && (
                    <div className="seg small-seg">
                      {hasChart && <button className={view === "chart" ? "on" : ""} onClick={() => setView("chart")}>Chart</button>}
                      {geo && <button className={view === "map" ? "on" : ""} onClick={() => setView("map")}>Map</button>}
                      <button className={view === "table" ? "on" : ""} onClick={() => setView("table")}>Table</button>
                    </div>
                  )}
                </div>

                {result.story && result.rows.length > 0 && <StoryPanel story={result.story} />}

                {result.rows.length === 0 ? (
                  <p className="muted">The query ran and returned no rows.</p>
                ) : view === "chart" && hasChart ? (
                  <Charts specs={result.viz!} rows={result.rows} params={result.params} />
                ) : view === "map" && geo ? (
                  <ResultMap rows={result.rows} keys={geo} />
                ) : (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr>
                      </thead>
                      <tbody>
                        {result.rows.map((row, i) => (
                          <tr key={i}>
                            {columns.map((c) => <td key={c} title={cell(row[c])}>{cell(row[c])}</td>)}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                <details open>
                  <summary>SQL that ran</summary>
                  <pre>{result.sql}</pre>
                </details>

                {result.explain && (
                  <details>
                    <summary>Query plan (EXPLAIN ANALYZE, BUFFERS)</summary>
                    <pre>{result.explain.join("\n")}</pre>
                  </details>
                )}
              </>
            )}
          </>
        )}
      </main>
    </div>
  );
}
