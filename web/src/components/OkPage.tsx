import { type ReactNode, useEffect, useState } from 'react'
import { costText, sentence, type Card } from '@/lib/api'
import { when } from '@/lib/decisions'
import { firstNameOf } from '@/lib/role'
import { claimOf, plain, summaryLine } from '@/lib/today'
import { cn } from '@/lib/utils'

// The sign-off page: a passed-on decision for its approver, or an internal message for its person,
// reached from a push or Teams (/ok/<token>, no dashboard password, only a valid link). Phone
// first and calm: who asks, the case in one sentence, the recommendation with its cost, two
// buttons. Approve carries out the option through the same path as the inbox (signoff.py).

interface Answer { decision: 'approved' | 'declined' | 'done'; by: string; at: string; note?: string | null; simulated?: boolean }
interface Page {
  kind: 'escalation' | 'notify'
  to: string
  who: string
  asked_by: string
  asked_by_short: string
  asked_at: string | null
  headline: string | null
  card: Partial<Card> | null
  status: string | null
  recommendation: { action: string | null; cost: string | null; effect: string | null } | null
  note: string | null
  subject: string | null
  message: string | null
  answer: Answer | null
  acks: { answer: 'got_it' | 'done'; at: string }[]
  now: string
}

const url = (token: string) => `/api/ok/${encodeURIComponent(token)}`
const blue = 't-body h-[54px] w-full cursor-pointer rounded-[14px] bg-accent font-medium text-white shadow-[0_1px_2px_rgba(35,72,208,.25)] hover:bg-[#1d3db5] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-default disabled:opacity-40'
const quiet = 't-body h-[54px] w-full cursor-pointer rounded-[14px] bg-soft text-ink hover:bg-hair focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-default disabled:opacity-40'

/** The contradiction as one plain sentence, as Today says it. */
function caseLine(card: Partial<Card> | null): string {
  if (!card) return ''
  const c = { options: [], evidence: [], ...card } as unknown as Card
  const claim = claimOf(c)
  if (claim) return `${claim.pre}${claim.sys.value}${claim.mid}${claim.doc.value}${claim.post}`.replace(/^./, (x) => x.toUpperCase())
  return summaryLine(c)
}

const stop = (t: string) => (/[.!?]$/.test(t) ? t : `${t}.`)
/** A long headline keeps its first clause: 'The 8,000 art books arrive around 20:33; the line ...'. */
const headOf = (h: string | null, part?: string | null) => {
  const t = plain(h, part)
  return sentence(t.length > 70 ? t.split(/;\s/)[0] : t)
}

/** 'Message DC site lead: Expect the van' -> 'Expect the van'. */
const subjectOf = (s: string | null, to: string) => (s ?? '').replace(new RegExp(`^(Message|Tell|Ask)\\s+${to.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}:?\\s*`), '') || s || ''

