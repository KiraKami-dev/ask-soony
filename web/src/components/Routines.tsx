import { BellRing, Hand, Loader2, MoonStar, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { fmtDay, fmtTime, type Card, type State } from '@/lib/api'
import { headlineOf } from '@/lib/today'
import { cn } from '@/lib/utils'
import { MiniFlow } from './routines/FlowGraph'
import { hideDraft, pendingOf, refreshLive, refreshNow, useLive, type Suggestion } from './routines/live'
import { ProposalLines, ProposalSheet, groupProposals } from './routines/Proposals'
import { RoutineCard } from './routines/RoutineCard'
import { SAMPLE, body, call, type Mode, type Proposal, type Routine, type Run } from './routines/types'

// The Routines page (/routines). Routines the agents wrote after a person decided something by
// hand: a recipe of conditions and steps, compiled to small Python and run in the Monty sandbox
// on every check (routines.py). Here they are reviewed, changed, switched on or off, and drafted
// from a decision. Same calm language as Today: one centred column, whitespace instead of frames,
// three type sizes, green only for work a routine did. Every routine is one view: the
// When / Then / Always diagram that is also its editor. The page tells one story: every
// decision teaches the next one; a routine starts by asking, then tells you, then runs
// quietly; money and plan changes always stay with you.
//
// Links in: /routines?draft=<cardId> drafts a routine from that decision ("Show me how" on
// Today) and shows it first; /routines?routine=<id> scrolls to that routine ("How it decided").

const DECIDED: Record<string, string> = {
  approved: 'Approved', sent: 'Sent', escalated: 'Passed on', dismissed: 'Dismissed',
}

/** A headline with its numbers taken out, like the server's look-alike key (routines._shape). */
const shape = (t?: string | null) => (t ?? '').replace(/\d[\d,.' ]*\d|\d/g, '#').replace(/\s+/g, ' ').trim().toLowerCase()

/** Decided cards that look alike (same supplier, kind, headline shape and outcome), newest first. */
function lookAlikes(cards: Card[]): Card[][] {
  const by = new Map<string, Card[]>()
  for (const c of cards) {
    const k = [c.supplier_name ?? '', c.kind ?? '', shape(c.headline), c.status].join('|')
    by.set(k, [...(by.get(k) ?? []), c])
  }
  const at = (c: Card) => String(c.ui?.at ?? '')
  return [...by.values()]
    .map((g) => [...g].sort((a, b) => at(b).localeCompare(at(a))))
    .sort((a, b) => at(b[0]).localeCompare(at(a[0])))
}

const times = (n: number) => (n === 2 ? 'twice' : `${n} times`)

/** The trust ladder in one row: what changes as a routine earns trust, and what never does. */
function Ladder() {
  const rung = (icon: ReactNode, title: string, text: string, last?: boolean) => (
    <li className={cn('flex min-w-0 items-start gap-3 sm:px-5', !last && 'sm:border-r sm:border-hair', 'sm:first:pl-0')}>
      <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-soft text-ink-2 [&>svg]:block">{icon}</span>
      <span className="t-small min-w-0">
        <span className="block font-medium text-ink">{title}</span>
        <span className="block text-ink-3">{text}</span>
      </span>
    </li>
  )
  return (
    <ol className="mt-8 grid grid-cols-2 gap-x-4 gap-y-5 sm:grid-cols-4 sm:gap-x-0" aria-label="How a routine earns trust">
      {rung(<Hand size={15} strokeWidth={1.75} />, '1 · Ask me', 'It prepares, you send with one tap')}
      {rung(<BellRing size={15} strokeWidth={1.75} />, '2 · Tell me', 'It acts, then tells you')}
      {rung(<MoonStar size={15} strokeWidth={1.75} />, '3 · Quietly', 'It acts, you find it in the history')}
      {rung(<ShieldCheck size={15} strokeWidth={1.75} />, 'Always', 'Money and plan changes come to you', true)}
    </ol>
  )
}

/** Keeps proposals, drafts and "would handle" in step with the app's refresh, on every page. */
export function RoutinesLive({ state }: { state: State }) {
  useEffect(() => { void refreshLive() }, [state.now, state.last_check_at])
  return null
}

export function Routines({ state, onChanged, phone }: { state: State; onChanged: () => void; phone?: boolean }) {
  const [items, setItems] = useState<Routine[] | null>(null)
  const [draft, setDraft] = useState<{ routine: Routine; preview: Run } | null>(null)
  const [writing, setWriting] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [review, setReview] = useState<Proposal[] | null>(null)
  const [voiceEdits, setVoiceEdits] = useState<Record<string, { routine: Routine; preview: Run }>>({})
  const [target, setTarget] = useState<string | null>(null)
  const tested = useRef(new Set<string>())
  const top = useRef<HTMLDivElement>(null)

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/routines')
      if (r.ok) setItems((await r.json()).routines)
    } catch { /* offline for a moment: keep the last list */ }
  }, [])
  useEffect(() => {
    void load()
    void refreshNow()
    const t = window.setInterval(load, 15000)
    return () => clearInterval(t)
  }, [load])
  const live = useLive()
  const proposals = pendingOf(live)
  const groups = groupProposals(proposals, state)

  // Links in: draft from a decision, or show one routine. Read once per URL, then cleaned up.
  const search = window.location.search
  useEffect(() => {
    const q = new URLSearchParams(search)
    const cardId = q.get('draft')
    const rid = q.get('routine')
    if (!cardId && !rid) return
    window.history.replaceState(null, '', '/routines')
    if (cardId) { void suggest(cardId); top.current?.scrollIntoView({ block: 'start' }) }
    if (rid) setTarget(rid)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search])
  useEffect(() => {
    if (!target || !items) return
    const el = document.getElementById(`routine-${target}`)
    if (el) { el.scrollIntoView({ block: 'start', behavior: 'smooth' }); window.setTimeout(() => setTarget(null), 2400) }
  }, [target, items])

  const list = items ?? []
  const waiting = live.drafts.filter((d) => !list.some((r) => r.id === d.routine.id) && draft?.routine.id !== d.routine.id)
  // Test each waiting voice draft once on today's inbox (its stored test may be hours old).
  useEffect(() => {
    for (const d of waiting) {
      const id = d.routine.id
      if (tested.current.has(id) || voiceEdits[id] || !d.routine.spec) continue
      tested.current.add(id)
      call<{ routine: Routine; preview: Run }>('/api/routines/preview', body(d.routine))
        .then((out) => setVoiceEdits((v) => (v[id] ? v : { ...v, [id]: { routine: { ...d.routine, code: out.routine.code }, preview: out.preview } })))
        .catch(() => { /* keep the stored test run */ })
    }
  }, [waiting, voiceEdits])

  // Decisions made by hand; a card with a routine says so, the card behind an open draft is hidden.
  const automatedBy = new Map(list.filter((r) => r.created_from).map((r) => [r.created_from!, r.name]))
  const drafting = new Set([draft?.routine.created_from, writing, ...waiting.map((d) => d.card_id)])
  const suggestions = live.suggestions.filter((x) => !x.card_ids.some((id) => drafting.has(id) || automatedBy.has(id)))
  const suggested = new Set(suggestions.flatMap((x) => x.card_ids))
  const decided = lookAlikes(state.cards.filter((c) => c.status in DECIDED && !drafting.has(c.id) && !suggested.has(c.id)))
  const writingCard = state.cards.find((c) => c.id === writing)

  async function suggest(cardId: string) {
    setWriting(cardId); setError(''); setDraft(null)
    top.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })
    try {
      setDraft(await call<{ routine: Routine; preview: Run }>('/api/routines/suggest', { card_id: cardId }))
    } catch (e) {
      setError(`The agent could not write a routine: ${(e as Error).message}.`)
    } finally { setWriting(null) }
  }
  /** Save a draft; a new routine starts by asking unless the buyer chose otherwise. */
  const save = (routine: Routine, mode: Mode) => call('/api/routines', { ...body(routine), mode })
  async function turnOn(mode: Mode) {
    if (!draft) return
    const id = draft.routine.id
    await save(draft.routine, mode)
    setDraft(null)
    await load()
    setTarget(id)
    onChanged()
  }
  const changed = () => { void load(); void refreshNow(); onChanged() }
  const label = (text: string) => <h2 className="t-small font-medium text-ink-3">{text}</h2>
  const suggestionWords = (x: Suggestion) => {
    const card = state.cards.find((c) => c.id === x.last_card_id)
    const about = card ? headlineOf(card, state) : x.label ?? ''
    const did = x.decision === 'approve' ? x.action || 'Approved it' : x.decision === 'escalate' ? 'Passed it on' : 'Dismissed it'
    return { did, about }
  }

  return (
    <main className={cn('mx-auto w-full max-w-[1040px] pb-32', phone ? 'px-6 pt-6' : 'px-12 pt-16')}>
      <div ref={top} className="scroll-mt-24" />
      <h1 className="t-big text-ink">Routines</h1>
      <p className="t-body mt-4 max-w-[640px] text-ink-2">
        Every decision teaches the next one. A routine starts by asking, then tells you, then runs quietly. Money and plan changes always stay with you.
      </p>
      <Ladder />

      {groups.length > 0 && (
        <section className="mt-12 space-y-3" aria-label="Waiting for your OK">
          {label('Waiting for your OK')}
          <ProposalLines groups={groups} onReview={setReview} max={6} />
        </section>
      )}

      {(writing || draft || error) && (
        <section className="mt-14" aria-label="Handle them the same way">
          {writing && (
            <div className="rise">
              {label('Writing a routine from your decision')}
              <p className="t-body mt-3 flex items-center gap-2.5 text-ink"><Loader2 size={17} className="animate-spin text-ink-3" /> One moment…</p>
              <p className="t-small mt-2 max-w-[640px] text-ink-2">
                {writingCard ? <>From “{headlineOf(writingCard, state)}” </> : null}
                The agent writes the steps, checks them in the sandbox and tests them on today's inbox before you see them. Up to half a minute.
              </p>
            </div>
          )}
          {draft && (
            <div className="rise">
              <RoutineCard
                key={`draft-${draft.routine.id}`}
                big
                routine={draft.routine}
                onChanged={changed}
                draft={{
                  preview: draft.preview,
                  onTurnOn: turnOn,
                  onDiscard: () => setDraft(null),
                  onReplace: (routine, preview) => setDraft({ routine, preview }),
                }}
              />
            </div>
          )}
          {error && <p className="t-small text-late" role="alert">{error}</p>}
        </section>
      )}

      {suggestions.length > 0 && (
        <section className="mt-16" aria-label="Worth a routine">
          {label('Worth a routine')}
          <ul className="mt-3 divide-y divide-hair border-y border-hair">
            {suggestions.map((x) => {
              const { did, about } = suggestionWords(x)
              return (
                <li key={x.key} className="flex flex-col gap-3 py-5 sm:flex-row sm:items-center sm:justify-between sm:gap-8">
                  <div className="min-w-0">
                    <p className="t-small text-routine"><span className="dot-routine">You did this {times(x.count)}</span>{x.supplier ? <span className="text-ink-3"> · {x.supplier}</span> : null}</p>
                    <p className="t-body mt-1 text-ink">{did}</p>
                    {about && <p className="t-small mt-0.5 line-clamp-1 text-ink-3">For cases like “{about}”</p>}
                  </div>
                  <button
                    className="t-small inline-flex h-10 shrink-0 cursor-pointer items-center justify-center rounded-xl bg-accent px-5 font-medium text-white shadow-[0_1px_2px_rgba(35,72,208,.25)] hover:bg-[#1d3db5] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-60 max-sm:w-full"
                    disabled={!!writing}
                    onClick={() => void suggest(x.last_card_id)}
                  >
                    Make it a routine
                  </button>
                </li>
              )
            })}
          </ul>
        </section>
      )}

      {waiting.length > 0 && (
        <section className="mt-16 space-y-14" aria-label="Drafted from your call">
          {label('Drafted from your call')}
          {waiting.map((d) => {
            const id = d.routine.id
            const cur = voiceEdits[id] ?? { routine: d.routine, preview: d.preview ?? { at: d.at ?? '', ok: true, dry: true } }
            return (
              <div key={`voice-${id}`}>
                {d.instruction && <p className="t-body mb-5 text-ink-2">You said: <span className="text-ink">“{d.instruction}”</span></p>}
                <RoutineCard
                  routine={cur.routine}
                  onChanged={changed}
                  draft={{
                    preview: cur.preview,
                    onTurnOn: async (mode) => { await save(cur.routine, mode); hideDraft(id); await load(); onChanged() },
                    onDiscard: () => hideDraft(id),
                    onReplace: (routine, preview) => setVoiceEdits((v) => ({ ...v, [id]: { routine, preview } })),
                  }}
                />
              </div>
            )
          })}
        </section>
      )}

      <section className="mt-16" aria-label="Your routines">
        {label('Your routines')}
        {items && list.length === 0 && !draft && !writing && (
          <div className="mt-4">
            <p className="t-body max-w-[640px] text-ink-2">
              None yet. A routine is a decision you made once, done the same way the next time it comes up.
            </p>
            <div className="mt-5 max-w-[860px]"><MiniFlow spec={SAMPLE} /></div>
            <p className="t-small mt-3 text-ink-3">
              {suggestions.length ? 'Start with one of the repeats above, or any decision below.' : 'Pick a decision below and the agent writes the steps for you to check.'}
            </p>
          </div>
        )}
        <div className="mt-6 divide-y divide-hair">
          {list.map((r) => (
            <div key={r.id} id={`routine-${r.id}`} className={cn('scroll-mt-24 py-12 transition-[background-color,box-shadow] duration-700 first:pt-4', target === r.id && 'rounded-2xl bg-soft shadow-[0_0_0_16px_var(--color-soft)]')}>
              <RoutineCard routine={r} onChanged={changed} />
            </div>
          ))}
        </div>
      </section>

      <section className="mt-20" aria-label="Automate a decision you made">
        {label('Automate a decision you made')}
        {decided.length === 0 && <p className="t-small mt-3 text-ink-3">{draft ? 'Review the suggestion above.' : suggestions.length ? 'Everything else you decided is covered above.' : 'Nothing decided yet.'}</p>}
        <ul className="mt-2">
          {decided.map((g) => {
            const c = g[0]
            const auto = g.map((x) => automatedBy.get(x.id)).find(Boolean)
            return (
              <li key={c.id} className="flex flex-col gap-1 py-3 sm:flex-row sm:items-baseline sm:justify-between sm:gap-6">
                <div className="min-w-0">
                  <div className="t-body line-clamp-2 text-ink">
                    {headlineOf(c, state)}
                    {g.length > 1 && <span className="t-small ml-2 whitespace-nowrap rounded-full bg-soft px-2 py-0.5 align-[2px] text-ink-2">{g.length} like this</span>}
                  </div>
                  <div className="t-small text-ink-3">
                    {DECIDED[c.status]}{c.ui?.at ? ` · ${fmtDay(c.ui.at)} ${fmtTime(c.ui.at)}` : ''}{c.supplier_name ? ` · ${c.supplier_name}` : ''}
                  </div>
                </div>
                {auto ? (
                  <span className="t-small dot-routine shrink-0 text-routine sm:max-w-[45%] sm:text-right">‘{auto}’</span>
                ) : (
                  <button className="t-small uline w-fit shrink-0 disabled:opacity-60" disabled={!!writing} onClick={() => void suggest(c.id)}>
                    Handle cases like this
                  </button>
                )}
              </li>
            )
          })}
        </ul>
      </section>

      <ProposalSheet items={review} ctx={state} onClose={() => setReview(null)} onChanged={changed} />
    </main>
  )
}
