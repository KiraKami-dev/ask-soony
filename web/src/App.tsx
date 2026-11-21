import { useCallback, useEffect, useMemo, useState } from "react";
import { getFeed, getState, postAction, postMode } from "./api";
import Detail from "./Detail";
import type { ActionKind, Card, FeedEvent, State } from "./types";

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

function usePoll<T>(fn: () => Promise<T>) {
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
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [load]);
  return { data, setData, error, reload: load };
}

function Dot({ risk }: { risk: boolean }) {
  return <span className={`inline-block h-2.5 w-2.5 rounded-full ${risk ? "bg-danger" : "bg-ok"}`} />;
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
  return (
    <div
      onClick={onSelect}
      className={`cursor-pointer rounded-lg border bg-white p-4 ${selected ? "border-accent" : "border-slate-200"}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="font-medium">{card.title}</div>
        <div className="shrink-0 text-right">
          <div className="font-semibold text-danger">{num(card.at_risk)}</div>
          <div className="text-xs text-slate-500">units at risk</div>
        </div>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
        {card.stage === "before_arrival" && (
          <span className="rounded bg-accent/10 px-1.5 py-0.5 font-semibold text-accent">EARLY WARNING</span>
        )}
        {card.action_status && (
          <span className="rounded bg-ok/10 px-1.5 py-0.5 font-semibold text-ok">
            {card.action_status.status === "replied" ? "REPLIED" : "SENT"}
          </span>
        )}
        <span>{card.edition}</span>
        {left && <span>Act within {left}</span>}
      </div>
      <div className="mt-3 flex flex-wrap gap-2" onClick={(e) => e.stopPropagation()}>
        <button className={`${btn} border-accent bg-accent text-white`} onClick={() => onAction(card.id, "allow_once")}>
          Allow once
        </button>
        <button className={`${btn} border-slate-300`} onClick={() => onAction(card.id, "allow_always")}>
          Allow always
        </button>
        <button className={`${btn} border-slate-300`} onClick={onSelect}>
          Edit
        </button>
        <button className={`${btn} border-slate-300 text-slate-500`} onClick={() => onAction(card.id, "dismiss")}>
          Dismiss
        </button>
      </div>
    </div>
  );
}

export default function App() {
  const { data: state, setData: setState, error } = usePoll<State>(getState);
  const { data: feed } = usePoll<FeedEvent[]>(getFeed);
  const [selected, setSelected] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    if (state?.now) setOffset(Date.parse(state.now) - Date.now());
  }, [state?.now]);
  const now = useNow(offset);

  const { open, handled } = useMemo(() => {
    const cards = state?.cards ?? [];
    const time = (c: Card) => (c.act_by ? Date.parse(c.act_by) : Infinity);
    const sorted = [...cards].sort((a, b) => time(a) - time(b) || b.at_risk - a.at_risk);
    const done = (c: Card) => !!c.human_decision;
    return { open: sorted.filter((c) => !done(c)), handled: sorted.filter(done) };
  }, [state]);

  const act = (id: string, action: ActionKind, note?: string) => {
    setState((s) =>
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
    return <div className="p-8 text-slate-500">{error ? "Cannot reach the API." : "Loading"}</div>;
  }

  const atRisk = state.editions.reduce((n, e) => n + e.at_risk, 0);
  const deadline =
    state.packing_ends ?? state.editions.map((e) => e.launch_date).filter(Boolean).sort()[0] ?? null;
  const left = countdown(deadline, now);
  const title = state.editions.map((e) => e.name).join(" / ");
  const selectedCard = state.cards.find((c) => c.id === selected) ?? open[0] ?? null;

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-6">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">{title || "Decision inbox"}</h1>
          {left && <div className="text-sm text-slate-500">Time left to pack: {left}</div>}
        </div>
        <div className="flex items-center gap-3">
          {error && <span className="text-xs text-danger">Connection lost</span>}
          <span className="text-xs text-slate-400">source: {state.source}</span>
          <div className="flex rounded-md border border-slate-300 bg-white p-0.5 text-sm">
            {(["ask", "autonomous"] as const).map((m) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className={`rounded px-3 py-1 ${state.mode === m ? "bg-accent text-white" : "text-slate-600"}`}
              >
                {m === "ask" ? "Ask me" : "Autonomous"}
              </button>
            ))}
          </div>
        </div>
      </header>

      <p className="rounded-lg border border-slate-200 bg-white p-4 text-lg">
        <span className="text-slate-500">System says everything is on track. </span>
        <span className={atRisk > 0 ? "font-semibold text-danger" : "font-semibold text-ok"}>
          {atRisk > 0 ? `Documents say ${num(atRisk)} units at risk.` : "Documents agree."}
        </span>
      </p>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {state.editions.map((e) => (
          <div key={e.product} className="flex items-center justify-between rounded-lg border border-slate-200 bg-white p-4">
            <div className="flex items-center gap-2">
              <Dot risk={e.at_risk > 0} />
              <div>
                <div className="font-medium">{e.name}</div>
                <div className="text-xs text-slate-500">{e.at_risk > 0 ? "At risk" : "On track"}</div>
              </div>
            </div>
            <div className="text-right text-sm">
              <div>{num(e.remaining)} remaining</div>
              <div className={e.at_risk > 0 ? "text-danger" : "text-slate-500"}>{num(e.at_risk)} at risk</div>
            </div>
          </div>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="space-y-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Needs your decision</h2>
          {open.length === 0 && <div className="text-slate-500">Nothing needs a decision.</div>}
          {open.map((c) => (
            <CardRow
              key={c.id}
              card={c}
              now={now}
              selected={selectedCard?.id === c.id}
              onSelect={() => setSelected(c.id)}
              onAction={act}
            />
          ))}

          {handled.length > 0 && (
            <details className="rounded-lg border border-slate-200 bg-white p-4">
              <summary className="cursor-pointer text-sm font-semibold text-slate-600">
                Handled automatically ({handled.length})
              </summary>
              <ul className="mt-3 space-y-1 text-sm">
                {handled.map((c) => (
                  <li key={c.id} className="flex justify-between gap-3">
                    <span>{c.title}</span>
                    <span className="shrink-0 text-slate-500">{c.human_decision?.action.replace("_", " ")}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}

          {selectedCard && <Detail key={selectedCard.id} card={selectedCard} onAction={act} />}
        </div>

        <aside className="space-y-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Agents at work</h2>
          <ul className="space-y-2">
            {(feed ?? []).map((ev, i) => (
              <li key={i} className="rounded-lg border border-slate-200 bg-white p-3 text-sm">
                <div className="flex justify-between text-xs text-slate-500">
                  <span className="font-medium text-slate-700">{ev.actor}</span>
                  <span>{new Date(ev.at).toLocaleTimeString("en", { hour: "2-digit", minute: "2-digit" })}</span>
                </div>
                {ev.text}
              </li>
            ))}
            {feed?.length === 0 && <li className="text-sm text-slate-500">No activity yet.</li>}
          </ul>
        </aside>
      </div>
    </div>
  );
}
