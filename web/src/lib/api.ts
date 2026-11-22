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
  /** Already carried out on a half-done decision: not offered again (indices stay as they are). */
  done?: boolean
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
  /** Someone else signs this (approval policy); it stays with the viewer until passed on. */
  signoff?: string | null
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
    /** Facts read from the reply: the date and quantity the supplier commits to. */
    reply_date?: string | null; reply_quantity?: number | string | null; reply_after_launch?: boolean | null
  }
  escalation_brief: string
  /** Approved with an option that did not close the gap while another would: open again. */
  follow_up?: { done: string; done_index: number; at: string | null; still_short: number | null } | null
  /** Look-alikes fold under this key when set (questions to the site: one per kind). */
  group?: string
  /** A question to the people at the site (stale count, due shipment, uncounted stock). */
  site_question?: SiteQuestion
  /** Where the order this case placed stands: 'Ordered 06:50 · Karton confirmed 8,000 at 07:13 · truck details pending'. */
  commitment_line?: string | null
  commitments?: string[]
  /** The sign-off link behind a passed-on case or an internal message: who it reached, their answer. */
  signoff_request?: SignoffRequest | null
}

export interface SignoffAnswer {
  decision: 'approved' | 'declined' | 'done'
  by: string; at: string; note?: string | null; simulated?: boolean; status?: string
}
export interface SignoffRequest {
  id: string
  kind: 'escalation' | 'notify'
  /** 'Dr. Maren Vogt', 'DC site lead' */
  to: string
  /** 'Maren', 'DC site lead' */
  who: string
  asked_at: string | null
  sent_at: string | null
  channels: ('push' | 'teams')[]
  delivered: boolean
  answer: SignoffAnswer | null
  acks: { answer: 'got_it' | 'done'; by: string; at: string }[]
}

export type SiteKind = 'packed' | 'received' | 'count'
export interface FloorAnswer {
  id?: string; kind?: SiteKind; count?: number | null; at?: string | null; by?: string | null
  arrived?: boolean; photo_url?: string | null; by_books?: boolean; gone?: boolean
}
export interface SiteQuestion {
  kind: SiteKind
  subject: string
  last_known: { value: number | string | null; as_of: string | null; source: string | null }
  asked_at: string | null
  answer: FloorAnswer | null
  site: string
}
/** One edition's packing count: what was counted (log or the site), and what Launch assumes. */
export interface FloorEdition {
  label: string
  rate: number | null
  log: { number: number | null; as_of: string | null; source: string | null }
  counted: { number: number | null; as_of: string | null; source: string | null; by: string | null; reported: boolean }
  projected: number | null
  stale_hours: number
  age_hours: number
  stale: boolean
  reported: FloorAnswer | null
  below_plan: { copies: number; since: string | null; stood_still: boolean } | null
  line: string
}
export interface Floor {
  site: string
  stale_after_hours: number
  editions: Record<string, FloorEdition>
  questions: { id: string; kind: SiteKind; answered: boolean }[]
}

