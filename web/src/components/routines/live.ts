import { useSyncExternalStore } from 'react'
import { call, type Mode, type Proposal, type WouldHandle } from './types'

// What the routines prepared for the buyer, shared by the Routines sheet, the decision pane and
// the inbox list: proposals waiting for an OK, routines that would handle a card, and actions
// undone on this screen. Refreshed on the app's own refresh (see `refreshLive`), never on its
// own timer. Endpoints that are not there yet (404) simply count as empty.

interface Live {
  proposals: Proposal[]
  would: Record<string, WouldHandle[]>
  /** Action id -> "undone" or the reason it could not be undone. */
  undo: Record<string, { ok: boolean; reason?: string }>
  /** Proposals the buyer just accepted or skipped, hidden before the server agrees. */
  settled: Set<string>
}

let live: Live = { proposals: [], would: {}, undo: {}, settled: new Set() }
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
    const [p, w] = await Promise.all([
      getJson<{ proposals?: Proposal[] }>('/api/routines/proposals'),
      getJson<Record<string, WouldHandle[]>>('/api/routines/would-handle'),
    ])
    const proposals = Array.isArray(p?.proposals) ? p.proposals.filter((x) => x && x.id) : []
    const would: Record<string, WouldHandle[]> = {}
    for (const [k, v] of Object.entries(w ?? {})) if (Array.isArray(v) && v.length) would[k] = v
    // Forget settled ids the server no longer lists as pending.
    const pending = new Set(proposals.filter((x) => x.status === 'pending').map((x) => x.id))
    const settled = new Set([...live.settled].filter((id) => pending.has(id)))
    set({ proposals, would, settled })
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

export async function answerProposal(id: string, how: 'accept' | 'skip'): Promise<void> {
  await call(`/api/routines/proposals/${encodeURIComponent(id)}/${how}`)
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
