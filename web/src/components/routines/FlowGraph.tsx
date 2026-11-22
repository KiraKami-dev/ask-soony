import { Filter, GripVertical, Plus, Radar, ShieldCheck, X } from 'lucide-react'
import { useEffect, useRef, useState, type DragEvent, type KeyboardEvent, type ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { StepIcon, merge, type Mark, type Row } from './RoutineFlow'
import { PALETTE, condWords, paletteKey, triggerText, type Cond, type Run, type Spec, type Step } from './types'

// The routine as a calm node editor, drawn from the recipe alone and edited in place:
// WHEN (trigger, then every condition) and THEN (the steps) as two lanes, and the fixed guard as
// its own ALWAYS lane underneath. With `onChange` the buyer edits it here: × on a condition or
// step removes it, steps drag to a new place, "Add step" opens a small picker. With `before`
// every node shows what a change does (new, changed, removed, moved). Counters come from the
// latest run; a test run's counts stay ink-3, a real run's read in routine green.
// Below the `lg` breakpoint (and without `wide`) it is one vertical, thumb-friendly path.

const REFUSED = 'Needs you, it costs money or changes the plan'

interface Counts { matched: number; byStep: Map<string, string>; guard: number }

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
  icon, title, text, was, badge, active, wide, person, mark, moved, onRemove, edge, axis, grip,
}: {
  icon: ReactNode; title: string; text: string; badge?: string; active?: boolean; wide?: boolean
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
        'group relative w-full min-w-0 rounded-xl border bg-white px-3.5 pb-3 pt-2.5 transition-[border-color,box-shadow,opacity] duration-200',
        'border-hair hover:border-ink-3/35 hover:shadow-[0_6px_18px_-10px_rgba(24,24,26,.18)] focus-within:border-ink-3/35',
        onRemove && 'max-lg:pr-12',
        wide && 'lg:w-[232px] lg:shrink-0',
        ringed && 'border-accent/45 bg-[color-mix(in_srgb,var(--color-accent)_3%,white)] shadow-[0_0_0_3px_color-mix(in_srgb,var(--color-accent)_9%,transparent)]',
        mark === 'removed' && 'border-dashed bg-transparent opacity-60',
        edgeShadow,
      )}
      title={text}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="t-small flex min-w-0 items-center gap-2 font-medium text-ink">
          <span className={cn('grid h-6 w-6 shrink-0 place-items-center rounded-md [&>svg]:block', ringed ? 'bg-accent/10 text-accent' : 'bg-soft text-ink-2')}>{icon}</span>
          <span className={cn('line-clamp-2', mark === 'removed' && 'line-through decoration-ink-3')}>{title}</span>
        </div>
        {word ? (
          <span className={cn('t-small shrink-0 font-medium', mark === 'removed' ? 'text-ink-3' : 'text-accent')}>{word}</span>
        ) : badge ? (
          <span className={cn('t-small shrink-0 whitespace-nowrap', active ? 'dot-routine text-routine' : 'text-ink-3')}>{badge}</span>
        ) : grip ? (
          <GripVertical size={14} className="shrink-0 text-ink-3 opacity-0 transition-opacity group-hover:opacity-100 max-lg:hidden" aria-hidden />
        ) : null}
      </div>
      {!same(title, text) && <div className={cn('t-small mt-1.5 line-clamp-3 text-ink-2', wide && 'lg:line-clamp-4', mark === 'removed' && 'line-through decoration-ink-3')}>
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
            'absolute grid cursor-pointer place-items-center rounded-full bg-white text-ink-3 shadow-[0_0_0_1px_var(--color-hair)] transition-opacity hover:text-ink focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-accent',
            'right-2 top-1/2 h-9 w-9 -translate-y-1/2',
            'lg:-right-2.5 lg:-top-2.5 lg:h-6 lg:w-6 lg:translate-y-0 lg:opacity-0 lg:group-focus-within:opacity-100 lg:group-hover:opacity-100',
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
const ArrowDown = () => <div className="ml-[22px] shrink-0 py-0.5 text-ink-3/45"><Down /></div>

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
    if (!p) return
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
  return { editable, palette, removeCond, removeStep, addStep, stepDrag, edgeOf, drag, setDrag, end }
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
          open ? 'border-ink-3/60 bg-white text-ink' : 'border-ink-3/35',
          wide ? 'h-[54px] px-4' : 'h-12 w-full justify-center',
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
          {ed.palette.map((p) => (
            <button
              key={p.key}
              type="button"
              role="menuitem"
              draggable
              onDragStart={(e) => { e.dataTransfer.setData('text/plain', JSON.stringify({ kind: 'palette', key: p.key })); ed.setDrag({ kind: 'palette', key: p.key }) }}
              onDragEnd={ed.end}
              onClick={() => { ed.addStep(p.key); setOpen(false) }}
              className="flex w-full cursor-pointer items-start gap-3 rounded-xl px-3 py-2.5 text-left outline-none hover:bg-soft focus-visible:bg-soft"
            >
              <span className="mt-px grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-soft text-ink-2"><StepIcon step={p.make()} size={14} className="text-ink-2" /></span>
              <span className="min-w-0">
                <span className="t-small block font-medium text-ink">{p.label}</span>
                <span className="t-small block text-ink-3">{p.hint}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/** The fixed guard: its own lane, the same in every routine and every mode. */
function Guard({ badge, active }: { badge?: string; active?: boolean }) {
  return (
    <div className="flex items-start gap-3 rounded-xl bg-soft px-4 py-3">
      <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-md bg-white text-ink shadow-[0_0_0_1px_var(--color-hair)]">
        <ShieldCheck size={14} strokeWidth={1.75} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="t-small font-medium text-ink">Money and plan changes come to you</p>
        <p className="t-small text-ink-2">Whatever the routine says, an option that costs money or changes the plan is never carried out. It asks you instead.</p>
      </div>
      {badge && <span className={cn('t-small shrink-0 whitespace-nowrap', active ? 'dot-routine text-routine' : 'text-ink-3')}>{badge}</span>}
    </div>
  )
}

interface StepNode { key: string; drag: DragProps; dragging: boolean; node: ReactNode }

function nodesFor(spec: Spec, run: Run | undefined, wide: boolean, before: Spec | undefined, ed: Editor, axis: 'x' | 'y') {
  const c = count(spec, run)
  const guard = <Guard badge={c?.guard ? `${c.guard} came to you` : undefined} active={!!c?.guard && !run?.dry} />
  const trigger = (
    <Node
      wide={wide}
      icon={<Radar size={14} strokeWidth={1.75} />}
      title={triggerText(spec.on).title}
      text={triggerText(spec.on).text}
      mark={before && before.on !== spec.on ? 'changed' : undefined}
    />
  )
  const conds: Row<Cond>[] = merge<Cond>(spec.when, before?.when)
  let seen = 0
  const conditions = conds.map((row) => {
    const isLive = row.mark !== 'removed'
    const first = isLive && seen === 0
    if (isLive) seen += 1
    const last = isLive && seen === spec.when.length
    return {
      key: row.item.id,
      node: (
        <Node
          wide={wide}
          icon={<Filter size={14} strokeWidth={1.75} />}
          title={first || !isLive ? 'Only if' : 'And'}
          text={condWords(row.item)}
          was={row.mark === 'changed' && row.old ? condWords(row.old) : undefined}
          mark={row.mark}
          badge={last && c ? `${c.matched} matched` : undefined}
          active={last && !!c?.matched && !run?.dry}
          onRemove={ed.editable && isLive ? () => ed.removeCond(row.item.id) : undefined}
        />
      ),
    }
  })
  const steps: StepNode[] = merge<Step>(spec.then, before?.then).map((row) => {
    const s = row.item
    const badge = c?.byStep.get(s.id)
    const isLive = row.mark !== 'removed'
    const drag = isLive ? ed.stepDrag(s.id, axis) : {}
    return {
      key: s.id,
      drag,
      dragging: ed.drag?.kind === 'step' && ed.drag.id === s.id,
      node: (
        <Node
          wide={wide}
          icon={<StepIcon step={s} size={14} className="" />}
          person={s.do === 'call'}
          title={stepTitle(s)}
          text={s.text}
          was={row.mark === 'changed' && row.old ? row.old.text : undefined}
          mark={row.mark}
          moved={row.moved}
          badge={badge}
          active={!!badge && !run?.dry && !badge.endsWith('for your OK')}
          edge={isLive ? ed.edgeOf(s.id) : null}
          axis={axis}
          grip={!!drag.draggable}
          onRemove={ed.editable && isLive ? () => ed.removeStep(s.id) : undefined}
        />
      ),
    }
  })
  return { guard, trigger, conditions, steps }
}

const grab = (s: { drag: DragProps; dragging: boolean }) =>
  cn('rise min-w-0', s.drag.draggable && 'cursor-grab active:cursor-grabbing', s.dragging && 'opacity-40')

/** A lane: its label in a narrow column, the nodes to the right. */
function Lane({ label, children, line }: { label: string; children: ReactNode; line?: boolean }) {
  return (
    <div className="grid grid-cols-[64px_minmax(0,1fr)] gap-x-4">
      <div className="flex flex-col items-start pt-[15px]">
        <span className="t-small font-medium text-ink-3">{label}</span>
        {line && <div className="ml-[3px] mt-2 w-px flex-1 bg-hair" />}
      </div>
      <div className="flex min-w-0 flex-wrap items-start gap-y-4 pb-6">{children}</div>
    </div>
  )
}

/**
 * The wide view reads like a sentence in lanes: WHEN (trigger, then every condition) left to
 * right, THEN (the steps) left to right, ALWAYS (the guard) underneath. Lanes wrap.
 */
function Lanes({ spec, run, before, ed }: { spec: Spec; run?: Run; before?: Spec; ed: Editor }) {
  const { guard, trigger, conditions, steps } = nodesFor(spec, run, true, before, ed, 'x')
  const arrow = <div className="mt-[22px] shrink-0 px-1.5 text-ink-3/45"><Right /></div>
  return (
    <div className="flex flex-col">
      <Lane label="When" line>
        {[{ key: 'trigger', node: trigger }, ...conditions].map((it, i) => (
          <div key={it.key} className="rise flex items-start">{i > 0 && arrow}{it.node}</div>
        ))}
      </Lane>
      <Lane label="Then" line>
        {steps.map((s, i) => (
          <div key={s.key} className="flex items-start">
            {i > 0 && arrow}
            <div {...s.drag} className={grab(s)}>{s.node}</div>
          </div>
        ))}
        <AddStep ed={ed} wide />
      </Lane>
      <div className="grid grid-cols-[64px_minmax(0,1fr)] gap-x-4">
        <span className="t-small pt-[15px] font-medium text-ink-3">Always</span>
        <div className="min-w-0">{guard}</div>
      </div>
    </div>
  )
}

export function FlowGraph({
  spec, run, wide, before, onChange, disabled,
}: {
  spec: Spec
  run?: Run
  /** Lanes on a large screen; one vertical path below `lg` either way. */
  wide?: boolean
  /** The version before a change: every node shows what the change does. */
  before?: Spec
  /** Edit in place: remove, reorder, add. Each edit is a new spec for the caller to test. */
  onChange?: (spec: Spec) => void
  disabled?: boolean
}) {
  const ed = useEditor(spec, onChange, disabled)
  const { guard, trigger, conditions, steps } = nodesFor(spec, run, false, before, ed, 'y')
  const head = (t: string, first?: boolean) => <p className={cn('t-small mb-2 font-medium text-ink-3', !first && 'mt-5')}>{t}</p>
  const vertical = (
    <div className="flex flex-col">
      {head('When', true)}
      {[{ key: 'trigger', node: trigger }, ...conditions].map((it, i) => (
        <div key={it.key} className="rise">{i > 0 && <ArrowDown />}{it.node}</div>
      ))}
      {head('Then')}
      {steps.map((s, i) => (
        <div key={s.key}>
          {i > 0 && <ArrowDown />}
          <div {...s.drag} className={grab(s)}>{s.node}</div>
        </div>
      ))}
      {ed.editable && ed.palette.length > 0 && <div className="mt-3"><AddStep ed={ed} /></div>}
      {head('Always')}
      {guard}
    </div>
  )
  if (!wide) return vertical
  return (
    <>
      <div className="hidden lg:block"><Lanes spec={spec} run={run} before={before} ed={ed} /></div>
      <div className="lg:hidden">{vertical}</div>
    </>
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
