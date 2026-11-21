import { useCallback, useEffect, useMemo, useState } from "react";
import { getFeed, getNetwork, getState, postAction, postMode } from "./api";
import Detail from "./Detail";
import GlobeView, { type FlyTo } from "./GlobeView";
import {
  buildModel,
  cardRisk,
  cardTarget,
  COLOR,
  computeFocus,
  editionCover,
  isOpen,
  type Selection,
} from "./model";
import Parts from "./Parts";
import type { ActionKind, Card, FeedEvent, Network, State } from "./types";

const POLL_MS = 5000;
const num = (n: number) => n.toLocaleString("en");

function useNow(offset: number) {
  const [t, setT] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setT(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  return t + offset;
}

function countdown(target: string | null | undefined, now: number): string | null {
  if (!target) return null;
  const ms = Date.parse(target) - now;
  if (Number.isNaN(ms)) return null;
  const abs = Math.abs(ms);
  const h = Math.floor(abs / 3_600_000);
  const m = Math.floor((abs % 3_600_000) / 60_000);
  const text = h > 0 ? `${h}h ${m}m` : `${m}m`;
  return ms < 0 ? `${text} overdue` : text;
}

function usePoll<T>(fn: () => Promise<T>, ms = POLL_MS) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState(false);
  const load = useCallback(
    () =>
      fn()
        .then((d) => {
          setData(d);
          setError(false);
        })
        .catch(() => setError(true)),
    [fn],
  );
  useEffect(() => {
    load();
    const id = setInterval(load, ms);
    return () => clearInterval(id);
  }, [load, ms]);
  return { data, setData, error };
}

const btn = "rounded-md border px-3 py-1.5 text-sm";

