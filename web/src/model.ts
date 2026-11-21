import type { Card, NetNode, NetShipment, Network, State } from "./types";

export type Risk = "red" | "amber" | "green";

export const COLOR = {
  red: "#ef4444",
  amber: "#f59e0b",
  green: "#22c55e",
  accent: "#5aa9ff",
  grey: "#94a3b8",
  dim: "#475569",
  light: "#e2e8f0",
};

export type ShipState = "late" | "stale" | "booked" | "ok" | "arrived";

export interface ShipView {
  s: NetShipment;
  lat: number;
  lng: number;
  frac: number;
  state: ShipState;
}

export interface Segment {
  key: string;
  routeId: string;
  mode: string;
  a: NetNode;
  b: NetNode;
}

export type Selection = { kind: "node" | "ship" | "route"; id: string } | null;

export interface Focus {
  nodes: Set<string>;
  ships: Set<string>;
  segs: Set<string>;
}

export interface Model {
  nodes: Map<string, NetNode>;
  segments: Segment[];
  ships: ShipView[];
  openCards: Card[];
  cardNodes: Map<string, string[]>;
  nodeRisk: Map<string, Risk>;
  segRisk: Map<string, Risk>;
}

const TERMINAL = /^(arrived|deliver|received|in_delivery|complete)/i;
const rad = (d: number) => (d * Math.PI) / 180;
export const pairKey = (a: string, b: string) => `${a}>${b}`;

function km(a: [number, number], b: [number, number]) {
  const dLat = rad(b[0] - a[0]);
  const dLon = rad(b[1] - a[1]);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a[0])) * Math.cos(rad(b[0])) * Math.sin(dLon / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(h));
}

/** Point at fraction f of the way along a polyline of [lat, lon] stops, by distance. */
export function along(coords: [number, number][], f: number): [number, number] {
  const lens = coords.slice(1).map((c, i) => km(coords[i], c));
  const total = lens.reduce((a, b) => a + b, 0);
  let left = Math.min(Math.max(f, 0), 1) * total;
  for (let i = 0; i < lens.length; i++) {
    if (left <= lens[i] || i === lens.length - 1) {
      const t = lens[i] ? Math.min(left / lens[i], 1) : 0;
      const [la, lo] = coords[i];
      const [lb, lp] = coords[i + 1];
      const dLon = ((lp - lo + 540) % 360) - 180;
      return [la + (lb - la) * t, lo + dLon * t];
    }
    left -= lens[i];
  }
  return coords[0];
}

function shipView(s: NetShipment, nodes: Map<string, NetNode>, now: number, staleH: number): ShipView | null {
  const coords = s.path.flatMap((id) => {
    const n = nodes.get(id);
    return n ? [[n.lat, n.lon] as [number, number]] : [];
  });
  if (coords.length < 2) return null;
  const eta = s.eta ? Date.parse(s.eta) : NaN;
  let dep = s.etd ? Date.parse(s.etd) : NaN;
  if (Number.isNaN(dep) && !Number.isNaN(eta) && s.transit_days) dep = eta - s.transit_days * 864e5;
  const span = eta - dep;
  const frac = span > 0 ? Math.min(Math.max((now - dep) / span, 0), 1) : 0;
  const [lat, lng] = along(coords, frac);
  const updated = s.updated_at ? Date.parse(s.updated_at) : NaN;
  let state: ShipState = "ok";
  if (TERMINAL.test(s.status)) state = "arrived";
  else if (!Number.isNaN(eta) && now > eta) state = "late";
  else if (!Number.isNaN(updated) && now - updated > staleH * 36e5) state = "stale";
  else if (now < dep) state = "booked";
  return { s, lat, lng, frac, state };
}

const worst = (a: Risk | undefined, b: Risk): Risk =>
  a === "red" || b === "red" ? "red" : a === "amber" || b === "amber" ? "amber" : "green";

export const cardRisk = (c: Card): Risk => (c.at_risk > 0 ? "red" : "amber");
export const isOpen = (c: Card) => !c.human_decision;

/** Nodes a card touches: suppliers of its part, plus any node whose id or name the card mentions. */
function linkNodes(c: Card, net: Network): string[] {
  const out = new Set<string>(net.part_suppliers[c.part]?.map((s) => s.supplier) ?? []);
  const text = [c.title, c.system_says, c.paper_says, c.part, JSON.stringify(c.evidence)].join(" ");
  const lower = text.toLowerCase();
  for (const n of net.nodes) {
    if (text.includes(n.id) || (n.kind !== "port" && n.name.length > 3 && lower.includes(n.name.toLowerCase()))) {
      out.add(n.id);
    }
  }
  return [...out];
}

