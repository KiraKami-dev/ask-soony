import { Check, ChevronDown, ChevronRight, Eye, Workflow } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { DONE_WORD, ago, fmtNum, partName, tidy, until, type Card, type Level, type State } from '@/lib/api'
import { sameIssueOn, when, type Group, type Sections } from '@/lib/decisions'
import { cn } from '@/lib/utils'

interface Props {
  state: State
  nowMs: number
  sec: Sections
  selectedId: string | null
  onSelect: (id: string) => void
  expanded: Set<string>
  onToggle: (key: string) => void
  fresh: Set<string>
}

export const HEADING = 'text-xs font-semibold uppercase tracking-wide text-ink-3'

/** Ids in the order j/k walks them: one per row, a group's members only when it is open. */
export function visibleOrder(sec: Sections, expanded: Set<string>): string[] {
  return [
    ...sec.needs.flatMap((g) => (expanded.has(g.key) ? g.cards.map((c) => c.id) : [g.lead.id])),
    ...sec.waiting.map((c) => c.id),
    ...sec.handled.flatMap((h) => (h.card ? [h.card.id] : [])),
    ...sec.done.map((c) => c.id),
  ]
}

const BAR: Record<Level, string> = { red: 'bg-stop', amber: 'bg-warn', grey: 'bg-silent', blue: 'bg-line' }
const first = (s?: string) => (!s ? '' : /\s[a-z]/.test(s) ? s : (s.split(' ').find((w) => !w.endsWith('.')) ?? s))

export function DecisionList(p: Props) {
  const { state, sec } = p
  const [showDone, setShowDone] = useState(false)
  const suppliers = new Set(state.shipments.map((s) => s.supplier_id)).size

  return (
    <div className="space-y-6 py-5">
      <Section title={`Needs you · ${sec.needs.reduce((n, g) => n + g.cards.length, 0)}`}>
        {sec.needs.length ? (
          sec.needs.map((g) => <GroupRow key={g.key} g={g} {...p} />)
        ) : (
          <p className="px-4 text-[15px] leading-relaxed text-ink-2">
            <span className="font-semibold text-ink">Nothing needs you.</span> Agents are watching {state.shipments.length} shipments;
            last check {ago(state.last_check_at, p.nowMs)}.
          </p>
        )}
      </Section>

      {sec.waiting.length > 0 && (
        <Section title={`Waiting on others · ${sec.waiting.length}`}>
          {sec.waiting.map((c) => (
            <Row
              key={c.id}
              card={c}
              {...p}
              sub={c.status === 'escalated' ? `Waiting for ${first(c.approver) || 'the approver'}` : `With ${first(c.approver) || 'the approver'}`}
            />
          ))}
        </Section>
      )}

      {sec.handled.length > 0 && (
        <Section title={`Handled for you · ${sec.handled.length}`}>
          {sec.handled.map((h) => {
            const body = (
              <>
                <span className="absolute inset-y-2 left-0 w-[3px] rounded-full bg-routine" />
                <span className="line-clamp-2 text-[14px] font-semibold leading-snug text-ink">{tidy(h.text)}</span>
                <span className="mt-1 flex items-center gap-1.5 text-xs font-semibold text-routine">
                  <Workflow size={12} className="shrink-0" />
                  <span className="truncate">By routine · {h.routine}</span>
                  {h.at && <span className="ml-auto shrink-0 font-medium text-ink-3">{when(h.at, p.nowMs)}</span>}
                </span>
              </>
            )
            return h.card ? (
              <button
                key={h.key}
                onClick={() => p.onSelect(h.card!.id)}
                className={cn(rowCls, p.selectedId === h.card.id && 'bg-panel')}
              >
                {body}
              </button>
            ) : (
              <div key={h.key} className={cn(rowCls, 'cursor-default hover:bg-transparent')}>{body}</div>
            )
          })}
        </Section>
      )}

      {sec.done.length > 0 && (
        <section>
          <button onClick={() => setShowDone(!showDone)} className={cn(HEADING, 'flex cursor-pointer items-center gap-1 px-4 hover:text-ink')}>
            {showDone ? <ChevronDown size={14} /> : <ChevronRight size={14} />} Decided by you · {sec.done.length}
          </button>
          {showDone && (
            <div className="mt-2 space-y-px">
              {sec.done.map((c) => (
                <Row
                  key={c.id}
                  card={c}
                  {...p}
                  muted
                  sub={
                    <span className="inline-flex items-center gap-1 text-ink-2">
                      <Check size={12} /> {DONE_WORD[c.status] ?? 'Done'}{c.ui?.at ? ` ${when(c.ui.at, p.nowMs)}` : ''}
                    </span>
                  }
                />
              ))}
            </div>
          )}
        </section>
      )}

      <section className="mx-4 flex items-start gap-2.5 border-t border-line pt-5 text-sm text-ink-3">
        <Eye size={16} className="mt-0.5 shrink-0" />
        <span>
          Watching {state.shipments.length} shipments from {suppliers} suppliers and every new portal file
          {state.read_since_6 > 0 ? ` (${state.read_since_6} read since 06:00)` : ''}. Last check {ago(state.last_check_at, p.nowMs)}.
        </span>
      </section>
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h2 className={cn(HEADING, 'px-4')}>{title}</h2>
      <div className="mt-2 space-y-px">{children}</div>
    </section>
  )
}

