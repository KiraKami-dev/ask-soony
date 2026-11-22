export type Level = 'red' | 'amber' | 'grey' | 'blue'

export interface Option {
  index: number
  action?: string
  cost?: string
  effect?: string
  recommended: boolean
  draft?: string
  to?: string
  channel?: string
  routine_type?: string | null
}
export interface Evidence {
  source?: string
  value?: string | number
  doc?: string
  page?: number
  quote?: string
  translation?: string
  language?: string
  unit?: string
}
export interface TrailStep { agent?: string; at?: string; text?: string }

export interface Card {
  id: string
  kind?: string
  part?: string
  /** Short readable name of the part code, from the master data ("Art Book"). */
  part_name?: string | null
  headline: string
  summary?: string
  number: number | null
  number_label: string
  edition?: string
  cell_hours_lost?: number
  slack_hours?: number
  deadline?: string
  runs_dry?: string | null
  /** When the late goods arrive, if a finding states it (may be after launch). */
  arrives_at?: string | null
  severity?: string
  level: Level
  status: string
  approver?: string
  routed_to_other: boolean
  reason?: string
  called_at?: string
  options: Option[]
  evidence: Evidence[]
  trail: TrailStep[]
  supplier_id?: string
  supplier_name?: string
  shipment?: string
  routes: string[]
  ui: {
    status?: string; at?: string; note?: string
    // Supplier reply tied back by the alert agent (replies.py)
    reply?: string; reply_from?: string; reply_at?: string; reply_text?: string
  }
  escalation_brief: string
}

export interface MapNode { id: string; kind: 'supplier' | 'port' | 'dc' | 'site'; name: string; lat: number; lon: number; country?: string }
export interface Leg { id: string; origin: string; destination: string; mode: string; via?: string; path: [number, number][] | null }
export interface Shipment {
  id: string; route_id: string; mode: string; supplier_id: string; status: string
  eta?: string; etd?: string; progress: number; departed: boolean; silent: boolean; silent_hours?: number
  level: Level; note?: string
}
export interface StripItem {
  edition: string; label: string; level: Level; status: string
  copies_at_risk: number; slack_hours: number | null
  breaks_first: { text?: string; deadline?: string; stops_at?: string | null; card: string } | null
  finishes_at?: string | null
  stops_at?: string | null
}

/** One edition on the packing clock: when its line finishes, or stops for want of parts. */
export interface ClockEdition {
  edition: string
  label: string
  level: Level
  /** Last copy packed at plan rate; null if the line stops first or the shifts run out. */
  finishes_at: string | null
  /** Line runs out of parts (projected; never at night); null if it does not stop. */
  stops_at: string | null
  /** Largest copies-at-risk figure on the edition's open cards, or the verifier's if higher. */
  copies_at_risk: number
  slack_hours: number | null
  remaining: number | null
  rate_per_hour: number | null
  /** When the packed count behind `remaining` was taken; packing is projected at plan rate since. */
  packed_as_of: string | null
  /** Copies the hours left cannot pack even with every part present. */
  short_of_time: number | null
  breaks_first_card: string | null
}
export interface Clock {
  /** Trucks leave: the end of the last shift. */
  launch: string
  /** Shift windows [start, end] from the start of today until launch, ISO. */
  windows: [string, string][]
  editions: ClockEdition[]
}
export interface EventItem { id: string; at: string; path?: string; file?: string; node?: string }
export interface ActivityItem {
  at?: string; text: string; kind: string; detail?: string | null; card?: string
  /** Who handled it: "inbox", "voice" or "routine: <name>". Missing on older items. */
  via?: string | null
}

export interface State {
  now: string
  viewer: string
  portal_found: boolean
  map: { nodes: MapNode[]; legs: Leg[]; version: string }
  route_level: Record<string, Level>
  badges: Record<string, number>
  shipments: Shipment[]
  strip: StripItem[]
  clock?: Clock
  cards: Card[]
  inbox_ids: string[]
  calm: boolean
  events: EventItem[]
  activity: ActivityItem[]
  last_drop_at: string | null
  last_check_at: string | null
  read_since_6: number
  trust: Record<string, string>
}

export async function fetchState(): Promise<State> {
  const r = await fetch('/api/state')
  if (!r.ok) throw new Error(`state ${r.status}`)
  return r.json()
}

export async function post<T = unknown>(url: string, body: unknown = {}): Promise<T> {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!r.ok) throw new Error(`${url} ${r.status}`)
  return r.json()
}