function CardRow({
  card,
  now,
  selected,
  onSelect,
  onAction,
}: {
  card: Card;
  now: number;
  selected: boolean;
  onSelect: () => void;
  onAction: (id: string, a: ActionKind) => void;
}) {
  const left = countdown(card.act_by, now);
  const risk = cardRisk(card) === "red" ? COLOR.red : COLOR.amber;
  return (
    <div
      onClick={onSelect}
      className="cursor-pointer rounded-lg border bg-slate-900 p-3"
      style={{ borderColor: selected ? COLOR.accent : "#1e293b", borderLeft: `3px solid ${risk}` }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="text-sm font-medium text-slate-100">{card.title}</div>
        <div className="shrink-0 text-right">
          <div className="font-semibold" style={{ color: risk }}>
            {num(card.at_risk)}
          </div>
          <div className="text-[11px] text-slate-400">units at risk</div>
        </div>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-400">
        {card.stage === "before_arrival" && (
          <span className="rounded bg-sky-400/15 px-1.5 py-0.5 font-semibold text-sky-300">EARLY WARNING</span>
        )}
        {card.action_status && (
          <span className="rounded bg-green-500/15 px-1.5 py-0.5 font-semibold text-green-400">
            {card.action_status.status === "replied" ? "REPLIED" : "SENT"}
          </span>
        )}
        <span>{card.edition}</span>
        {left && <span>Act within {left}</span>}
      </div>
      <div className="mt-3 flex flex-wrap gap-2" onClick={(e) => e.stopPropagation()}>
        <button
          className={`${btn} border-sky-400 bg-sky-500 text-slate-950`}
          onClick={() => onAction(card.id, "allow_once")}
        >
          Allow once
        </button>
        <button className={`${btn} border-slate-700`} onClick={() => onAction(card.id, "allow_always")}>
          Allow always
        </button>
        <button className={`${btn} border-slate-700`} onClick={onSelect}>
          Edit
        </button>
        <button className={`${btn} border-slate-700 text-slate-400`} onClick={() => onAction(card.id, "dismiss")}>
          Dismiss
        </button>
      </div>
    </div>
  );
}

export default function App() {
  const { data: state, setData: setState, error } = usePoll<State>(getState);
  const { data: net } = usePoll<Network>(getNetwork, 15000);
  const { data: feed } = usePoll<FeedEvent[]>(getFeed);
  const [selectedCard, setSelectedCard] = useState<string | null>(null);
  const [part, setPart] = useState<string | null>(null);
  const [sel, setSel] = useState<Selection>(null);
  const [flyTo, setFlyTo] = useState<FlyTo | null>(null);
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    if (state?.now) setOffset(Date.parse(state.now) - Date.now());
  }, [state?.now]);
  const now = useNow(offset);
  const modelNow = Math.floor(now / 15000) * 15000;

  const model = useMemo(() => (state && net ? buildModel(state, net, modelNow) : null), [state, net, modelNow]);
  const focus = useMemo(() => (model && net ? computeFocus(model, net, part, sel) : null), [model, net, part, sel]);

  const { open, handled } = useMemo(() => {
    const cards = state?.cards ?? [];
    const time = (c: Card) => (c.act_by ? Date.parse(c.act_by) : Infinity);
    const sorted = [...cards].sort((a, b) => time(a) - time(b) || b.at_risk - a.at_risk);
    return { open: sorted.filter(isOpen), handled: sorted.filter((c) => !isOpen(c)) };
  }, [state]);
  const shown = part ? open.filter((c) => c.part === part) : open;

  const fly = (lat: number, lng: number, alt = 1.4) => setFlyTo({ lat, lng, alt, k: Date.now() });

  const pickCard = (c: Card) => {
    setSelectedCard(c.id);
    setPart(c.part || null);
    setSel(null);
    const t = model && cardTarget(model, c);
    if (t) fly(t.lat, t.lon);
  };

  const pickSel = (s: Selection) => {
    setSel(s);
    if (!s || !model) return;
    if (s.kind === "node") {
      const n = model.nodes.get(s.id);
      if (n) fly(n.lat, n.lon);
    } else if (s.kind === "ship") {
      const v = model.ships.find((x) => x.s.id === s.id);
      if (v) fly(v.lat, v.lng);
    } else {
      const seg = model.segments.find((x) => x.key === s.id);
      if (seg) fly((seg.a.lat + seg.b.lat) / 2, (seg.a.lon + seg.b.lon) / 2, 2);
    }
  };

  const act = (id: string, action: ActionKind, note?: string) => {
    setState(
      (s) =>
        s && {
          ...s,
          cards: s.cards.map((c) =>
            c.id === id ? { ...c, human_decision: { action, note, ts: new Date().toISOString() } } : c,
          ),
        },
    );
    postAction(id, action, note).catch(() => undefined);
  };

  const setMode = (mode: State["mode"]) => {
    setState((s) => s && { ...s, mode });
    postMode(mode).catch(() => undefined);
  };

  if (!state) {
    return <div className="p-8 text-slate-400">{error ? "Cannot reach the API." : "Loading"}</div>;
  }

  const remaining = state.editions.reduce((n, e) => n + Math.max(e.remaining, 0), 0);
  const deadline =
    state.packing_ends ?? state.editions.map((e) => e.launch_date).filter(Boolean).sort()[0] ?? null;
  const left = countdown(deadline, now);
  const detailCard = state.cards.find((c) => c.id === selectedCard) ?? shown[0] ?? null;

  const side = sel && model ? sidePanel(sel) : null;
  function sidePanel(s: NonNullable<Selection>) {
    if (!model || !net) return null;
    const ids = new Set<string>();
    let title = s.id;
    let sub: string = s.kind;
    if (s.kind === "node") {
      const n = model.nodes.get(s.id);
      title = n?.name ?? s.id;
      sub = n?.kind ?? "node";
    } else if (s.kind === "ship") {
      sub = "shipment";
    } else {
      const seg = model.segments.find((x) => x.key === s.id);
      title = seg ? `${seg.a.name} to ${seg.b.name}` : s.id;
      sub = seg?.mode ?? "route";
    }
    const ships = model.ships.filter((v) => focus?.ships.has(v.s.id));
    ships.forEach((v) => v.s.parts.forEach((p) => ids.add(p)));
    if (s.kind === "node")
      for (const [p, sups] of Object.entries(net.part_suppliers)) if (sups.some((x) => x.supplier === s.id)) ids.add(p);
    const cards = open.filter((c) => ids.has(c.part) || model.cardNodes.get(c.id)?.includes(s.id));
    const desc = (p: string) => state!.parts.find((x) => x.part === p)?.description || p;
    return { title, sub, ships, cards, parts: [...ids].map((p) => ({ p, d: desc(p) })) };
  }

  return (
    <div className="mx-auto grid max-w-[1700px] gap-5 p-5 lg:grid-cols-[minmax(0,1fr)_380px]">
      <div className="min-w-0 space-y-5">
        <header className="space-y-3 rounded-lg border border-slate-800 bg-slate-900/70 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h1 className="text-lg font-semibold text-slate-100">
                Ship {num(remaining)} units{deadline ? ` by ${new Date(deadline).toLocaleString("en", { weekday: "short", hour: "2-digit", minute: "2-digit" })}` : ""}
                {left && <span className="ml-2 text-sky-300">· {left}</span>}
              </h1>
              <div className="text-xs text-slate-500">
                {error ? <span style={{ color: COLOR.red }}>Connection lost · </span> : null}
                data: {state.source}
                {net ? ` · network: ${net.source}` : ""}
              </div>
            </div>
            <div className="flex rounded-md border border-slate-700 bg-slate-950 p-0.5 text-sm">
              {(["ask", "autonomous"] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  className={`rounded px-3 py-1 ${state.mode === m ? "bg-sky-500 text-slate-950" : "text-slate-400"}`}
                >
                  {m === "ask" ? "Ask me" : "Autonomous"}
                </button>
              ))}
            </div>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {state.editions.map((e) => {
              const c = editionCover(state, e);
              const col = c.now < 100 ? COLOR.red : COLOR.green;
              return (
                <div key={e.product}>
                  <div className="mb-1 flex justify-between text-xs text-slate-300">
                    <span>{e.name}</span>
                    <span>
                      {c.now}% covered now
                      {c.ifActed > c.now ? ` → ${c.ifActed}% if you act` : ""}
                    </span>
                  </div>
                  <div className="relative h-2 rounded bg-slate-800">
                    <div className="absolute h-2 rounded opacity-40" style={{ width: `${c.ifActed}%`, background: COLOR.green }} />
                    <div className="absolute h-2 rounded" style={{ width: `${c.now}%`, background: col }} />
                  </div>
                </div>
              );
            })}
          </div>
        </header>

        <section className="relative h-[52vh] min-h-[380px] overflow-hidden rounded-lg border border-slate-800 bg-[radial-gradient(ellipse_at_center,#0b1730,#020617)]">
          {model && <GlobeView model={model} focus={focus} selection={sel} flyTo={flyTo} onSelect={pickSel} />}
          {!model && <div className="p-4 text-sm text-slate-500">Loading network</div>}
          <div className="pointer-events-none absolute bottom-3 left-3 space-y-1 rounded bg-slate-950/70 p-2 text-[11px] text-slate-300">
            <div><span style={{ color: COLOR.red }}>●</span> late or red card · <span style={{ color: COLOR.amber }}>●</span> warning · <span style={{ color: COLOR.green }}>●</span> fine</div>
            <div><span className="text-slate-400">●</span> grey with ? = no update for {net?.stale_hours ?? 24}h</div>
          </div>
          {(sel || part) && (
            <button
              onClick={() => {
                setSel(null);
                setPart(null);
              }}
              className="absolute left-3 top-3 rounded border border-slate-700 bg-slate-950/80 px-2 py-1 text-xs"
            >
              Clear focus
            </button>
          )}
          {side && (
            <aside className="absolute right-3 top-3 max-h-[90%] w-64 space-y-3 overflow-auto rounded-lg border border-slate-700 bg-slate-950/90 p-3 text-xs">
              <div>
                <div className="text-sm font-semibold text-slate-100">{side.title}</div>
                <div className="text-slate-400">{side.sub}</div>
              </div>
              <div>
                <div className="mb-1 uppercase tracking-wide text-slate-500">Cards ({side.cards.length})</div>
                {side.cards.map((c) => (
                  <button key={c.id} className="mb-1 block text-left text-sky-300" onClick={() => pickCard(c)}>
                    {c.title}
                  </button>
                ))}
              </div>
              <div>
                <div className="mb-1 uppercase tracking-wide text-slate-500">Shipments ({side.ships.length})</div>
                {side.ships.slice(0, 8).map((v) => (
                  <div key={v.s.id} className="flex justify-between">
                    <span>{v.s.id}</span>
                    <span className="text-slate-400">{v.state}</span>
                  </div>
                ))}
              </div>
              <div>
                <div className="mb-1 uppercase tracking-wide text-slate-500">Parts</div>
                {side.parts.map((x) => (
                  <button key={x.p} className="mr-2 text-slate-200 underline" onClick={() => setPart(x.p)}>
                    {x.d}
                  </button>
                ))}
              </div>
            </aside>
          )}
        </section>

        <Parts
          editions={state.editions}
          parts={state.parts}
          products={net?.products ?? []}
          selected={part}
          onSelect={(p) => {
            setPart(p);
            setSel(null);
            if (p && model && net) {
              const s = model.nodes.get(net.part_suppliers[p]?.[0]?.supplier ?? "");
              if (s) fly(s.lat, s.lon, 1.8);
            }
          }}
        />
      </div>

      <aside className="space-y-4">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
          Needs your decision{part ? ` · filtered` : ""}
        </h2>
        {shown.length === 0 && <div className="text-sm text-slate-500">Nothing needs a decision.</div>}
        {shown.map((c) => (
          <CardRow key={c.id} card={c} now={now} selected={detailCard?.id === c.id} onSelect={() => pickCard(c)} onAction={act} />
        ))}
        {handled.length > 0 && (
          <details className="rounded-lg border border-slate-800 bg-slate-900 p-3">
            <summary className="cursor-pointer text-sm text-slate-300">Handled automatically ({handled.length})</summary>
            <ul className="mt-2 space-y-1 text-sm">
              {handled.map((c) => (
                <li key={c.id} className="flex justify-between gap-3">
                  <span>{c.title}</span>
                  <span className="shrink-0 text-slate-400">{c.human_decision?.action.replace("_", " ")}</span>
                </li>
              ))}
            </ul>
          </details>
        )}
        {detailCard && <Detail key={detailCard.id} card={detailCard} onAction={act} />}

        <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Agents at work</h2>
        <ul className="space-y-2">
          {(feed ?? []).slice(0, 30).map((ev, i) => (
            <li key={i} className="rounded-lg border border-slate-800 bg-slate-900 p-2 text-sm">
              <div className="flex justify-between text-xs text-slate-400">
                <span className="font-medium text-slate-200">{ev.actor}</span>
                <span>{new Date(ev.at).toLocaleTimeString("en", { hour: "2-digit", minute: "2-digit" })}</span>
              </div>
              {ev.text}
            </li>
          ))}
          {feed?.length === 0 && <li className="text-sm text-slate-500">No activity yet.</li>}
        </ul>
      </aside>
    </div>
  );
}
