import { BellRing, MailWarning, MessageSquare, NotebookPen, PackageSearch, Phone, Send, Smartphone } from 'lucide-react'
import type { Channel, Step } from './types'

// Step icons and the before/after merge the routine diagram (FlowGraph) uses to show what a
// change does, node by node.

export type Mark = 'same' | 'added' | 'removed' | 'changed'
export interface Row<T> { item: T; mark: Mark; old?: T; moved?: boolean }

/** Before and after merged by block id, removed blocks kept at their old place. */
export function merge<T extends { id: string; text: string }>(after: T[], before?: T[]): Row<T>[] {
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

export function StepIcon({ step, size = 16, className }: { step: Pick<Step, 'do' | 'channel'>; size?: number; className?: string }) {
  const c = className ?? 'text-ink-3'
  if (step.do === 'carry_out') return <Send size={size} strokeWidth={1.75} className={c} />
  if (step.do === 'log') return <NotebookPen size={size} strokeWidth={1.75} className={c} />
  if (step.do === 'chase') return <MailWarning size={size} strokeWidth={1.75} className={c} />
  if (step.do === 'ask_status') return <PackageSearch size={size} strokeWidth={1.75} className={c} />
  if (step.do === 'call') return <Phone size={size} strokeWidth={1.75} className={c} />
  return <ChannelIcon channel={step.channel} size={size} className={className} />
}

export function ChannelIcon({ channel, size = 16, className = 'text-ink-3' }: { channel?: Channel; size?: number; className?: string }) {
  if (channel === 'phone') return <Smartphone size={size} strokeWidth={1.75} className={className} />
  if (channel === 'teams') return <MessageSquare size={size} strokeWidth={1.75} className={className} />
  return <BellRing size={size} strokeWidth={1.75} className={className} />
}