export function OkPage({ token }: { token: string }) {
  const [page, setPage] = useState<Page | null>(null)
  const [gone, setGone] = useState(false)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [dup, setDup] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)

  const load = () =>
    fetch(url(token))
      .then(async (r) => (r.ok ? setPage(await r.json()) : setGone(true)))
      .catch(() => setGone(true))
  useEffect(() => {
    document.title = 'Your OK · Lighthouse'
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  async function answer(a: string, force = false) {
    setError(''); setBusy(a)
    try {
      const r = await fetch(url(token), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ answer: a, note: note.trim() || null, force }) })
      const j = await r.json().catch(() => ({}))
      if (r.status === 409 && j.detail?.duplicate) { setDup(j.detail.text); return }
      if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : 'That did not go through. Try again.')
      setDup(null)
      if (a === 'got_it') { await load(); return }
      setDone(j.text)
    } catch (e) {
      setError((e as Error).message)
    } finally { setBusy(null) }
  }

  // The approver goes on to Launch as themselves (?as=<first name>, remembered on this device).
  const asWho = page?.kind === 'escalation' ? firstNameOf(page.who || page.to).toLowerCase() : ''
  const appLink = asWho ? `/launch?as=${encodeURIComponent(asWho)}` : '/'
  const shell = (children: ReactNode) => (
    <main className="min-h-full bg-ground px-6 pb-[max(32px,env(safe-area-inset-bottom))] pt-12">
      <div className="mx-auto max-w-[440px]">
        {children}
        {page && (
          <p className="t-small mt-10 text-center">
            <a href={appLink} className="text-ink-3 hover:text-ink">Open in Lighthouse{asWho ? ': where the launch stands' : ''}</a>
          </p>
        )}
      </div>
    </main>
  )
  const tick = (
    <svg width="36" height="36" viewBox="0 0 24 24" aria-hidden="true" className="text-accent">
      <path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
  if (gone) return shell(
    <>
      <h1 className="t-big text-ink">This link has expired.</h1>
      <p className="t-body mt-4 text-ink-2">The launch is over or the link is not valid any more. Nothing to do here.</p>
    </>,
  )
  if (!page) return shell(<p className="t-body text-ink-3">Loading…</p>)
  if (done) return shell(
    <div className="rise" role="status">
      {tick}
      <h1 className="t-big mt-6 text-ink">{done}</h1>
      <p className="t-body mt-4 text-ink-2">You can close this page.</p>
    </div>,
  )

  const nowMs = new Date(page.now).getTime()
  const part = page.card?.part_name
  const asked = [page.kind === 'escalation' ? `${page.asked_by_short || 'The buyer'} asks` : `From ${page.asked_by_short || 'the buyer'}`, page.asked_at ? when(page.asked_at, nowMs) : '']
    .filter(Boolean).join(' · ')

  // Answered already (here, on another device, or the case moved on).
  const a = page.answer
  const closed = a || (page.kind === 'escalation' && page.status !== 'escalated')
  if (closed) {
    const said = !a
      ? 'This case is not waiting for you any more.'
      : a.decision === 'approved' ? `Approved at ${when(a.at, nowMs)}${a.simulated ? ' (simulated for the demo)' : ''}.`
        : a.decision === 'declined' ? `Declined at ${when(a.at, nowMs)}${a.note ? `: ${a.note}` : '.'}`
          : `Marked done at ${when(a.at, nowMs)}.`
    return shell(
      <>
        <p className="t-small text-ink-3">{asked}</p>
        <h1 className="t-big mt-3 text-ink">{stop(headOf(page.headline, part))}</h1>
        <p className="t-body mt-6 text-ink">{said}</p>
        <p className="t-body mt-2 text-ink-2">Nothing more to do here.</p>
      </>,
    )
  }

  if (page.kind === 'notify') {
    const got = page.acks.find((x) => x.answer === 'got_it')
    return shell(
      <>
        <p className="t-small text-ink-3">{asked}</p>
        <h1 className="t-big mt-3 text-ink">{sentence(subjectOf(page.subject, page.to))}</h1>
        {page.headline && <p className="t-body mt-4 text-ink-2">About: {stop(headOf(page.headline, part))}</p>}
        {page.message && <p className="t-body mt-6 whitespace-pre-wrap rounded-[14px] bg-soft px-4 py-3.5 text-ink">{page.message}</p>}
        {error && <p className="t-body mt-6 text-late" role="alert">{error}</p>}
        <div className="mt-9 grid gap-3">
          <button onClick={() => void answer('done')} disabled={!!busy} className={blue}>{busy === 'done' ? 'Sending…' : 'Done'}</button>
          {got
            ? <p className="t-small text-center text-ink-3">You said “Got it” at {when(got.at, nowMs)}. Tap Done when it is done.</p>
            : <button onClick={() => void answer('got_it')} disabled={!!busy} className={quiet}>{busy === 'got_it' ? 'Sending…' : 'Got it'}</button>}
        </div>
        <p className="t-small mt-4 text-center text-ink-3">{page.asked_by_short || 'The buyer'} sees your answer on the case.</p>
      </>,
    )
  }

  const rec = page.recommendation
  const cost = costText(rec?.cost)
  const line = caseLine(page.card)
  return shell(
    <>
      <p className="t-small text-ink-3">{asked}{page.card?.supplier_name ? ` · ${page.card.supplier_name}` : ''}</p>
      <h1 className="t-big mt-3 text-ink">{stop(headOf(page.headline, part))}</h1>
      {line && <p className="t-body mt-4 text-ink-2">{line}</p>}

      {rec?.action && (
        <div className="mt-9">
          <p className="t-small mb-1.5 text-ink-3">{page.asked_by_short || 'The buyer'} recommends</p>
          <p className="t-body text-ink">{sentence(plain(rec.action, part))}</p>
          <p className="t-body text-ink-2">{cost === 'No cost' ? 'No cost.' : `Cost: ${stop(cost)}`}{rec.effect ? ` ${stop(sentence(rec.effect))}` : ''}</p>
        </div>
      )}
      {page.note && (
        <p className="t-body mt-6 text-ink-2"><span className="text-ink-3">{page.asked_by_short || 'The buyer'}: </span>“{page.note}”</p>
      )}

      <div className="mt-9">
        <label htmlFor="note" className="t-small text-ink-3">A line back (optional)</label>
        <input id="note" value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} className="t-body mt-2 h-12 w-full rounded-[14px] bg-soft px-4 text-ink focus:outline-2 focus:outline-accent" />
      </div>

      {dup && (
        <div className="t-body mt-6 text-ink" role="alert">
          {dup}{' '}
          <button onClick={() => void answer('approve', true)} disabled={!!busy} className="uline cursor-pointer">Send anyway</button>
        </div>
      )}
      {error && <p className="t-body mt-6 text-late" role="alert">{error}</p>}
      <div className={cn('mt-9 grid gap-3')}>
        <button onClick={() => void answer('approve')} disabled={!!busy || !rec?.action} className={blue}>
          {busy === 'approve' ? 'Approving…' : 'Approve'}
        </button>
        <button onClick={() => void answer('decline')} disabled={!!busy} className={quiet}>
          {busy === 'decline' ? 'Sending…' : 'Decline'}
        </button>
      </div>
      <p className="t-small mt-4 text-center text-ink-3">
        Approve carries it out now, in your name. Decline sends it back to {page.asked_by_short || 'the buyer'}.
      </p>
    </>,
  )
}