export const COLORS: Record<Level, string> = {
  blue: '#1d6fe0',
  amber: '#d97706',
  red: '#dc2626',
  grey: '#7b8494',
}
export const LEVEL_RANK: Record<Level, number> = { red: 3, amber: 2, grey: 1, blue: 0 }

const TZ = 'Europe/Berlin'
export const fmtTime = (iso?: string | null) =>
  iso ? new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: TZ }).format(new Date(iso)) : ''
export const fmtDay = (iso?: string | null) =>
  iso ? new Intl.DateTimeFormat('en-GB', { weekday: 'short', timeZone: TZ }).format(new Date(iso)) : ''
export const fmtNum = (n: number | null | undefined) => (n == null ? '–' : Math.round(n).toLocaleString('en-GB'))

export function ago(iso: string | null | undefined, nowMs: number): string {
  if (!iso) return 'never'
  const min = Math.max(0, Math.round((nowMs - new Date(iso).getTime()) / 60000))
  if (min < 1) return 'just now'
  if (min < 60) return `${min} min ago`
  const h = Math.floor(min / 60)
  return h < 48 ? `${h} h ago` : `${Math.floor(h / 24)} days ago`
}

export function until(iso: string | undefined, nowMs: number): { text: string; urgent: boolean } | null {
  if (!iso) return null
  const min = Math.round((new Date(iso).getTime() - nowMs) / 60000)
  if (min <= 0) return { text: 'now', urgent: true }
  const text = min < 60 ? `${min} min` : `${Math.floor(min / 60)} h ${min % 60 ? `${min % 60} min` : ''}`.trim()
  return { text: `in ${text}`, urgent: min < 180 }
}

export const DONE_WORD: Record<string, string> = {
  approved: 'Approved', sent: 'Sent', escalated: 'Escalated', dismissed: 'Dismissed',
}
export const DECIDED = Object.keys(DONE_WORD)

/** What handled an activity item, read from its `via` field. */
export function viaOf(a: ActivityItem): { kind: 'routine'; name: string } | { kind: 'voice' } | null {
  const v = (a.via ?? '').trim()
  if (/^routine\s*:/i.test(v)) return { kind: 'routine', name: v.replace(/^routine\s*:\s*/i, '') }
  if (/^voice$/i.test(v)) return { kind: 'voice' }
  return null
}

// Internal part / product codes (CMP-GAM-001, FG-GH6-STD): never shown on their own.
const CODE = /^[A-Z]{2,5}(-[A-Z0-9]+)+$/

/** A readable name for a part code, found on the cards; undefined if none has one. */
export function partName(part: string | undefined, cards: Card[]): string | undefined {
  if (!part) return undefined
  if (!CODE.test(part)) return part
  const c = cards.find(
    (c) => c.part && !CODE.test(c.part) && (c.id.endsWith(part) || c.options.some((o) => o.action?.includes(`(${part})`))),
  )
  return c?.part
}

const fmtDate = (ymd: string) =>
  new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' }).format(
    new Date(`${ymd}T00:00:00Z`),
  )

/** Display text: drop bracketed internal codes, write ISO dates as "Mon 23 Nov". */
export function tidy(t: string | undefined | null): string {
  if (!t) return ''
  return t
    .replace(/\s*\((?:[A-Z]{2,5}-)+[A-Z0-9-]+\)/g, '')
    .replace(/\b(\d{4}-\d{2}-\d{2})(?:T[\d:.]+(?:Z|[+-]\d{2}:\d{2})?)?\b/g, (_, d: string) => fmtDate(d))
}

const cap = (t: string) => (t ? t[0].toUpperCase() + t.slice(1) : t)

/** One cost wording everywhere. */
export function costText(cost: string | undefined | null): string {
  const c = (cost ?? '').trim()
  if (!c || /^(none|no cost|free|0|-|–|n\/a)$/i.test(c)) return 'No cost'
  return cap(tidy(c))
}
export const sentence = (t: string | undefined | null) => cap(tidy(t))

/** Slack as people say it; "–" when it would only confuse (on track, or nothing at risk). */
export function slackText(hours: number | null, onTrack: boolean, atRisk: number): string {
  if (hours == null || (onTrack && (hours < 0 || atRisk === 0))) return '–'
  const sign = hours < 0 ? '−' : ''
  const a = Math.abs(hours)
  if (a < 1) return `${sign}${Math.round(a * 60)} min`
  return `${sign}${Math.round(a * 10) / 10} h`
}