export function buildModel(state: State, net: Network, now: number): Model {
  const nodes = new Map(net.nodes.map((n) => [n.id, n]));
  const openCards = state.cards.filter(isOpen);
  const cardNodes = new Map(openCards.map((c) => [c.id, linkNodes(c, net)]));

  const nodeRisk = new Map<string, Risk>();
  for (const c of openCards) {
    for (const id of cardNodes.get(c.id) ?? []) nodeRisk.set(id, worst(nodeRisk.get(id), cardRisk(c)));
  }

  const ships = net.shipments.flatMap((s) => {
    const v = shipView(s, nodes, now, net.stale_hours);
    return v ? [v] : [];
  });

  const segRisk = new Map<string, Risk>();
  for (const v of ships) {
    let r: Risk = "green";
    for (const c of openCards) if (v.s.parts.includes(c.part)) r = worst(r, cardRisk(c));
    if (v.state === "late") r = "red";
    else if (v.state === "stale") r = worst(r, "amber");
    if (r === "green") continue;
    for (let i = 1; i < v.s.path.length; i++) {
      const k = pairKey(v.s.path[i - 1], v.s.path[i]);
      segRisk.set(k, worst(segRisk.get(k), r));
    }
  }

  const segments: Segment[] = [];
  for (const r of net.routes) {
    for (let i = 1; i < r.path.length; i++) {
      const a = nodes.get(r.path[i - 1]);
      const b = nodes.get(r.path[i]);
      if (!a || !b) continue;
      const key = pairKey(a.id, b.id);
      if (r.mode === "feeder" && nodeRisk.has(a.id)) segRisk.set(key, nodeRisk.get(a.id)!);
      segments.push({ key, routeId: r.id, mode: r.mode, a, b });
    }
  }
  return { nodes, segments, ships, openCards, cardNodes, nodeRisk, segRisk };
}

/** Suppliers of a part, from the network mapping. */
export const suppliersOf = (net: Network, part: string) => net.part_suppliers[part]?.map((s) => s.supplier) ?? [];

/** What to light up on the globe for a part or a selection; null means show everything. */
export function computeFocus(m: Model, net: Network, part: string | null, sel: Selection): Focus | null {
  let ships: ShipView[] | null = null;
  const nodes = new Set<string>();
  const segs = new Set<string>();
  if (sel?.kind === "node") {
    nodes.add(sel.id);
    ships = m.ships.filter((v) => v.s.supplier === sel.id || v.s.path.includes(sel.id));
  } else if (sel?.kind === "ship") {
    ships = m.ships.filter((v) => v.s.id === sel.id);
  } else if (sel?.kind === "route") {
    segs.add(sel.id);
    ships = m.ships.filter((v) => v.s.path.some((_, i, p) => i > 0 && pairKey(p[i - 1], p[i]) === sel.id));
  } else if (part) {
    const sups = suppliersOf(net, part);
    sups.forEach((s) => nodes.add(s));
    ships = m.ships.filter((v) => v.s.parts.includes(part));
    for (const s of m.segments) if (s.mode === "feeder" && sups.includes(s.a.id)) segs.add(s.key);
  }
  if (!ships) return null;
  for (const v of ships) {
    v.s.path.forEach((id, i) => {
      nodes.add(id);
      if (i > 0) segs.add(pairKey(v.s.path[i - 1], id));
    });
    if (v.s.supplier) {
      nodes.add(v.s.supplier);
      for (const s of m.segments) if (s.mode === "feeder" && s.a.id === v.s.supplier && s.b.id === v.s.path[0]) segs.add(s.key);
    }
  }
  return { nodes, segs, ships: new Set(ships.map((v) => v.s.id)) };
}

/** Where to look when a card is picked: its supplier if it has one, else any linked node. */
export function cardTarget(m: Model, c: Card): NetNode | null {
  const ids = m.cardNodes.get(c.id) ?? [];
  const nodes = ids.map((id) => m.nodes.get(id)).filter((n): n is NetNode => !!n);
  return nodes.find((n) => n.kind === "supplier") ?? nodes[0] ?? null;
}

export function editionCover(state: State, e: State["editions"][number]) {
  const acted = state.cards
    .filter(isOpen)
    .filter((c) => c.edition === e.product || c.edition === e.name)
    .reduce((n, c) => n + Math.max(c.at_risk - c.at_risk_if_acted, 0), 0);
  const total = Math.max(e.remaining, 0);
  const pct = (units: number) => (total > 0 ? Math.round((Math.min(Math.max(units, 0), total) / total) * 100) : 100);
  return { now: pct(total - e.at_risk), ifActed: pct(total - Math.max(e.at_risk - acted, 0)) };
}

export function partRisk(p: { gap: number; runs_dry: string | null }): Risk {
  return p.gap > 0 ? "red" : p.runs_dry ? "amber" : "green";
}
