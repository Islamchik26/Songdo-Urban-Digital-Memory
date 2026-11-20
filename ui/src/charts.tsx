// Charts for task results, drawn from the declarative specs in api/visuals.py.
// Plain SVG/HTML, themed through CSS variables (styles.css, "Charts"). Every value a chart shows on
// hover is also in the table view; identity never rests on colour alone (status = colour + icon + label).
import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { ChartSpec, Measure, Row, Story } from "./api";

type Role = "good" | "warning" | "serious" | "critical" | "neutral";
const ICON: Record<Role, string> = { good: "✓", warning: "!", serious: "▲", critical: "✕", neutral: "•" };

// ---------------------------------------------------------------------------- helpers
const n = (v: unknown): number | null => (v === null || v === undefined || v === "" ? null : Number(v));

function fmt(v: number | null, unit = ""): string {
  if (v === null || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  const s = Number.isInteger(v) ? v.toLocaleString()
    : a >= 1000 ? Math.round(v).toLocaleString() : a >= 100 ? v.toFixed(0) : a >= 1 ? v.toFixed(1)
    : v.toPrecision(2);
  const u = v === 1 && /^[a-z]{3,}s$/i.test(unit) ? unit.slice(0, -1) : unit;
  return !u ? s : u === "%" ? `${s}%` : `${s} ${u}`;
}

function hhmm(v: unknown): string {
  const d = new Date(String(v));
  if (Number.isNaN(d.getTime())) return String(v ?? "");
  return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Seoul" });
}

function day(v: unknown): string {
  const d = new Date(String(v));
  if (Number.isNaN(d.getTime())) return String(v ?? "");
  return d.toLocaleDateString("en-GB", { month: "short", day: "numeric", timeZone: "Asia/Seoul" });
}

/** Round axis ticks: 0, 5, 10 ... covering [lo, hi]. */
function ticks(lo: number, hi: number, count = 5): number[] {
  if (hi === lo) hi = lo + 1;
  const raw = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let t = Math.floor(lo / step) * step; t <= hi + step * 0.001; t += step) out.push(Number(t.toPrecision(12)));
  return out;
}

function value(row: Row, spec: { column: string; divide?: number }): number | null {
  const v = n(row[spec.column]);
  return v === null ? null : spec.divide ? v / spec.divide : v;
}

/** First row per key: repeated rows (e.g. one per device with the same outdoor value) drawn once. */
function distinct(rows: Row[], keys: string[]): Row[] {
  const seen = new Set<string>();
  return rows.filter((r) => { const k = keys.map((c) => String(r[c])).join("|"); return seen.has(k) ? false : (seen.add(k), true); });
}

function roleOf(spec: ChartSpec["status"], row: Row): Role {
  if (!spec) return "neutral";
  return (spec.map[String(row[spec.column])] as Role) ?? "neutral";
}

