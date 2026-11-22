import { Filter, GripVertical, Plus, Radar, ShieldCheck, X } from 'lucide-react'
import { useEffect, useRef, useState, type DragEvent, type KeyboardEvent, type ReactNode } from 'react'
import type { State } from '@/lib/api'
import { cn } from '@/lib/utils'
import { funnel, lookAlikeConds, type Funnel } from './funnel'
import { StepIcon, merge, type Mark, type Row } from './RoutineFlow'
import { PALETTE, condWords, paletteKey, triggerText, unavailable, type Cond, type Run, type Spec, type Step } from './types'

// The routine as a calm node editor (n8n-like), drawn from the recipe alone and edited in place.
// Nodes only for the parts that stand on their own, connected by port-and-hairline links: one
// WHEN node (the trigger), ONE "Only if" node holding every condition as a numbered checklist
// (an AND list, so never links between conditions), and one node per THEN step, chained in order,
// ending in a dashed "Add step" node. All on a faint dot-grid canvas; the fixed guard is a quiet
// ALWAYS line underneath. Each condition shows how many open cases are still left after it right
// now (funnel.ts), so "why does nothing run" reads off the diagram. With `onChange` the buyer
// edits it here: × on a condition or step (on hover or focus, always on touch), steps drag to a
// new place, look-alike conditions offer to drop the repeat. With `before` every line shows what
// a change does. Step counters come from the latest run; a test run's stay ink-3, a real run's
// read in routine green. Below `lg` (and without `wide`) the nodes form one vertical chain.

const REFUSED = 'Needs you, it costs money or changes the plan'

interface Counts { matched: number; byStep: Map<string, string>; guard: number }

const NOUN: Record<string, [string, string]> = {
  decisions: ['open decision', 'open decisions'],
  replies: ['supplier answer', 'supplier answers'],
  waiting: ['message waiting', 'messages waiting'],
  shipments: ['shipment', 'shipments'],
}
const noun = (spec: Spec, n: number) => (NOUN[spec.on ?? 'decisions'] ?? NOUN.decisions)[n === 1 ? 0 : 1]

function count(spec: Spec, run?: Run): Counts | null {
  if (!run || !run.ok) return null
  const all = run.did ?? []
  const did = all.filter((d) => !d.proposed)
  const byStep = new Map<string, string>()
  const sent = did.filter((d) => d.do === 'carry_out').length
  const refused = did.filter((d) => d.do === 'notify' && (d.text ?? '').startsWith(REFUSED)).length
  const told = did.filter((d) => d.do === 'notify' && !(d.text ?? '').startsWith(REFUSED))
  const sendTexts = spec.then.filter((s) => s.do === 'carry_out').map((s) => s.text)
  const noted = (run.log ?? []).filter((l) => l && !sendTexts.some((t) => l.startsWith(t))).length
  const did_ = (k: Step['do']) => did.filter((d) => d.do === k).length
  const asked = (k: Step['do']) => all.filter((d) => d.proposed && d.do === k).length
  for (const s of spec.then) {
    if (asked(s.do)) { byStep.set(s.id, `${asked(s.do)} for your OK`); continue }
    if (s.do === 'carry_out' && sent) byStep.set(s.id, `${sent} sent`)
    if (s.do === 'notify') {
      const n = told.filter((d) => d.channel === (s.channel ?? 'both')).length
      if (n) byStep.set(s.id, `${n} told`)
    }
    if (s.do === 'log' && noted) byStep.set(s.id, `${noted} noted`)
    if (s.do === 'chase' && did_('chase')) byStep.set(s.id, `${did_('chase')} chased`)
    if (s.do === 'ask_status' && did_('ask_status')) byStep.set(s.id, `${did_('ask_status')} asked`)
    if (s.do === 'call' && did_('call')) byStep.set(s.id, `${did_('call')} called`)
  }
  const handled = typeof run.result === 'number' ? run.result : new Set(all.map((d) => d.card || d.shipment)).size
  return { matched: handled, byStep, guard: refused }
}

