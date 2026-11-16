// Thin React wrapper around a Leaflet map. Pages draw into layer groups they own.
import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

export const SONGDO_CENTER: L.LatLngTuple = [37.3895, 126.6455];

interface Props {
  onReady: (map: L.Map) => void;
  className?: string;
  baseMap?: boolean;
  zoom?: number;
}

export default function LeafletMap({ onReady, className, baseMap = true, zoom = 14 }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const tilesRef = useRef<L.TileLayer | null>(null);

  useEffect(() => {
    if (!host.current) return;
    const map = L.map(host.current, { center: SONGDO_CENTER, zoom, zoomControl: true, preferCanvas: false });
    L.control.scale({ imperial: false }).addTo(map);
    tilesRef.current = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    });
    mapRef.current = map;
    onReady(map);
    // the container may get its final size after the first paint
    const resize = new ResizeObserver(() => map.invalidateSize());
    resize.observe(host.current);
    return () => {
      resize.disconnect();
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const tiles = tilesRef.current;
    if (!map || !tiles) return;
    if (baseMap) tiles.addTo(map);
    else tiles.remove();
  }, [baseMap]);

  return <div ref={host} className={className ?? "leaflet-host"} />;
}

/** HTML-escape text before it goes into a popup. */
export function esc(value: unknown): string {
  return String(value ?? "—").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string,
  );
}

/** Popup body: title plus key/value lines. */
export function popup(title: string, rows: [string, unknown][]): string {
  const body = rows
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`)
    .join("");
  return `<div class="pop"><strong>${esc(title)}</strong><table>${body}</table></div>`;
}
