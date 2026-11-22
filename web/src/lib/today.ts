// What the Today screen says, derived from /api/state: which decision comes first, a short
// headline, the contradiction as one sentence, what it means for today's packing, and the
// recommendation. Nothing here knows a specific supplier, part or number.

import { DECIDED, costText, fmtDay, fmtTime, sentence, tidy, viaOf, type Card, type Evidence, type State } from '@/lib/api'
import { byUrgency, groupKey, launchAt, mismatchPair, sourceName, span } from '@/lib/decisions'

const ms = (iso?: string | null) => (iso ? new Date(iso).getTime() : NaN)

/** Open decisions for the viewer, most urgent first: the server's inbox order, then urgency. */
export function todayOrder(state: State): Card[] {
  const open = state.cards.filter((c) => c.status === 'open' && !c.routed_to_other)
  const rank = new Map(state.inbox_ids.map((id, i) => [id, i]))
  return open.sort((a, b) => {
    const ra = rank.get(a.id) ?? Infinity, rb = rank.get(b.id) ?? Infinity
    return ra !== rb ? ra - rb : byUrgency(a, b)
  })
}

/** "Trucks leave in 13 h 30 min"; null without a launch time. */
export function trucksLeave(state: State, nowMs: number): string | null {
  const at = launchAt(state)
  if (!at) return null
  const left = ms(at) - nowMs
  return left > 0 ? `Trucks leave in ${span(left)}` : 'The trucks have left'
}

export const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString('en-GB')} ${n === 1 ? one : many}`

// ---------------------------------------------------------------- words

/** "Game Card 128 GB" -> "game cards"; quoted names keep their case. */
export function noun(partName: string | null | undefined, many = true): string {
  if (!partName) return many ? 'parts' : 'part'
  if (partName.includes('"')) {
    const [head, ...rest] = partName.split(' ')
    return [head.toLowerCase() + (many && !head.endsWith('s') ? 's' : ''), ...rest].join(' ')
  }
  const words = partName.split(/\s+/)
  while (words.length > 1 && /^(\d+[\w.]*|[A-Z]{2,}|[–-])$/.test(words[words.length - 1])) words.pop()
  const out = words.map((w) => (/^[A-Z][a-z]+$/.test(w) ? w.toLowerCase() : w))
  const last = out.length - 1
  if (many && !/s$/i.test(out[last])) out[last] += 's'
  return out.join(' ')
}

const PARENS = /\s*\([^)]*\)/g
/** Text without the part's long product name: "Art Book Grand Heist 6 (96 p, hardcover)" -> "art books". */
export function plain(text: string | null | undefined, partName?: string | null): string {
  let t = tidy(text).replace(PARENS, '')
  if (partName) {
    const base = partName.replace(PARENS, '').trim()
    const i = t.indexOf(base)
    if (i >= 0) {
      const after = t.slice(i + base.length)
      // A run of capitalised words and numbers right after the name is the product title.
      const tail = after.match(/^(?:\s*[–-]\s*|\s+)(?:[A-Z][A-Za-z]*|\d+)(?:\s+(?:[A-Z][A-Za-z]*|\d+))*(?=[\s,.:;]|$)/)
      const before = t.slice(0, i)
      const counted = /\d\s*$/.test(before) && !/(^|\D)1\s*$/.test(before)
      const n = noun(partName, counted)
      t = before + (i === 0 ? n[0].toUpperCase() + n.slice(1) : n) + after.slice(tail ? tail[0].length : 0)
    }
  }
  return t.replace(/\s+([,.:;])/g, '$1').replace(/\s{2,}/g, ' ').trim()
}

const firstSentence = (t: string) => {
  const m = t.match(/^.*?[.!?](?=\s+[A-Z0-9]|$)/)
  return (m ? m[0] : t).trim()
}
const stop = (t: string) => (/[.!?]$/.test(t) ? t : `${t}.`)
const cap = (t: string) => (t ? t[0].toUpperCase() + t.slice(1) : t)

/** True when the goods a card is about arrive after the trucks leave. */
export function afterLaunch(card: Card, state: State): boolean {
  const l = launchAt(state)
  return !!(card.arrives_at && l && ms(card.arrives_at) > ms(l))
}

/** The big headline: plain words, one sentence, no part codes. */
export function headlineOf(card: Card, state: State): string {
  const h = plain(card.headline, card.part_name)
  if (afterLaunch(card, state)) {
    const n = tidy(card.headline).match(/\d[\d,.]*\d|\d/)?.[0]
    if (n) return `${n} ${noun(card.part_name, n !== '1')} will arrive after launch.`
  }
  // Drop a trailing ", but ..." clause on long headlines; the sentence below says it.
  const one = firstSentence(h).replace(/\s+on PO \d{6,}/g, '')
  const short = one.length > 70 ? one.replace(/,\s+(but|which|while)\b.*$/, '') : one
  return stop(cap(short))
}

/** One line for a list: shorter still. */
export function shortTitle(card: Card, state: State): string {
  return headlineOf(card, state)
    .replace(/\bwill arrive after launch\b/, 'arrive late')
    .replace(/^We have /, '')
    .replace(/\.$/, '')
}

/** "PackRight" from "Guangzhou PackRight", "Hwaseong" from "Hwaseong Memory". */
export function shortName(name?: string | null): string {
  if (!name) return ''
  const words = name.split(/\s+/)
  return words.find((w) => /^[A-Z][a-z]+[A-Z]/.test(w)) ?? words[0]
}

// ---------------------------------------------------------------- the contradiction

export interface Side {
  index: number
  ev: Evidence
  /** The words that become the tappable citation. */
  value: string
}
export interface Claim {
  sys: Side
  doc: Side
  /** Text around the two citations: before sys, between, after doc. */
  pre: string
  mid: string
  post: string
}

const ID = /\b[A-Z]{0,4}[ -]?\d{6,}\b/
function pick(v: string | number | undefined): string {
  const s = tidy(String(v ?? '')).replace(PARENS, '').replace(/\s+(?:on|for)\s+[A-Z]{1,4}\s*\d{6,}/g, '').trim()
  const segs = s.split(/\s+·\s+|,\s+(?=[a-z])/).map((x) => x.trim()).filter(Boolean)
  const useful = segs.filter((x) => !ID.test(x))
  const dated = useful.find((x) => /\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b|\b\d{1,2} [A-Z][a-z]{2}\b/.test(x) && /\d/.test(x))
  return (dated ?? useful[0] ?? s).replace(/\bdue\b/, 'by')
}

/** "shipping advice" from "Paper says · shipping advice Shipping_advice_020-... (KO/EN)". */
export function docKind(source?: string): string {
  return sourceName(source)
    .split(/\s+/)
    .filter((w) => w && !/[\d_]/.test(w) && !/^[A-Z]{2,}(-|$)/.test(w))
    .join(' ')
    .toLowerCase()
}

export function claimOf(card: Card): Claim | null {
  const pair = mismatchPair(card)
  if (!pair) return null
  const s = card.evidence[pair.sys], d = card.evidence[pair.doc]
  const sysWho = /\bSAP\b/.test(s.source ?? '') ? 'SAP' : 'The system'
  const sv = pick(s.value), dv = pick(d.value)
  const expects = /^\S+\s+by\b/.test(sv)
  const kind = docKind(d.source) || 'document'
  const owner = d.doc?.startsWith('suppliers/') && card.supplier_name ? `${shortName(card.supplier_name)}'s` : 'the'
  // Our own count (verifier: stock + arriving vs still to pack), not a document's words, and a
  // system side that is a verdict ("Plan says ok"), not a number: say it as what it is.
  const counted = !d.doc && !docKind(d.source)
  const verdict = !/\d/.test(sv)
  return {
    sys: {
      index: pair.sys,
      ev: s,
      // "Plan says ok" -> "The plan says it is covered"
      value: verdict ? sv.replace(/^(Plan|System)\b/, (w) => w.toLowerCase()).replace(/\bsays\b/i, 'says it is').replace(/\bok\b/i, 'covered') : sv,
    },
    doc: { index: pair.doc, ev: d, value: dv },
    pre: verdict ? (/^(plan|sap|system)\b/i.test(sv) ? 'The ' : '') : `${sysWho} ${expects ? 'expects' : 'says'} `,
    mid: counted ? ', but we count ' : `, but ${owner} ${kind} says `,
    post: '.',
  }
}