const rowCls =
  'relative block w-full cursor-pointer rounded-lg py-2.5 pl-4 pr-3 text-left hover:bg-panel/70 focus-visible:outline-2 focus-visible:outline-ok'

function Due({ card, nowMs }: { card: Card; nowMs: number }) {
  if (!card.deadline || card.status !== 'open') return null
  const u = until(card.deadline, nowMs)
  const urgent = card.level === 'red' && u?.urgent
  return (
    <span className="shrink-0 text-right leading-tight">
      <span className={cn('block text-[14px] font-bold tabular-nums', urgent ? 'text-stop' : 'text-ink')}>{when(card.deadline, nowMs)}</span>
      {u && <span className="block text-xs text-ink-3">{u.text}</span>}
    </span>
  )
}

function Row({
  card, state, nowMs, selectedId, onSelect, fresh, sub, muted, chip, selected,
}: Props & { card: Card; sub?: ReactNode; muted?: boolean; chip?: ReactNode; selected?: boolean }) {
  const part = card.part_name || partName(card.part, state.cards)
  const on = selected ?? selectedId === card.id
  return (
    <div
      role="button"
      tabIndex={0}
      id={`row-${card.id}`}
      onClick={() => onSelect(card.id)}
      onKeyDown={(e) => { if (e.key === 'Enter' && e.target === e.currentTarget) onSelect(card.id) }}
      aria-current={on ? 'true' : undefined}
      className={cn(rowCls, on && 'bg-panel', fresh.has(card.id) && 'slide-in')}
    >
      <span className={cn('absolute inset-y-2 left-0 w-[3px] rounded-full', muted ? 'bg-line' : BAR[card.level])} />
      <span className="flex items-start gap-3">
        <span className="min-w-0 flex-1">
          <span className={cn('line-clamp-2 text-[14px] font-semibold leading-snug', muted ? 'text-ink-2' : 'text-ink')}>{tidy(card.headline)}</span>
          <span className="mt-1 flex min-w-0 items-center gap-2 text-xs text-ink-3">
            <span className="truncate">{sub ?? (card.supplier_name || part)}</span>
            {chip}
          </span>
        </span>
        <Due card={card} nowMs={nowMs} />
      </span>
    </div>
  )
}

function GroupRow(p: Props & { g: Group }) {
  const { g } = p
  if (g.cards.length === 1) return <Row card={g.lead} {...p} />
  const open = p.expanded.has(g.key)
  const inGroup = g.cards.some((c) => c.id === p.selectedId)
  const chip = (
    <button
      onClick={(e) => { e.stopPropagation(); p.onToggle(g.key) }}
      aria-expanded={open}
      className="inline-flex shrink-0 cursor-pointer items-center gap-0.5 rounded-full bg-white px-2 py-0.5 text-xs font-semibold text-ink-2 ring-1 ring-line hover:text-ink"
    >
      ×{g.cards.length} like this {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
    </button>
  )
  return (
    <div>
      <Row card={g.lead} {...p} chip={chip} selected={open ? p.selectedId === g.lead.id : inGroup} />
      {open && (
        <div className="ml-4 border-l border-line">
          {g.cards.slice(1).map((c) => {
            const label = sameIssueOn({ ...g, cards: [g.lead, c] }) ?? tidy(c.headline)
            return (
              <button
                key={c.id}
                id={`row-${c.id}`}
                onClick={() => p.onSelect(c.id)}
                className={cn(rowCls, 'py-2 text-[13px]', p.selectedId === c.id && 'bg-panel')}
              >
                <span className="flex items-baseline justify-between gap-3">
                  <span className="truncate font-semibold text-ink-2">{label}</span>
                  {c.number != null && c.number > 0 && (
                    <span className="shrink-0 text-xs tabular-nums text-ink-3">{fmtNum(c.number)} {c.number_label}</span>
                  )}
                </span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
