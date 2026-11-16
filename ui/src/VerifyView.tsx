// Verification dashboard: the handbook's quality gates as a live checklist, plus the test suite on demand.
import { useEffect, useState } from "react";
import { getVerification, loadToken, postJson } from "./api";
import type { TestRun, Verification } from "./api";

export default function VerifyView() {
  const [data, setData] = useState<Verification | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [tests, setTests] = useState<TestRun | null>(null);
  const [testError, setTestError] = useState("");
  const [testing, setTesting] = useState(false);
  const [checkedAt, setCheckedAt] = useState("");

  function load() {
    setLoading(true);
    getVerification()
      .then((v) => {
        setData(v);
        setError("");
        setCheckedAt(new Date().toLocaleTimeString());
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }
  useEffect(load, []);

  async function runTests() {
    setTesting(true);
    setTestError("");
    const r = await postJson("/api/v1/system/run-tests", undefined, loadToken());
    if (r.ok) setTests(r.body as TestRun);
    else setTestError(`HTTP ${r.status}: ${JSON.stringify((r.body as { detail?: unknown })?.detail ?? r.body)}`);
    setTesting(false);
  }

  const groups = data ? [...new Set(data.checks.map((c) => c.component))] : [];

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2 className="title">Verification</h2>
          <p className="summary">
            Live checks against the running database: schemas, rows in every domain, cross-domain views, materialized
            view freshness, the append-only ledger, spatial indexes, constraints, quarantine, provenance and backup.
          </p>
        </div>
        <button className="ghost" onClick={load} disabled={loading}>{loading ? "Checking…" : "Re-check"}</button>
      </div>

      {error && <p className="error">{error}</p>}

      {data && (
        <>
          <div className="kpis wide-kpis">
            <div className="pass"><b>{data.summary.PASS}</b><span>pass</span></div>
            <div className="warn"><b>{data.summary.WARN}</b><span>warn</span></div>
            <div className="fail"><b>{data.summary.FAIL}</b><span>fail</span></div>
            <div><b>{checkedAt}</b><span>checked at</span></div>
          </div>
          <div className="table-wrap tall">
            <table>
              <thead><tr><th>Status</th><th>Component</th><th>Check</th><th>Detail</th></tr></thead>
              <tbody>
                {groups.flatMap((g) =>
                  data.checks.filter((c) => c.component === g).map((c, i) => (
                    <tr key={`${g}-${i}`}>
                      <td><span className={`pill ${c.status === "PASS" ? "ok" : c.status === "WARN" ? "warn" : "bad"}`}>{c.status}</span></td>
                      <td>{c.component}</td>
                      <td>{c.check}</td>
                      <td title={c.detail}>{c.detail}</td>
                    </tr>
                  )),
                )}
              </tbody>
            </table>
          </div>
        </>
      )}

      <section className="card test-card">
        <div className="activity-head">
          <h3>Automated test suite</h3>
          <span className="muted small">python -m unittest tests/test_platform.py · runs in a separate process, rolls back its writes</span>
          <button className="run" onClick={runTests} disabled={testing}>{testing ? "Running… (≈10 s)" : "Run tests"}</button>
        </div>
        {testError && <p className="error small">{testError}</p>}
        {tests && (
          <>
            <p>
              <span className={`pill ${tests.passed ? "ok" : "bad"}`}>{tests.passed ? "PASSED" : "FAILED"}</span>{" "}
              {tests.summary} <span className="muted">· {tests.elapsed_s} s</span>
            </p>
            <details open={!tests.passed}>
              <summary>Output (last lines)</summary>
              <pre>{tests.output_tail.join("\n")}</pre>
            </details>
          </>
        )}
      </section>
    </div>
  );
}
