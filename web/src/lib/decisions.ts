// What the main screen derives from /api/state: the inbox sections, near-identical cards grouped
// into one row, the one-sentence launch verdict and the countdown to the trucks. Nothing here
// knows about a specific supplier, part or number: it all comes from the cards and the strip.

import {
  DECIDED, LEVEL_RANK, fmtDay, fmtTime, tidy, viaOf, type ActivityItem, type Card, type State, type StripItem,
} from '@/lib/api'

/** Several cards with the same problem (same supplier, same headline once numbers are removed). */
export interface Group {
  key: string
  cards: Card[]
  /** The card shown for the group: the most urgent one. */
  lead: Card
}

const shape = (t: string) => tidy(t).replace(/\d[\d,.' ]*\d|\d/g, '#').replace(/\s+/g, ' ').trim().toLowerCase()
export const groupKey = (c: Card) => `${c.supplier_id ?? c.supplier_name ?? ''}|${c.kind ?? ''}|${shape(c.headline)}`

const deadlineMs = (c: Card) => (c.deadline ? new Date(c.deadline).getTime() : Number.POSITIVE_INFINITY)
/** Most urgent first: earliest decide-by time, then the worse level, then the bigger number. */
export const byUrgency = (a: Card, b: Card) =>
  deadlineMs(a) - deadlineMs(b) || LEVEL_RANK[b.level] - LEVEL_RANK[a.level] || (b.number ?? 0) - (a.number ?? 0)

export function groupCards(cards: Card[]): Group[] {
  const map = new Map<string, Card[]>()
  for (const c of [...cards].sort(byUrgency)) {
    const k = groupKey(c)
    map.set(k, [...(map.get(k) ?? []), c])
  }
  return [...map.entries()].map(([key, cs]) => ({ key, cards: cs, lead: cs[0] }))
}

/** "PO 4500318774, 4500318765 and 4500318786": what differs between the cards of a group. */
export function sameIssueOn(g: Group): string | null {
  if (g.cards.length < 2) return null
  const first = tidy(g.lead.headline)
  const firstTokens = new Set(first.match(/\d[\d,.]*\d|\d/g) ?? [])
  const lead = [...firstTokens].find((t) => t.replace(/\D/g, '').length >= 4) ?? [...firstTokens][0]
  // The word in front of the lead's own number ("PO") names what the others are.
  const word = lead ? first.slice(0, first.indexOf(lead)).trim().split(/\s+/).pop() : undefined
  const others = g.cards.slice(1).map((c) => {
    const toks = tidy(c.headline).match(/\d[\d,.]*\d|\d/g) ?? []
    return toks.find((t) => !firstTokens.has(t)) ?? tidy(c.headline)
  })
  const list = others.length > 1 ? `${others.slice(0, -1).join(', ')} and ${others[others.length - 1]}` : others[0]
  return word && /^[A-Za-z]{1,12}$/.test(word) ? `${word} ${list}` : list
}

export const routineOf = (state: State, cardId: string): string | null => {
  const a = state.activity.find((x) => x.card === cardId && viaOf(x)?.kind === 'routine')
  const v = a ? viaOf(a) : null
  return v?.kind === 'routine' ? v.name : null
}

export interface Handled {
  key: string
  card?: Card
  text: string
  at?: string
  routine: string | null
  status?: string
}

export interface Sections {
  needs: Group[]
  waiting: Card[]
  /** Decided by a routine (and routine actions without a card). */
  handled: Handled[]
  /** Decided by a person. */
  done: Card[]
}

export function sections(state: State): Sections {
  const open = state.cards.filter((c) => c.status === 'open')
  const needs = groupCards(open.filter((c) => !c.routed_to_other))
  const waiting = state.cards
    .filter((c) => (c.routed_to_other && c.status === 'open') || c.status === 'escalated')
    .sort(byUrgency)
  const decided = state.cards.filter((c) => DECIDED.includes(c.status) && c.status !== 'escalated')
  const handled: Handled[] = []
  const done: Card[] = []
  for (const c of decided) {
    const r = routineOf(state, c.id)
    if (r) {
      const a = state.activity.find((x) => x.card === c.id && viaOf(x)?.kind === 'routine')
      handled.push({ key: c.id, card: c, text: c.headline, at: a?.at ?? c.ui?.at, routine: r, status: c.status })
    } else done.push(c)
  }
  // Routine work that is not tied to a card on screen still counts as handled.
  state.activity.forEach((a: ActivityItem, i) => {
    const v = viaOf(a)
    if (v?.kind !== 'routine' || (a.card && state.cards.some((c) => c.id === a.card))) return
    handled.push({ key: `act-${i}`, text: a.text, at: a.at, routine: v.name })
  })
  const t = (x?: string) => (x ? new Date(x).getTime() : 0)
  handled.sort((a, b) => t(b.at) - t(a.at))
  done.sort((a, b) => t(b.ui?.at) - t(a.ui?.at))
  return { needs, waiting, handled, done }
}

// ---------------------------------------------------------------- clock

/** The launch moment (trucks leave): from the server's packing clock when it sends one. */
export function launchAt(state: State): string | null {
  const v = state.clock?.launch
  return v && !Number.isNaN(Date.parse(v)) ? v : null
}

/** "6 h 12 min", "1 d 4 h", "12 min". */
export function span(ms: number): string {
  const min = Math.max(0, Math.round(ms / 60000))
  if (min < 60) return `${min} min`
  const h = Math.floor(min / 60)
  if (h >= 24) return `${Math.floor(h / 24)} d ${h % 24} h`
  return `${h} h ${String(min % 60).padStart(2, '0')} min`
}

/** "10:00" today, "Mon 10:00" on another day. */
export function when(iso: string | null | undefined, nowMs: number): string {
  if (!iso) return ''
  return fmtDay(iso) === fmtDay(new Date(nowMs).toISOString()) ? fmtTime(iso) : `${fmtDay(iso)} ${fmtTime(iso)}`
}

// ---------------------------------------------------------------- the verdict

export type Bit = { text: string; tone?: 'stop' | 'warn' }

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`
const spare = (h: number) => (h < 1 ? `${Math.max(1, Math.round(h * 60))} min` : `${Math.round(h * 10) / 10} h`)

/**
 * One sentence for the whole launch, from the worst strip item: "Standard stops Sun 17:00 unless
 * you decide 3 things by 10:00." Only the words about the failing edition carry a colour.
 */
export function verdict(state: State, nowMs: number): Bit[] {
  const strip = state.strip
  if (!strip.length) return [{ text: 'Waiting for the first packing plan.' }]
  const bad = strip.filter((s) => s.level === 'red')
  const tight = strip.filter((s) => s.level === 'amber')
  const { needs } = sections(state)

  const tail = (items: StripItem[]): Bit[] => {
    const eds = new Set(items.map((s) => s.edition))
    const relevant = needs.filter((g) => g.cards.some((c) => !c.edition || eds.has(c.edition)))
    const ms = (x: string) => new Date(x).getTime()
    const dl = items.map((s) => s.breaks_first?.deadline).filter((x): x is string => !!x).sort((a, b) => ms(a) - ms(b))[0]
    const due = dl ? relevant.filter((g) => g.lead.deadline && ms(g.lead.deadline) <= ms(dl)) : relevant
    const n = (due.length ? due : relevant).length
    if (!n) return [{ text: '.' }]
    return [{ text: ` unless you decide ${plural(n, 'thing', 'things')}${dl && due.length ? ` by ${when(dl, nowMs)}` : ''}.` }]
  }
  const say = (s: StripItem, tone: 'stop' | 'warn'): Bit => {
    const stops = s.stops_at ?? s.breaks_first?.stops_at
    if (tone === 'warn') return { text: `${s.label} is tight`, tone }
    const at = stops ? when(stops, nowMs) : ''
    if (!stops) return { text: `${s.label} runs short`, tone }
    const past = new Date(stops).getTime() <= nowMs
    return { text: `${s.label} ${past ? 'stopped' : 'stops'} ${/^\d/.test(at) ? 'at ' : ''}${at}`, tone }
  }
  const join = (bits: Bit[]): Bit[] => bits.flatMap((b, i) => (i ? [{ text: ' and ' }, b] : [b]))

  if (bad.length) return [...join(bad.map((s) => say(s, 'stop'))), ...tail(bad)]
  if (tight.length) {
    const s = tight[0]
    const h = s.slack_hours
    return [say(s, 'warn'), { text: h != null && h > 0 ? `: ${spare(h)} spare` : '' }, ...tail(tight)]
  }
  const all = strip.length === 2 ? 'Both editions make' : strip.length === 1 ? `${strip[0].label} makes` : 'All editions make'
  const least = strip.filter((s) => s.slack_hours != null).sort((a, b) => a.slack_hours! - b.slack_hours!)[0]
  return [
    { text: `${all} the trucks.` },
    ...(least && least.slack_hours! > 0 ? [{ text: ` ${least.label} has ${spare(least.slack_hours!)} spare.` }] : []),
  ]
}

// ---------------------------------------------------------------- evidence

const SYSTEM = /system|sap|erp|plan|book stock/i
const PAPER = /paper|document|scan|advice|confirmation|delivery note|notice|invoice|report|offer|email/i

/** The contradiction a card is about: what the system says against what a document says. */
export function mismatchPair(card: Card): { sys: number; doc: number } | null {
  const sys = card.evidence.findIndex((e) => SYSTEM.test(e.source ?? '') && e.value != null)
  const doc = card.evidence.findIndex(
    (e, i) => i !== sys && !SYSTEM.test(e.source ?? '') && PAPER.test(e.source ?? '') && e.value != null,
  )
  return sys >= 0 && doc >= 0 ? { sys, doc } : null
}

const LANG: Record<string, string> = { KR: 'Korean', KO: 'Korean', CN: 'Chinese', ZH: 'Chinese', PL: 'Polish', EN: 'English', DE: 'German' }
/** "Korean" for a source labelled "(KR/EN)": the original language, not the English copy. */
export function languageOf(source: string | undefined, language?: string): string | null {
  const codes = [...(language ? [language] : []), ...((source ?? '').match(/\(([A-Z]{2}(?:\/[A-Z]{2})*)\)/)?.[1].split('/') ?? [])]
  const named = codes.map((c) => LANG[c.toUpperCase()]).filter(Boolean)
  return named.find((l) => l !== 'English') ?? named[0] ?? null
}

/** The source's name without the "System says ·" prefix and the language tag. */
export const sourceName = (src: string | undefined) =>
  tidy((src ?? '').replace(/^(System|Paper) says\s*·?\s*/i, '').replace(/\s*\([A-Z]{2}(?:\/[A-Z]{2})*\)\s*$/, '')).trim()

/** "48,000 due Sat 21 Nov" -> big "48,000", small "due Sat 21 Nov". */
export function splitValue(v: string | number | undefined): { big: string; rest: string } {
  const s = tidy(String(v ?? ''))
  const m = s.match(/^([−-]?\d[\d,.' ]*\d|\d)\b\s*(.*)$/)
  if (m && m[1].length <= 9) return { big: m[1].trim(), rest: m[2] }
  return { big: s, rest: '' }
}
