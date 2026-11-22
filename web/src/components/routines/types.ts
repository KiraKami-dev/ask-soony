// Shapes of /api/routines (src/ask_soony/routines.py) and small helpers shared by the routine UI.

export type Field = 'evidence' | 'headline' | 'option_action' | 'option_to' | 'supplier_name' | 'kind'
export type Channel = 'phone' | 'teams' | 'both'

export interface Cond { id: string; field: Field; op: 'contains' | 'is' | 'any_of'; value: string; text: string }
export interface Step {
  id: string
  do: 'carry_out' | 'notify' | 'log'
  text: string
  match?: { to?: string; action_contains?: string }
  channel?: Channel
  message?: string
}
export interface Spec { when: Cond[]; then: Step[] }

export interface Did {
  do: 'carry_out' | 'notify'
  card?: string; option?: string; text?: string; channel?: Channel; status?: string; dry?: boolean
}
export interface Run { at: string; ok: boolean; dry?: boolean; result?: unknown; error?: string; log?: string[]; did?: Did[] }
export interface Routine {
  id: string; name: string; description: string; spec?: Spec | null; code: string; enabled: boolean
  created_from?: string | null; created_at?: string; runs?: Run[]; handled?: number; note?: string
}

/** The fields the save, edit and preview endpoints take. */
export const body = (r: Routine) => ({
  id: r.id, name: r.name, description: r.description, spec: r.spec, created_from: r.created_from ?? null,
})

/** POST that keeps the server's reason on failure (FastAPI puts it in `detail`). */
export async function call<T>(url: string, payload: unknown = {}, method = 'POST'): Promise<T> {
  const r = await fetch(url, {
    method, headers: { 'Content-Type': 'application/json' }, body: method === 'DELETE' ? undefined : JSON.stringify(payload),
  })
  if (!r.ok) {
    let reason = ''
    try { reason = String((await r.json()).detail ?? '') } catch { /* not JSON */ }
    throw new Error(reason || `the server answered ${r.status}`)
  }
  return r.json()
}

export const newId = (prefix: string) => `${prefix}${Math.random().toString(36).slice(2, 8)}`

/** Steps a buyer can add with one click or drag. */
export const PALETTE: { key: string; label: string; make: () => Step }[] = [
  { key: 'phone', label: 'Tell me on my phone', make: () => ({ id: newId('s'), do: 'notify', channel: 'phone', message: '{headline}', text: 'Tell me on my phone' }) },
  { key: 'teams', label: 'Tell me on Teams', make: () => ({ id: newId('s'), do: 'notify', channel: 'teams', message: '{headline}', text: 'Tell me on Teams' }) },
  { key: 'log', label: 'Note it in the history', make: () => ({ id: newId('s'), do: 'log', message: 'Handled by a routine: {headline}', text: 'Note it in the history' }) },
]

export function paletteKey(s: Step): string | null {
  if (s.do === 'log') return 'log'
  if (s.do === 'notify' && (s.channel === 'phone' || s.channel === 'teams')) return s.channel
  return null
}

const where = (c?: Channel) => (c === 'phone' ? ' on your phone' : c === 'teams' ? ' on Teams' : '')

/** One line per thing a run did (or would do, in a test run). */
export function didLines(run: Run, spec?: Spec | null): { kind: 'send' | 'tell' | 'note'; channel?: Channel; text: string }[] {
  const out: { kind: 'send' | 'tell' | 'note'; channel?: Channel; text: string }[] = []
  for (const d of run.did ?? []) {
    if (d.do === 'carry_out') {
      out.push({ kind: 'send', text: `${d.dry ? 'Would send' : 'Sent'}: ${d.option ?? 'the message'}` })
    } else {
      out.push({ kind: 'tell', channel: d.channel, text: `${d.dry ? 'Would tell you' : 'Told you'}${where(d.channel)}: ${d.text ?? ''}` })
    }
  }
  // Lines from "note it" steps; the carry-out bookkeeping lines are already covered above.
  const sendTexts = (spec?.then ?? []).filter((s) => s.do === 'carry_out').map((s) => s.text)
  for (const line of run.log ?? []) {
    if (!line || sendTexts.some((t) => line.startsWith(t))) continue
    out.push({ kind: 'note', text: `${run.dry ? 'Would note' : 'Noted'}: ${line}` })
  }
  return out
}

/** "Told you about 1 card", "Sent 2 messages", "Nothing to do" for the stats line. */
export function summarise(run: Run): string {
  if (!run.ok) return 'The last run failed'
  const did = run.did ?? []
  const sent = did.filter((d) => d.do === 'carry_out').length
  const told = new Set(did.filter((d) => d.do === 'notify').map((d) => d.card)).size
  const parts: string[] = []
  if (sent) parts.push(`Sent ${sent} message${sent === 1 ? '' : 's'}`)
  if (told) parts.push(`${sent ? 'told' : 'Told'} you about ${told} card${told === 1 ? '' : 's'}`)
  return parts.join(', ') || 'Nothing to do'
}
