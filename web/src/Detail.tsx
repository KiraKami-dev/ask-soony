import { useState } from "react";
import type { ActionKind, Card, Evidence, Option } from "./types";
import VoiceCall from "./VoiceCall";

function EvidenceItem({ e }: { e: Evidence }) {
  if (typeof e === "string") return <li className="text-slate-700">{e}</li>;
  return (
    <li className="space-y-0.5">
      <div className="text-xs text-slate-500">
        {[e.doc_path, e.page != null ? `p. ${e.page}` : null, e.language].filter(Boolean).join(" · ")}
      </div>
      {e.quote && <blockquote className="border-l-2 border-slate-300 pl-2">{e.quote}</blockquote>}
      {e.translation && <div className="pl-2 text-slate-500">Translation: {e.translation}</div>}
    </li>
  );
}

function OptionItem({ o }: { o: Option }) {
  if (typeof o === "string") return <li>{o}</li>;
  const meta = [o.cost && `cost: ${o.cost}`, o.effect && `effect: ${o.effect}`].filter(Boolean);
  return (
    <li>
      <span className={o.recommended ? "font-medium text-accent" : ""}>{o.action}</span>
      {o.recommended && <span className="ml-2 text-xs text-accent">recommended</span>}
      {meta.length > 0 && <div className="text-xs text-slate-500">{meta.join(" · ")}</div>}
    </li>
  );
}

const h = "mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500";

export default function Detail({
  card,
  onAction,
}: {
  card: Card;
  onAction: (id: string, action: ActionKind, note?: string) => void;
}) {
  const [note, setNote] = useState("");
  return (
    <section className="space-y-5 rounded-lg border border-slate-200 bg-white p-5">
      <h2 className="text-base font-semibold">{card.title}</h2>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-md bg-slate-100 p-3">
          <div className={h}>System says</div>
          <p>{card.system_says}</p>
        </div>
        <div className="rounded-md border border-danger/30 bg-danger/5 p-3">
          <div className={h}>Documents say</div>
          <p>{card.paper_says}</p>
        </div>
      </div>

      {card.evidence.length > 0 && (
        <div>
          <div className={h}>Evidence</div>
          <ul className="list-inside space-y-2 text-sm">
            {card.evidence.map((e, i) => (
              <EvidenceItem key={i} e={e} />
            ))}
          </ul>
        </div>
      )}

      {card.options.length > 0 && (
        <div>
          <div className={h}>Options</div>
          <ul className="list-inside list-disc space-y-1 text-sm">
            {card.options.map((o, i) => (
              <OptionItem key={i} o={o} />
            ))}
          </ul>
        </div>
      )}

      {card.messages.length > 0 && (
        <div>
          <div className={h}>Draft messages</div>
          <div className="space-y-3">
            {card.messages.map((m, i) => (
              <div key={i} className="rounded-md border border-slate-200 p-3 text-sm">
                <div className="text-xs text-slate-500">
                  To {m.to_role} ({m.to_email})
                </div>
                <div className="font-medium">{m.subject}</div>
                <p className="mt-1 whitespace-pre-wrap text-slate-700">{m.body}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      <VoiceCall key={card.id} card={card} onAction={onAction} />

      <div>
        <div className={h}>Edit before sending</div>
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Your changes or instructions"
          className="w-full rounded-md border border-slate-300 p-2 text-sm"
          rows={2}
        />
        <button
          disabled={!note.trim()}
          onClick={() => onAction(card.id, "edit", note.trim())}
          className="mt-2 rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-40"
        >
          Save edit
        </button>
      </div>
    </section>
  );
}
