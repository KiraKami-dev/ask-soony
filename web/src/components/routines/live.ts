import { useSyncExternalStore } from 'react'
import { call, type Mode, type Proposal, type Routine, type Run, type WouldHandle } from './types'

// What the routines prepared for the buyer, shared by the Routines sheet, Today and Plan:
// proposals waiting for an OK, routines that would handle a card, repeated decisions worth a
// routine, and actions undone on this screen. Refreshed on the app's own refresh (see `refreshLive`), never on its
// own timer. Endpoints that are not there yet (404) simply count as empty.

/** A routine the voice assistant drafted during a call (GET /api/routines/drafts). */
export interface VoiceDraft { card_id: string; instruction?: string; at?: string; via?: string; routine: Routine; preview?: Run }

/** Past decisions made the same way at least twice, with no routine for them yet (GET /api/routines/suggestions). */
export interface Suggestion {
  key: string
  count: number
  card_ids: string[]
  decision: 'approve' | 'escalate' | 'dismiss'
  /** The option's action, as the agents wrote it ("Ask ... to ..."); null for escalate and dismiss. */
  action?: string | null
  /** The latest card's headline. */
  label?: string | null
  supplier?: string | null
  last_card_id: string
  last_at: string
}

interface Live {
  proposals: Proposal[]
  /** Voice drafts not hidden on this device and not saved as a routine yet. */
  drafts: VoiceDraft[]
  would: Record<string, WouldHandle[]>
  /** Action id -> "undone" or the reason it could not be undone. */
  undo: Record<string, { ok: boolean; reason?: string }>
  /** Proposals the buyer just accepted or skipped, hidden before the server agrees. */
  settled: Set<string>
  /** Repeated decisions worth a routine, newest first. */
  suggestions: Suggestion[]
}

const HIDDEN_KEY = 'ask-soony.hidden-routine-drafts'
function readHidden(): Set<string> {
  try { return new Set(JSON.parse(localStorage.getItem(HIDDEN_KEY) ?? '[]') as string[]) } catch { return new Set() }
}
let hidden = readHidden()
let allDrafts: VoiceDraft[] = []
const visibleDrafts = () => allDrafts.filter((d) => !hidden.has(d.routine.id))

let live: Live = { proposals: [], drafts: [], would: {}, undo: {}, settled: new Set(), suggestions: [] }
const subs = new Set<() => void>()
const set = (next: Partial<Live>) => { live = { ...live, ...next }; subs.forEach((f) => f()) }
const subscribe = (f: () => void) => { subs.add(f); return () => { subs.delete(f) } }

async function getJson<T>(url: string): Promise<T | null> {
  try {
    const r = await fetch(url)
    return r.ok ? ((await r.json()) as T) : null
  } catch { return null }
}

let lastAt = 0
let inflight: Promise<void> | null = null
/** Fetch proposals and would-handle; calls within `minMs` of the last one are skipped. */
export function refreshLive(minMs = 4000): Promise<void> {
  if (inflight) return inflight
  if (Date.now() - lastAt < minMs) return Promise.resolve()
  lastAt = Date.now()
  inflight = (async () => {
    const [p, w, d, s] = await Promise.all([
      getJson<{ proposals?: Proposal[] }>('/api/routines/proposals'),
      getJson<Record<string, WouldHandle[]>>('/api/routines/would-handle'),
      getJson<{ drafts?: VoiceDraft[] }>('/api/routines/drafts'),
      getJson<{ suggestions?: Suggestion[] }>('/api/routines/suggestions'),
    ])
    const suggestions = Array.isArray(s?.suggestions)
      ? s.suggestions.filter((x) => x && x.count >= 2 && Array.isArray(x.card_ids) && x.last_card_id)
      : live.suggestions
    if (d) allDrafts = (d.drafts ?? []).filter((x) => x?.routine?.id)
    const proposals = Array.isArray(p?.proposals) ? p.proposals.filter((x) => x && x.id) : []
    const would: Record<string, WouldHandle[]> = {}
    for (const [k, v] of Object.entries(w ?? {})) if (Array.isArray(v) && v.length) would[k] = v
    // Forget settled ids the server no longer lists as pending.
    const pending = new Set(proposals.filter((x) => x.status === 'pending').map((x) => x.id))
    const settled = new Set([...live.settled].filter((id) => pending.has(id)))
    set({ proposals, would, settled, suggestions, drafts: visibleDrafts() })
  })().finally(() => { inflight = null })
  return inflight
}

export function useLive(): Live {
  return useSyncExternalStore(subscribe, () => live)
}

/** Pending proposals not yet answered on this screen; for one card when `cardId` is given. */
export function pendingOf(l: Live, cardId?: string): Proposal[] {
  return l.proposals.filter(
    (p) => (p.status ?? 'pending') === 'pending' && !l.settled.has(p.id) && (cardId == null || p.card_id === cardId),
  )
}

/** The suggestion a decided card belongs to (read outside React, right after a decision). */
export function suggestionFor(cardId: string): Suggestion | null {
  return live.suggestions.find((x) => x.card_ids.includes(cardId) && x.count >= 2) ?? null
}

/** Hide a voice draft on this device (turned on, or "Not now"). */
export function hideDraft(routineId: string) {
  hidden = new Set(hidden).add(routineId)
  try { localStorage.setItem(HIDDEN_KEY, JSON.stringify([...hidden])) } catch { /* private mode */ }
  set({ drafts: visibleDrafts() })
}

/** Ask the server again right away (after a change on this screen). */
export function refreshNow(): Promise<void> {
  lastAt = 0
  return refreshLive(0)
}

export async function answerProposal(id: string, how: 'accept' | 'skip'): Promise<void> {
  const r = await call<{ ok?: boolean; result?: { error?: string } }>(`/api/routines/proposals/${encodeURIComponent(id)}/${how}`)
  // Accept answers 200 with ok: false when the step itself did not go through.
  if (r && r.ok === false) throw new Error(r.result?.error || 'it did not go through')
  // An accepted one stays a moment so its "Done" shows.
  window.setTimeout(() => {
    set({ settled: new Set(live.settled).add(id) })
    lastAt = 0
    void refreshLive()
  }, how === 'accept' ? 1500 : 0)
}

/** Undo a routine's action; remembers the outcome so the list and the pane agree. */
export async function undoAction(actionId: string): Promise<{ ok: boolean; reason?: string }> {
  let out: { ok: boolean; reason?: string }
  try {
    const r = await call<{ ok?: boolean; undone?: boolean; reason?: string }>(`/api/routines/actions/${encodeURIComponent(actionId)}/undo`)
    out = r.ok && r.undone !== false ? { ok: true } : { ok: false, reason: r.reason || 'This can no longer be undone.' }
  } catch (e) {
    const m = (e as Error).message
    out = { ok: false, reason: /404|not found/i.test(m) ? 'Undo is not available yet.' : m }
  }
  set({ undo: { ...live.undo, [actionId]: out } })
  return out
}

export async function setMode(routineId: string, mode: Mode): Promise<void> {
  await call(`/api/routines/${encodeURIComponent(routineId)}/mode`, { mode })
  lastAt = 0
  void refreshLive()
}

export async function setEnabled(routineId: string, enabled: boolean): Promise<void> {
  await call(`/api/routines/${encodeURIComponent(routineId)}/toggle`, { enabled })
  lastAt = 0
  void refreshLive()
}

/** Navigate inside the app from anywhere (the app listens to popstate). */
export function goTo(path: string) {
  window.history.pushState(null, '', path)
  window.dispatchEvent(new PopStateEvent('popstate'))
  window.scrollTo(0, 0)
}
