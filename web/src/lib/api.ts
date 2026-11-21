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
  headline: string
  summary?: string
  number: number | null
  number_label: string
  edition?: string
  cell_hours_lost?: number
  slack_hours?: number
  deadline?: string
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
  ui: { status?: string; at?: string; note?: string }
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
  breaks_first: { text?: string; deadline?: string; card: string } | null
}
export interface EventItem { id: string; at: string; path?: string; file?: string; node?: string }
export interface ActivityItem { at?: string; text: string; kind: string; detail?: string; card?: string }

export interface State {
  now: string
  viewer: string
  portal_found: boolean
  map: { nodes: MapNode[]; legs: Leg[]; version: string }
  route_level: Record<string, Level>
  badges: Record<string, number>
  shipments: Shipment[]
  strip: StripItem[]
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
