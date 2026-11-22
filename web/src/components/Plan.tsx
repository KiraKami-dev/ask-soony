import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { LaunchTimeline } from '@/components/LaunchTimeline'
import { DONE_WORD, ago, fmtNum, tidy, type Card, type State } from '@/lib/api'
import { actionIdOf, sameIssueOn, sections, when, type Group, type Handled } from '@/lib/decisions'
import { shortName, shortTitle } from '@/lib/today'
import { cn } from '@/lib/utils'
import { UndoAction } from './routines/Undo'

// Plan: the overview. The launch timeline, then every case in four quiet groups. Nothing is
// decided here: a row (or a timeline marker) opens the case on Today, where decisions happen.

interface Props {
  state: State
  nowMs: number
  phone: boolean
  /** Opens a case on Today (/d/<id>?from=plan). */
  onOpen: (cardId: string) => void
  onChanged: () => void
}

const DONE_SHOWN = 6
const first = (s?: string) => (!s ? '' : /\s[a-z]/.test(s) ? s : (s.split(' ').find((w) => !w.endsWith('.')) ?? s))
const typing = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))
/** A focused row or button answers Enter itself. */
const pressable = (t: EventTarget | null) =>
  t instanceof HTMLElement && (/^(BUTTON|A)$/.test(t.tagName) || t.getAttribute('role') === 'button')

export function Plan({ state, nowMs, phone, onOpen, onChanged }: Props) {
  const sec = useMemo(() => sections(state), [state])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [allDone, setAllDone] = useState(false)
  const done = allDone ? sec.done : sec.done.slice(0, DONE_SHOWN)
  const needN = sec.needs.reduce((n, g) => n + g.cards.length, 0)
  const suppliers = new Set(state.shipments.map((s) => s.supplier_id)).size

  // The order j / k walks: one id per visible row.
  const order = useMemo(() => [
    ...sec.needs.flatMap((g) => (expanded.has(g.key) ? g.cards.map((c) => c.id) : [g.lead.id])),
    ...sec.waiting.map((c) => c.id),
    ...sec.handled.flatMap((h) => (h.card ? [h.card.id] : [])),
    ...done.map((c) => c.id),
  ], [sec, expanded, done])

  // Desk keyboard: j / k (or arrows) move the selection, Enter opens it on Today.
  useEffect(() => {
    if (phone) return
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || typing(e.target)) return
      const k = e.key
      if (k === 'Enter') {
        if (selectedId && !pressable(e.target)) { e.preventDefault(); onOpen(selectedId) }
        return
      }
      const down = k === 'j' || k === 'J' || k === 'ArrowDown'
      const up = k === 'k' || k === 'K' || k === 'ArrowUp'
      if (!down && !up) return
      e.preventDefault()
      const i = selectedId ? order.indexOf(selectedId) : -1
      const n = i < 0 ? 0 : Math.min(order.length - 1, Math.max(0, i + (down ? 1 : -1)))
      const id = order[n]
      if (!id) return
      setSelectedId(id)
      document.getElementById(`row-${id}`)?.scrollIntoView({ block: 'nearest' })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [phone, order, selectedId, onOpen])

  // Arriving at /plan#handled (from All clear's "undo"): bring that group into view.
  useEffect(() => {
    const id = window.location.hash.slice(1)
    if (id) document.getElementById(id)?.scrollIntoView({ block: 'start' })
  }, [])

  const toggle = (key: string) =>
    setExpanded((e) => { const n = new Set(e); if (n.has(key)) n.delete(key); else n.add(key); return n })
  const row = { state, nowMs, phone, selectedId, onOpen }

  return (
    <div className="flex-1">
      <div className={cn('border-b border-hair', phone ? 'px-6 pb-2 pt-2' : 'px-8 pb-4 xl:px-12')}>
        <LaunchTimeline state={state} nowMs={nowMs} selectedId={selectedId} onSelect={onOpen} compact={phone} />
      </div>

      <div className={cn('mx-auto w-full max-w-[880px] space-y-12', phone ? 'px-3 pb-24 pt-8' : 'px-8 pb-32 pt-14')}>
        <Section title="Needs you" n={needN}>
          {sec.needs.length ? (
            sec.needs.map((g) => <GroupRows key={g.key} g={g} open={expanded.has(g.key)} onToggle={() => toggle(g.key)} {...row} />)
          ) : (
            <li className="t-small px-3.5 text-ink-2">Nothing needs you right now.</li>
          )}
        </Section>

        {sec.waiting.length > 0 && (
          <Section title="Waiting on others" n={sec.waiting.length}>
            {sec.waiting.map((c) => (
              <Row
                key={c.id}
                card={c}
                {...row}
                dot="hollow"
                who={shortName(c.supplier_name) || c.part_name || ''}
                at={c.ui?.at ? `since ${when(c.ui.at, nowMs)}` : ''}
                status={`${c.status === 'escalated' ? 'Waiting for' : 'With'} ${first(c.approver) || 'the approver'}`}
              />
            ))}
          </Section>
        )}

        {sec.handled.length > 0 && (
          <Section id="handled" title="Handled for you" n={sec.handled.length} routine>
            {sec.handled.map((h) => <HandledRow key={h.key} h={h} {...row} onChanged={onChanged} />)}
          </Section>
        )}

        {sec.done.length > 0 && (
          <Section title="Decided by you" n={sec.done.length}>
            {done.map((c) => {
              const a = state.activity.find((x) => x.card === c.id)
              // A sent email cannot be taken back, so no Undo for it.
              const undo = c.status !== 'sent' ? actionIdOf(a) : null
              return (
                <Row
                  key={c.id}
                  card={c}
                  {...row}
                  dot="quiet"
                  who={shortName(c.supplier_name) || c.part_name || ''}
                  at={c.ui?.at ? when(c.ui.at, nowMs) : ''}
                  status={DONE_WORD[c.status] ?? 'Done'}
                  undo={undo ? <UndoAction actionId={undo} onUndone={onChanged} /> : null}
                />
              )
            })}
            {sec.done.length > DONE_SHOWN && (
              <li className="px-3.5 pt-2">
                <button onClick={() => setAllDone(!allDone)} className="t-small cursor-pointer text-ink-3 hover:text-ink">
                  <span className="border-b border-hair">{allDone ? 'Show fewer' : `Show all ${sec.done.length}`}</span>
                </button>
              </li>
            )}
          </Section>
        )}

        <div className="t-small space-y-2 px-3.5 text-ink-3">
          <p>
            Watching {state.shipments.length} shipments from {suppliers} suppliers and every new portal file
            {state.read_since_6 > 0 ? ` (${state.read_since_6} read since 06:00)` : ''}. Last check {ago(state.last_check_at, nowMs)}.
          </p>
          {!phone && <p><kbd>J</kbd> <kbd>K</kbd> to move, <kbd>Enter</kbd> to open it in Today</p>}
        </div>
      </div>
    </div>
  )
}

