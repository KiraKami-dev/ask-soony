import { Filter, Lock, Plus, Radar, X } from 'lucide-react'
import { useEffect, useRef, useState, type DragEvent, type ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { StepIcon, merge, type Mark, type Row } from './RoutineFlow'
import { PALETTE, condWords, paletteKey, triggerText, type Cond, type Run, type Spec, type Step } from './types'

// The routine as a flow graph (n8n style), drawn from the recipe alone, and edited in place:
// WHEN (trigger, then every condition) and THEN (the steps) as two lanes, with the fixed guard
// hanging below the step that sends (or in its own ALWAYS lane). With `onChange` the buyer edits
// it here: × on a condition or step removes it, steps drag to a new place, "+ add" at the end
// of the Then lane adds one. With `before` every node shows what a change does. Counters come
// from the latest run; a test run's counts stay ink-3, a real run's read in routine green.
// Below the `lg` breakpoint (and without `wide`) it is one vertical path.

const REFUSED = 'Needs you, it costs money or changes the plan'

interface Counts { matched: number; byStep: Map<string, string>; guard: number }

function count(spec: Spec, run?: Run): Counts | null {
  if (!run || !run.ok) return null
  const did = run.did ?? []
  const byStep = new Map<string, string>()
  const sent = did.filter((d) => d.do === 'carry_out').length
  const refused = did.filter((d) => d.do === 'notify' && (d.text ?? '').startsWith(REFUSED)).length
  const told = did.filter((d) => d.do === 'notify' && !(d.text ?? '').startsWith(REFUSED))
  const sendTexts = spec.then.filter((s) => s.do === 'carry_out').map((s) => s.text)
  const noted = (run.log ?? []).filter((l) => l && !sendTexts.some((t) => l.startsWith(t))).length
  for (const s of spec.then) {
    if (s.do === 'carry_out' && sent) byStep.set(s.id, `${sent} sent`)
    if (s.do === 'notify') {
      const n = told.filter((d) => d.channel === (s.channel ?? 'both')).length
      if (n) byStep.set(s.id, `${n} told`)
    }
    if (s.do === 'log' && noted) byStep.set(s.id, `${noted} noted`)
    const did_ = (k: Step['do']) => did.filter((d) => d.do === k).length
    if (s.do === 'chase' && did_('chase')) byStep.set(s.id, `${did_('chase')} chased`)
    if (s.do === 'ask_status' && did_('ask_status')) byStep.set(s.id, `${did_('ask_status')} asked`)
    if (s.do === 'call' && did_('call')) byStep.set(s.id, `${did_('call')} called`)
  }
  const handled = typeof run.result === 'number' ? run.result : new Set(did.map((d) => d.card)).size
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

const MARK_WORD: Record<Mark, string | null> = { same: null, added: 'new', removed: 'removed', changed: 'changed' }

/** Where a dragged step would land, shown as an accent edge on the node it is dropped on. */
type Edge = 'before' | 'after' | null

/**
 * One node: a soft tile with a small ink-3 icon, the title and its words. No border and no
 * shadow; the guard alone is a dashed hairline.
 */
function Node({
  icon, title, text, was, badge, active, dashed, wide, person, mark, moved, onRemove, edge, axis,
}: {
  icon: ReactNode; title: string; text: string; badge?: string; active?: boolean; dashed?: boolean; wide?: boolean
  /** The text before a change, struck through. */
  was?: string
  person?: boolean
  mark?: Mark
  moved?: boolean
  onRemove?: () => void
  edge?: Edge
  axis?: 'x' | 'y'
}) {
  const word = mark ? MARK_WORD[mark] ?? (moved ? 'moved' : null) : null
  const edgeShadow =
    edge && axis === 'x' ? (edge === 'before' ? 'shadow-[inset_3px_0_0_var(--color-accent)]' : 'shadow-[inset_-3px_0_0_var(--color-accent)]')
    : edge ? (edge === 'before' ? 'shadow-[inset_0_3px_0_var(--color-accent)]' : 'shadow-[inset_0_-3px_0_var(--color-accent)]')
    : ''
  return (
    <div
      className={cn(
        'group relative w-full min-w-0 rounded-xl px-3.5 py-3',
        onRemove && 'max-lg:pr-11',
        wide && 'lg:w-[236px] lg:shrink-0',
        dashed ? 'border border-dashed border-ink-3/45' : 'bg-soft',
        mark === 'added' && 'bg-accent/[.07]',
        mark === 'removed' && 'opacity-55',
        edgeShadow,
      )}
      title={text}
    >
      <div className="flex items-baseline justify-between gap-2">
        <div className="t-small flex min-w-0 items-center gap-1.5 font-medium text-ink">
          <span className="shrink-0 text-ink-3 [&>svg]:block">{icon}</span>
          <span className={cn('line-clamp-2', mark === 'removed' && 'line-through')}>{title}</span>
        </div>
        {word ? (
          <span className={cn('t-small shrink-0', mark === 'added' ? 'font-medium text-accent' : 'text-ink-3')}>{word}</span>
        ) : badge ? (
          <span className={cn('t-small shrink-0 whitespace-nowrap', active ? 'dot-routine text-routine' : 'text-ink-3')}>{badge}</span>
        ) : null}
      </div>
      <div className={cn('t-small mt-1 line-clamp-3 text-ink-2', wide && 'lg:line-clamp-4', mark === 'removed' && 'line-through')}>
        {was && was !== text && <><span className="text-ink-3 line-through">{was}</span>{' '}</>}
        {text}
      </div>
      {person && <div className="t-small mt-1 text-ink-3">Rings a person</div>}
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove “${title}”`}
          title="Remove"
          className="absolute right-2 top-2.5 grid h-6 w-6 lg:-right-2 lg:-top-2 cursor-pointer place-items-center rounded-full bg-ground text-ink-3 shadow-[0_0_0_1px_var(--color-hair)] hover:text-ink focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-accent lg:opacity-0 lg:group-focus-within:opacity-100 lg:group-hover:opacity-100"
        >
          <X size={13} />
        </button>
      )}
    </div>
  )
}

const stroke = (dashed?: boolean) => ({ stroke: 'currentColor', strokeWidth: 1.25, fill: 'none', strokeDasharray: dashed ? '3 3' : undefined })
const Down = ({ dashed }: { dashed?: boolean }) => (
  <svg width="10" height="22" viewBox="0 0 10 22" aria-hidden className="block"><line x1="5" y1="0" x2="5" y2="20" {...stroke(dashed)} /><path d="M1.5 16 L5 21 L8.5 16" {...stroke()} /></svg>
)
const Right = ({ dashed }: { dashed?: boolean }) => (
  <svg width="28" height="10" viewBox="0 0 28 10" aria-hidden className="block"><line x1="0" y1="5" x2="26" y2="5" {...stroke(dashed)} /><path d="M22 1.5 L27 5 L22 8.5" {...stroke()} /></svg>
)

/** A thin line with a small arrow down; a side branch runs right (or down on a phone). */
function Arrow({ dashed, side }: { dashed?: boolean; side?: boolean }) {
  const body = side ? (
    <>
      <div className="ml-[26px] sm:hidden"><Down dashed={dashed} /></div>
      <div className="mt-[19px] hidden sm:block"><Right dashed={dashed} /></div>
    </>
  ) : <div className="ml-[26px]"><Down dashed={dashed} /></div>
  return <div className="shrink-0 text-ink-3/50">{body}</div>
}

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

/** "+ add" at the end of the Then lane: the palette as a short quiet menu. */
function AddStep({ ed, wide }: { ed: Editor; wide?: boolean }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as globalThis.Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); setOpen(false) } }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc, true)
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', esc, true) }
  }, [open])
  if (!ed.editable || !ed.palette.length) return null
  return (
    <div ref={ref} className={cn('relative', wide && 'ml-3 mt-[9px]')}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-haspopup="menu"
        className="t-small inline-flex h-9 cursor-pointer items-center gap-1.5 rounded-xl border border-dashed border-ink-3/45 px-3 text-ink-2 hover:text-ink focus-visible:outline-2 focus-visible:outline-accent"
      >
        <Plus size={13} /> add
      </button>
      {open && (
        <ul className="fade absolute left-0 top-11 z-10 w-[240px] rounded-xl bg-ground p-1.5 shadow-[0_12px_40px_-12px_rgba(0,0,0,.3),0_0_0_1px_var(--color-hair)]" role="menu">
          {ed.palette.map((p) => (
            <li key={p.key}>
              <button
                type="button"
                role="menuitem"
                draggable
                onDragStart={(e) => { e.dataTransfer.setData('text/plain', JSON.stringify({ kind: 'palette', key: p.key })); ed.setDrag({ kind: 'palette', key: p.key }) }}
                onDragEnd={ed.end}
                onClick={() => { ed.addStep(p.key); setOpen(false) }}
                title="Click to add at the end, or drag it into place"
                className="t-small flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-ink-2 hover:bg-soft hover:text-ink"
              >
                <StepIcon step={p.make()} size={14} />
                {p.label}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

interface StepNode { key: string; sends: boolean; drag: DragProps; dragging: boolean; node: ReactNode }

function nodesFor(spec: Spec, run: Run | undefined, wide: boolean, before: Spec | undefined, ed: Editor, axis: 'x' | 'y') {
  const c = count(spec, run)
  const guard = (
    <Node
      wide={wide}
      dashed
      icon={<Lock size={14} strokeWidth={1.75} />}
      title="You decide"
      text="Costs money or changes the plan? It comes to you."
      badge={c?.guard ? `${c.guard} to you` : undefined}
      active={!!c?.guard && !run?.dry}
    />
  )
  const trigger = (
    <Node
      wide={wide}
      icon={<Radar size={14} strokeWidth={1.75} />}
      title={triggerText(spec.on).title}
      text={triggerText(spec.on).text}
      mark={before && before.on !== spec.on ? 'changed' : undefined}
      badge={run?.dry ? 'Test run' : undefined}
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
          title={first ? 'If' : 'And'}
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
    return {
      key: s.id,
      sends: s.do === 'carry_out' && isLive,
      drag: isLive ? ed.stepDrag(s.id, axis) : {},
      dragging: ed.drag?.kind === 'step' && ed.drag.id === s.id,
      node: (
        <Node
          wide={wide}
          icon={<StepIcon step={s} size={14} className="text-ink-3" />}
          person={s.do === 'call'}
          title={stepTitle(s)}
          text={s.text}
          was={row.mark === 'changed' && row.old ? row.old.text : undefined}
          mark={row.mark}
          moved={row.moved}
          badge={badge}
          active={!!badge && !run?.dry}
          edge={isLive ? ed.edgeOf(s.id) : null}
          axis={axis}
          onRemove={ed.editable && isLive ? () => ed.removeStep(s.id) : undefined}
        />
      ),
    }
  })
  return { guard, trigger, conditions, steps }
}

const grab = (s: { drag: DragProps; dragging: boolean }) => cn('min-w-0', s.drag.draggable && 'cursor-grab active:cursor-grabbing', s.dragging && 'opacity-40')

/**
 * The wide view reads like a sentence in two lanes: WHEN (trigger, then every condition) left to
 * right, then a turn down into THEN (the steps) left to right. Lanes wrap instead of running off
 * the edge; the guard hangs below the step it protects, or sits in its own ALWAYS lane.
 */
function Lanes({ spec, run, before, ed }: { spec: Spec; run?: Run; before?: Spec; ed: Editor }) {
  const { guard, trigger, conditions, steps } = nodesFor(spec, run, true, before, ed, 'x')
  const hasSend = steps.some((s) => s.sends)
  const arrow = <div className="mt-[19px] shrink-0 px-1 text-ink-3/50"><Right /></div>
  const lane = (label: string, body: ReactNode, last = false) => (
    <div className="grid grid-cols-[56px_minmax(0,1fr)] gap-x-3">
      <div className="flex flex-col items-center pt-[12px]">
        <span className="t-small text-ink-3">{label}</span>
        {!last && (
          <div className="mt-2 flex flex-1 flex-col items-center text-ink-3/50">
            <div className="w-px flex-1 bg-current" />
            <svg width="10" height="7" viewBox="0 0 10 7" aria-hidden className="block"><path d="M1.5 1 L5 6 L8.5 1" {...stroke()} /></svg>
          </div>
        )}
      </div>
      <div className="flex min-w-0 flex-wrap items-start gap-y-5 pb-7">{body}</div>
    </div>
  )
  return (
    <div className="flex flex-col">
      {lane('When', [{ key: 'trigger', node: trigger }, ...conditions].map((it, i) => (
        <div key={it.key} className="flex items-start">{i > 0 && arrow}{it.node}</div>
      )))}
      {lane(
        'Then',
        <>
          {steps.map((s, i) => (
            <div key={s.key} className="flex items-start">
              {i > 0 && arrow}
              {s.sends ? (
                <div className="flex flex-col">
                  <div {...s.drag} className={grab(s)}>{s.node}</div>
                  <div className="ml-[26px] text-ink-3/50"><Down dashed /></div>
                  {guard}
                </div>
              ) : (
                <div {...s.drag} className={grab(s)}>{s.node}</div>
              )}
            </div>
          ))}
          <AddStep ed={ed} wide />
        </>,
        hasSend,
      )}
      {!hasSend && lane('Always', guard, true)}
    </div>
  )
}

export function FlowGraph({
  spec, run, wide, before, onChange, disabled,
}: {
  spec: Spec
  run?: Run
  /** Two lanes on a large screen; one vertical path below `lg` either way. */
  wide?: boolean
  /** The version before a change: every node shows what the change does. */
  before?: Spec
  /** Edit in place: remove, reorder, add. Each edit is a new spec for the caller to test. */
  onChange?: (spec: Spec) => void
  disabled?: boolean
}) {
  const ed = useEditor(spec, onChange, disabled)
  const { guard, trigger, conditions, steps } = nodesFor(spec, run, false, before, ed, 'y')
  const sendAt = steps.findIndex((s) => s.sends)
  // Narrow: one vertical path; conditions stack (they all have to hold); the step that sends
  // carries the guard as a side branch.
  const columns: { key: string; node: ReactNode }[] = [
    { key: 'trigger', node: trigger },
    {
      key: 'when',
      node: (
        <div className="flex flex-col">
          {conditions.map((cond, i) => (
            <div key={cond.key}>
              {i > 0 && <Arrow />}
              {cond.node}
            </div>
          ))}
        </div>
      ),
    },
    ...steps.map((s, i) => {
      const node = <div {...s.drag} className={grab(s)}>{s.node}</div>
      if (i !== sendAt) return { key: s.key, node }
      return {
        key: s.key,
        node: (
          <div className="flex flex-col sm:flex-row sm:items-start">
            <div className="min-w-0 sm:flex-[3]">{node}</div>
            <Arrow side dashed />
            <div className="min-w-0 pl-6 sm:flex-[2] sm:pl-0">{guard}</div>
          </div>
        ),
      }
    }),
  ]
  if (sendAt < 0) columns.push({ key: 'guard', node: guard })
  const vertical = (
    <div className="flex flex-col">
      {columns.map((col, i) => (
        <div key={col.key} className="flex min-w-0 flex-col">
          {i > 0 && <Arrow dashed={col.key === 'guard'} />}
          {col.node}
        </div>
      ))}
      {ed.editable && ed.palette.length > 0 && <div className="mt-4"><AddStep ed={ed} /></div>}
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
