import type { Card, State } from '@/lib/api'
import type { Cond, Spec } from './types'

// How many open cases pass each stage of a routine right now: the trigger's cases, then what is
// left after every condition. It mirrors the compiled `holds()` in routines.py on the cards the
// page already has, so the diagram can say why nothing would run ("stops at line 3"). Fields the
// page cannot see (hours since our email, shipment silence) return null: no funnel then.

export interface Funnel {
  /** Cases the trigger looks at now. */
  total: number
  /** Cases left after each condition, by condition id. */
  left: Map<string, number>
  /** Cases that pass every condition. */
  pass: number
  /** The first condition nothing gets past, when some got that far. */
  stop: { id: string; index: number; reached: number } | null
}

const hoursLeft = (c: Card, now: string) => {
  if (!c.deadline) return null
  const ms = Date.parse(c.deadline) - Date.parse(now)
  return Number.isFinite(ms) ? Math.round(ms / 360000) / 10 : null
}

function text(c: Card, field: Cond['field'] | 'edition' | 'part_name'): string {
  if (field === 'evidence') return (c.evidence ?? []).map((e) => `${e.source ?? ''} ${e.value ?? e.quote ?? ''}`).join(' ')
  if (field === 'option_action') return (c.options ?? []).map((o) => o.action ?? '').join(' ')
  if (field === 'option_to') return (c.options ?? []).map((o) => o.to ?? '').join(' ')
  const v = (c as unknown as Record<string, unknown>)[field]
  return typeof v === 'string' ? v : ''
}

/** One condition on one card; undefined when the page cannot tell. */
function holds(c: Card, cond: Cond, now: string): boolean | undefined {
  const value = String(cond.value).toLowerCase()
  if (cond.op === 'lt' || cond.op === 'gt') {
    if (cond.field !== 'hours_left') return undefined
    const n = hoursLeft(c, now)
    if (n === null) return false
    return cond.op === 'lt' ? n < Number(value) : n > Number(value)
  }
  if (cond.field === 'has_reply') return !!c.ui?.reply === (value === 'yes')
  if (cond.field === 'reply_after_launch') return (c.ui?.reply_after_launch === true) === (value === 'yes')
  const t = text(c, cond.field).toLowerCase()
  if (cond.op === 'is') return t === value
  if (cond.op === 'any_of') return value.split(',').some((v) => v.trim() && t.includes(v.trim()))
  return t.includes(value)
}

function cases(spec: Spec, state: State): Card[] | null {
  const on = spec.on ?? 'decisions'
  if (on === 'decisions') {
    const inbox = new Set(state.inbox_ids)
    return state.cards.filter((c) => inbox.has(c.id) && c.status === 'open')
  }
  if (on === 'replies') return state.cards.filter((c) => c.status === 'sent' && !!c.ui?.reply)
  if (on === 'waiting') return state.cards.filter((c) => c.status === 'sent' && !c.ui?.reply)
  return null
}

export function funnel(spec: Spec, state?: State | null): Funnel | null {
  if (!state) return null
  let left = cases(spec, state)
  if (!left) return null
  const total = left.length
  const out = new Map<string, number>()
  let stop: Funnel['stop'] = null
  for (const [index, cond] of spec.when.entries()) {
    const next: Card[] = []
    for (const c of left) {
      const ok = holds(c, cond, state.now)
      if (ok === undefined) return null
      if (ok) next.push(c)
    }
    if (!stop && left.length > 0 && next.length === 0) stop = { id: cond.id, index, reached: left.length }
    left = next
    out.set(cond.id, left.length)
  }
  return { total, left: out, pass: left.length, stop }
}

const STOP = new Set('a an the and or of to for from in on at by with that this is are be can now its it s'.split(' '))
const words = (t: string) =>
  new Set(t.toLowerCase().replace(/[’']s\b/g, '').split(/[^a-z0-9]+/).filter((w) => w.length > 1 && !STOP.has(w)))

/**
 * Conditions that say nearly the same thing (a drafting model likes to repeat itself): the later
 * one of each pair, with the id of the line it repeats. Same field and a fair word overlap, or a
 * large overlap on any field.
 */
export function lookAlikeConds(conds: Cond[], wordsOf: (c: Cond) => string): Map<string, string> {
  const out = new Map<string, string>()
  conds.forEach((b, j) => {
    for (const a of conds.slice(0, j)) {
      if (out.has(a.id)) continue
      const wa = words(wordsOf(a)), wb = words(wordsOf(b))
      const shared = [...wa].filter((w) => wb.has(w)).length
      const jac = shared / Math.max(1, new Set([...wa, ...wb]).size)
      if ((a.field === b.field && jac >= 0.3) || jac >= 0.6) { out.set(b.id, a.id); break }
    }
  })
  return out
}
