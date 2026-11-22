import { useState, type ReactNode } from 'react'
import { DONE_WORD, ago, fmtNum, tidy, type Card, type State } from '@/lib/api'
import { sameIssueOn, when, type Group, type Sections } from '@/lib/decisions'
import { shortName, shortTitle } from '@/lib/today'
import { cn } from '@/lib/utils'
import { UndoAction } from './routines/Undo'

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

/** The quiet section label, as on Today. */
export const HEADING = 't-small text-ink-3'

/** Ids in the order j/k walks them: one per row, a group's members only when it is open. */
export function visibleOrder(sec: Sections, expanded: Set<string>): string[] {
  return [
    ...sec.needs.flatMap((g) => (expanded.has(g.key) ? g.cards.map((c) => c.id) : [g.lead.id])),
    ...sec.waiting.map((c) => c.id),
    ...sec.handled.flatMap((h) => (h.card ? [h.card.id] : [])),
    ...sec.done.map((c) => c.id),
  ]
}

const first = (s?: string) => (!s ? '' : /\s[a-z]/.test(s) ? s : (s.split(' ').find((w) => !w.endsWith('.')) ?? s))

// Every decision as one quiet row: a dot, the short headline, the time. Red only on what misses
// the trucks, green only for work a routine did, the blue dot for the row in the pane.
export function DecisionList(p: Props) {
  const { state, sec } = p
  const [showDone, setShowDone] = useState(false)
  const suppliers = new Set(state.shipments.map((s) => s.supplier_id)).size
  const needN = sec.needs.reduce((n, g) => n + g.cards.length, 0)

  return (
    <div className="space-y-9 py-6">
      <Section title="Needs you" n={needN}>
        {sec.needs.length ? (
          sec.needs.map((g) => <GroupRow key={g.key} g={g} {...p} />)
        ) : (
          <li className="t-small px-3.5 text-ink-2">
            Nothing needs you. Agents are watching {state.shipments.length} shipments; last check {ago(state.last_check_at, p.nowMs)}.
          </li>
        )}
      </Section>

      {sec.waiting.length > 0 && (
        <Section title="Waiting on others" n={sec.waiting.length}>
          {sec.waiting.map((c) => (
            <Row key={c.id} card={c} {...p} sub={`${c.status === 'escalated' ? 'Waiting for' : 'With'} ${first(c.approver) || 'the approver'}`} quiet />
          ))}
        </Section>
      )}

      {sec.handled.length > 0 && (
        <section id="handled">
          <h2 className={cn(HEADING, 'dot-routine px-3.5 text-routine')}>Handled for you · {sec.handled.length}</h2>
          <ul className="mt-2">
            {sec.handled.map((h) => {
              const on = !!h.card && p.selectedId === h.card.id
              const body = (
                <>
                  <span className={cn('block truncate', on ? 'font-medium text-ink' : 'text-ink-2')}>{tidy(h.text)}</span>
                  <span className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 text-ink-3">
                    <span className="truncate text-routine">{h.routine}</span>
                    {h.at && <span>· {when(h.at, p.nowMs)}</span>}
                    {h.actionId && <UndoAction actionId={h.actionId} />}
                  </span>
                </>
              )
              // A div, not a button: the row holds its own Undo button.
              return (
                <li key={h.key}>
                  {h.card ? (
                    <div
                      role="button"
                      tabIndex={0}
                      id={`row-${h.card.id}`}
                      onClick={() => p.onSelect(h.card!.id)}
                      onKeyDown={(e) => { if (e.key === 'Enter' && e.target === e.currentTarget) p.onSelect(h.card!.id) }}
                      aria-current={on ? 'true' : undefined}
                      className={cn(rowCls, 'pl-[38px]', on && 'bg-soft')}
                    >
                      {body}
                    </div>
                  ) : (
                    <div className={cn(rowCls, 'cursor-default pl-[38px]')}>{body}</div>
                  )}
                </li>
              )
            })}
          </ul>
        </section>
      )}

      {sec.done.length > 0 && (
        <section>
          <button
            onClick={() => setShowDone(!showDone)}
            aria-expanded={showDone}
            className={cn(HEADING, 'cursor-pointer px-3.5 hover:text-ink focus-visible:outline-2 focus-visible:outline-accent')}
          >
            Decided by you · {sec.done.length} <span className="ml-1 border-b border-hair">{showDone ? 'hide' : 'show'}</span>
          </button>
          {showDone && (
            <ul className="mt-2">
              {sec.done.map((c) => (
                <Row
                  key={c.id}
                  card={c}
                  {...p}
                  quiet
                  sub={`${DONE_WORD[c.status] ?? 'Done'}${c.ui?.at ? ` ${when(c.ui.at, p.nowMs)}` : ''}`}
                />
              ))}
            </ul>
          )}
        </section>
      )}

      <p className="t-small px-3.5 text-ink-3">
        Watching {state.shipments.length} shipments from {suppliers} suppliers and every new portal file
        {state.read_since_6 > 0 ? ` (${state.read_since_6} read since 06:00)` : ''}. Last check {ago(state.last_check_at, p.nowMs)}.
      </p>
      <p className="t-small hidden px-3.5 text-ink-3 md:block"><kbd>J</kbd> <kbd>K</kbd> to move, <kbd>A</kbd> to approve</p>
    </div>
  )
}