function Section({ id, title, n, routine, children }: { id?: string; title: string; n: number; routine?: boolean; children: ReactNode }) {
  return (
    <section id={id} className="scroll-mt-6">
      <h2 className={cn('t-small px-3.5', routine ? 'dot-routine text-routine' : 'text-ink-3')}>{title} · {n}</h2>
      <ul className="mt-3">{children}</ul>
    </section>
  )
}

type RowBase = { state: State; nowMs: number; phone: boolean; selectedId: string | null; onOpen: (id: string) => void }

const rowCls =
  't-small grid w-full cursor-pointer items-baseline gap-x-3.5 rounded-[10px] px-3.5 py-3 text-left hover:bg-soft/70 focus-visible:outline-2 focus-visible:outline-accent'

/**
 * One case: dot, headline, who and when underneath, where it stands in words on the right (on a
 * phone, at the end of the line underneath). A div with role=button: it may hold an Undo button.
 */
function Row({
  card, state, phone, selectedId, onOpen, dot, who, at, status, late, extra, undo, title, routine,
}: RowBase & {
  card: Card
  dot: 'open' | 'late' | 'hollow' | 'routine' | 'quiet'
  who: ReactNode
  at: string
  status: string
  late?: boolean
  extra?: ReactNode
  undo?: ReactNode
  title?: string
  /** The who part is a routine's name: green. */
  routine?: boolean
}) {
  const on = selectedId === card.id
  const quiet = dot === 'quiet' || dot === 'hollow' || dot === 'routine'
  const statusEl = <span className={cn(late ? 'text-late' : 'text-ink-3')}>{status}</span>
  const sep = <span aria-hidden="true">·</span>
  return (
    <li>
      <div
        role="button"
        tabIndex={0}
        id={`row-${card.id}`}
        onClick={() => onOpen(card.id)}
        onKeyDown={(e) => { if (e.key === 'Enter' && e.target === e.currentTarget) { e.preventDefault(); onOpen(card.id) } }}
        aria-current={on ? 'true' : undefined}
        className={cn(rowCls, phone ? 'grid-cols-[8px_minmax(0,1fr)]' : 'grid-cols-[8px_minmax(0,1fr)_auto]', on && 'bg-soft')}
      >
        <i
          aria-hidden="true"
          className={cn(
            'h-[7px] w-[7px] -translate-y-px rounded-full',
            on ? 'bg-accent'
              : dot === 'late' ? 'bg-late'
              : dot === 'open' ? 'bg-ink'
              : dot === 'routine' ? 'bg-routine'
              : 'bg-ground ring-1 ring-ink-3/50',
          )}
        />
        <span className="min-w-0">
          <span className={cn('line-clamp-2', on ? 'font-medium text-ink' : quiet ? 'text-ink-2' : 'text-ink')}>
            {title ?? shortTitle(card, state)}
          </span>
          <span className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 text-ink-3">
            {who && <span className={cn('truncate', routine && 'text-routine')}>{who}</span>}
            {who && at && sep}
            {at && <span>{at}</span>}
            {phone && status !== 'Your call' && <>{(who || at) && sep}{statusEl}</>}
            {extra}
            {phone && undo && <>{sep}{undo}</>}
          </span>
        </span>
        {!phone && (
          <span className="flex items-baseline justify-end gap-2 text-right">
            {statusEl}
            {undo && <><span aria-hidden="true" className="text-ink-3">·</span>{undo}</>}
          </span>
        )}
      </div>
    </li>
  )
}

