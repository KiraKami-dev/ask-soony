import { CheckCircle2, Mic, Sparkles, Workflow, X } from 'lucide-react'
import { DecisionCard } from '@/components/DecisionCard'
import { Button } from '@/components/ui/button'
import { DONE_WORD, ago, fmtDay, fmtTime, sentence, tidy, viaOf, type ActivityItem, type Card, type State } from '@/lib/api'
import type { MapSelection } from '@/components/SupplyMap'

interface Props {
  state: State
  nowMs: number
  selection: MapSelection
  onClear: () => void
  fresh: Set<string>
  /** A card the person just decided: offer to handle the next one like it on its own. */
  decided: Card | null
  onAutomate: (card: Card) => void
  onSkipAutomate: () => void
  onChanged: () => void
  onHover: (routes: string[]) => void
  onDrawer: (card: Card, tab: 'evidence' | 'trail') => void
}

export const HEADING = 'text-xs font-semibold uppercase tracking-wide text-ink-3'

export function Rail(p: Props) {
  const { state, selection } = p
  const match = (c: Card) =>
    selection.routeId ? c.routes.includes(selection.routeId) : selection.nodeId ? c.supplier_id === selection.nodeId : true
  const mine = state.cards.filter((c) => c.status === 'open' && !c.routed_to_other && match(c))
  const withOther = state.cards.filter((c) => (c.routed_to_other && c.status === 'open') || c.status === 'escalated')
  const filterName = selection.routeId
    ? `route ${selection.routeId}`
    : selection.nodeId
      ? state.map.nodes.find((n) => n.id === selection.nodeId)?.name
      : null

  const card = (c: Card) => (
    <DecisionCard
      key={c.id}
      card={c}
      cards={state.cards}
      nowMs={p.nowMs}
      isNew={p.fresh.has(c.id)}
      onChanged={p.onChanged}
      onHover={p.onHover}
      onDrawer={p.onDrawer}
    />
  )

  return (
    <div className="space-y-4">
      {p.decided && (
        <AutomateOffer card={p.decided} onYes={() => p.onAutomate(p.decided!)} onNo={p.onSkipAutomate} />
      )}

      {filterName && (
        <button
          onClick={p.onClear}
          className="inline-flex cursor-pointer items-center gap-2 rounded-full bg-ink px-3.5 py-1.5 text-sm font-semibold text-white"
        >
          Showing {filterName} <X size={14} />
        </button>
      )}

      {mine.length > 0 ? (
        <>
          <h1 className={HEADING}>Needs you · {mine.length}</h1>
          {mine.map(card)}
        </>
      ) : (
        <Calm state={state} nowMs={p.nowMs} filtered={!!filterName} waiting={withOther.length} />
      )}

      {withOther.length > 0 && (
        <>
          <h2 className={`pt-2 ${HEADING}`}>With others · {withOther.length}</h2>
          {withOther.map(card)}
        </>
      )}

      <Activity items={state.activity} />
    </div>
  )
}

/** The one place that offers automation: after a decision, by button or by voice. */
export function AutomateOffer({ card, onYes, onNo }: { card: Card; onYes: () => void; onNo: () => void }) {
  return (
    <section className="slide-in rounded-xl border-2 border-ok/40 bg-ok/5 p-4" aria-live="polite">
      <div className="flex items-start gap-1.5 text-sm font-semibold text-ok">
        <CheckCircle2 size={16} className="mt-0.5 shrink-0" />
        <span className="min-w-0">{DONE_WORD[card.status] ?? 'Done'}: {tidy(card.headline)}</span>
      </div>
      <div className="mt-1.5 text-[17px] font-bold leading-snug">Handle the next one like this on its own?</div>
      <p className="mt-1 text-[15px] text-ink-2">
        You'll see what it would do before it starts. Anything that costs money or changes the plan still comes to you.
      </p>
      <div className="mt-3 flex gap-2">
        <Button size="sm" onClick={onYes}><Sparkles size={16} /> Show me how</Button>
        <Button size="sm" variant="ghost" onClick={onNo}>Not now</Button>
      </div>
    </section>
  )
}

function Calm({ state, nowMs, filtered, waiting }: { state: State; nowMs: number; filtered: boolean; waiting: number }) {
  const suppliers = new Set(state.shipments.map((s) => s.supplier_id)).size
  return (
    <section className="rounded-2xl border border-line p-5 sm:p-6">
      <CheckCircle2 size={32} className="text-ok" />
      <h1 className="mt-3 text-[22px] font-extrabold leading-tight sm:text-[24px]">
        {filtered ? 'Nothing open here.' : 'Nothing needs you right now.'}
      </h1>
      <p className="mt-2 text-[15px] text-ink-2">
        {waiting > 0 && `${waiting} ${waiting === 1 ? 'decision is' : 'decisions are'} with others. `}
        Agents are watching {state.shipments.length} shipments from {suppliers} suppliers and every new portal file
        {state.read_since_6 > 0 && ` (${state.read_since_6} read since 06:00)`}. Last check {ago(state.last_check_at, nowMs)}.
      </p>
      <p className="mt-1 text-sm text-ink-3">When something needs a decision, it shows up here first.</p>
    </section>
  )
}

function Via({ a }: { a: ActivityItem }) {
  const v = viaOf(a)
  if (!v) return null
  const chip = 'inline-flex max-w-full items-center gap-1 rounded bg-ok/10 px-1.5 py-0.5 text-xs font-semibold text-ok'
  return v.kind === 'routine' ? (
    <span className={chip} title={`Handled by the routine "${v.name}"`}>
      <Workflow size={12} className="shrink-0" /> <span className="truncate">By routine · {v.name}</span>
    </span>
  ) : (
    <span className={chip}><Mic size={12} className="shrink-0" /> By voice</span>
  )
}

function Activity({ items }: { items: ActivityItem[] }) {
  if (!items.length) return null
  return (
    <section className="pt-2">
      <h2 className={HEADING}>Activity</h2>
      <ul className="mt-2 divide-y divide-line">
        {items.slice(0, 12).map((a, i) => (
          <li key={i} className="flex gap-3 py-2.5 text-[15px]">
            <span className="w-[72px] shrink-0 pt-px text-sm tabular-nums text-ink-3">
              {a.at ? `${fmtDay(a.at)} ${fmtTime(a.at)}` : ''}
            </span>
            <span className="min-w-0 text-ink-2">
              <span className="break-words">{sentence(a.text)}</span>
              {a.detail && <span className="block text-sm text-ink-3">{tidy(a.detail)}</span>}
              {viaOf(a) && <span className="mt-1 block"><Via a={a} /></span>}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}
