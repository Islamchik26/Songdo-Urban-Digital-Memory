// City map with an "as of T" time slider. Every frame is rebuilt by the database from history.
import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import LeafletMap, { esc, popup } from "./LeafletMap";
import { getFrame, getMapLayers, kstIso, kstText } from "./api";
import type { Frame, MapLayers } from "./api";

const FIXTURE_DATE = "2026-10-02";
const ROUTE_COLORS = ["#2563eb", "#0b7a5b", "#c2410c", "#7c3aed", "#be185d", "#0891b2"];
const VEHICLE_COLORS: Record<string, string> = { BUS: "#2563eb", EV_BUS: "#0b7a5b", AUTONOMOUS_SHUTTLE: "#c2410c" };
const SEVERITY_COLORS: Record<string, string> = { CRITICAL: "#7f1d1d", HIGH: "#dc2626", MEDIUM: "#f59e0b", LOW: "#eab308" };
const GRADE_COLORS: Record<string, string> = { NORMAL: "#16a34a", BAD: "#f97316", VERY_BAD: "#dc2626", NO_DATA: "#94a3b8" };
const SPEEDS = [1, 5, 15]; // simulated minutes per step

type LayerKey = "stops" | "routes" | "attractions" | "buildings" | "facilities" | "stations" | "vehicles" | "incidents";
const LAYER_LABELS: Record<LayerKey, string> = {
  vehicles: "Vehicles (as of T)",
  incidents: "Active incidents + restriction radius",
  stations: "Air stations, PM2.5 at T",
  routes: "Route stop patterns",
  stops: "Bus stops (513, official)",
  attractions: "Attractions",
  buildings: "Smart buildings",
  facilities: "Fire / 119 / police",
};