export interface MapNode { id: string; kind: 'supplier' | 'port' | 'dc' | 'site'; name: string; lat: number; lon: number; country?: string }
export interface Leg { id: string; origin: string; destination: string; mode: string; via?: string; path: [number, number][] | null }
export interface Shipment {
  id: string; route_id: string; mode: string; supplier_id: string
  /** Reconciled status (tracking.py) when known, else what the list says. */
  status: string
  /** What the hand-kept shipment list says. */
  list_status?: string
  eta?: string; etd?: string; progress: number; departed: boolean
  /** No news from any source for a while, and needed for the launch. */
  silent: boolean; silent_hours?: number
  level: Level; note?: string
  derived?: DerivedShipment
}
/** One shipment's truth from every source: receipts > site > documents and replies > the list. */
export interface DerivedShipment {
  status: 'booked' | 'departed' | 'in_transit' | 'at_port' | 'delayed' | 'arrived_not_booked' | 'received' | 'unknown'
  label: string
  as_of: string | null
  source: 'receipt' | 'site' | 'document' | 'reply' | 'tracker' | 'list' | null
  source_label: string | null
  contradictions: string[]
  last_heard: string | null
  silent_hours: number | null
  silent: boolean
  matters: boolean
  alert: boolean
  copies: number | null
  received: { qty: number; full: boolean; shipped: number | null; order_balance: number | null; receipts: string[] } | null
  eta_passed: boolean
  departure_overdue: boolean
  tracker_status: string | null
  claims: { source: string; label: string; status: string | null; at: string | null; text: string; anchor: { file?: string; row?: number; page?: number } }[]
}
/** The hand-kept shipment list itself: how old its newest row is and which rows other sources contradict. */
export interface TrackerInfo {
  file: string; rows: number; newest_update: string | null; list_age_hours: number | null
  stale_after_hours: number; frozen: boolean; contradicted: string[]; received_in_list: string[]
}
export interface StripItem {
  edition: string; label: string; level: Level; status: string
  copies_at_risk: number; slack_hours: number | null
  breaks_first: { text?: string; deadline?: string; stops_at?: string | null; card: string } | null
  finishes_at?: string | null
  stops_at?: string | null
  remaining?: number | null
  rate_per_hour?: number | null
  packed_as_of?: string | null
  short_of_time?: number | null
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
/** One shipment or lot on its way for a part, as the verifier counted it. */
export interface Inbound {
  reference: string | null
  quantity: number | null
  eta: string | null
  /** Counted as arriving before packing ends. */
  counted: boolean
  reason: string | null
  after_launch: boolean
  supplier_id?: string | null
  shipment: { id: string; status: string; mode: string; eta?: string; silent: boolean; silent_hours: number | null; note?: string | null } | null
  /** A confirmed order (commitments.py): its own row, or a note on the shipment a reply names. */
  commitment?: Commitment | null
}
export interface Commitment {
  id: string
  card_id: string
  supplier_name: string
  quantity: number | null
  status: 'offered' | 'confirmed' | 'loading' | 'shipped' | 'delivered' | 'declined'
  /** When the supplier confirmed it. */
  confirmed_at?: string | null
  eta: string | null
  estimated: boolean
  /** The whole row in words, arithmetic included. */
  line: string
  /** Copies it saves by the trucks; null when not worked out (shared part). */
  saves: number | null
}
/** One part of an edition's bill of materials (verifier's dispatch.json, GET /api/state launch). */
export interface LaunchPart {
  part: string
  name: string
  /** Without the launch title and brackets: 'Art Book'. */
  short_name: string
  editions: string[]
  needed: number | null
  usable: number | null
  book_stock: number | null
  book_as_of: string | null
  gap: number | null
  /** Copies this part cannot cover; null when the data does not say. */
  short: number | null
  /** When the line runs out of it; null when it lasts, or unknown (shared part, no rate). */
  runs_dry: string | null
  shared: boolean
  inbound: Inbound[]
  /** Counted on the shelf by the site; replaces the book stock. */
  counted?: { count: number; at: string | null; by: string | null; book: number; photo_url?: string | null } | null
  /** Open or passed-on cases about it, most urgent first. */
  cards: string[]
}
/** A decision that costs money, still in the air (launch_view.money). */
export interface PricedDecision {
  card: string
  status: string
  label: string | null
  part_name?: string | null
  cost_text?: string | null
  /** The amount as the option states it; null: price pending. */
  cost_value: number | null
  currency?: string | null
  holder?: string | null
  holder_role: 'viewer' | 'approver' | 'supplier'
  /** Who signs, when someone other than the buyer must (approval policy). */
  signoff?: string | null
  since?: string | null
  copies_at_risk?: number | null
}
export interface LaunchMoney {
  committed_today: { currency: string; value: number }[]
  committed_items: { card: string; label: string | null; cost_value: number; currency: string; at: string }[]
  pending_decisions: PricedDecision[]
  approver?: string | null
  viewer?: string | null
}
export interface LaunchData {
  hours_left: number | null
  editions: ({ edition: string; day_one: number | null } & Partial<FloorEdition>)[]
  parts: LaunchPart[]
  floor?: Floor | null
  money?: LaunchMoney
}
export interface EventItem { id: string; at: string; path?: string; file?: string; node?: string }
export interface ActivityItem {
  at?: string; text: string; kind: string; detail?: string | null; card?: string
  /** Who handled it: "inbox", "voice" or "routine: <name>". Missing on older items. */
  via?: string | null
  /** Who decided, when the action records it. */
  by?: string | null
  /** The action behind it, for undo (POST /api/routines/actions/{id}/undo). Missing: no undo. */
  action_id?: string | null
  id?: string | null
}

export interface State {
  now: string
  viewer: string
  portal_found: boolean
  map: { nodes: MapNode[]; legs: Leg[]; version: string }
  route_level: Record<string, Level>
  badges: Record<string, number>
  shipments: Shipment[]
  tracker?: TrackerInfo
  strip: StripItem[]
  clock?: Clock
  launch?: LaunchData
  cards: Card[]
  inbox_ids: string[]
  calm: boolean
  events: EventItem[]
  activity: ActivityItem[]
  last_drop_at: string | null
  last_check_at: string | null
  /** The buyer's job title, set per customer; null: 'Buyer'. */
  viewer_title?: string | null
  /** This instance polls the portal; false on the demo. */
  watching?: boolean
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
export function viaOf(a: ActivityItem): { kind: 'routine'; name: string } | { kind: 'voice' } | { kind: 'call' } | null {
  const v = (a.via ?? '').trim()
  if (/^routine\s*:/i.test(v)) return { kind: 'routine', name: v.replace(/^routine\s*:\s*/i, '') }
  if (/^voice$/i.test(v)) return { kind: 'voice' }
  if (/^call$/i.test(v)) return { kind: 'call' }
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