/** The supporting sentence when there is no system-vs-document pair. */
export function summaryLine(card: Card): string {
  return card.summary ? stop(firstSentence(plain(card.summary, card.part_name))) : ''
}

// ---------------------------------------------------------------- for today

const WORDS = ['', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve']
function hoursWords(h: number): string {
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} minutes`
  const r = Math.round(h)
  return r <= 12 ? `${WORDS[r]} hour${r === 1 ? '' : 's'}` : `${r} hours`
}
const at = (iso: string, nowMs: number) =>
  fmtDay(iso) === fmtDay(new Date(nowMs).toISOString()) ? fmtTime(iso) : `${fmtDay(iso)} ${fmtTime(iso)}`

/** "Standard runs out of game cards at 17:00" + ", three hours before the trucks." */
export function forToday(card: Card, state: State, nowMs: number): { bold: string; rest: string } | null {
  const ed = state.clock?.editions.find((e) => e.edition === card.edition)
  const launch = launchAt(state)
  if (!ed) return null
  // The edition's stop belongs to this card only if this card is what breaks first. Otherwise
  // talk about this card's own part (when it runs dry), or say nothing: never borrow another
  // part's stop time for this decision.
  const ownStop = ed.breaks_first_card === card.id || !ed.breaks_first_card
  const stopsAt = ownStop ? ed.stops_at : card.runs_dry
  if (!stopsAt || (launch && ms(stopsAt) >= ms(launch))) return null
  const what = noun(card.part_name)
  const stopMs = ms(stopsAt)
  if (stopMs <= nowMs) return { bold: `${ed.label} is out of ${what} since ${at(stopsAt, nowMs)}`, rest: '.' }
  const gap = launch ? (ms(launch) - stopMs) / 3600000 : 0
  return {
    bold: `${ed.label} runs out of ${what} at ${at(stopsAt, nowMs)}`,
    rest: gap > 0.1 ? `, ${hoursWords(gap)} before the trucks.` : '.',
  }
}

// ---------------------------------------------------------------- recommendation and outcome

export const recommendedIndex = (card: Card) => Math.max(0, card.options.findIndex((o) => o.recommended))

export function actionText(card: Card, i: number): string {
  const o = card.options[i]
  return o ? stop(cap(plain(o.action, card.part_name))) : ''
}
export function costLine(card: Card, i: number): string {
  const c = costText(card.options[i]?.cost)
  return c === 'No cost' ? 'No cost.' : stop(c)
}
export const costsMoney = (card: Card, i: number) => costText(card.options[i]?.cost) !== 'No cost'

const PAST: Record<string, string> = {
  ask: 'Asked', tell: 'Told', order: 'Ordered', send: 'Sent', book: 'Booked', collect: 'Collected',
  call: 'Called', move: 'Moved', release: 'Released', hold: 'Held', request: 'Requested', confirm: 'Confirmed',
  correct: 'Corrected', switch: 'Switched', pay: 'Paid', accept: 'Accepted', approve: 'Approved',
  cancel: 'Cancelled', expedite: 'Expedited', fly: 'Flew', reserve: 'Reserved', notify: 'Notified',
}
/** "Asked Boise Semi for 6,800 game cards before launch." from the option's imperative. */
export function doneText(card: Card, i: number, status?: string): string {
  const a = actionText(card, i)
  if (status === 'escalated') return `Passed on to ${card.approver || 'the approver'}.`
  if (status === 'dismissed') return 'Taken off the list.'
  const m = a.match(/^(\w+)\s+(.*)$/)
  const past = m && PAST[m[1].toLowerCase()]
  return past ? `${past} ${m![2]}` : a ? `Approved: ${a[0].toLowerCase()}${a.slice(1)}` : 'Approved.'
}

/** Open look-alikes of a card (same supplier, kind and headline shape). */
export const lookAlikes = (card: Card, state: State) =>
  state.cards.filter((x) => x.id !== card.id && x.status === 'open' && !x.routed_to_other && groupKey(x) === groupKey(card))

/** Routine work since 18:00 yesterday (or the last 14 h). */
export function routineCount(state: State, nowMs: number): number {
  const since = nowMs - 14 * 3600000
  return state.activity.filter((a) => viaOf(a)?.kind === 'routine' && (!a.at || ms(a.at) >= since)).length
}

export const isDecided = (status: string) => DECIDED.includes(status)

/** The launch status line for the all-clear screen: honest when an edition stops. */
export function launchStatus(state: State, nowMs: number): string {
  const launch = launchAt(state)
  const stops = (state.clock?.editions ?? []).filter((e) => e.stops_at && (!launch || ms(e.stops_at) < ms(launch)))
  if (stops.length) {
    const e = stops.sort((a, b) => ms(a.stops_at) - ms(b.stops_at))[0]
    const past = ms(e.stops_at) <= nowMs
    return `${e.label} ${past ? 'is waiting for parts' : `stops at ${at(e.stops_at!, nowMs)} unless parts arrive`}. Your agents are on it.`
  }
  return launch ? `On track for the trucks at ${fmtTime(launch)}.` : 'On track.'
}

export const sentenceOf = sentence

// ---------------------------------------------------------------- repeated decisions

const ORDINAL = ['', 'first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth', 'ninth', 'tenth']
/** "second", "third", … "11th". */
export function ordinal(n: number): string {
  if (ORDINAL[n]) return ORDINAL[n]
  const t = n % 100
  const suf = t >= 11 && t <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th'
  return `${n}${suf}`
}

const firstName = (s?: string) => (!s ? '' : /\s[a-z]/.test(s) ? s : (s.split(' ').find((w) => !w.endsWith('.')) ?? s))

/**
 * What the person did, in plain past-tense words for "That's the third time you …":
 * "asked PackRight to switch to the new artwork", "passed it on to Maren".
 */
export function repeatWords(decision: string, action: string | null | undefined, card: Card | null): string {
  if (decision === 'escalate') return `passed this kind of case on to ${firstName(card?.approver) || 'the approver'}`
  if (decision === 'dismiss') return 'took this kind of case off the list'
  const supplier = card?.supplier_name
  let a = plain(action, card?.part_name).replace(/\.$/, '')
  if (supplier) a = a.split(supplier).join(shortName(supplier))
  a = a.replace(/\b(?:PO|order)\s+\d{6,}/g, 'the order')
  const m = a.match(/^(\w+)\s+(.*)$/)
  const past = m && PAST[m[1].toLowerCase()]
  if (past) return `${past.toLowerCase()} ${m![2]}`
  return a ? `approved ‘${a[0].toLowerCase()}${a.slice(1)}’` : 'approved this kind of case'
}
