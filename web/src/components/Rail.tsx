import { CheckCircle2, X } from 'lucide-react'
import { DecisionCard, type Offer } from '@/components/DecisionCard'
import { ago, fmtDay, fmtTime, type ActivityItem, type Card, type State } from '@/lib/api'
import type { MapSelection } from '@/components/SupplyMap'

interface Props {
  state: State
  nowMs: number
  selection: MapSelection
  onClear: () => void
  fresh: Set<string>
  offer: Offer | null
  onOffer: (o: Offer | null) => void
  onChanged: () => void
  onHover: (routes: string[]) => void
  onDrawer: (card: Card, tab: 'evidence' | 'trail') => void
}

export function Rail(p: Props) {
  const { state, selection } = p
  const match = (c: Card) =>
    selection.routeId ? c.routes.includes(selection.routeId) : selection.nodeId ? c.supplier_id === selection.nodeId : true
  const mine = state.cards.filter((c) => c.status === 'open' && !c.routed_to_other && match(c))
  const withOther = state.cards.filter((c) => (c.routed_to_other && c.status === 'open') || c.status === 'escalated')
  const pinned = p.offer && !mine.some((c) => c.id === p.offer!.cardId) ? p.offer.card : null
  const filterName = selection.routeId
    ? `route ${selection.routeId}`
    : selection.nodeId
      ? state.map.nodes.find((n) => n.id === selection.nodeId)?.name
      : null

  const card = (c: Card) => (
    <DecisionCard
      key={c.id}
      card={c}
      nowMs={p.nowMs}
      isNew={p.fresh.has(c.id)}
      offer={p.offer}
      onOffer={p.onOffer}
      onChanged={p.onChanged}
      onHover={p.onHover}
      onDrawer={p.onDrawer}
    />
  )

  return (
    <div className="space-y-4">
      {filterName && (
        <button
          onClick={p.onClear}
          className="inline-flex cursor-pointer items-center gap-2 rounded-full bg-ink px-3.5 py-1.5 text-sm font-semibold text-white"
        >
          Showing {filterName} <X size={14} />
        </button>
      )}

      {pinned && card(pinned)}
      {mine.length > 0 ? (
        <>
          <h1 className="text-sm font-bold uppercase tracking-wide text-ink-3">
            Needs you · {mine.length}
          </h1>
          {mine.map(card)}
        </>
      ) : (
        !pinned && <Calm state={state} nowMs={p.nowMs} filtered={!!filterName} />
      )}

      {withOther.length > 0 && (
        <>
          <h2 className="pt-2 text-sm font-bold uppercase tracking-wide text-ink-3">With others · {withOther.length}</h2>
          {withOther.map(card)}
        </>
      )}

      <Activity items={state.activity} />
    </div>
  )
}

function Calm({ state, nowMs, filtered }: { state: State; nowMs: number; filtered: boolean }) {
  return (
    <section className="rounded-2xl border border-line p-6">
      <CheckCircle2 size={36} className="text-ok" />
      <h1 className="mt-3 text-[26px] font-extrabold leading-tight">
        {filtered ? 'Nothing open here.' : 'On track. Nothing needs you right now.'}
      </h1>
      <p className="mt-2 text-[15px] text-ink-2">
        Agents read {state.read_since_6} new files since 06:00 · last check {ago(state.last_check_at, nowMs)}
      </p>
    </section>
  )
}

function Activity({ items }: { items: ActivityItem[] }) {
  if (!items.length) return null
  return (
    <section className="pt-2">
      <h2 className="text-sm font-bold uppercase tracking-wide text-ink-3">Activity</h2>
      <ul className="mt-2 divide-y divide-line">
        {items.slice(0, 12).map((a, i) => (
          <li key={i} className="flex gap-3 py-2.5 text-[15px]">
            <span className="w-[72px] shrink-0 text-sm tabular-nums text-ink-3">
              {a.at ? `${fmtDay(a.at)} ${fmtTime(a.at)}` : ''}
            </span>
            <span className="min-w-0 text-ink-2">
              <span className="break-words">{a.text}</span>
              {a.detail && <span className="block text-sm text-ink-3">{a.detail}</span>}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}
