import { useEffect, useMemo, useState } from "react";
import { getHealth, getRegistry, runScenario } from "./api";
import type { Health, Registry, RunResult, Scenario } from "./api";

function cell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function defaults(s: Scenario): Record<string, string> {
  return Object.fromEntries(s.params.map((p) => [p.name, p.default === null ? "" : String(p.default)]));
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [registry, setRegistry] = useState<Registry | null>(null);
  const [loadError, setLoadError] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [explain, setExplain] = useState(true);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<RunResult | null>(null);
  const [runError, setRunError] = useState("");

  useEffect(() => {
    getHealth().then(setHealth);
    getRegistry()
      .then((r) => {
        setRegistry(r);
        const first = r.scenarios.find((s) => s.status === "ready") ?? r.scenarios[0];
        if (first) select(first);
      })
      .catch((e: Error) => setLoadError(e.message));
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
  }

  async function run() {
    if (!selected) return;
    setRunning(true);
    setRunError("");
    try {
      setResult(await runScenario(selected.id, values, explain));
    } catch (e) {
      setResult(null);
      setRunError((e as Error).message);
    } finally {
      setRunning(false);
    }
  }

  const readyCount = registry?.scenarios.filter((s) => s.status === "ready").length ?? 0;
  const columns = result && result.rows.length > 0 ? Object.keys(result.rows[0]) : [];

  return (
    <div className="layout">
      <aside className="sidebar">
        <header>
          <h1>Songdo Urban Digital Memory</h1>
          <p className={`health ${health?.status ?? "pending"}`}>
            {health === null && "Checking database…"}
            {health?.status === "ok" && `PostgreSQL ${health.postgres} · PostGIS ${health.postgis}`}
            {health?.status === "down" && `Database unavailable: ${health.detail}`}
          </p>
          {registry && (
            <p className="muted">
              {readyCount} of {registry.scenarios.length} tasks ready
            </p>
          )}
        </header>

        {loadError && <p className="error">Could not load tasks: {loadError}</p>}

        <nav>
          {registry &&
            Object.entries(registry.domains).map(([key, label]) => (
              <section key={key}>
                <h2>{label}</h2>
                <ul>
                  {registry.scenarios
                    .filter((s) => s.domain === key)
                    .map((s) => (
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
            ))}
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
              <form
                className="params"
                onSubmit={(e) => {
                  e.preventDefault();
                  run();
                }}
              >
                {selected.params.map((p) => (
                  <label key={p.name}>
                    {p.label}
                    <input
                      value={values[p.name] ?? ""}
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
              </form>
            )}

            {runError && <p className="error">{runError}</p>}

            {result && (
              <>
                <p className="stats">
                  {result.row_count} row{result.row_count === 1 ? "" : "s"}
                  {result.truncated && " (truncated)"} · {result.elapsed_ms} ms
                </p>

                {result.rows.length === 0 ? (
                  <p className="muted">The query ran and returned no rows.</p>
                ) : (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          {columns.map((c) => (
                            <th key={c}>{c}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {result.rows.map((row, i) => (
                          <tr key={i}>
                            {columns.map((c) => (
                              <td key={c}>{cell(row[c])}</td>
                            ))}
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
                  <details open>
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
