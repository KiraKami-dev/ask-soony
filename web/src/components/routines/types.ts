// Shapes of /api/routines (src/ask_soony/routines.py) and small helpers shared by the routine UI.

export type Field =
  | 'evidence' | 'headline' | 'option_action' | 'option_to' | 'supplier_name' | 'kind'
  | 'hours_left' | 'waiting_hours' | 'has_reply' | 'reply_after_launch' | 'silent_hours'
export type Channel = 'phone' | 'teams' | 'both'
/** What a routine looks at on each check. Missing means open decisions. */
export type Trigger = 'decisions' | 'waiting' | 'replies' | 'shipments'
/** How much a routine may do on its own. Missing means "tell". */
export type Mode = 'ask' | 'tell' | 'quiet'
export type StepKind = 'carry_out' | 'notify' | 'log' | 'chase' | 'call' | 'ask_status'

export interface Cond {
  id: string; field: Field; op: 'contains' | 'is' | 'any_of' | 'lt' | 'gt'; value: string | number | boolean; text: string
}
export interface Step {
  id: string
  do: StepKind
  text: string
  match?: { to?: string; action_contains?: string }
  channel?: Channel
  message?: string
}
export interface Spec { on?: Trigger; when: Cond[]; then: Step[] }

export interface Did {
  do: StepKind
  card?: string; shipment?: string; option?: string; text?: string; channel?: Channel; status?: string; dry?: boolean
  /** Set on a real run: the recorded action, for Undo. */
  action_id?: string | null
  /** Set in "ask me" mode: the proposal waiting for an OK instead of the action. */
  proposed?: string
}
export interface Run { at: string; ok: boolean; dry?: boolean; result?: unknown; error?: string; log?: string[]; did?: Did[] }
export interface Routine {
  id: string; name: string; description: string; spec?: Spec | null; code: string; enabled: boolean
  mode?: Mode
  created_from?: string | null; created_at?: string; runs?: Run[]; handled?: number; note?: string
}

/** Something a routine in "ask me" mode prepared and waits to be confirmed (GET /api/routines/proposals). */
export interface Proposal {
  id: string
  routine_id: string
  routine_name: string
  card_id?: string | null
  shipment_id?: string | null
  step: { do: StepKind | string; text?: string; channel?: Channel; [k: string]: unknown }
  preview_text?: string | null
  created_at?: string
  status: 'pending' | 'done' | 'skipped'
}
/** A routine that matches a card (GET /api/routines/would-handle). */
export interface WouldHandle { routine_id: string; routine_name: string; mode?: Mode; enabled: boolean }

/** The trust ladder: a routine starts by asking, then tells you, then runs quietly. */
export const MODES: { key: Mode; label: string; hint: string; changes: string }[] = [
  { key: 'ask', label: 'Ask me', hint: 'Prepares each step, you send it with one tap', changes: 'Nothing leaves before you tap Send.' },
  { key: 'tell', label: 'Tell me', hint: 'Does it, then tells you what it did', changes: 'It acts on its own and you get a note each time.' },
  { key: 'quiet', label: 'Quietly', hint: 'Does it, you find it under Handled for you', changes: 'No notes; it shows up in the history only.' },
]
export const modeOf = (r: Pick<Routine, 'mode'>): Mode => (r.mode === 'ask' || r.mode === 'quiet' ? r.mode : 'tell')

/** The trigger node: what the routine looks at, in the buyer's words. */
export function triggerText(on?: Trigger): { title: string; text: string } {
  if (on === 'waiting') return { title: 'Messages still waiting for an answer', text: 'Every portal check, the emails we sent that have no reply yet' }
  if (on === 'replies') return { title: 'Suppliers who answered', text: 'Every portal check, the decisions a supplier has replied to' }
  if (on === 'shipments') return { title: 'Shipments that stopped updating', text: 'Every portal check, shipments with no new status for a while' }
  return { title: 'Every portal check · open decisions', text: 'New files, replies and open decisions, about every minute' }
}

const hoursWord = (v: unknown) => {
  const n = Number(v)
  if (!Number.isFinite(n)) return String(v)
  return n < 1 ? `${Math.round(n * 60)} min` : `${Math.round(n * 10) / 10} h`
}
const yes = (v: unknown) => v === true || /^(yes|true|1)$/i.test(String(v))

/**
 * A condition as a buyer reads it. The recipe's own text wins; the numeric and yes/no fields get
 * plain words when it is missing, so a field name never reaches the screen.
 */