function GroupRows(p: RowBase & { g: Group; open: boolean; onToggle: () => void }) {
  const { g, nowMs } = p
  const lead = g.lead
  const late = lead.level === 'red'
  const decideBy = (c: Card) =>
    c.deadline ? (new Date(c.deadline).getTime() <= nowMs ? 'decide now' : `decide by ${when(c.deadline, nowMs)}`) : ''
  const toggle = g.cards.length > 1 && (
    <>
      <span aria-hidden="true">·</span>
      <button
        onClick={(e) => { e.stopPropagation(); p.onToggle() }}
        onKeyDown={(e) => e.stopPropagation()}
        aria-expanded={p.open}
        className="cursor-pointer text-ink-3 underline decoration-hair underline-offset-4 hover:text-ink"
      >
        {p.open ? 'hide' : `${g.cards.length - 1} more like this`}
      </button>
    </>
  )
  return (
    <>
      <Row
        card={lead}
        {...p}
        dot={late ? 'late' : 'open'}
        who={shortName(lead.supplier_name) || lead.part_name || ''}
        at={decideBy(lead)}
        status={late ? 'Misses the trucks' : 'Your call'}
        late={late}
        extra={toggle}
      />
      {p.open && g.cards.slice(1).map((c) => {
        const label = sameIssueOn({ ...g, cards: [g.lead, c] }) ?? tidy(c.headline)
        const on = p.selectedId === c.id
        return (
          <li key={c.id}>
            <button
              id={`row-${c.id}`}
              onClick={() => p.onOpen(c.id)}
              aria-current={on ? 'true' : undefined}
              className={cn(rowCls, 'flex items-baseline justify-between gap-3 py-2 pl-[38px]', on && 'bg-soft')}
            >
              <span className={cn('truncate', on ? 'font-medium text-ink' : 'text-ink-2')}>Same issue on {label}</span>
              <span className="shrink-0 text-ink-3">{c.number != null && c.number > 0 ? fmtNum(c.number) : decideBy(c)}</span>
            </button>
          </li>
        )
      })}
    </>
  )
}

function HandledRow(p: RowBase & { h: Handled; onChanged: () => void }) {
  const { h, nowMs } = p
  const status = (h.status && DONE_WORD[h.status]) || 'Done'
  const undo = h.actionId && h.status !== 'sent' ? <UndoAction actionId={h.actionId} onUndone={p.onChanged} /> : null
  const who = h.routine ? `‘${h.routine}’` : 'A routine'
  if (h.card) {
    return (
      <Row
        card={h.card}
        {...p}
        dot="routine"
        title={shortTitle(h.card, p.state)}
        who={who}
        routine
        at={h.at ? when(h.at, nowMs) : ''}
        status={status}
        undo={undo}
      />
    )
  }
  // Routine work without a case on screen: shown, not opened.
  return (
    <li>
      <div className={cn(rowCls, 'cursor-default hover:bg-transparent', p.phone ? 'grid-cols-[8px_minmax(0,1fr)]' : 'grid-cols-[8px_minmax(0,1fr)_auto]')}>
        <i aria-hidden="true" className="h-[7px] w-[7px] -translate-y-px rounded-full bg-routine" />
        <span className="min-w-0">
          <span className="line-clamp-2 text-ink-2">{tidy(h.text)}</span>
          <span className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 text-ink-3">
            <span className="truncate text-routine">{who}</span>
            {h.at && <><span aria-hidden="true">·</span><span>{when(h.at, nowMs)}</span></>}
            {p.phone && undo && <><span aria-hidden="true">·</span>{undo}</>}
          </span>
        </span>
        {!p.phone && <span className="flex justify-end">{undo}</span>}
      </div>
    </li>
  )
}