function Section({ title, n, children }: { title: string; n: number; children: ReactNode }) {
  return (
    <section>
      <h2 className={cn(HEADING, 'px-3.5')}>{title} · {n}</h2>
      <ul className="mt-2">{children}</ul>
    </section>
  )
}

const rowCls =
  't-small relative block w-full cursor-pointer rounded-[10px] px-3.5 py-2.5 text-left hover:bg-soft/70 focus-visible:outline-2 focus-visible:outline-accent'

function Row({
  card, state, nowMs, selectedId, onSelect, fresh, sub, quiet, extra, selected,
}: Props & { card: Card; sub?: ReactNode; quiet?: boolean; extra?: ReactNode; selected?: boolean }) {
  const on = selected ?? selectedId === card.id
  const late = card.level === 'red' && card.status === 'open'
  const time = card.deadline && card.status === 'open' ? when(card.deadline, nowMs) : ''
  return (
    <li>
      <div
        role="button"
        tabIndex={0}
        id={`row-${card.id}`}
        onClick={() => onSelect(card.id)}
        onKeyDown={(e) => { if (e.key === 'Enter' && e.target === e.currentTarget) onSelect(card.id) }}
        aria-current={on ? 'true' : undefined}
        className={cn(rowCls, 'grid grid-cols-[8px_1fr_auto] items-baseline gap-x-3.5', on && 'bg-soft', fresh.has(card.id) && 'slide-in')}
      >
        <i
          className={cn('h-[7px] w-[7px] -translate-y-px rounded-full', on ? 'bg-accent' : late ? 'bg-late' : 'bg-ink-3', quiet && !on && 'bg-hair ring-1 ring-ink-3/40')}
          aria-hidden="true"
        />
        <span className="min-w-0">
          <span className={cn('line-clamp-2', on ? 'font-medium text-ink' : quiet ? 'text-ink-2' : 'text-ink')}>
            {shortTitle(card, state)}
          </span>
          <span className="mt-0.5 flex min-w-0 items-center gap-2 text-ink-3">
            <span className="truncate">{sub ?? (shortName(card.supplier_name) || card.part_name)}</span>
            {extra}
          </span>
        </span>
        <span className="text-ink-3">{time}</span>
        {late && <span className="sr-only">Misses the trucks.</span>}
      </div>
    </li>
  )
}

function GroupRow(p: Props & { g: Group }) {
  const { g } = p
  if (g.cards.length === 1) return <Row card={g.lead} {...p} />
  const open = p.expanded.has(g.key)
  const inGroup = g.cards.some((c) => c.id === p.selectedId)
  const toggle = (
    <button
      onClick={(e) => { e.stopPropagation(); p.onToggle(g.key) }}
      onKeyDown={(e) => e.stopPropagation()}
      aria-expanded={open}
      className="shrink-0 cursor-pointer text-ink-3 underline decoration-hair underline-offset-4 hover:text-ink"
    >
      {open ? 'hide' : `${g.cards.length - 1} more like this`}
    </button>
  )
  return (
    <>
      <Row card={g.lead} {...p} extra={<><span>·</span>{toggle}</>} selected={open ? p.selectedId === g.lead.id : inGroup} />
      {open && g.cards.slice(1).map((c) => {
        const label = sameIssueOn({ ...g, cards: [g.lead, c] }) ?? tidy(c.headline)
        const on = p.selectedId === c.id
        return (
          <li key={c.id}>
            <button
              id={`row-${c.id}`}
              onClick={() => p.onSelect(c.id)}
              aria-current={on ? 'true' : undefined}
              className={cn(rowCls, 'flex items-baseline justify-between gap-3 py-2 pl-[38px]', on && 'bg-soft')}
            >
              <span className={cn('truncate', on ? 'font-medium text-ink' : 'text-ink-2')}>{label}</span>
              {c.number != null && c.number > 0 && (
                <span className="shrink-0 text-ink-3">{fmtNum(c.number)}</span>
              )}
            </button>
          </li>
        )
      })}
    </>
  )
}
