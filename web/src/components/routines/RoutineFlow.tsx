import { BellRing, GripVertical, Lock, MailWarning, MessageSquare, NotebookPen, PackageSearch, Phone, Plus, Radar, Send, Smartphone, X } from 'lucide-react'
import { useState, type DragEvent, type ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { PALETTE, condWords, paletteKey, triggerText, type Channel, type Cond, type Spec, type Step } from './types'

// One routine as blocks a buyer reads at a glance: WHEN (conditions), THEN (steps in order) and
// the fixed ALWAYS guard. With `before` it shows what a change does, block by block. With
// `onChange` the buyer edits directly: × removes, drag reorders, the palette adds steps.

type Mark = 'same' | 'added' | 'removed' | 'changed'
interface Row<T> { item: T; mark: Mark; old?: T; moved?: boolean }

/** Before and after merged by block id, removed blocks kept at their old place. */
function merge<T extends { id: string; text: string }>(after: T[], before?: T[]): Row<T>[] {
  if (!before) return after.map((item) => ({ item, mark: 'same' }))
  const old = new Map(before.map((b) => [b.id, b]))
  const now = new Set(after.map((a) => a.id))
  const commonBefore = before.filter((b) => now.has(b.id)).map((b) => b.id)
  const commonAfter = after.filter((a) => old.has(a.id)).map((a) => a.id)
  // Removed blocks hang after the last surviving block that preceded them.
  const anchored = new Map<string | null, T[]>()
  let anchor: string | null = null
  for (const b of before) {
    if (now.has(b.id)) { anchor = b.id; continue }
    anchored.set(anchor, [...(anchored.get(anchor) ?? []), b])
  }
  const gone = (key: string | null) => (anchored.get(key) ?? []).map((item): Row<T> => ({ item, mark: 'removed' }))
  const rows: Row<T>[] = gone(null)
  for (const a of after) {
    const b = old.get(a.id)
    const mark: Mark = !b ? 'added' : JSON.stringify(b) === JSON.stringify(a) ? 'same' : 'changed'
    const moved = !!b && commonBefore.indexOf(a.id) !== commonAfter.indexOf(a.id)
    rows.push({ item: a, mark, old: b, moved })
    rows.push(...gone(a.id))
  }
  return rows
}

function Tag({ mark, moved }: { mark: Mark; moved?: boolean }) {
  if (mark === 'added') return <span className="shrink-0 rounded bg-ok px-1.5 py-0.5 text-[11px] font-bold uppercase tracking-wide text-white">New</span>
  if (mark === 'removed') return <span className="shrink-0 text-[11px] font-bold uppercase tracking-wide text-stop">Removed</span>
  if (mark === 'changed') return <span className="shrink-0 text-[11px] font-bold uppercase tracking-wide text-ok">Changed</span>
  if (moved) return <span className="shrink-0 text-[11px] font-bold uppercase tracking-wide text-ok">Moved</span>
  return null
}

function Phrase<T extends { text: string }>({ row, words = (x) => x.text }: { row: Row<T>; words?: (x: T) => string }) {
  const now = words(row.item)
  const was = row.old ? words(row.old) : now
  if (row.mark === 'changed' && was !== now) {
    return (
      <span>
        <span className="text-ink-3 line-through">{was}</span>{' '}
        <span>{now}</span>
      </span>
    )
  }
  return <span className={cn(row.mark === 'removed' && 'line-through')}>{now}</span>
}

const markClass = (m: Mark) =>
  m === 'added' ? 'bg-ok/5 ring-1 ring-ok/40' : m === 'removed' ? 'opacity-60' : m === 'changed' ? 'ring-1 ring-ok/40' : ''

export function StepIcon({ step, size = 16, className }: { step: Pick<Step, 'do' | 'channel'>; size?: number; className?: string }) {
  if (step.do === 'carry_out') return <Send size={size} className={className ?? 'text-ok'} />
  if (step.do === 'log') return <NotebookPen size={size} className={className ?? 'text-ink-3'} />
  if (step.do === 'chase') return <MailWarning size={size} className={className ?? 'text-ok'} />
  if (step.do === 'ask_status') return <PackageSearch size={size} className={className ?? 'text-ok'} />
  // A call reaches a person: it carries the routine colour wherever it shows.
  if (step.do === 'call') return <Phone size={size} className={className ?? 'text-routine'} />
  return <ChannelIcon channel={step.channel} size={size} />
}

export function ChannelIcon({ channel, size = 16 }: { channel?: Channel; size?: number }) {
  if (channel === 'phone') return <Smartphone size={size} className="text-ok" />
  if (channel === 'teams') return <MessageSquare size={size} className="text-ok" />
  return <BellRing size={size} className="text-ok" />
}

const Connector = () => <div aria-hidden className="ml-6 h-3 w-px bg-line" />

function Block({ label, tone, children, className }: { label: string; tone?: 'ok'; children: ReactNode; className?: string }) {
  return (
    <div className={cn('rounded-lg border border-line bg-white px-3 py-2.5', className)}>
      <div className={cn('mb-2 text-xs font-bold uppercase tracking-wide', tone === 'ok' ? 'text-ok' : 'text-ink-3')}>{label}</div>
      {children}
    </div>
  )
}

function RemoveButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className="grid h-5 w-5 shrink-0 cursor-pointer place-items-center rounded text-ink-3 opacity-0 transition-opacity hover:bg-stop/10 hover:text-stop focus-visible:opacity-100 group-hover:opacity-100"
    >
      <X size={13} />
    </button>
  )
}

