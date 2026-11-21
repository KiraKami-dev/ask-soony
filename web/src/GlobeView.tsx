import { useEffect, useMemo, useRef, useState } from "react";
import Globe, { type GlobeMethods } from "react-globe.gl";
import { COLOR, pairKey, type Focus, type Model, type Risk, type Selection, type ShipView } from "./model";

/* react-globe.gl accessors receive untyped datum objects. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type D = any;

export interface FlyTo {
  lat: number;
  lng: number;
  alt?: number;
  k: number;
}

const riskColor = (r: Risk | undefined, neutral = COLOR.green) =>
  r === "red" ? COLOR.red : r === "amber" ? COLOR.amber : neutral;

function alpha(hex: string, a: number) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`;
}

const esc = (s: string) => s.replace(/[&<>"]/g, (c) => `&#${c.charCodeAt(0)};`);

const SHIP_COLOR: Record<ShipView["state"], string> = {
  late: COLOR.red,
  stale: COLOR.grey,
  booked: COLOR.dim,
  ok: COLOR.accent,
  arrived: COLOR.light,
};

export default function GlobeView({
  model,
  focus,
  selection,
  flyTo,
  onSelect,
}: {
  model: Model;
  focus: Focus | null;
  selection: Selection;
  flyTo: FlyTo | null;
  onSelect: (s: Selection) => void;
}) {
  const ref = useRef<GlobeMethods | undefined>(undefined);
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 800, h: 560 });
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Start looking at the first site (the destination), then fly where asked.
  useEffect(() => {
    if (!ready || !ref.current) return;
    const dc = [...model.nodes.values()].find((n) => n.kind === "site");
    if (dc) ref.current.pointOfView({ lat: dc.lat, lng: dc.lon, altitude: 1.9 }, 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);

  useEffect(() => {
    if (flyTo && ref.current) {
      ref.current.pointOfView({ lat: flyTo.lat, lng: flyTo.lng, altitude: flyTo.alt ?? 1.5 }, 1000);
    }
  }, [flyTo?.k]); // eslint-disable-line react-hooks/exhaustive-deps

  const lit = (set: Set<string> | undefined, id: string) => !focus || !!set?.has(id) || false;
  const selected = (kind: string, id: string) => selection?.kind === kind && selection.id === id;

  const points = useMemo(() => {
    const nodes = [...model.nodes.values()].map((n) => {
      const risk = model.nodeRisk.get(n.id);
      const base = n.kind === "port" && !risk ? COLOR.dim : riskColor(risk);
      const open = model.openCards.filter((c) => model.cardNodes.get(c.id)?.includes(n.id)).length;
      return {
        kind: "node",
        id: n.id,
        lat: n.lat,
        lng: n.lon,
        color: alpha(base, lit(focus?.nodes, n.id) ? 1 : 0.2),
        alt: n.kind === "site" ? 0.07 : n.kind === "supplier" ? 0.035 : 0.012,
        r: selected("node", n.id) ? 0.9 : n.kind === "site" ? 0.65 : n.kind === "supplier" ? 0.5 : 0.3,
        tip: `<b>${esc(n.name)}</b><br/>${n.kind}${open ? ` · ${open} open` : ""}`,
      };
    });
    const ships = model.ships.map((v) => ({
      kind: "ship",
      id: v.s.id,
      lat: v.lat,
      lng: v.lng,
      color: alpha(SHIP_COLOR[v.state], lit(focus?.ships, v.s.id) ? 1 : 0.15),
      alt: v.s.mode === "air" ? 0.1 : 0.02,
      r: selected("ship", v.s.id) ? 0.55 : 0.3,
      tip: `<b>${esc(v.s.id)}</b><br/>${esc(v.s.status || "?")} · ${v.state}`,
    }));
    return [...nodes, ...ships];
  }, [model, focus, selection]); // eslint-disable-line react-hooks/exhaustive-deps

  const arcs = useMemo(
    () =>
      model.segments.map((s) => {
        const risk = model.segRisk.get(s.key);
        const base = risk ? riskColor(risk) : s.mode === "air" ? COLOR.light : s.mode === "sea" ? COLOR.accent : COLOR.dim;
        const on = lit(focus?.segs, s.key) || selected("route", s.key);
        return {
          key: s.key,
          sLat: s.a.lat,
          sLng: s.a.lon,
          eLat: s.b.lat,
          eLng: s.b.lon,
          mode: s.mode,
          color: alpha(base, on ? (risk ? 0.95 : 0.7) : 0.08),
          tip: `${esc(s.a.name)} to ${esc(s.b.name)} · ${esc(s.mode)}`,
        };
      }),
    [model, focus, selection], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const rings = useMemo(
    () =>
      [...model.nodes.values()]
        .filter((n) => model.nodeRisk.get(n.id) === "red")
        .map((n) => ({ lat: n.lat, lng: n.lon })),
    [model],
  );

  const labels = useMemo(() => {
    const names = [...model.nodes.values()]
      .filter((n) => n.kind !== "port" || focus?.nodes.has(n.id))
      .slice(0, 30)
      .map((n) => ({
        lat: n.lat,
        lng: n.lon,
        text: n.name,
        size: n.kind === "site" ? 1.1 : 0.8,
        color: alpha(COLOR.light, lit(focus?.nodes, n.id) ? 0.9 : 0.15),
        alt: 0.09,
      }));
    const stale = model.ships
      .filter((v) => v.state === "stale")
      .slice(0, 20)
      .map((v) => ({ lat: v.lat, lng: v.lng, text: "?", size: 1.6, color: COLOR.light, alt: 0.05 }));
    return [...names, ...stale];
  }, [model, focus]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div ref={box} className="absolute inset-0">
      <Globe
        ref={ref}
        width={size.w}
        height={size.h}
        globeImageUrl="/earth-dark.jpg"
        backgroundColor="rgba(0,0,0,0)"
        atmosphereColor={COLOR.accent}
        atmosphereAltitude={0.15}
        onGlobeReady={() => setReady(true)}
        pointsData={points}
        pointLat={(d: D) => d.lat}
        pointLng={(d: D) => d.lng}
        pointColor={(d: D) => d.color}
        pointAltitude={(d: D) => d.alt}
        pointRadius={(d: D) => d.r}
        pointLabel={(d: D) => d.tip}
        pointsMerge={false}
        onPointClick={(d: D) => onSelect({ kind: d.kind, id: d.id })}
        arcsData={arcs}
        arcStartLat={(d: D) => d.sLat}
        arcStartLng={(d: D) => d.sLng}
        arcEndLat={(d: D) => d.eLat}
        arcEndLng={(d: D) => d.eLng}
        arcColor={(d: D) => d.color}
        arcStroke={(d: D) => (d.mode === "sea" ? 0.45 : d.mode === "air" ? 0.3 : 0.25)}
        arcAltitude={(d: D) => (d.mode === "road" || d.mode === "feeder" ? 0.012 : d.mode === "air" ? 0.22 : null)}
        arcAltitudeAutoScale={0.35}
        arcDashLength={(d: D) => (d.mode === "air" ? 0.12 : d.mode === "sea" ? 0.5 : 0.2)}
        arcDashGap={(d: D) => (d.mode === "air" ? 0.25 : d.mode === "sea" ? 0.2 : 0.3)}
        arcDashAnimateTime={(d: D) => (d.mode === "air" ? 2200 : d.mode === "sea" ? 6000 : 3500)}
        arcLabel={(d: D) => d.tip}
        onArcClick={(d: D) => onSelect({ kind: "route", id: d.key })}
        ringsData={rings}
        ringColor={() => (t: number) => `rgba(239,68,68,${1 - t})`}
        ringMaxRadius={3.2}
        ringPropagationSpeed={1.4}
        ringRepeatPeriod={1600}
        labelsData={labels}
        labelLat={(d: D) => d.lat}
        labelLng={(d: D) => d.lng}
        labelText={(d: D) => d.text}
        labelSize={(d: D) => d.size}
        labelColor={(d: D) => d.color}
        labelAltitude={(d: D) => d.alt}
        labelDotRadius={0}
        labelResolution={2}
      />
    </div>
  );
}

export { pairKey };