function stepTitle(s: Step): string {
  if (s.do === 'carry_out') return 'Send it'
  if (s.do === 'log') return 'Note it'
  if (s.do === 'chase') return 'Chase once'
  if (s.do === 'ask_status') return 'Ask for a status'
  if (s.do === 'call') return 'Call you'
  return s.channel === 'phone' ? 'Tell you on your phone' : s.channel === 'teams' ? 'Tell you on Teams' : 'Tell you'
}

/** "Tell you on Teams" and "Tell me on Teams" say the same: show the words once. */
const same = (a: string, b: string) => {
  const n = (t: string) => t.toLowerCase().replace(/\b(me|my|you|your)\b/g, '').replace(/[^a-z]/g, '')
  return n(a) === n(b)
}

const MARK_WORD: Record<Mark, string | null> = { same: null, added: 'New', removed: 'Removed', changed: 'Changed' }

/** Where a dragged step would land, shown as an accent edge on the node it is dropped on. */
type Edge = 'before' | 'after' | null

/**
 * One node: a crisp white tile on a hairline, an icon chip, the title and its words. Hover lifts
 * it a little; a change rings it in accent so the before/after reads at a glance.
 */
function Node({
  icon, kicker, title, text, was, badge, active, person, mark, moved, onRemove, edge, axis, grip,
}: {
  icon: ReactNode; title: string; text: string; badge?: string; active?: boolean
  /** A small word above the title ("Then · 1"). */
  kicker?: string
  /** The text before a change, struck through. */
  was?: string
  person?: boolean
  mark?: Mark
  moved?: boolean
  onRemove?: () => void
  edge?: Edge
  axis?: 'x' | 'y'
  /** Shows the drag handle on hover. */
  grip?: boolean
}) {
  const word = mark ? MARK_WORD[mark] ?? (moved ? 'Moved' : null) : null
  const edgeShadow =
    edge && axis === 'x' ? (edge === 'before' ? 'shadow-[-3px_0_0_var(--color-accent)]' : 'shadow-[3px_0_0_var(--color-accent)]')
    : edge ? (edge === 'before' ? 'shadow-[0_-3px_0_var(--color-accent)]' : 'shadow-[0_3px_0_var(--color-accent)]')
    : ''
  const ringed = mark === 'added' || mark === 'changed' || (mark === 'same' && moved)
  return (
    <div
      className={cn(
        'group relative w-full min-w-0 rounded-xl border bg-white px-3.5 pb-3 pt-3 transition-[border-color,box-shadow,opacity] duration-200',
        'border-hair shadow-[0_1px_2px_rgba(24,24,26,.04),0_6px_16px_-12px_rgba(24,24,26,.16)] hover:border-ink-3/35 hover:shadow-[0_6px_18px_-10px_rgba(24,24,26,.18)] focus-within:border-ink-3/35',
        onRemove && 'max-lg:pr-12',
        ringed && 'border-accent/45 bg-[color-mix(in_srgb,var(--color-accent)_3%,white)] shadow-[0_0_0_3px_color-mix(in_srgb,var(--color-accent)_9%,transparent)]',
        mark === 'removed' && 'border-dashed bg-transparent opacity-60',
        edgeShadow,
      )}
      title={text}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="t-small flex min-w-0 items-start gap-2.5 leading-tight">
          <span className={tile(ringed)}>{icon}</span>
          <span className="min-w-0">
            {kicker && <span className="block text-ink-3">{kicker}</span>}
            <span className={cn('line-clamp-2 font-medium text-ink', mark === 'removed' && 'line-through decoration-ink-3')}>{title}</span>
          </span>
        </div>
        {word ? (
          <span className={cn('t-small shrink-0 font-medium', mark === 'removed' ? 'text-ink-3' : 'text-accent')}>{word}</span>
        ) : badge ? (
          <span className={cn('t-small shrink-0 whitespace-nowrap', active ? 'dot-routine text-routine' : 'text-ink-3')}>{badge}</span>
        ) : grip ? (
          <GripVertical size={14} className="shrink-0 text-ink-3 opacity-0 transition-opacity group-hover:opacity-100 max-lg:hidden" aria-hidden />
        ) : null}
      </div>
      {!same(title, text) && <div className={cn('t-small mt-1.5 line-clamp-4 text-ink-2', mark === 'removed' && 'line-through decoration-ink-3')}>
        {was && was !== text && <><span className="text-ink-3 line-through">{was}</span>{' '}</>}
        {text}
      </div>}
      {person && <div className="t-small mt-1 text-ink-3">Rings a person</div>}
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove “${title}”`}
          title="Remove"
          className={cn(
            // The same quiet ✕ as a condition line: no ring, a soft circle on hover.
            'absolute grid cursor-pointer place-items-center rounded-full text-ink-3 transition-opacity hover:bg-soft hover:text-ink focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-accent',
            'right-1.5 top-1.5 h-9 w-9',
            'lg:right-2 lg:top-2 lg:h-6 lg:w-6 lg:opacity-0 lg:group-focus-within:opacity-100 lg:group-hover:opacity-100',
          )}
        >
          <X size={13} />
        </button>
      )}
    </div>
  )
}

const stroke = { stroke: 'currentColor', strokeWidth: 1.25, fill: 'none' }
const Down = () => (
  <svg width="10" height="20" viewBox="0 0 10 20" aria-hidden className="block"><line x1="5" y1="0" x2="5" y2="18" {...stroke} /><path d="M2 14.5 L5 19 L8 14.5" {...stroke} /></svg>
)
const Right = () => (
  <svg width="24" height="10" viewBox="0 0 24 10" aria-hidden className="block"><line x1="0" y1="5" x2="22" y2="5" {...stroke} /><path d="M18.5 2 L23 5 L18.5 8" {...stroke} /></svg>
)

type Drag = { kind: 'step'; id: string } | { kind: 'palette'; key: string }
type DragProps = {
  draggable?: boolean
  onDragStart?: (e: DragEvent) => void
  onDragEnd?: () => void
  onDragOver?: (e: DragEvent) => void
  onDragLeave?: (e: DragEvent) => void
  onDrop?: (e: DragEvent) => void
}

/** The editing state shared by both layouts. */
function useEditor(spec: Spec, onChange?: (s: Spec) => void, disabled?: boolean) {
  const editable = !!onChange && !disabled
  const [drag, setDrag] = useState<Drag | null>(null)
  const [drop, setDrop] = useState<{ id: string; edge: 'before' | 'after' } | null>(null)
  const change = (next: Partial<Spec>) => onChange?.({ ...spec, ...next })
  const present = new Set(spec.then.map(paletteKey).filter(Boolean))
  const palette = PALETTE.filter((p) => !present.has(p.key))

  const removeCond = (id: string) => change({ when: spec.when.filter((c) => c.id !== id) })
  const removeStep = (id: string) => change({ then: spec.then.filter((s) => s.id !== id) })
  const addStep = (key: string, at = spec.then.length) => {
    const p = PALETTE.find((x) => x.key === key)
    if (!p || unavailable(key, spec.on)) return
    const steps = [...spec.then]
    steps.splice(at, 0, p.make())
    change({ then: steps })
  }
  function end() { setDrag(null); setDrop(null) }
  /** Drag props for one step node; `axis` is the direction its lane runs. */
  function stepDrag(id: string, axis: 'x' | 'y'): DragProps {
    if (!editable) return {}
    return {
      draggable: true,
      onDragStart: (e) => {
        e.dataTransfer.setData('text/plain', JSON.stringify({ kind: 'step', id }))
        e.dataTransfer.effectAllowed = 'move'
        setDrag({ kind: 'step', id })
      },
      onDragEnd: end,
      onDragOver: (e) => {
        e.preventDefault()
        const box = (e.currentTarget as HTMLElement).getBoundingClientRect()
        const first = axis === 'x' ? e.clientX < box.left + box.width / 2 : e.clientY < box.top + box.height / 2
        setDrop({ id, edge: first ? 'before' : 'after' })
      },
      onDragLeave: (e) => { if (!(e.currentTarget as HTMLElement).contains(e.relatedTarget as globalThis.Node)) setDrop(null) },
      onDrop: (e) => {
        e.preventDefault()
        let d = drag
        if (!d) { try { d = JSON.parse(e.dataTransfer.getData('text/plain')) as Drag } catch { d = null } }
        const target = drop ?? { id, edge: 'after' as const }
        end()
        if (!d) return
        const idx = spec.then.findIndex((s) => s.id === target.id)
        const at = target.edge === 'before' ? idx : idx + 1
        if (d.kind === 'palette') return addStep(d.key, at)
        const stepId = d.id
        const from = spec.then.findIndex((s) => s.id === stepId)
        if (from < 0) return
        const steps = [...spec.then]
        const [moved] = steps.splice(from, 1)
        steps.splice(at > from ? at - 1 : at, 0, moved)
        if (steps.some((s, i) => s.id !== spec.then[i].id)) change({ then: steps })
      },
    }
  }
  const edgeOf = (id: string): Edge => (drag && drop?.id === id && !(drag.kind === 'step' && drag.id === id) ? drop.edge : null)
  return { editable, palette, removeCond, removeStep, addStep, stepDrag, edgeOf, drag, setDrag, end, on: spec.on }
}
type Editor = ReturnType<typeof useEditor>

/** "Add step": a dashed slot that opens a small picker, each step type described in one line. */
function AddStep({ ed, wide }: { ed: Editor; wide?: boolean }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as globalThis.Node)) setOpen(false) }
    const esc = (e: globalThis.KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); setOpen(false) } }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc, true)
    ref.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus()
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', esc, true) }
  }, [open])
  if (!ed.editable || !ed.palette.length) return null
  const move = (e: KeyboardEvent) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
    e.preventDefault()
    const items = [...(ref.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? [])]
    const i = items.indexOf(document.activeElement as HTMLButtonElement)
    items[(i + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]?.focus()
  }
  return (
    <div ref={ref} className={cn('relative', wide ? 'lg:ml-3' : 'w-full')}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-haspopup="menu"
        className={cn(
          't-small inline-flex cursor-pointer items-center gap-2 rounded-xl border border-dashed text-ink-2 transition-colors hover:border-ink-3/60 hover:text-ink focus-visible:outline-2 focus-visible:outline-accent',
          open ? 'border-ink-3/60 bg-white text-ink' : 'border-ink-3/35 bg-white/75 hover:bg-white',
          wide ? 'h-[54px] px-4' : 'h-11 w-full justify-center',
        )}
      >
        <Plus size={14} /> Add step
      </button>
      {open && (
        <div
          role="menu"
          aria-label="Add a step"
          onKeyDown={move}
          className={cn(
            'fade absolute z-20 mt-2 rounded-2xl bg-white p-1.5 shadow-[0_18px_48px_-16px_rgba(24,24,26,.28),0_0_0_1px_var(--color-hair)]',
            wide ? 'left-0 top-full w-[320px] max-lg:right-0 max-lg:w-auto' : 'inset-x-0 top-full',
          )}
        >
          <p className="t-small px-3 pb-1 pt-2 text-ink-3">Add a step at the end, or drag one into place</p>
          {ed.palette.map((p) => { const why = unavailable(p.key, ed.on); return (
            <button
              key={p.key}
              type="button"
              role="menuitem"
              disabled={!!why}
              title={why ?? undefined}
              draggable={!why}
              onDragStart={(e) => { e.dataTransfer.setData('text/plain', JSON.stringify({ kind: 'palette', key: p.key })); ed.setDrag({ kind: 'palette', key: p.key }) }}
              onDragEnd={ed.end}
              onClick={() => { ed.addStep(p.key); setOpen(false) }}
              className="flex w-full cursor-pointer items-start gap-3 rounded-xl px-3 py-2.5 text-left outline-none hover:bg-soft focus-visible:bg-soft disabled:cursor-default disabled:opacity-50 disabled:hover:bg-transparent"
            >
              <span className="mt-px grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-soft text-ink-2"><StepIcon step={p.make()} size={14} className="text-ink-2" /></span>
              <span className="min-w-0">
                <span className="t-small block font-medium text-ink">{p.label}</span>
                <span className="t-small block text-ink-3">{why ?? p.hint}</span>
              </span>
            </button>
          ) })}
        </div>
      )}
    </div>
  )
}

/** The fixed guard: one quiet, reassuring line under the canvas, the same in every mode. */
function Guard({ badge, active }: { badge?: string; active?: boolean }) {
  return (
    <div className="flex items-start gap-2.5 px-1">
      <ShieldCheck size={15} strokeWidth={1.75} className="mt-[3px] shrink-0 text-ink-3" aria-hidden />
      <p className="t-small min-w-0 flex-1 text-ink-3">
        <span className="font-medium text-ink-2">Always: money and plan changes come to you.</span>{' '}
        Whatever the routine says, an option that costs money or changes the plan is never carried out. It asks you instead.
      </p>
      {badge && <span className={cn('t-small shrink-0 whitespace-nowrap', active ? 'dot-routine text-routine' : 'text-ink-3')}>{badge}</span>}
    </div>
  )
}

/** The node look every box on the canvas shares. */
export const NODE = 'rounded-xl border border-hair bg-white shadow-[0_1px_2px_rgba(24,24,26,.04),0_6px_16px_-12px_rgba(24,24,26,.16)]'
const tile = (on?: boolean) => cn('grid h-7 w-7 shrink-0 place-items-center rounded-lg [&>svg]:block', on ? 'bg-accent/10 text-accent' : 'bg-soft text-ink-2')

/** A node's head row: the icon tile, a small kicker and the title. */
export function NodeHead({ icon, kicker, title, accent, right }: { icon: ReactNode; kicker: string; title: string; accent?: boolean; right?: ReactNode }) {
  return (
    <div className="flex items-start gap-2.5 px-3.5 pt-3">
      <span className={tile(accent)}>{icon}</span>
      <div className="t-small min-w-0 flex-1 leading-tight">
        <p className="text-ink-3">{kicker}</p>
        <p className="font-medium text-ink">{title}</p>
      </div>
      {right}
    </div>
  )
}

/** WHEN: what the routine looks at, and how many cases that is right now. */
function Trigger({ spec, before, fun }: { spec: Spec; before?: Spec; fun: Funnel | null }) {
  const t = triggerText(spec.on)
  const changed = !!before && (before.on ?? 'decisions') !== (spec.on ?? 'decisions')
  return (
    <div className={cn(NODE, 'min-w-0 pb-3', changed && 'border-accent/45 shadow-[0_0_0_3px_color-mix(in_srgb,var(--color-accent)_9%,transparent)]')}>
      <NodeHead
        icon={<Radar size={14} strokeWidth={1.75} />}
        kicker="When"
        title={t.title}
        accent={changed}
        right={changed ? <span className="t-small shrink-0 font-medium text-accent">Changed</span> : undefined}
      />
      <p className="t-small mt-1.5 px-3.5 text-ink-3">{t.text}</p>
      {fun && <p className="t-small mx-3.5 mt-2.5 border-t border-hair pt-2.5 text-ink-2">{fun.total} {noun(spec, fun.total)} now</p>}
    </div>
  )
}

/** The × on a condition line: small on hover or focus with a pointer, always there on touch. */
function RemoveLine({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={`Remove “${label}”`}
      title="Remove"
      className={cn(
        'grid shrink-0 cursor-pointer place-items-center rounded-full text-ink-3 transition-opacity hover:bg-soft hover:text-ink focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-accent',
        '-my-1.5 h-9 w-9 lg:-my-0.5 lg:h-6 lg:w-6 lg:opacity-0 lg:group-focus-within:opacity-100 lg:group-hover:opacity-100',
      )}
    >
      <X size={13} />
    </button>
  )
}

/**
 * ONLY IF: one node holding every condition as a numbered checklist (all must hold, order does
 * not matter). The count on each line is how many cases are still left after it.
 */
function Conditions({ spec, before, ed, fun, c }: { spec: Spec; before?: Spec; ed: Editor; fun: Funnel | null; c: Counts | null }) {
  const rows: Row<Cond>[] = merge<Cond>(spec.when, before?.when)
  const repeats = lookAlikeConds(spec.when, condWords)
  const number = new Map(spec.when.map((x, i) => [x.id, i + 1]))
  const total = fun?.total ?? 0
  const foot = fun ? verdict(spec, fun, number) : c ? `${c.matched} matched in the last test.` : null
  return (
    <div className={cn(NODE, 'min-w-0')}>
      <NodeHead
        icon={<Filter size={14} strokeWidth={1.75} />}
        kicker="Only if"
        title={spec.when.length === 1 ? 'This holds' : `All ${spec.when.length} of these hold`}
      />
      <ol className="mt-2.5 divide-y divide-hair border-t border-hair">
        {rows.map((row) => {
          const cond = row.item
          const live = row.mark !== 'removed'
          const n = number.get(cond.id)
          // Past the line where everything stops, more zeros say nothing: leave them out.
          const past = !!fun?.stop && (n ?? 0) > fun.stop.index + 1
          const left = live && !past ? fun?.left.get(cond.id) : undefined
          const stop = fun?.stop?.id === cond.id
          const ringed = row.mark === 'added' || row.mark === 'changed'
          const repeatOf = live ? repeats.get(cond.id) : undefined
          return (
            <li
              key={cond.id}
              className={cn('rise group px-3.5 py-2.5', ringed && 'bg-accent/[.04]', !live && 'opacity-60')}
              title={`${cond.field.replace('_', ' ')} ${cond.op === 'any_of' ? 'any of' : cond.op} ${String(cond.value)}`}
            >
              <div className="flex items-start gap-2.5">
                <span className="t-small w-7 shrink-0 text-center text-ink-3 tabular-nums">{live ? n : ''}</span>
                <p className={cn('t-small min-w-0 flex-1 text-ink', !live && 'text-ink-3 line-through decoration-ink-3')}>
                  {row.mark === 'changed' && row.old && condWords(row.old) !== condWords(cond) && (
                    <><span className="text-ink-3 line-through">{condWords(row.old)}</span>{' '}</>
                  )}
                  {condWords(cond)}
                </p>
                {row.mark !== 'same' && MARK_WORD[row.mark] && (
                  <span className={cn('t-small shrink-0 font-medium', live ? 'text-accent' : 'text-ink-3')}>{MARK_WORD[row.mark]}</span>
                )}
                {left !== undefined && (
                  <span
                    className={cn('t-small shrink-0 whitespace-nowrap tabular-nums', stop ? 'font-medium text-ink' : 'text-ink-3')}
                    title={`${left} of ${total} ${noun(spec, total)} left after this line`}
                  >
                    {left} left
                  </span>
                )}
                {ed.editable && live && <RemoveLine label={condWords(cond)} onClick={() => ed.removeCond(cond.id)} />}
              </div>
              {repeatOf && (
                <p className="t-small mt-1 pl-[38px] text-ink-3">
                  Says nearly the same as line {number.get(repeatOf)}.
                  {ed.editable && <> <button type="button" className="uline" onClick={() => ed.removeCond(cond.id)}>Keep one</button></>}
                </p>
              )}
            </li>
          )
        })}
      </ol>
      {foot && <p className="t-small rounded-b-xl border-t border-hair bg-soft/60 px-3.5 py-2.5 pl-[52px] text-ink-2" aria-live="polite">{foot}</p>}
    </div>
  )
}

/** One sentence on why it would or would not run now. */
function verdict(spec: Spec, f: Funnel, number: Map<string, number>): string {
  if (f.total === 0) return `No ${noun(spec, 2)} right now. It waits for the next one.`
  if (f.pass > 0) return `Right now ${f.pass} of ${f.total} ${noun(spec, f.total)} pass every line.`
  if (f.stop) {
    const line = number.get(f.stop.id)
    return f.stop.index === 0
      ? `Right now none of the ${f.total} ${noun(spec, f.total)} pass line 1.`
      : `Right now ${f.stop.reached} of ${f.total} get as far as line ${line}, and none pass it.`
  }
  return `Right now none of the ${f.total} ${noun(spec, f.total)} pass every line.`
}

/** THEN: one node per step, chained in order, and the dashed "Add step" node at the end. */
function Steps({ spec, run, before, ed, c }: { spec: Spec; run?: Run; before?: Spec; ed: Editor; c: Counts | null }) {
  const rows = merge<Step>(spec.then, before?.then)
  return (
    <div className="flex min-w-0 flex-col">
      {rows.map((row, i) => {
        const s = row.item
        const badge = c?.byStep.get(s.id)
        const live = row.mark !== 'removed'
        const drag = live ? ed.stepDrag(s.id, 'y') : {}
        const dragging = ed.drag?.kind === 'step' && ed.drag.id === s.id
        return (
          <div key={s.id}>
            {i > 0 && <Link />}
            <div {...drag} className={cn('rise min-w-0', drag.draggable && 'cursor-grab active:cursor-grabbing', dragging && 'opacity-40')}>
              <Node
                icon={<StepIcon step={s} size={14} className="" />}
                kicker={rows.length > 1 ? `Then · ${i + 1}` : 'Then'}
                person={s.do === 'call'}
                title={stepTitle(s)}
                text={s.text}
                was={row.mark === 'changed' && row.old ? row.old.text : undefined}
                mark={row.mark}
                moved={row.moved}
                badge={badge}
                active={!!badge && !run?.dry && !badge.endsWith('for your OK')}
                edge={live ? ed.edgeOf(s.id) : null}
                axis="y"
                grip={!!drag.draggable}
                onRemove={ed.editable && live ? () => ed.removeStep(s.id) : undefined}
              />
            </div>
          </div>
        )
      })}
      {ed.editable && ed.palette.length > 0 && <><Link dashed /><AddStep ed={ed} /></>}
    </div>
  )
}

const port = 'h-[7px] w-[7px] shrink-0 rounded-full border border-ink-3/45 bg-white'

/**
 * A connector between two nodes, n8n style: a port dot on the node it leaves, a hairline, and a
 * small head on the node it enters. Across on a wide screen (`across`), down otherwise.
 */
export function Link({ across, dashed, short }: { across?: boolean; dashed?: boolean; short?: boolean }) {
  const line = dashed ? 'border-dashed border-ink-3/35' : 'border-ink-3/40'
  const down = (
    <div className={cn('ml-[24px] flex w-[9px] flex-col items-center', short ? 'h-4' : 'h-7')}>
      <span className={cn(port, '-mt-[4px]')} />
      <span className={cn('w-0 flex-1 border-l', line)} />
      <svg width="9" height="5" viewBox="0 0 9 5" className="mb-px block text-ink-3/55"><path d="M1 0.5 L4.5 4.5 L8 0.5" {...stroke} /></svg>
    </div>
  )
  if (!across) return <div aria-hidden className="shrink-0">{down}</div>
  return (
    <div aria-hidden className="shrink-0">
      <div className="lg:hidden">{down}</div>
      <div className="mt-[23px] hidden h-[9px] w-12 items-center lg:flex">
        <span className={cn(port, '-ml-[4px]')} />
        <span className={cn('h-0 flex-1 border-t', line)} />
        <svg width="5" height="9" viewBox="0 0 5 9" className="mr-px block text-ink-3/55"><path d="M0.5 1 L4.5 4.5 L0.5 8" {...stroke} /></svg>
      </div>
    </div>
  )
}

/** The canvas: a very light dot grid behind the nodes. */
const CANVAS = {
  backgroundImage: 'radial-gradient(circle, color-mix(in srgb, var(--color-ink-3) 22%, transparent) 1px, transparent 1.2px)',
  backgroundSize: '18px 18px',
}

export function FlowGraph({
  spec, run, wide, before, onChange, disabled, ctx,
}: {
  spec: Spec
  run?: Run
  /** Nodes in a row on a large screen; one vertical chain below `lg` either way. */
  wide?: boolean
  /** The version before a change: every line shows what the change does. */
  before?: Spec
  /** Edit in place: remove, reorder, add. Each edit is a new spec for the caller to test. */
  onChange?: (spec: Spec) => void
  disabled?: boolean
  /** Today's cases, for the "left after this line" counts. */
  ctx?: State | null
}) {
  const ed = useEditor(spec, onChange, disabled)
  const c = count(spec, run)
  const fun = funnel(spec, ctx)
  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-2xl border border-hair bg-ground p-3 sm:p-5" style={CANVAS}>
        <div className={cn('flex flex-col', wide && 'lg:flex-row lg:items-start')}>
          <section aria-label="When" className={cn('min-w-0', wide && 'lg:w-[200px] lg:shrink-0')}>
            <Trigger spec={spec} before={before} fun={fun} />
          </section>
          <Link across={wide} />
          <section aria-label="Only if" className={cn('min-w-0', wide && 'lg:flex-1')}>
            <Conditions spec={spec} before={before} ed={ed} fun={fun} c={c} />
          </section>
          <Link across={wide} />
          <section aria-label="Then" className={cn('min-w-0', wide && 'lg:w-[240px] lg:shrink-0')}>
            <Steps spec={spec} run={run} before={before} ed={ed} c={c} />
          </section>
        </div>
      </div>
      <Guard badge={c?.guard ? `${c.guard} came to you` : undefined} active={!!c?.guard && !run?.dry} />
    </div>
  )
}

/** A small read-only sketch of a recipe (When → Then → Always) for explaining what a routine is. */
export function MiniFlow({ spec }: { spec: Spec }) {
  const t = triggerText(spec.on)
  const chip = (label: string, icon: ReactNode, text: string) => (
    <div className="flex min-w-0 items-center gap-2 rounded-xl border border-hair bg-white px-3 py-2">
      <span className="grid h-6 w-6 shrink-0 place-items-center rounded-md bg-soft text-ink-2 [&>svg]:block">{icon}</span>
      <span className="t-small min-w-0">
        <span className="block text-ink-3">{label}</span>
        <span className="block text-ink">{text}</span>
      </span>
    </div>
  )
  const arrow = <div className="shrink-0 text-ink-3/45 max-sm:ml-[22px] max-sm:rotate-0"><span className="hidden sm:block"><Right /></span><span className="sm:hidden"><Down /></span></div>
  return (
    <div className="flex flex-col items-stretch gap-1 sm:flex-row sm:items-center sm:gap-0">
      {chip('When', <Filter size={14} strokeWidth={1.75} />, spec.when[0] ? condWords(spec.when[0]) : t.title)}
      {arrow}
      {spec.then[0] && chip('Then', <StepIcon step={spec.then[0]} size={14} className="" />, spec.then[0].text)}
      {arrow}
      {chip('Always', <ShieldCheck size={14} strokeWidth={1.75} />, 'Money and plan changes come to you')}
    </div>
  )
}
