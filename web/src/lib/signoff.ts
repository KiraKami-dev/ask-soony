import { fmtTime, post, type Card, type SignoffRequest } from '@/lib/api'
import { when } from '@/lib/decisions'

// Where a passed-on case or an internal message went, and what came back, in one honest line:
// "Sent to Maren's phone at 07:49", "Not delivered: no channel reaches Maren", "Maren approved at
// 08:12", "Maren declined: too expensive". Read from card.signoff_request (signoff.py).

const possessive = (who: string) => (who.endsWith('s') ? `${who}'` : `${who}'s`)

/** Where it went: "Maren's phone", "Teams", "Maren's phone and Teams". */
export function reachedWhere(r: Pick<SignoffRequest, 'who' | 'channels'>): string {
  const bits = [r.channels.includes('push') ? `${possessive(r.who)} phone` : '', r.channels.includes('teams') ? 'Teams' : '']
  return bits.filter(Boolean).join(' and ')
}

export function signoffLine(card: Card, nowMs: number): string | null {
  const r = card.signoff_request
  if (!r) return null
  const a = r.answer
  if (a?.decision === 'approved') return `${r.who} approved at ${when(a.at, nowMs)}${a.simulated ? ' (simulated for the demo)' : ''}`
  if (a?.decision === 'declined') return `${r.who} declined${a.note ? `: ${a.note}` : ''}`
  if (a?.decision === 'done') return `${r.who}: done at ${when(a.at, nowMs)}`
  const ack = r.acks[r.acks.length - 1]
  if (ack?.answer === 'got_it') return `${r.who} got it at ${when(ack.at, nowMs)}`
  if (!r.delivered) return `Not delivered: no channel reaches ${r.who}`
  return `Sent to ${reachedWhere(r)} at ${when(r.sent_at, nowMs)}`
}

/** True while a request waits for its person (Send again makes sense). */
export const signoffOpen = (card: Card) => !!card.signoff_request && !card.signoff_request.answer

/** The done screen after Pass it on or an internal message: where it really went. */
export function sentSub(who: string, channels: string[] | undefined, nowIso = new Date().toISOString()): string {
  if (!channels) return 'Recorded.'
  if (!channels.length) return `Not delivered: no channel reaches ${who}. Recorded only; tell them yourself.`
  return `Sent to ${reachedWhere({ who, channels: channels as SignoffRequest['channels'] })} at ${fmtTime(nowIso)}. You'll see their answer here.`
}

export const resendSignoff = (cardId: string) =>
  post<{ channels: string[]; at: string }>(`/api/cards/${encodeURIComponent(cardId)}/signoff/resend`)