function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T | null>(null);
  const [w, setW] = useState(640);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, Math.floor(e.contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

// ---------------------------------------------------------------------------- tooltip
interface Tip { x: number; y: number; title: string; lines: [string, string, string?][] }

function Tooltip({ tip }: { tip: Tip | null }) {
  if (!tip) return null;
  return (
    <div className="chart-tip" style={{ left: tip.x, top: tip.y }} role="status">
      <div className="chart-tip-title">{tip.title}</div>
      {tip.lines.map(([label, val, key], i) => (
        <div key={i} className="chart-tip-row">
          {key && <span className={`tip-key ${key}`} aria-hidden />}
          <b>{val}</b><span>{label}</span>
        </div>
      ))}
    </div>
  );
}

function StatusLegend({ status }: { status: NonNullable<ChartSpec["status"]> }) {
  return (
    <div className="chart-legend">
      {Object.entries(status.labels ?? {}).map(([k, label]) => {
        const role = (status.map[k] as Role) ?? "neutral";
        return (
          <span key={k}><i className={`swatch st-${role}`} aria-hidden /><span className="st-icon">{ICON[role]}</span>{label}</span>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------- line / columns
function LineChart({ spec, rows }: { spec: ChartSpec; rows: Row[] }) {
  const [box, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const isTime = spec.x!.format === "time";
  const isColumns = spec.type === "columns";
  const unit = spec.y!.unit ?? (spec.y!.unit_column ? String(rows[0]?.[spec.y!.unit_column] ?? "") : "");
  const labels = rows.map((r) => (isTime ? hhmm(r[spec.x!.column]) : String(r[spec.x!.column] ?? "")));
  const ys = rows.map((r) => value(r, spec.y!));
  const band = spec.band;
  const thresholds = (spec.thresholds ?? [])
    .map((t) => ({ ...t, v: n(rows[0]?.[t.column]) }))
    .filter((t): t is typeof t & { v: number } => t.v !== null);

  const all = [...ys, ...(band ? rows.flatMap((r) => [n(r[band.min]), n(r[band.max])]) : []), ...thresholds.map((t) => t.v)]
    .filter((v): v is number => v !== null);
  const lo = Math.min(0, ...all);
  const hi = Math.max(...all, lo + 1);
  const yt = ticks(lo, hi * 1.15);
  const y0 = yt[0], y1 = yt[yt.length - 1];

  const longLabels = !isTime && labels.some((l) => l.length > 6);
  const m = { l: 52, r: thresholds.length ? 78 : 16, t: 14, b: longLabels ? 96 : 34 };
  const H = 260;
  const iw = Math.max(10, width - m.l - m.r), ih = H - m.t - m.b;
  const slot = iw / Math.max(1, rows.length);
  const X = (i: number) => m.l + slot * (i + 0.5);
  const Y = (v: number) => m.t + ih - ((v - y0) / (y1 - y0)) * ih;
  const every = isTime ? Math.ceil(rows.length / 8) : Math.ceil(rows.length / Math.max(1, Math.floor(iw / 22)));

  const path = ys.map((v, i) => (v === null ? "" : `${i === 0 || ys[i - 1] === null ? "M" : "L"}${X(i)},${Y(v)}`)).join(" ");
  const bandPath = band
    ? rows.map((r, i) => `${i ? "L" : "M"}${X(i)},${Y(n(r[band.max]) ?? 0)}`).join(" ") + " " +
      rows.map((_, j) => { const i = rows.length - 1 - j; return `L${X(i)},${Y(n(rows[i][band.min]) ?? 0)}`; }).join(" ") + " Z"
    : "";
  const markerRole = (r: Row): Role | null => {
    const mk = spec.markers;
    if (!mk) return null;
    const v = r[mk.column];
    return v ? ((mk.map[String(v)] as Role) ?? "neutral") : null;
  };
  const maxIdx = ys.reduce<number>((best, v, i) => (v !== null && (best < 0 || v > (ys[best] ?? -Infinity)) ? i : best), -1);

  function onMove(e: React.PointerEvent<SVGRectElement>) {
    const rect = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
    const i = Math.round((e.clientX - rect.left - m.l) / slot - 0.5);
    setHover(Math.min(rows.length - 1, Math.max(0, i)));
  }

  const tip: Tip | null = hover === null ? null : {
    x: Math.min(X(hover) + 12, width - 210), y: 8,
    title: labels[hover],
    lines: [
      [spec.y!.label ?? "", fmt(ys[hover], unit), isColumns ? "bar" : "line"],
      ...(band ? [[band.label ?? "Range", `${fmt(n(rows[hover][band.min]))} – ${fmt(n(rows[hover][band.max]), unit)}`, "band"] as [string, string, string]] : []),
      ...(markerRole(rows[hover]) ? [["Alert", String(rows[hover][spec.markers!.column]), `st-${markerRole(rows[hover])}`] as [string, string, string]] : []),
    ],
  };

  return (
    <div className="chart-box" ref={box}>
      <svg width={width} height={H} role="img" aria-label={spec.title}>
        {yt.map((t) => (
          <g key={t}>
            <line className={t === 0 && (spec.zero_line || isColumns) ? "axis" : "grid"} x1={m.l} x2={m.l + iw} y1={Y(t)} y2={Y(t)} />
            <text className="tick" x={m.l - 8} y={Y(t)} dy="0.32em" textAnchor="end">{fmt(t)}</text>
          </g>
        ))}
        <text className="axis-label" x={12} y={m.t + ih / 2} transform={`rotate(-90 12 ${m.t + ih / 2})`} textAnchor="middle">
          {spec.y!.label}{unit ? ` (${unit})` : ""}
        </text>
        {labels.map((l, i) => (i % every === 0 ? (
          longLabels
            ? <text key={i} className="tick" transform={`translate(${X(i)},${m.t + ih + 10}) rotate(-40)`} textAnchor="end">{l.length > 14 ? l.slice(0, 13) + "…" : l}</text>
            : <text key={i} className="tick" x={X(i)} y={m.t + ih + 18} textAnchor="middle">{l}</text>
        ) : null))}
        {band && <path className="band" d={bandPath} />}
        {thresholds.map((t) => (
          <g key={t.column}>
            <line className={`threshold st-line-${t.role ?? "neutral"}`} x1={m.l} x2={m.l + iw} y1={Y(t.v)} y2={Y(t.v)} />
            <text className="tick strong" x={m.l + iw + 6} y={Y(t.v)} dy="0.32em">{t.label} {fmt(t.v)}</text>
          </g>
        ))}
        {isColumns
          ? ys.map((v, i) => {
              if (v === null) return null;
              const bw = Math.min(24, slot * 0.6), top = Y(Math.max(v, 0)), base = Y(0);
              const h = Math.abs(base - top), r = Math.min(4, h);
              const x = X(i) - bw / 2;
              return (
                <path key={i} className={`col ${hover === i ? "on" : ""}`}
                      d={`M${x},${base} V${top + r} Q${x},${top} ${x + r},${top} H${x + bw - r} Q${x + bw},${top} ${x + bw},${top + r} V${base} Z`} />
              );
            })
          : <path className="series-line" d={path} />}
        {!isColumns && ys.map((v, i) => {
          const role = markerRole(rows[i]);
          if (v === null || (!role && hover !== i && i !== maxIdx)) return null;
          return <circle key={i} className={`dot ${role ? `st-${role}` : ""}`} cx={X(i)} cy={Y(v)} r={role ? 5 : 4} />;
        })}
        {maxIdx >= 0 && ys[maxIdx] !== null && (
          <text className="value-label" x={X(maxIdx)} y={Y(ys[maxIdx]!) - 10} textAnchor="middle">{fmt(ys[maxIdx], unit)}</text>
        )}
        {hover !== null && <line className="crosshair" x1={X(hover)} x2={X(hover)} y1={m.t} y2={m.t + ih} />}
        <rect x={m.l} y={m.t} width={iw} height={ih} fill="transparent"
              onPointerMove={onMove} onPointerLeave={() => setHover(null)} />
      </svg>
      <Tooltip tip={tip} />
      {(band || spec.markers || thresholds.length > 0) && (
        <div className="chart-legend">
          <span><i className="line-key" aria-hidden />{spec.y!.label}</span>
          {band && <span><i className="swatch band-key" aria-hidden />{band.label}</span>}
          {spec.markers && <span><i className="dot-key st-critical" aria-hidden />{spec.markers.label}</span>}
          {thresholds.map((t) => <span key={t.column}><i className={`line-key st-line-${t.role}`} aria-hidden />{t.label} limit</span>)}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------- horizontal bars
function Bars({ spec, rows, params }: { spec: ChartSpec; rows: Row[]; params: Record<string, unknown> }) {
  const [tip, setTip] = useState<Tip | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const base = spec.distinct ? distinct(rows, spec.distinct as string[]) : rows;
  const shown = spec.limit ? base.slice(0, spec.limit) : base;
  const v = spec.value as Measure;
  const unit = v.unit ?? "";
  const ref = spec.reference;
  const refOf = (r: Row): number | null => {
    if (!ref) return null;
    const raw = ref.param !== undefined ? n(params[ref.param]) : ref.value !== undefined ? ref.value : n(r[ref.column!]);
    return raw === null ? null : v.divide && ref.param === undefined && ref.value === undefined ? raw / v.divide : raw;
  };
  const markOf = (r: Row) => (spec.marker ? value(r, { column: spec.marker.column, divide: v.divide }) : null);
  const max = v.max ?? Math.max(...shown.flatMap((r) => [value(r, v), refOf(r), markOf(r)]).filter((x): x is number => x !== null), 1) * 1.08;
  const pct = (x: number) => `${Math.max(0, Math.min(100, (x / max) * 100))}%`;
  const label = (r: Row) => (spec.label as string[])
    .map((c) => (spec.time_columns?.includes(c) ? hhmm(r[c]) : String(r[c] ?? ""))).filter(Boolean);

  function show(e: React.PointerEvent | React.FocusEvent, r: Row) {
    const host = box.current!.getBoundingClientRect();
    const el = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const role = roleOf(spec.status, r);
    setTip({
      x: Math.min(el.left - host.left + 40, host.width - 220), y: el.bottom - host.top + 4,
      title: label(r).join(" · "),
      lines: [
        [v.label ?? "", value(r, v) === null ? (spec.null_label ?? "—") : fmt(value(r, v), unit), `st-${role}`],
        ...(ref && refOf(r) !== null ? [[ref.label, fmt(refOf(r), unit), "ref"] as [string, string, string]] : []),
        ...(spec.marker && markOf(r) !== null ? [[spec.marker.label, fmt(markOf(r), unit), "mark"] as [string, string, string]] : []),
        ...(spec.status ? [["Status", spec.status.labels?.[String(r[spec.status.column])] ?? String(r[spec.status.column])] as [string, string]] : []),
      ],
    });
  }

  return (
    <div className="chart-box" ref={box}>
      {spec.limit && base.length > spec.limit && (
        <p className="muted small">Showing the first {spec.limit} of {base.length}; every row is in the table.</p>
      )}
      <div className="hbars">
        {shown.map((r, i) => {
          const x = value(r, v), rf = refOf(r), mk = markOf(r);
          const role = roleOf(spec.status, r);
          const [main, ...rest] = label(r);
          return (
            <div key={i} className="hbar-row" tabIndex={0}
                 onPointerEnter={(e) => show(e, r)} onPointerLeave={() => setTip(null)}
                 onFocus={(e) => show(e, r)} onBlur={() => setTip(null)}>
              <div className="hbar-label" title={label(r).join(" · ")}>
                <span>{main}</span>{rest.length > 0 && <small>{rest.join(" · ")}</small>}
              </div>
              <div className="hbar-track">
                {x === null
                  ? <span className="hbar-null">{spec.null_label ?? "no value"}</span>
                  : <div className={`hbar-fill ${spec.status ? `st-${role}` : "series-1"}`} style={{ width: pct(Math.max(0, x)) }} />}
                {rf !== null && <i className="hbar-ref" style={{ left: pct(rf) }} aria-hidden />}
                {mk !== null && <i className="hbar-mark" style={{ left: pct(mk) }} aria-hidden />}
              </div>
              <div className="hbar-value">
                {spec.status && <span className="st-icon" aria-hidden>{ICON[role]}</span>}
                {x === null ? "—" : fmt(x, unit)}
              </div>
            </div>
          );
        })}
      </div>
      <Tooltip tip={tip} />
      <div className="chart-legend">
        {spec.status && <StatusLegend status={spec.status} />}
        {ref && <span><i className="ref-key" aria-hidden />{ref.label}{ref.param !== undefined ? `: ${fmt(n(params[ref.param]), unit)}` : ""}</span>}
        {spec.marker && <span><i className="mark-key" aria-hidden />{spec.marker.label}</span>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------- paired bars
function PairedBars({ spec, rows }: { spec: ChartSpec; rows: Row[] }) {
  const series = spec.series ?? [spec.a!, spec.b!];
  const max = Math.max(...rows.flatMap((r) => series.map((s) => n(r[s.column]))).filter((x): x is number => x !== null), 1) * 1.05;
  const w = (x: number | null) => `${x === null ? 0 : Math.max(0, (x / max) * 100)}%`;
  return (
    <div className="chart-box">
      <div className="pairs">
        {rows.map((r, i) => {
          const role = roleOf(spec.status, r);
          const status = spec.status ? (spec.status.labels?.[String(r[spec.status.column])] ?? String(r[spec.status.column])) : "";
          return (
            <div key={i} className="pair">
              <div className="pair-head">
                <span className="pair-title">{String(r[spec.label as string] ?? "")}</span>
                {spec.sub && <small className="muted">{String(r[spec.sub] ?? "")}</small>}
                {spec.status && <span className={`status-badge st-${role}`}><span aria-hidden>{ICON[role]}</span> {status}</span>}
                {spec.note_column && <small className="muted">{spec.note_label} {fmt(n(r[spec.note_column]), spec.note_unit ?? "")}</small>}
              </div>
              {series.map((s, k) => (
                <div key={s.column} className="pair-row">
                  <span className="pair-label">{s.label}</span>
                  <div className="hbar-track"><div className={`hbar-fill series-${k + 1}`} style={{ width: w(n(r[s.column])) }} /></div>
                  <span className="hbar-value">{fmt(n(r[s.column]), spec.unit)}</span>
                </div>
              ))}
            </div>
          );
        })}
      </div>
      <div className="chart-legend">
        {series.map((s, k) => <span key={s.column}><i className={`swatch series-${k + 1}`} aria-hidden />{s.label}</span>)}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------- timeline
function Timeline({ spec, rows, params }: { spec: ChartSpec; rows: Row[]; params: Record<string, unknown> }) {
  const clock = spec.mode === "clock", dates = spec.mode === "date";
  const DAY = 86_400_000;
  // clock: minutes after midnight; date: whole days, end inclusive; default: timestamps (open end = as-of time)
  const at = (v: unknown): number => {
    if (clock) { const [h, m] = String(v ?? "0:0").split(":").map(Number); return h * 60 + (m || 0); }
    return new Date(String(v)).getTime();
  };
  const startOf = (r: Row) => at(r[spec.start!]);
  const endOf = (r: Row) => {
    if (clock) return at(r[spec.end!]);
    if (dates) return at(r[spec.end!]) + DAY;
    return at(r[spec.end!] ?? (spec.open_end_param ? params[spec.open_end_param] : null) ?? new Date().toISOString());
  };
  const t0 = clock ? 0 : Math.min(...rows.map(startOf));
  const t1 = clock ? 1440 : Math.max(...rows.map(endOf));
  const span = Math.max(1, t1 - t0);
  const pct = (t: number) => ((t - t0) / span) * 100;
  const axis = [0, 0.25, 0.5, 0.75, 1].map((f) => {
    const t = t0 + f * span;
    return clock ? `${String(Math.floor(t / 60)).padStart(2, "0")}:00` : day(new Date(t).toISOString());
  });
  const marker = clock && spec.marker_param && params[spec.marker_param] ? at(params[spec.marker_param]) : null;
  const dur = spec.duration as { column: string; unit: string } | undefined;
  const note = spec.note ?? (clock ? "Each bar is the opening time on that weekday; the vertical line is the time you asked about."
    : dates ? "Each bar runs from the first to the last day of the event."
    : `Each bar is one stay, from the day the device was installed in the room to the day it was moved out.${spec.open_end_param ? " An open stay is drawn up to the time you asked about." : ""}`);
  return (
    <div className="chart-box">
      <div className="timeline">
        {rows.map((r, i) => {
          const left = pct(startOf(r)), right = pct(endOf(r));
          const current = spec.current ? Boolean(r[spec.current]) : false;
          const role = spec.status ? roleOf(spec.status, r) : null;
          return (
            <div key={i} className="tl-row">
              <div className="hbar-label">
                <span>{String(r[spec.label as string] ?? "")}</span>
                {spec.sub && <small>{spec.sub_prefix ?? ""}{String(r[spec.sub] ?? "")}</small>}
              </div>
              <div className="tl-track">
                <div className={`tl-bar ${current || role || clock || dates ? "current" : ""} ${role ? `st-${role}` : ""}`}
                     style={{ left: `${left}%`, width: `${Math.max(0.8, right - left)}%` }}
                     title={clock ? `${String(r[spec.start!])} – ${String(r[spec.end!])}`
                       : `${day(r[spec.start!])} → ${r[spec.end!] ? day(r[spec.end!]) : "still there"}`} />
                {marker !== null && <i className="tl-marker" style={{ left: `${pct(marker)}%` }} aria-hidden />}
              </div>
              <div className="hbar-value">
                {role && <span className="st-icon" aria-hidden>{ICON[role]}</span>}
                {dur ? fmt(n(r[dur.column]), dur.unit) : ""}
                {current && <span className="now-badge">current</span>}
              </div>
            </div>
          );
        })}
        <div className="tl-row tl-axis">
          <div />
          <div className="tl-track axis-only">
            {axis.map((t, i) => <span key={i} style={{ left: `${i * 25}%` }}>{t}</span>)}
          </div>
          <div />
        </div>
      </div>
      {(spec.status || marker !== null) && (
        <div className="chart-legend">
          {spec.status && <StatusLegend status={spec.status} />}
          {marker !== null && <span><i className="ref-key" aria-hidden />{spec.marker_label ?? "Time asked"}: {String(params[spec.marker_param!]).slice(0, 5)}</span>}
        </div>
      )}
      <p className="muted small">{note}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------- stepper
function Stepper({ spec, rows }: { spec: ChartSpec; rows: Row[] }) {
  const mins = (s: unknown) => { const v = n(s); return v === null ? "" : v < 60 ? `${v} s` : `${Math.round(v / 60)} min`; };
  const gapText = (r: Row) => (spec.gap ? fmt(n(r[spec.gap.column]), spec.gap.unit) : mins(r[spec.duration as string]));
  const note = spec.note ?? "Numbers between the steps are the time the incident spent in the previous state.";
  return (
    <div className="chart-box">
      <div className={spec.scroll ? "stepper-scroll" : ""}>
      <ol className="stepper" style={spec.scroll ? { minWidth: `${rows.length * 112}px`, flexWrap: "nowrap" } : undefined}>
        {rows.length > 1 && (
          <span className="stepper-track" aria-hidden
                style={{ left: `${50 / rows.length}%`, right: `${50 / rows.length}%` }} />
        )}
        {rows.map((r, i) => (
          <li key={i} className={i === rows.length - 1 ? "last" : ""}>
            {i > 0 && <span className="step-gap">{gapText(r)}</span>}
            <span className="step-dot" aria-hidden>{i + 1}</span>
            <span className="step-name">{String(r[spec.state!] ?? "")}</span>
            {spec.time && <span className="step-time">{hhmm(r[spec.time])}</span>}
            {spec.detail && r[spec.detail] ? <small>{String(r[spec.detail])}</small> : null}
          </li>
        ))}
      </ol>
      </div>
      <p className="muted small">{note}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------- tree
function Tree({ spec, rows }: { spec: ChartSpec; rows: Row[] }) {
  return (
    <div className="chart-box">
      <ul className="tree">
        {rows.map((r, i) => {
          const parts = String(r[spec.path!] ?? "").split(" > ");
          const depth = parts.length;
          const count = spec.count ? n(r[spec.count.column]) ?? 0 : 0;
          return (
            <li key={i} className={`depth-${Math.min(depth, 4)}`} style={{ paddingLeft: `${(depth - 1) * 22}px` }}>
              <span className="tree-name">{depth > 1 && <span className="tree-elbow" aria-hidden>└</span>}{parts[parts.length - 1]}</span>
              {spec.kind && <span className="tree-kind">{String(r[spec.kind] ?? "").replace("_", " ").toLowerCase()}</span>}
              {spec.count && depth > 1 && (
                <span className={`count-pill ${count ? "" : "zero"}`}>
                  {count} {count === 1 ? spec.count.label.replace(/s$/, "") : spec.count.label}
                </span>
              )}
              {spec.detail && r[spec.detail] ? <small className="tree-detail">{String(r[spec.detail])}</small> : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------- meters
const PRETTY: Record<string, string> = { PM25: "PM2.5", O3: "O₃", NO2: "NO₂", SO2: "SO₂" };

function Meters({ spec, rows }: { spec: ChartSpec; rows: Row[] }) {
  return (
    <div className="chart-box">
      <div className="meters">
        {rows.map((r, i) => {
          const val = n(r[spec.value as string]);
          const unit = spec.unit_column ? String(r[spec.unit_column] ?? "") : "";
          const th = (spec.thresholds ?? []).map((t) => ({ label: t.label, v: n(r[t.column]) }))
            .filter((t): t is { label: string; v: number } => t.v !== null);
          const top = Math.max(...th.map((t) => t.v * 1.3), (val ?? 0) * 1.1, 1e-9);
          const role = roleOf(spec.status, r);
          const status = spec.status ? (spec.status.labels?.[String(r[spec.status.column])] ?? "") : "";
          const label = String(r[spec.label as string] ?? "");
          return (
            <div key={i} className="meter">
              <div className="meter-head">
                <span className="meter-name">{PRETTY[label] ?? label}</span>
                <span className={`status-badge st-${role}`}><span aria-hidden>{ICON[role]}</span> {status}</span>
              </div>
              <div className="meter-value">{fmt(val)} <small>{unit}</small></div>
              <div className="meter-track" role="img" aria-label={`${label} ${fmt(val, unit)}, ${status}`}>
                <div className={`meter-fill st-${role}`} style={{ width: `${Math.min(100, ((val ?? 0) / top) * 100)}%` }} />
                {th.map((t) => <i key={t.label} className="meter-tick" style={{ left: `${(t.v / top) * 100}%` }} title={`${t.label} above ${t.v}`} />)}
              </div>
              <div className="meter-scale">
                {th.map((t) => `${t.label} above ${fmt(t.v)}`).join(" · ")}
              </div>
              {spec.age && <small className="muted">{fmt(n(r[spec.age.column]))} {spec.age.unit}</small>}
            </div>
          );
        })}
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------- counts
/** Rows grouped by one column and counted (or distinct values of another column counted). */
function Counts({ spec, rows }: { spec: ChartSpec; rows: Row[] }) {
  const groups = new Map<string, Set<string> | number>();
  for (const r of rows) {
    const key = String(r[spec.group!] ?? "—");
    if (spec.distinct) {
      const set = (groups.get(key) as Set<string> | undefined) ?? new Set<string>();
      set.add(String(r[spec.distinct as string]));
      groups.set(key, set);
    } else {
      groups.set(key, ((groups.get(key) as number | undefined) ?? 0) + 1);
    }
  }
  const items = [...groups.entries()]
    .map(([key, v]) => ({ key, count: typeof v === "number" ? v : v.size }))
    .sort((a, b) => b.count - a.count || a.key.localeCompare(b.key));
  const max = Math.max(...items.map((i) => i.count), 1) * 1.08;
  const unit = spec.unit ?? "";
  return (
    <div className="chart-box">
      <div className="hbars">
        {items.map((it) => {
          const role = spec.status ? ((spec.status.map[it.key] as Role) ?? "neutral") : null;
          const label = spec.labels?.[it.key] ?? spec.status?.labels?.[it.key] ?? it.key;
          return (
            <div key={it.key} className="hbar-row" title={`${label}: ${it.count} ${unit}`}>
              <div className="hbar-label"><span>{label}</span>{label !== it.key && <small>{it.key}</small>}</div>
              <div className="hbar-track">
                <div className={`hbar-fill ${role ? `st-${role}` : "series-1"}`} style={{ width: `${(it.count / max) * 100}%` }} />
              </div>
              <div className="hbar-value">
                {role && <span className="st-icon" aria-hidden>{ICON[role]}</span>}
                {it.count.toLocaleString()} <small className="muted">{it.count === 1 ? unit.replace(/s$/, "") : unit}</small>
              </div>
            </div>
          );
        })}
      </div>
      {spec.status && <div className="chart-legend"><StatusLegend status={spec.status} /></div>}
    </div>
  );
}

// ---------------------------------------------------------------------------- cards
function noteText(note: NonNullable<ChartSpec["notes"]>[number], v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (note.format === "bool") return v ? "Yes" : "No";
  if (note.format === "date") return day(v);
  if (note.format === "time") return hhmm(v);
  if (note.format === "datetime") return `${day(v)} ${hhmm(v)}`;
  if (note.labels?.[String(v)]) return note.labels[String(v)];
  if (typeof v === "number") return fmt(v, note.unit ?? "");
  return note.unit ? `${String(v)} ${note.unit}` : String(v);
}

/** 45 -> "45 min", 300 -> "5 h", 6336 -> "4.4 days". */
function sinceText(minutesAgo: number): string {
  if (minutesAgo < 60) return `${Math.round(minutesAgo)} min`;
  if (minutesAgo < 48 * 60) return `${Math.round(minutesAgo / 60)} h`;
  return `${(minutesAgo / 1440).toFixed(1)} days`;
}

function Cards({ spec, rows }: { spec: ChartSpec; rows: Row[] }) {
  const v = spec.value as Measure;
  const shown = rows.slice(0, spec.limit ?? 60);
  const levelOf = (r: Row): Role | null => {
    const lv = spec.level;
    if (!lv) return null;
    const x = n(r[lv.column]);
    if (x === null) return "neutral";
    return x < lv.good_below ? "good" : x < lv.warning_below ? "warning" : "serious";
  };
  return (
    <div className="chart-box">
      {rows.length > shown.length && <p className="muted small">Showing {shown.length} of {rows.length}; every row is in the table.</p>}
      <div className="cards">
        {shown.map((r, i) => {
          const role = spec.status ? roleOf(spec.status, r) : levelOf(r);
          const badge = spec.status ? (spec.status.labels?.[String(r[spec.status.column])] ?? String(r[spec.status.column] ?? ""))
            : spec.level && role ? spec.level.labels[role as "good" | "warning" | "serious"] ?? "" : "";
          const rawTitle = String(r[spec.card_title!] ?? "");
          const sub = (spec.subtitle ?? []).map((c) => String(r[c] ?? "")).filter(Boolean)
            .map((s) => s.replace(/_/g, " ")).join(" · ");
          const val = value(r, v);
          const unit = v.unit ?? (v.unit_column ? String(r[v.unit_column] ?? "") : "");
          const rawLabel = v.label_column ? String(r[v.label_column] ?? "") : "";
          const vlabel = v.label ?? (v.label_map?.[rawLabel] ?? rawLabel.replace(/_/g, " ").toLowerCase());
          const shownValue = v.format === "minutes" && val !== null ? sinceText(val) : fmt(val);
          const shownUnit = v.format === "minutes" ? "" : unit;
          return (
            <div key={i} className="card-item">
              <div className="card-head">
                <span className="card-title" title={rawTitle}>{spec.pretty_title ? PRETTY[rawTitle] ?? rawTitle : rawTitle}</span>
                {role && badge && <span className={`status-badge st-${role}`}><span aria-hidden>{ICON[role]}</span> {badge}</span>}
              </div>
              {sub && <div className="card-sub">{sub}</div>}
              <div className="card-value">{shownValue} <small>{shownUnit}</small></div>
              {vlabel && <div className="card-value-label">{vlabel}</div>}
              {spec.notes && (
                <dl className="card-notes">
                  {spec.notes.map((note) => (
                    <div key={note.column}><dt>{note.label}</dt><dd>{noteText(note, r[note.column])}</dd></div>
                  ))}
                </dl>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------- public pieces
export function StoryPanel({ story }: { story: Story }) {
  return (
    <section className="story" aria-label="Summary">
      <p className="story-headline">{story.headline}</p>
      {story.stats.length > 0 && (
        <div className="story-stats">
          {story.stats.map((s) => (
            <div key={s.label} className="stat-tile"><span className="stat-label">{s.label}</span><span className="stat-value">{s.value}</span></div>
          ))}
        </div>
      )}
    </section>
  );
}

export function Charts({ specs, rows, params }: { specs: ChartSpec[]; rows: Row[]; params: Record<string, unknown> }) {
  const body = useMemo(() => specs.map((spec, i): ReactNode => {
    const props = { spec, rows, params };
    const chart =
      spec.type === "line" || spec.type === "columns" ? <LineChart {...props} />
      : spec.type === "bars" ? <Bars {...props} />
      : spec.type === "paired_bars" ? <PairedBars {...props} />
      : spec.type === "timeline" ? <Timeline {...props} />
      : spec.type === "stepper" ? <Stepper {...props} />
      : spec.type === "tree" ? <Tree {...props} />
      : spec.type === "meters" ? <Meters {...props} />
      : spec.type === "counts" ? <Counts {...props} />
      : spec.type === "cards" ? <Cards {...props} />
      : <p className="muted">No drawing for chart type "{spec.type}".</p>;
    return (
      <figure key={i} className="chart-card">
        <figcaption>{spec.title}</figcaption>
        {chart}
      </figure>
    );
  }), [specs, rows, params]);
  return <div className="charts">{body}</div>;
}