export function condWords(c: Cond): string {
  if (c.text?.trim()) return c.text
  const more = c.op === 'gt'
  switch (c.field) {
    case 'hours_left': return `${more ? 'More' : 'Less'} than ${hoursWord(c.value)} before the deadline`
    case 'waiting_hours': return `No answer for ${more ? 'more' : 'less'} than ${hoursWord(c.value)}`
    case 'silent_hours': return `No shipment update for ${more ? 'more' : 'less'} than ${hoursWord(c.value)}`
    case 'has_reply': return yes(c.value) ? 'The supplier has answered' : 'The supplier has not answered yet'
    case 'reply_after_launch': return yes(c.value) ? 'The answer promises a date after launch' : 'The answer promises a date before launch'
    default: return `${c.op === 'contains' ? 'Mentions' : 'Is'} ${String(c.value)}`
  }
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
export const PALETTE: { key: string; label: string; hint: string; make: () => Step }[] = [
  { key: 'phone', label: 'Tell me on my phone', hint: 'A push note with the case, nothing is sent out', make: () => ({ id: newId('s'), do: 'notify', channel: 'phone', message: '{headline}', text: 'Tell me on my phone' }) },
  { key: 'teams', label: 'Tell me on Teams', hint: 'A card in your Teams channel', make: () => ({ id: newId('s'), do: 'notify', channel: 'teams', message: '{headline}', text: 'Tell me on Teams' }) },
  { key: 'call', label: 'Call me', hint: 'Rings your phone and reads the case to you', make: () => ({ id: newId('s'), do: 'call', text: 'Call me' }) },
  { key: 'chase', label: 'Chase once if no answer', hint: 'One friendly reminder in the same email thread', make: () => ({ id: newId('s'), do: 'chase', text: 'Chase once in the same thread if there is no answer' }) },
  { key: 'log', label: 'Note it in the history', hint: 'A line in the case history, nobody is told', make: () => ({ id: newId('s'), do: 'log', message: 'Handled by a routine: {headline}', text: 'Note it in the history' }) },
  { key: 'ask_status', label: 'Ask for a status', hint: 'Emails the supplier for where the shipment is', make: () => ({ id: newId('s'), do: 'ask_status', text: 'Ask the supplier for the shipment status' }) },
]

export function paletteKey(s: Step): string | null {
  if (s.do === 'log' || s.do === 'chase' || s.do === 'call' || s.do === 'ask_status') return s.do
  if (s.do === 'notify' && (s.channel === 'phone' || s.channel === 'teams')) return s.channel
  return null
}

const where = (c?: Channel) => (c === 'phone' ? ' on your phone' : c === 'teams' ? ' on Teams' : '')

export type LineKind = 'send' | 'tell' | 'note' | 'chase' | 'call' | 'ask_status'

/** One line per thing a run did (or would do, in a test run). */
export function didLines(run: Run, spec?: Spec | null): { kind: LineKind; channel?: Channel; text: string }[] {
  const out: { kind: LineKind; channel?: Channel; text: string }[] = []
  for (const d of run.did ?? []) {
    const about = d.text || d.option || ''
    if (d.proposed) {
      out.push({ kind: (d.do === 'carry_out' ? 'send' : d.do === 'notify' || d.do === 'log' ? 'tell' : d.do) as LineKind, text: `Prepared for your OK${about ? `: ${about}` : ''}` })
    } else if (d.do === 'carry_out') {
      out.push({ kind: 'send', text: `${d.dry ? 'Would send' : 'Sent'}: ${d.option ?? 'the message'}` })
    } else if (d.do === 'chase') {
      out.push({ kind: 'chase', text: `${d.dry ? 'Would chase' : 'Chased'} once in the same thread${about ? `: ${about}` : ''}` })
    } else if (d.do === 'call') {
      out.push({ kind: 'call', text: `${d.dry ? 'Would call you' : 'Called you'}${about ? `: ${about}` : ''}` })
    } else if (d.do === 'ask_status') {
      out.push({ kind: 'ask_status', text: `${d.dry ? 'Would ask' : 'Asked'} for a status${about ? `: ${about}` : ''}` })
    } else if (d.do === 'log') {
      out.push({ kind: 'note', text: `${d.dry ? 'Would note' : 'Noted'}: ${about}` })
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
  const all = run.did ?? []
  const asked = all.filter((d) => d.proposed).length
  const did = all.filter((d) => !d.proposed)
  const n = (k: StepKind) => did.filter((d) => d.do === k).length
  const sent = n('carry_out')
  const told = new Set(did.filter((d) => d.do === 'notify').map((d) => d.card)).size
  const s = (k: number) => (k === 1 ? '' : 's')
  const parts: string[] = []
  if (sent) parts.push(`sent ${sent} message${s(sent)}`)
  if (n('chase')) parts.push(`chased ${n('chase')} time${s(n('chase'))}`)
  if (n('ask_status')) parts.push(`asked for ${n('ask_status')} status${n('ask_status') === 1 ? '' : 'es'}`)
  if (n('call')) parts.push(`called you ${n('call') === 1 ? 'once' : `${n('call')} times`}`)
  if (told) parts.push(`told you about ${told} case${s(told)}`)
  if (asked) parts.push(`prepared ${asked} for your OK`)
  const out = parts.join(', ')
  return out ? out[0].toUpperCase() + out.slice(1) : 'Nothing to do'
}

/** The last thing a real run did that may still be undone (a decision without an email). */
export function lastUndoable(runs?: Run[]): { id: string; text: string } | null {
  for (const r of runs ?? []) {
    if (r.dry || !r.ok) continue
    for (const d of [...(r.did ?? [])].reverse()) {
      if (d.action_id && d.do === 'carry_out' && d.status !== 'sent') return { id: d.action_id, text: d.option || d.text || 'its last step' }
    }
  }
  return null
}

/** A sample recipe for the empty state's small diagram. */
export const SAMPLE: Spec = {
  on: 'waiting',
  when: [{ id: 'c1', field: 'waiting_hours', op: 'gt', value: 2, text: 'A supplier has not answered for 2 hours' }],
  then: [{ id: 's1', do: 'chase', text: 'Send one friendly reminder in the same thread' }],
}
