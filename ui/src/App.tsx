// Shell: header with database status and four pages. The URL hash keeps the page (#/map, #/ingest, ...).
import { useEffect, useState } from "react";
import { getHealth } from "./api";
import type { Health } from "./api";
import TasksView from "./TasksView";
import MapView from "./MapView";
import IngestView from "./IngestView";
import VerifyView from "./VerifyView";

const PAGES = [
  { id: "tasks", label: "Tasks", hint: "Run any of the 36 scenario tasks" },
  { id: "map", label: "City map", hint: "Replay the city at any moment" },
  { id: "ingest", label: "Ingest", hint: "Send records through the write path" },
  { id: "verify", label: "Verify", hint: "Quality gates and tests" },
] as const;
type PageId = (typeof PAGES)[number]["id"];

function pageFromHash(): PageId {
  const id = location.hash.replace(/^#\/?/, "").split("?")[0];
  return (PAGES.find((p) => p.id === id)?.id ?? "tasks") as PageId;
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [page, setPage] = useState<PageId>(pageFromHash);

  useEffect(() => {
    getHealth().then(setHealth);
    const onHash = () => setPage(pageFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden>◎</span>
          <div>
            <h1>Songdo Urban Digital Memory</h1>
            <p className={`health ${health?.status ?? "pending"}`}>
              {health === null && "Checking database…"}
              {health?.status === "ok" && `● PostgreSQL ${health.postgres} · PostGIS ${health.postgis}`}
              {health?.status === "down" && `● Database unavailable: ${health.detail}`}
            </p>
          </div>
        </div>
        <nav className="tabs">
          {PAGES.map((p) => (
            <a key={p.id} href={`#/${p.id}`} className={page === p.id ? "on" : ""} title={p.hint}>
              {p.label}
            </a>
          ))}
        </nav>
        <a className="docs-link" href="/docs" target="_blank" rel="noreferrer">API docs ↗</a>
      </header>
      <div className="content">
        {page === "tasks" && <TasksView />}
        {page === "map" && <MapView />}
        {page === "ingest" && <IngestView />}
        {page === "verify" && <VerifyView />}
      </div>
    </div>
  );
}