type Drag = { kind: 'step'; id: string } | { kind: 'palette'; key: string }

export function RoutineFlow({
  spec, before, onChange, disabled,
}: { spec: Spec; before?: Spec; onChange?: (spec: Spec) => void; disabled?: boolean }) {
  const editable = !!onChange && !disabled
  const when = merge<Cond>(spec.when, before?.when)
  const then = merge<Step>(spec.then, before?.then)
  const [drag, setDrag] = useState<Drag | null>(null)
  const [dropAt, setDropAt] = useState<number | null>(null)
  const present = new Set(spec.then.map(paletteKey).filter(Boolean))
  const palette = PALETTE.filter((p) => !present.has(p.key))

  const change = (next: Partial<Spec>) => onChange?.({ ...spec, ...next })
  const removeCond = (id: string) => change({ when: spec.when.filter((c) => c.id !== id) })
  const removeStep = (id: string) => change({ then: spec.then.filter((s) => s.id !== id) })
  const addStep = (key: string, at = spec.then.length) => {
    const p = PALETTE.find((x) => x.key === key)
    if (!p) return
    const steps = [...spec.then]
    steps.splice(at, 0, p.make())
    change({ then: steps })
  }

  function readDrag(e: DragEvent): Drag | null {
    if (drag) return drag
    try { return JSON.parse(e.dataTransfer.getData('text/plain')) as Drag } catch { return null }
  }
  function start(e: DragEvent, d: Drag) {
    e.dataTransfer.setData('text/plain', JSON.stringify(d))
    e.dataTransfer.effectAllowed = d.kind === 'step' ? 'move' : 'copy'
    setDrag(d)
  }
  function end() { setDrag(null); setDropAt(null) }
  function over(e: DragEvent, index: number) {
    if (!editable) return
    e.preventDefault()
    e.stopPropagation() // the list's own handler would move the drop point to the end
    const box = (e.currentTarget as HTMLElement).getBoundingClientRect()
    setDropAt(e.clientY < box.top + box.height / 2 ? index : index + 1)
  }
  function drop(e: DragEvent) {
    e.preventDefault()
    const d = readDrag(e)
    const at = dropAt ?? spec.then.length
    end()
    if (!d || !editable) return
    if (d.kind === 'palette') return addStep(d.key, at)
    const from = spec.then.findIndex((s) => s.id === d.id)
    if (from < 0) return
    const steps = [...spec.then]
    const [moved] = steps.splice(from, 1)
    steps.splice(at > from ? at - 1 : at, 0, moved)
    if (steps.some((s, i) => s.id !== spec.then[i].id)) change({ then: steps })
  }

  // Position of each live step in `spec.then`, for drop targets (removed rows have none).
  const indexOf = new Map(spec.then.map((s, i) => [s.id, i]))
  const indicator = <div aria-hidden className="my-0.5 h-0.5 rounded-full bg-ok" />

  return (
    <div>
      <Block label="When">
        <div className="mb-2 flex items-center gap-1.5 text-sm font-medium text-ink">
          <Radar size={14} className="shrink-0 text-ok" />
          {triggerText(spec.on).title}
          {before && before.on !== spec.on && <Tag mark="changed" />}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {when.map((row) => (
            <span
              key={row.item.id}
              className={cn(
                'group inline-flex max-w-full items-start gap-1.5 rounded-md bg-panel px-2 py-1 text-sm leading-snug text-ink ring-1 ring-line',
                markClass(row.mark),
              )}
            >
              <Phrase row={row} words={condWords} />
              <Tag mark={row.mark} moved={false} />
              {editable && row.mark !== 'removed' && (
                <RemoveButton label="Remove this condition" onClick={() => removeCond(row.item.id)} />
              )}
            </span>
          ))}
        </div>
      </Block>
      <Connector />
      <Block label="Then" tone="ok">
        <ol
          className="space-y-1"
          onDragOver={(e) => { if (editable) { e.preventDefault(); if (dropAt == null) setDropAt(spec.then.length) } }}
          onDrop={drop}
          onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDropAt(null) }}
        >
          {then.map((row) => {
            const i = indexOf.get(row.item.id)
            const live = row.mark !== 'removed' && i != null
            return (
              <li key={row.item.id}>
                {live && dropAt === i && drag && indicator}
                <div
                  draggable={editable && live}
                  onDragStart={(e) => start(e, { kind: 'step', id: row.item.id })}
                  onDragEnd={end}
                  onDragOver={(e) => live && over(e, i)}
                  className={cn(
                    'group flex items-start gap-2 rounded-md px-1.5 py-1.5 text-[15px] leading-snug',
                    markClass(row.mark),
                    editable && live && 'cursor-grab hover:bg-panel active:cursor-grabbing',
                    row.item.do === 'call' && row.mark === 'same' && 'bg-routine/5 ring-1 ring-routine/25',
                    drag?.kind === 'step' && drag.id === row.item.id && 'opacity-40',
                  )}
                >
                  {editable && (
                    <GripVertical size={14} className={cn('mt-0.5 shrink-0 text-ink-3 opacity-0', live && 'group-hover:opacity-100')} />
                  )}
                  <span className="mt-0.5 shrink-0"><StepIcon step={row.item} /></span>
                  <span className="min-w-0 flex-1">
                    <Phrase row={row} />
                    {row.item.do === 'call' && <span className="mt-0.5 block text-xs font-semibold text-routine">Rings a person</span>}
                  </span>
                  <Tag mark={row.mark} moved={row.moved} />
                  {editable && live && <RemoveButton label="Remove this step" onClick={() => removeStep(row.item.id)} />}
                </div>
              </li>
            )
          })}
          {dropAt === spec.then.length && drag && <li>{indicator}</li>}
        </ol>
        {editable && palette.length > 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5 border-t border-dashed border-line pt-2">
            <span className="mr-0.5 text-xs text-ink-3">Add a step</span>
            {palette.map((p) => (
              <button
                key={p.key}
                type="button"
                draggable
                onDragStart={(e) => start(e, { kind: 'palette', key: p.key })}
                onDragEnd={end}
                onClick={() => addStep(p.key)}
                title="Click to add, or drag it into place"
                className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-dashed border-ok/50 bg-white px-2.5 py-1 text-[13px] font-medium text-ink-2 hover:border-ok hover:bg-ok/5 hover:text-ink"
              >
                <Plus size={12} className="text-ok" />
                <StepIcon step={p.make()} size={13} />
                {p.label}
              </button>
            ))}
          </div>
        )}
      </Block>
      <Connector />
      <div className="flex items-start gap-2.5 rounded-lg border border-dashed border-line bg-panel px-3 py-2.5">
        <Lock size={15} className="mt-0.5 shrink-0 text-ink-3" />
        <div>
          <div className="text-xs font-bold uppercase tracking-wide text-ink-3">Always</div>
          <div className="mt-0.5 text-[15px] text-ink-2">Costs money or changes the plan? It comes to you.</div>
        </div>
      </div>
    </div>
  )
}