function hhmm(minute: number): string {
  return `${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
}

function vehicleIcon(type: string, heading: number | null, label: string, idle: boolean): L.DivIcon {
  const color = VEHICLE_COLORS[type] ?? "#475569";
  const rotate = heading ?? 0;
  const arrow = heading === null ? `<circle cx="12" cy="12" r="6" fill="${color}"/>` :
    `<path d="M12 2 L19 20 L12 16 L5 20 Z" fill="${color}" stroke="#fff" stroke-width="1.5"/>`;
  return L.divIcon({
    className: `veh${idle ? " idle" : ""}`,
    iconSize: [24, 24],
    iconAnchor: [12, 12],
    html: `<svg width="24" height="24" viewBox="0 0 24 24" style="transform:rotate(${rotate}deg)">${arrow}</svg>` +
      `<span class="veh-label">${esc(label)}</span>`,
  });
}

export default function MapView() {
  const mapRef = useRef<L.Map | null>(null);
  const groups = useRef<Partial<Record<LayerKey, L.LayerGroup>>>({});
  const [ready, setReady] = useState(false);
  const [layers, setLayers] = useState<MapLayers | null>(null);
  const [frame, setFrame] = useState<Frame | null>(null);
  const [error, setError] = useState("");
  // deep links: #/map?t=18:00&date=2026-10-02  or  #/map?live=1
  const query = new URLSearchParams(location.hash.split("?")[1]);
  const [mode, setMode] = useState<"replay" | "live">(query.get("live") === "1" ? "live" : "replay");
  const [date, setDate] = useState(query.get("date") ?? FIXTURE_DATE);
  const [minute, setMinute] = useState(() => {
    const m = /^(\d{1,2}):(\d{2})$/.exec(query.get("t") ?? "");
    return m ? Math.min(Number(m[1]) * 60 + Number(m[2]), 23 * 60 + 59) : 8 * 60 + 10;
  });
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [baseMap, setBaseMap] = useState(true);
  const [visible, setVisible] = useState<Record<LayerKey, boolean>>({
    vehicles: true, incidents: true, stations: true, routes: true,
    stops: false, attractions: true, buildings: true, facilities: true,
  });
  const requestId = useRef(0);

  // ---- static layers ------------------------------------------------------
  useEffect(() => {
    getMapLayers().then(setLayers).catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || !layers) return;
    const g = groups.current;
    const make = (key: LayerKey) => (g[key] = L.layerGroup());

    const routes = make("routes");
    const routeNumbers = [...new Set(layers.routes.map((r) => r.route_number))];
    for (const r of layers.routes) {
      const color = ROUTE_COLORS[routeNumbers.indexOf(r.route_number) % ROUTE_COLORS.length];
      L.polyline(r.path.map(([lon, lat]) => [lat, lon] as L.LatLngTuple), {
        color, weight: r.direction === 0 ? 4 : 3, opacity: 0.75, dashArray: r.direction === 1 ? "6 6" : undefined,
      })
        .bindPopup(popup(`Route ${r.route_number} · ${r.direction === 0 ? "outbound" : "inbound"}`, [
          ["Name", r.name], ["Type", r.route_type], ["Stops", r.stop_names.length],
          ["From", r.stop_names[0]], ["To", r.stop_names[r.stop_names.length - 1]],
          ["Note", "straight lines between stops, not the road path"],
        ]))
        .addTo(routes);
    }

    const stops = make("stops");
    for (const s of layers.stops) {
      L.circleMarker([s.lat, s.lon], { radius: 3, color: "#475569", weight: 1, fillOpacity: 0.7 })
        .bindPopup(popup(s.name, [["Stop number", s.stop_number], ["Stop ID", s.stop_id], ["Source", "MOLIT bus stop file"]]))
        .addTo(stops);
    }

    const attractions = make("attractions");
    for (const a of layers.attractions) {
      L.circleMarker([a.lat, a.lon], { radius: 7, color: "#7c3aed", weight: 2, fillColor: "#c4b5fd", fillOpacity: 0.9 })
        .bindPopup(popup(a.name, [["English", a.name_en], ["Category", a.category],
          ["Wheelchair", a.is_wheelchair_accessible ? "yes" : "no"], ["Parking", a.has_parking ? "yes" : "no"]]))
        .addTo(attractions);
    }

    const buildings = make("buildings");
    for (const b of layers.buildings) {
      L.marker([b.lat, b.lon], {
        icon: L.divIcon({ className: "sq bld", iconSize: [14, 14], html: "" }),
      })
        .bindPopup(popup(b.name, [["English", b.name_en], ["ID", b.building_id], ["Use", b.building_use],
          ["Floors", b.floors_above]]))
        .addTo(buildings);
    }

    const facilities = make("facilities");
    for (const f of layers.facilities) {
      const police = f.responder === "POLICE";
      L.marker([f.lat, f.lon], {
        icon: L.divIcon({ className: `sq fac ${police ? "police" : "fire"}`, iconSize: [16, 16], html: police ? "P" : "119" }),
      })
        .bindPopup(popup(f.name, [["English", f.name_en], ["Type", f.facility_type], ["Responder", f.responder],
          ["Location", "placeholder coordinate, to be verified"]]))
        .addTo(facilities);
    }

    make("stations");
    make("vehicles");
    make("incidents");
    return () => {
      Object.values(g).forEach((group) => group?.remove());
      groups.current = {};
    };
  }, [ready, layers]);

  // ---- layer visibility ---------------------------------------------------
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    (Object.keys(LAYER_LABELS) as LayerKey[]).forEach((key) => {
      const group = groups.current[key];
      if (!group) return;
      if (visible[key]) group.addTo(map);
      else group.remove();
    });
  }, [visible, ready, layers]);

  // ---- frames -------------------------------------------------------------
  const asOf = mode === "live" ? undefined : kstIso(date, minute);

  useEffect(() => {
    if (mode !== "replay") return;
    const id = ++requestId.current;
    const timer = setTimeout(() => {
      getFrame(asOf)
        .then((f) => {
          if (id === requestId.current) {
            setFrame(f);
            setError("");
          }
        })
        .catch((e: Error) => id === requestId.current && setError(e.message));
    }, playing ? 0 : 120); // small debounce while dragging the slider
    return () => clearTimeout(timer);
  }, [mode, asOf, playing]);

  useEffect(() => {
    if (mode !== "live") return;
    const load = () =>
      getFrame(undefined)
        .then((f) => {
          setFrame(f);
          setError("");
        })
        .catch((e: Error) => setError(e.message));
    load();
    const timer = setInterval(load, 2000);
    return () => clearInterval(timer);
  }, [mode]);

  // playback: advance only after the previous frame has arrived
  useEffect(() => {
    if (!playing || mode !== "replay" || !frame) return;
    const timer = setTimeout(() => {
      setMinute((m) => {
        const next = m + speed;
        if (next > 23 * 60 + 59) {
          setPlaying(false);
          return m;
        }
        return next;
      });
    }, 350);
    return () => clearTimeout(timer);
  }, [frame, playing, mode, speed]);

  // ---- dynamic layers -----------------------------------------------------
  useEffect(() => {
    const g = groups.current;
    if (!frame || !g.vehicles || !g.incidents || !g.stations || !layers) return;
    g.vehicles.clearLayers();
    g.incidents.clearLayers();
    g.stations.clearLayers();

    for (const v of frame.vehicles) {
      const idle = v.staleness_seconds > 60;
      L.marker([v.lat, v.lon], { icon: vehicleIcon(v.vehicle_type, v.heading_deg, v.route_number ?? v.vehicle_id, idle) })
        .bindPopup(popup(v.vehicle_id, [["Type", v.vehicle_type], ["Route", v.route_number], ["Trip", v.trip_id],
          ["Speed", `${v.speed_kmh} km/h`], ["Heading", v.heading_deg === null ? null : `${v.heading_deg}°`],
          ["Battery", v.battery_pct === null ? null : `${v.battery_pct}%`],
          ["Observed", kstText(v.observed_at)], ["Position age", `${v.staleness_seconds} s`]]))
        .addTo(g.vehicles);
    }

    for (const i of frame.incidents) {
      const color = SEVERITY_COLORS[i.severity] ?? "#dc2626";
      if (i.restriction_radius_m) {
        L.circle([i.lat, i.lon], { radius: i.restriction_radius_m, color, weight: 2, fillOpacity: 0.12, dashArray: "4 4" })
          .addTo(g.incidents);
      }
      L.marker([i.lat, i.lon], { icon: L.divIcon({ className: "inc", iconSize: [22, 22], html: "!" }) })
        .bindPopup(popup(i.title, [["ID", i.incident_id], ["Type", i.type_name], ["Severity", i.severity],
          ["Status at T", i.status], ["Since", kstText(i.status_since)], ["Reported", kstText(i.reported_at)],
          ["Assigned", i.assigned_facility_id], ["Restriction", i.restriction_radius_m ? `${i.restriction_radius_m} m` : null]]))
        .addTo(g.incidents);
    }

    const coords = new Map(layers.stations.map((s) => [s.station_id, s]));
    for (const a of frame.air) {
      const s = coords.get(a.station_id);
      if (!s) continue;
      const color = GRADE_COLORS[a.grade];
      L.marker([s.lat, s.lon], {
        icon: L.divIcon({
          className: "air",
          iconSize: [64, 22],
          iconAnchor: [32, 11],
          html: `<span style="background:${color}">${esc(a.name)} ${a.pm25 === null ? "–" : Math.round(a.pm25)}</span>`,
        }),
      })
        .bindPopup(popup(`${a.name} air station`, [["PM2.5", a.pm25 === null ? "no reading in last 3 h" : `${a.pm25} µg/m³`],
          ["Grade", a.grade], ["Reading time", kstText(a.observed_at)],
          ["Reading age", a.age_minutes === null ? null : `${a.age_minutes} min`], ["Source", "AirKorea / fixture"]]))
        .addTo(g.stations);
    }
  }, [frame, layers]);

  // ---- derived numbers ----------------------------------------------------
  const stats = useMemo(() => {
    if (!frame) return null;
    const moving = frame.vehicles.filter((v) => v.staleness_seconds <= 60);
    const avg = moving.length ? moving.reduce((s, v) => s + v.speed_kmh, 0) / moving.length : null;
    return { moving: moving.length, avg };
  }, [frame]);

  const range = layers?.time_range;

  return (
    <div className="map-page">
      <div className="map-wrap">
        <LeafletMap
          baseMap={baseMap}
          onReady={(map) => {
            mapRef.current = map;
            setReady(true);
          }}
        />
        <div className="map-clock">
          <span className="clock-mode">{mode === "live" ? "LIVE" : "REPLAY"}</span>
          {frame ? kstText(frame.as_of) : "…"} KST
          {frame && <span className="muted"> · frame built in {frame.elapsed_ms} ms</span>}
        </div>
      </div>

      <aside className="map-panel">
        <div className="seg">
          <button className={mode === "replay" ? "on" : ""} onClick={() => setMode("replay")}>Replay history</button>
          <button className={mode === "live" ? "on" : ""} onClick={() => { setMode("live"); setPlaying(false); }}>
            Live (now)
          </button>
        </div>

        {mode === "replay" ? (
          <div className="time-box">
            <div className="time-row">
              <input type="date" value={date} onChange={(e) => e.target.value && setDate(e.target.value)} />
              <strong className="big-time">{hhmm(minute)}</strong>
            </div>
            <input
              className="slider"
              type="range"
              min={0}
              max={23 * 60 + 59}
              value={minute}
              onChange={(e) => setMinute(Number(e.target.value))}
              aria-label="Time of day"
            />
            <div className="ticks"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span></div>
            <div className="time-row">
              <button className="run" onClick={() => setPlaying(!playing)}>{playing ? "Pause" : "Play"}</button>
              <label className="inline">
                step
                <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
                  {SPEEDS.map((s) => <option key={s} value={s}>{s} min</option>)}
                </select>
              </label>
            </div>
            <div className="presets">
              {[["Morning peak", 8 * 60 + 10], ["Collision 08:15", 8 * 60 + 15], ["Midday", 12 * 60 + 30],
                ["Fire 18:00", 18 * 60], ["Night", 22 * 60 + 30]].map(([label, m]) => (
                <button key={label} onClick={() => { setDate(FIXTURE_DATE); setMinute(m as number); }}>{label}</button>
              ))}
            </div>
            {range && (
              <p className="muted small">
                GPS history: {kstText(range.first_observation)} → {kstText(range.fixture_day_end)} (fixture day)
                {range.last_observation && range.last_observation > (range.fixture_day_end ?? "") &&
                  `, plus live packets up to ${kstText(range.last_observation)}`}
              </p>
            )}
          </div>
        ) : (
          <p className="muted small">
            Refreshes every 2 s. Start the telemetry node to see a shuttle move:
            <code>python -m consumers.smart_mobility_adapter --duration 60</code>
          </p>
        )}

        {error && <p className="error small">{error}</p>}

        {frame && stats && (
          <div className="kpis">
            <div><b>{stats.moving}</b><span>vehicles moving</span></div>
            <div><b>{stats.avg === null ? "–" : stats.avg.toFixed(1)}</b><span>avg km/h</span></div>
            <div><b>{frame.incidents.length}</b><span>active incidents</span></div>
            <div><b>{frame.alerts.length}</b><span>air alerts</span></div>
          </div>
        )}

        {frame && frame.incidents.length > 0 && (
          <section className="panel-list">
            <h3>Incidents active at T</h3>
            {frame.incidents.map((i) => (
              <button key={i.incident_id} className="list-item"
                onClick={() => mapRef.current?.flyTo([i.lat, i.lon], 16)}>
                <span className="dot" style={{ background: SEVERITY_COLORS[i.severity] }} />
                <span>{i.title}<small>{i.severity} · {i.status} since {kstText(i.status_since).slice(11, 16)}</small></span>
              </button>
            ))}
          </section>
        )}

        {frame && frame.alerts.length > 0 && (
          <section className="panel-list">
            <h3>Air alerts active at T</h3>
            {frame.alerts.map((a) => (
              <div key={a.alert_id} className="list-item static">
                <span className="dot" style={{ background: GRADE_COLORS[a.severity] ?? "#f97316" }} />
                <span>{a.station} {a.type_code}<small>{a.alert_message}</small></span>
              </div>
            ))}
          </section>
        )}

        <section className="panel-list">
          <h3>Layers</h3>
          {(Object.keys(LAYER_LABELS) as LayerKey[]).map((key) => (
            <label key={key} className="check-row">
              <input type="checkbox" checked={visible[key]}
                onChange={(e) => setVisible({ ...visible, [key]: e.target.checked })} />
              {LAYER_LABELS[key]}
            </label>
          ))}
          <label className="check-row">
            <input type="checkbox" checked={baseMap} onChange={(e) => setBaseMap(e.target.checked)} />
            OpenStreetMap background (needs internet)
          </label>
        </section>

        <section className="legend small">
          <span><i style={{ background: VEHICLE_COLORS.BUS }} />bus</span>
          <span><i style={{ background: VEHICLE_COLORS.EV_BUS }} />EV bus</span>
          <span><i style={{ background: VEHICLE_COLORS.AUTONOMOUS_SHUTTLE }} />autonomous shuttle</span>
          <span><i className="faded" />faded = no fix in last 60 s</span>
          <span><i style={{ background: GRADE_COLORS.NORMAL }} />PM2.5 normal</span>
          <span><i style={{ background: GRADE_COLORS.BAD }} />bad</span>
          <span><i style={{ background: GRADE_COLORS.VERY_BAD }} />very bad</span>
        </section>
        <p className="muted small">
          Real data: bus stops, air stations and AirKorea readings. Routes, vehicles, incidents, buildings and
          attractions content are generated fixtures.
        </p>
      </aside>
    </div>
  );
}
