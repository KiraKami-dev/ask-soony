import * as Dialog from '@radix-ui/react-dialog'
import { ArrowLeft, Check, Database, FileText } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { AnchorCrop, NUM_OR_ID, fileUrl, normTok, useAnchors, type Anchor } from '@/components/SourceAnchors'
import { VoiceCall, type Prepared, type ScreenWhat } from '@/components/VoiceCall'
import { pendingOf, undoAction, useLive } from '@/components/routines/live'
import { UndoAction } from '@/components/routines/Undo'
import { ProposalLines, ProposalSheet, RoutineLine, WouldLine, groupProposals } from '@/components/routines/Proposals'
import type { Proposal } from '@/components/routines/types'
import { DONE_WORD, post, sentence, tidy, type Card, type Evidence, type State } from '@/lib/api'
import { actionIdOf, languageOf, replyFacts, routineActionOf, routineOf, splitValue, when } from '@/lib/decisions'
import {
  actionText, claimOf, costLine, costsMoney, docKind, forToday, headlineOf, launchStatus, ordinal, plural,
  recommendedIndex, routineCount, shortName, shortTitle, summaryLine, type Claim,
} from '@/lib/today'
import { cn } from '@/lib/utils'

// The Sunday flow: one decision at a time, the source one tap away, then "Done" and what's next.

export interface DoneInfo {
  card: Card
  text: string
  sub: string
  actionId: string | null
  /** Open look-alikes to offer a routine for; 0 when none (or a routine exists). */
  offer: number
  /**
   * No open look-alikes, but this is the n-th time the same kind of case was decided the same
   * way (GET /api/routines/suggestions): offer to make it a routine. `words` is what was done.
   */
  repeat?: { n: number; words: string } | null
  k: number
}

/** Which part of the evidence drawer to open. */
type Drawer = (card: Card, tab: 'evidence' | 'trail') => void

interface Props {
  state: State
  nowMs: number
  phone: boolean
  card: Card | null
  order: Card[]
  onFocus: (id: string) => void
  done: DoneInfo | null
  onDone: (d: { card: Card; text: string; sub: string; actionId: string | null }) => void
  onClearDone: () => void
  onChanged: () => void
  onRoutines: (request?: { cardId: string; k: number }) => void
  go: (path: string) => void
  ringing?: boolean
  onRingingDone?: () => void
  /** Routine proposals waiting for an OK plus drafts from calls (the live store is read here). */
  pending: number
  /** A case opened by link (/d/<id>) that is no longer open: shown as it stands, not decided. */
  caseCard?: Card | null
  /** Arrived from Plan: a quiet way back. */
  onBack?: (() => void) | null
  onDrawer: Drawer
}

/** Opens the review sheet for what routines prepared. */
type Review = (items: Proposal[]) => void

const typing = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))

export function Today(p: Props) {
  const { state, nowMs, phone, card, done } = p
  const handled = routineCount(state, nowMs)
  const [review, setReview] = useState<Proposal[] | null>(null)
  // A live call holds its decision on screen: Done shows after the call ends, not mid-sentence.
  const [call, setCall] = useState<Card | null>(null)
  const callCard = call ? state.cards.find((c) => c.id === call.id) ?? call : null
  const focusCard = callCard ?? card
  const view = callCard ? 'decide' : done ? 'done' : p.caseCard ? 'case' : card ? 'decide' : 'clear'

  const foot = <FootLines handled={handled} onRoutines={() => p.onRoutines()} nowMs={nowMs} center={!phone} />

  const body =
    view === 'done' ? (
      <DoneView key={`done-${done!.k}`} {...p} done={done!} />
    ) : view === 'case' ? (
      <CaseView key={`case-${p.caseCard!.id}`} card={p.caseCard!} state={state} nowMs={nowMs} onChanged={p.onChanged} onDrawer={p.onDrawer} />
    ) : view === 'decide' ? (
      <Focus
        key={focusCard!.id}
        {...p}
        card={focusCard!}
        onReview={setReview}
        onCall={(live) => setCall(live ? focusCard : null)}
        doneText={done && done.card.id === focusCard!.id ? done.text : undefined}
      />
    ) : (
      <AllClear state={state} nowMs={nowMs} handled={handled} onRoutines={p.onRoutines} onReview={setReview} onChanged={p.onChanged} go={p.go} />
    )
  const sheet = (
    <ProposalSheet items={review} ctx={state} onClose={() => setReview(null)} onChanged={p.onChanged} />
  )
  const back = p.onBack && (
    <button
      onClick={p.onBack}
      className={cn('t-small inline-flex cursor-pointer items-center gap-1.5 text-ink-3 hover:text-ink focus-visible:outline-2 focus-visible:outline-accent', phone ? 'mb-6' : 'absolute -top-14 left-0')}
    >
      <ArrowLeft size={15} strokeWidth={1.75} aria-hidden="true" />Plan
    </button>
  )

  if (phone) {
    return (
      <main className="mx-auto w-full max-w-[560px] px-7 pb-[220px] pt-5">
        {back}
        {body}
        {view === 'decide' && handled > 0 && <div className="mt-14">{foot}</div>}
        {sheet}
      </main>
    )
  }
  // Desktop: a very quiet list on the left, the decision in a 640 px column.
  return (
    <div className="relative flex-1">
      <div className="mx-auto grid w-full max-w-[1440px] grid-cols-[240px_minmax(0,640px)] gap-x-16 px-12 pb-32 pt-24 xl:grid-cols-[300px_640px] xl:gap-x-[124px] xl:px-24">
        <aside aria-label="Needs you">
          {p.order.length > 0 && <QuietList {...p} />}
        </aside>
        <main className="relative min-w-0">{back}{body}</main>
      </div>
      {view !== 'clear' && view !== 'case' && <div className="fixed inset-x-0 bottom-8 z-10 flex justify-center">{foot}</div>}
      {sheet}
    </div>
  )
}

// ---------------------------------------------------------------- left list

function QuietList({ order, card, onFocus, state, nowMs }: Props) {
  // One line per problem: look-alikes fold into their first card.
  const seen = new Map<string, { c: Card; n: number }>()
  for (const c of order) {
    const k = `${c.supplier_id ?? c.supplier_name}|${c.kind}|${shortTitle(c, state).replace(/\d[\d,.]*/g, '#')}`
    const e = seen.get(k)
    if (e) e.n += 1
    else seen.set(k, { c, n: 1 })
  }
  const rows = [...seen.values()]
  const shown = rows.slice(0, 5)
  const more = rows.slice(5).reduce((n, r) => n + r.n, 0)
  return (
    <div>
      <p className="t-small mb-3 text-ink-3">Needs you · {plural(order.length, 'decision')}</p>
      <ul className="-mx-3.5">
        {shown.map(({ c, n }) => {
          const on = card?.id === c.id
          return (
            <li key={c.id}>
              <button
                onClick={() => onFocus(c.id)}
                aria-current={on ? 'true' : undefined}
                className={cn(
                  't-small grid w-full cursor-pointer grid-cols-[8px_1fr_auto] items-baseline gap-3.5 rounded-[10px] px-3.5 py-3 text-left focus-visible:outline-2 focus-visible:outline-accent',
                  on ? 'bg-soft font-medium text-ink' : 'text-ink-2 hover:text-ink',
                )}
              >
                <i className={cn('h-[7px] w-[7px] -translate-y-px rounded-full', on ? 'bg-accent' : 'bg-ink-3')} />
                <span className="min-w-0">
                  <span className="line-clamp-2">{shortTitle(c, state)}</span>
                  {n > 1 && <span className="block font-normal text-ink-3">{n} like this, one decision each</span>}
                </span>
                <span className="font-normal text-ink-3">{c.deadline ? when(c.deadline, nowMs) : ''}</span>
              </button>
            </li>
          )
        })}
      </ul>
      {more > 0 && <p className="t-small mt-2 text-ink-3">+{more} more</p>}
      <p className="t-small mt-6 text-ink-3"><kbd>J</kbd> <kbd>K</kbd> to move, <kbd>A</kbd> to approve</p>
    </div>
  )
}

// ---------------------------------------------------------------- one decision

function Focus(p: Props & {
  card: Card
  onReview: Review
  /** A call started or ended on this decision. */
  onCall: (live: boolean) => void
  /** Decided while a call is still live: shown in place of the buttons. */
  doneText?: string
}) {
  const { card, state, nowMs, phone } = p
  const [sel, setSel] = useState(() => recommendedIndex(card))
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [others, setOthers] = useState(false)
  const [passOn, setPassOn] = useState(false)
  const [note, setNote] = useState('')
  const [source, setSource] = useState<number | null>(null)
  // What the voice assistant prepared and reads out; "yes" or a tap confirms it.
  const [prepared, setPrepared] = useState<Prepared | null>(null)
  const [screenEvent, setScreenEvent] = useState<{ k: number; text: string } | null>(null)
  const [onCall, setOnCall] = useState(false)
  // An email to a supplier is shown before it goes out: it cannot be taken back.
  const [review, setReview] = useState(false)
  const [draft, setDraft] = useState('')
  const [linesEl, setLinesEl] = useState<HTMLDivElement | null>(null)
  const anchors = useAnchors(card.id)
  const claim = claimOf(card)
  const today = forToday(card, state, nowMs)
  const opt = card.options[sel]
  const recommended = sel === recommendedIndex(card)
  const money = opt ? costsMoney(card, sel) : false
  const emails = !!opt && opt.channel === 'email' && /@/.test(opt.to ?? '')
  const late = card.level === 'red'

  async function act(fn: () => Promise<{ text: string; sub: string; actionId: string | null }>) {
    setBusy(true); setError('')
    try {
      const out = await fn()
      p.onDone({ card, ...out })
      p.onChanged()
      setPrepared(null)
      // Decided with a tap while the assistant is on the line: tell it, so it does not decide again.
      if (onCall) setScreenEvent({ k: Date.now(), text: `The person decided on screen: ${out.text} It is done; do not call decide. Confirm in one short sentence and say goodbye.` })
    } catch (e) {
      setError(`That did not go through: ${(e as Error).message}. Nothing was sent.`)
    } finally { setBusy(false); setConfirm(false) }
  }
  const approve = (text?: string) =>
    act(async () => {
      const edited = text != null && text.trim() !== (opt?.draft ?? '').trim()
      const r = await post<{ action?: { id?: string; status?: string } }>(
        `/api/cards/${encodeURIComponent(card.id)}/approve`,
        edited ? { option: sel, text } : { option: sel },
      )
      const status = r.action?.status
      const at = new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
      const sent = status === 'sent'
      const sub = sent
        ? `Sent to ${shortName(card.supplier_name) || opt?.to} at ${at}. You'll see the reply here.`
        : opt?.channel === 'notify' && opt.to ? `${opt.to} has it now.` : 'Recorded.'
      // A sent email cannot be undone, so no Undo is offered for it.
      return { text: doneFrom(card, sel, status), sub, actionId: sent ? null : r.action?.id ?? null }
    })
  const openReview = () => { setDraft(opt?.draft ?? ''); setReview(true) }

  // The voice assistant prepared a decision: show it. An email opens its review sheet.
  const onPrepared = (pr: Prepared | null) => {
    setPrepared(pr)
    if (!pr) { setReview(false); return }
    const o = pr.decision === 'approve' ? card.options[pr.option_index] : undefined
    if (!o) return
    setSel(pr.option_index); setOthers(false); setConfirm(false)
    if (o.channel === 'email' && /@/.test(o.to ?? '')) { setSource(null); setDraft(o.draft ?? ''); setReview(true) }
  }
  // show_on_screen from the assistant: the source side, the options or the email.
  const onScreen = (what: ScreenWhat): boolean => {
    if (what === 'options') { setSource(null); setReview(false); setOthers(true); return true }
    if (what === 'email') {
      if (!emails) return false
      setSource(null); openReview(); return true
    }
    const isSys = (e: Evidence) => SYS.test(e.source ?? '')
    const i = claim
      ? (what === 'system' ? claim.sys.index : claim.doc.index)
      : card.evidence.findIndex((e) => (what === 'system' ? isSys(e) : !isSys(e)))
    if (i < 0) return false
    setReview(false); setSource(i); return true
  }
  const voicePrepared = !!prepared && !p.doneText
  const emailPrepared = voicePrepared && prepared!.decision === 'approve' && emails && sel === prepared!.option_index
  const escalate = () =>
    act(async () => {
      const r = await post<{ id?: string }>(`/api/cards/${encodeURIComponent(card.id)}/escalate`, { note })
      return { text: `Passed on to ${card.approver || 'the approver'}.`, sub: 'You will see their answer here.', actionId: r?.id ?? null }
    })
  const dismiss = () =>
    act(async () => {
      const r = await post<{ id?: string }>(`/api/cards/${encodeURIComponent(card.id)}/dismiss`)
      return { text: 'Taken off the list.', sub: 'Nothing was sent.', actionId: r?.id ?? null }
    })
  const onApprove = () => (emails ? openReview() : money ? setConfirm(true) : void approve())

  // Keyboard (desktop): j/k walk, a asks, Enter approves, Esc cancels.
  const ref = useRef({ approve, confirm, busy, emails, openReview })
  ref.current = { approve, confirm, busy, emails, openReview }
  useEffect(() => {
    if (phone) return
    const onKey = (e: KeyboardEvent) => {
      if (typing(e.target) || e.metaKey || e.ctrlKey || e.altKey || source != null || review) return
      const k = e.key
      if (ref.current.confirm && k === 'Enter') { e.preventDefault(); void ref.current.approve() }
      else if (k === 'Escape') { setConfirm(false); setOthers(false) }
      else if ((k === 'a' || k === 'A') && opt && !ref.current.busy) {
        e.preventDefault()
        if (ref.current.emails) ref.current.openReview()
        else setConfirm(true)
      }
      else if (k === 'j' || k === 'k' || k === 'ArrowDown' || k === 'ArrowUp') {
        const i = p.order.findIndex((c) => c.id === card.id)
        const n = p.order[Math.min(p.order.length - 1, Math.max(0, i + (k === 'j' || k === 'ArrowDown' ? 1 : -1)))]
        if (n && n.id !== card.id) { e.preventDefault(); p.onFocus(n.id) }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [phone, source, review, opt, card.id, p])

  const decideBy = card.deadline ? (new Date(card.deadline).getTime() <= nowMs ? 'decide now' : `decide by ${when(card.deadline, nowMs)}`) : ''
  const meta = [card.supplier_name || card.part_name, decideBy].filter(Boolean).join(' · ')

  const actions = opt && (
    <div className={cn(phone ? 'fixed inset-x-0 bottom-0 z-20 bg-gradient-to-t from-ground from-70% to-ground/0 px-6 pb-[max(28px,env(safe-area-inset-bottom))] pt-8' : 'mt-8')}>
      <div className={cn(phone && 'mx-auto max-w-[520px]')}>
        {confirm && (
          <p className={cn('t-small mb-3 text-ink-2', phone && 'text-center')} role="alert">
            Approve: <span className="text-ink">{actionText(card, sel).replace(/\.$/, '')}</span> ({costLine(card, sel).replace(/\.$/, '')})
            {!phone && <span className="text-ink-3"> · <kbd>Enter</kbd> confirms</span>}
          </p>
        )}
        {error && <p className="t-small mb-3 text-late" role="alert">{error}</p>}
        <div className={cn(phone ? 'grid justify-items-center gap-3.5' : 'flex flex-wrap items-center gap-6', p.ringing && phone && 'gap-5')}>
          {p.doneText ? (
            <p className="t-body text-ink" role="status"><Check size={18} strokeWidth={1.75} className="mr-2 inline -translate-y-px" />Done. {p.doneText}</p>
          ) : <button
            onClick={confirm ? () => approve() : onApprove}
            disabled={busy}
            className={cn(
              'inline-flex cursor-pointer items-center justify-center rounded-xl font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-60 t-body',
              // While the alert rings, Answer is the one blue button.
              p.ringing ? 'bg-soft text-ink hover:bg-hair' : 'bg-accent text-white shadow-[0_1px_2px_rgba(35,72,208,.25)] hover:bg-[#1d3db5]',
              phone ? 'h-[54px] w-full rounded-[14px]' : 'h-12 px-7',
            )}
          >
            {busy ? 'Sending…' : confirm ? 'Yes, approve' : emails ? 'Review email' : 'Approve'}
          </button>}
          <div className={cn('flex items-center', phone ? 't-small gap-7' : 't-body gap-6', p.ringing && 'order-first w-full flex-col gap-4 pb-2 lg:items-start')}>
            <VoiceCall
              card={card}
              onChanged={p.onChanged}
              ringing={p.ringing}
              onRingingDone={p.onRingingDone}
              quiet
              linesEl={linesEl}
              onLive={(l) => { setOnCall(l); p.onCall(l) }}
              onPrepared={onPrepared}
              onScreen={onScreen}
              screenEvent={screenEvent}
            />
            {p.doneText ? null : confirm ? (
              <button onClick={() => setConfirm(false)} className="cursor-pointer text-ink-2 hover:text-ink">Cancel</button>
            ) : (
              <button onClick={() => setOthers(!others)} aria-expanded={others} className="cursor-pointer text-ink-2 hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent">
                {others ? 'Fewer options' : 'Other options'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )

  return (
    <article className="rise" aria-label={headlineOf(card, state)}>
      <p className="t-small mb-4 text-ink-3">{meta}</p>
      <h1 className="t-big text-ink">{headlineOf(card, state)}</h1>

      <p className="t-body mt-6 text-ink-2">
        {claim ? (
          <ClaimLine claim={claim} late={late} open={source} onOpen={setSource} />
        ) : (
          <Cited text={summaryLine(card)} anchors={anchors.items} onOpen={setSource} open={source} />
        )}
      </p>
      {today && (
        <p className="t-body mt-4 text-ink-2">For today: <span className="font-medium text-ink">{today.bold}</span>{today.rest}</p>
      )}

      {opt && (
        <div className={cn(phone ? 'mt-9' : 'mt-12')}>
          <p className="t-small mb-1.5 text-ink-3">{recommended ? 'We recommend' : 'Your choice'}</p>
          <p className="t-body text-ink">{actionText(card, sel)}{phone && <span className="text-ink-2"> {costLine(card, sel)}</span>}</p>
          {!phone && (
            <p className="t-body text-ink-2">{costLine(card, sel)}{opt.effect ? ` ${cap(tidy(opt.effect))}.`.replace(/\.\.$/, '.') : ''}</p>
          )}
        </div>
      )}

      {others && (
        <div className="fade mt-8 space-y-1" aria-label="Other options">
          {card.options.map((_, i) =>
            i === sel ? null : (
              <button
                key={i}
                onClick={() => { setSel(i); setOthers(false); setConfirm(false) }}
                className="t-body block w-full cursor-pointer py-2 text-left text-ink-2 hover:text-ink focus-visible:outline-2 focus-visible:outline-accent"
              >
                <span className="text-ink-3">Or </span>{actionText(card, i).replace(/^./, (c) => c.toLowerCase())}{' '}
                <span className="t-small text-ink-3">{costLine(card, i)}</span>
              </button>
            ),
          )}
          {!passOn ? (
            <button onClick={() => setPassOn(true)} className="t-body block w-full cursor-pointer py-2 text-left text-ink-2 hover:text-ink">
              <span className="text-ink-3">Or </span>pass it on to {card.approver || 'the approver'}
            </button>
          ) : (
            <div className="py-2">
              <p className="t-small text-ink-3">They get this brief:</p>
              <p className="t-small mt-1 text-ink-2">{tidy(card.escalation_brief)}</p>
              <div className="mt-3 flex flex-wrap items-center gap-4">
                <input
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="Add one line (optional)"
                  className="t-small h-10 min-w-0 flex-1 rounded-lg bg-soft px-3 focus:outline-2 focus:outline-accent"
                  aria-label="Note for the approver"
                />
                <button disabled={busy} onClick={escalate} className="t-small cursor-pointer font-medium text-accent hover:underline">Send</button>
                <button onClick={() => setPassOn(false)} className="t-small cursor-pointer text-ink-3 hover:text-ink">Cancel</button>
              </div>
            </div>
          )}
          <button disabled={busy} onClick={dismiss} className="t-body block w-full cursor-pointer py-2 text-left text-ink-2 hover:text-ink">
            <span className="text-ink-3">Or </span>dismiss: nothing to do
          </button>
          <EvidenceLinks card={card} onDrawer={p.onDrawer} className="pt-2" />
        </div>
      )}

      {voicePrepared && !emailPrepared && (
        <div className="fade mt-10" role="status">
          <p className="t-small text-ink-3">On the call</p>
          <p className="t-body mt-1 text-ink">{prepared!.summary}</p>
          <p className="t-small mt-1 text-ink-2">Say ‘yes’ to go ahead{prepared!.decision === 'approve' && opt ? <>, or <button className="uline" onClick={onApprove}>approve here</button></> : null}.</p>
        </div>
      )}
      <div ref={setLinesEl} />
      {phone && <Automation state={state} card={card} onRoutines={p.onRoutines} onReview={p.onReview} onChanged={p.onChanged} className="mt-10" />}
      {actions}
      {!phone && <Automation state={state} card={card} onRoutines={p.onRoutines} onReview={p.onReview} onChanged={p.onChanged} className="mt-12" />}

      <SourceSheet card={card} claim={claim} index={source} anchors={anchors.items} ready={anchors.ready} onIndex={setSource} />
      {opt && (
        <EmailReview
          open={review}
          onOpenChange={setReview}
          to={opt.to ?? ''}
          toName={card.supplier_name ?? ''}
          subject={(opt as { subject?: string }).subject || headlineOf(card, state)}
          draft={draft}
          onDraft={setDraft}
          cost={money ? costLine(card, sel).replace(/\.$/, '') : null}
          busy={busy}
          error={error}
          voice={emailPrepared}
          onSend={() => { setReview(false); void approve(draft) }}
        />
      )}
    </article>
  )
}

// ---------------------------------------------------------------- email review

function EmailReview({
  open, onOpenChange, to, toName, subject, draft, onDraft, cost, busy, error, onSend, voice,
}: {
  /** Prepared on a live call: a spoken yes works as well as the button. */
  voice?: boolean
  open: boolean; onOpenChange: (o: boolean) => void; to: string; toName: string; subject: string
  draft: string; onDraft: (t: string) => void; cost: string | null; busy: boolean; error: string; onSend: () => void
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fade fixed inset-0 z-40 bg-[rgba(24,24,26,.28)]" />
        <Dialog.Content
          aria-describedby={undefined}
          className={cn(
            'sheet-up fixed z-50 overflow-y-auto bg-ground focus:outline-none',
            'inset-x-0 bottom-0 max-h-[92vh] rounded-t-[22px] px-6 pb-[max(28px,env(safe-area-inset-bottom))] pt-3 shadow-[0_-10px_40px_-16px_rgba(0,0,0,.25)]',
            'md:inset-x-auto md:bottom-auto md:left-1/2 md:top-[8vh] md:max-h-[84vh] md:w-[640px] md:-translate-x-1/2 md:rounded-2xl md:px-8 md:pb-8 md:pt-7 md:shadow-[0_20px_60px_-20px_rgba(0,0,0,.3)]',
          )}
        >
          <div className="mx-auto mb-5 h-[5px] w-9 rounded-full bg-hair md:hidden" aria-hidden="true" />
          <Dialog.Title className="t-small font-normal text-ink-3">{voice ? 'Say ‘yes’ or tap Send. Nothing goes out before.' : 'This email goes out when you press Send'}</Dialog.Title>
          <dl className="t-small mt-4 grid grid-cols-[64px_minmax(0,1fr)] gap-y-2 text-ink-2">
            <dt className="text-ink-3">To</dt>
            <dd className="min-w-0 break-words text-ink">{toName ? `${toName} · ` : ''}<span className="text-ink-2">{to}</span></dd>
            <dt className="text-ink-3">Subject</dt>
            <dd className="min-w-0 break-words text-ink">{subject}</dd>
          </dl>
          <label htmlFor="email-draft" className="sr-only">Email text</label>
          <textarea
            id="email-draft"
            value={draft}
            onChange={(e) => onDraft(e.target.value)}
            rows={10}
            className="t-body mt-4 w-full resize-y rounded-xl bg-soft p-4 leading-relaxed text-ink focus:outline-2 focus:outline-accent"
          />
          {cost && <p className="t-small mt-3 text-ink-2">This commits money: <span className="text-ink">{cost}</span>.</p>}
          {error && <p className="t-small mt-3 text-late" role="alert">{error}</p>}
          <div className="mt-6 flex items-center gap-6">
            <button
              onClick={onSend}
              disabled={busy || !draft.trim()}
              className="t-body inline-flex h-12 cursor-pointer items-center justify-center rounded-xl bg-accent px-7 font-medium text-white hover:bg-[#1d3db5] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-60"
            >
              {busy ? 'Sending…' : 'Send email'}
            </button>
            <Dialog.Close className="t-body cursor-pointer text-ink-2 hover:text-ink">Cancel</Dialog.Close>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

const cap = (t: string) => (t ? t[0].toUpperCase() + t.slice(1) : t)

function doneFrom(card: Card, i: number, status?: string): string {
  const a = actionText(card, i)
  const m = a.match(/^(\w+)\s+(.*)$/)
  const PAST: Record<string, string> = {
    ask: 'Asked', tell: 'Told', order: 'Ordered', send: 'Sent', book: 'Booked', collect: 'Collected', call: 'Called',
    move: 'Moved', release: 'Released', request: 'Requested', confirm: 'Confirmed', correct: 'Corrected', pay: 'Paid',
    accept: 'Accepted', cancel: 'Cancelled', expedite: 'Expedited', reserve: 'Reserved', notify: 'Notified', fly: 'Flew',
  }
  const past = m && PAST[m[1].toLowerCase()]
  // Short names and no order numbers: "Asked PackRight to make the order to rev C."
  const calm = (t: string) =>
    (card.supplier_name ? t.split(card.supplier_name).join(shortName(card.supplier_name)) : t).replace(/\bPO \d{6,}/g, 'the order')
  if (past) return calm(`${past} ${m![2]}`)
  return calm(status === 'sent' ? `Sent: ${a}` : `Approved: ${a}`)
}

const DbIcon = () => <Database aria-hidden="true" />
const DocIcon = () => <FileText aria-hidden="true" />

function ClaimLine({ claim, late, open, onOpen }: { claim: Claim; late: boolean; open: number | null; onOpen: (i: number) => void }) {
  return (
    <>
      {claim.pre}
      <button className="cite" aria-expanded={open === claim.sys.index} aria-haspopup="dialog" onClick={() => onOpen(claim.sys.index)}>
        {claim.sys.value}<DbIcon />
      </button>
      {claim.mid}
      <button className={cn('cite', late && 'late')} aria-expanded={open === claim.doc.index} aria-haspopup="dialog" onClick={() => onOpen(claim.doc.index)}>
        {claim.doc.value}<DocIcon />
      </button>
      {claim.post}
    </>
  )
}

/** A sentence whose numbers, when found in a source, open that source. */
function Cited({ text, anchors, onOpen, open }: { text: string; anchors: Record<number, Anchor[]> | null; onOpen: (i: number) => void; open: number | null }) {
  if (!text) return null
  const flat = Object.entries(anchors ?? {}).flatMap(([i, as]) => as.map((a) => ({ i: Number(i), a })))
  const parts: ReactNode[] = []
  let last = 0
  for (const m of text.matchAll(NUM_OR_ID)) {
    const hit = flat.find((x) => x.a.matched.includes(normTok(m[0])))
    if (!hit || !/\d/.test(m[0])) continue
    parts.push(text.slice(last, m.index))
    parts.push(
      <button key={m.index} className="cite" aria-expanded={open === hit.i} aria-haspopup="dialog" onClick={() => onOpen(hit.i)}>
        {m[0]}
      </button>,
    )
    last = (m.index ?? 0) + m[0].length
  }
  parts.push(text.slice(last))
  return <>{parts}</>
}

// ---------------------------------------------------------------- the source sheet

const SYS = /system|sap|erp/i
function sideLabel(card: Card, ev: Evidence): { who: string; isSys: boolean } {
  const isSys = SYS.test(ev.source ?? '')
  if (isSys) return { who: /\bSAP\b/.test(ev.source ?? '') ? 'SAP says' : 'The system says', isSys }
  const kind = docKind(ev.source) || 'the document'
  const owner = ev.doc?.startsWith('suppliers/') && card.supplier_name ? `${shortName(card.supplier_name)}'s ` : 'The '
  return { who: `${owner}${kind} says`.replace(/^The the/, 'The'), isSys }
}
const where = (a?: Anchor, ev?: Evidence) =>
  a ? (a.kind === 'table' || a.kind === 'text' ? `row ${(a.row ?? 0) + 1}` : `page ${a.page ?? 1}`) : ev?.page ? `page ${ev.page}` : ''

function SourceSheet({
  card, claim, index, anchors, ready, onIndex,
}: { card: Card; claim: Claim | null; index: number | null; anchors: Record<number, Anchor[]> | null; ready: boolean; onIndex: (i: number | null) => void }) {
  const ev = index != null ? card.evidence[index] : null
  const a = index != null ? anchors?.[index]?.[0] : undefined
  const otherIndex = claim && index != null ? (index === claim.sys.index ? claim.doc.index : index === claim.doc.index ? claim.sys.index : null) : null
  const other = otherIndex != null ? card.evidence[otherIndex] : null
  const otherA = otherIndex != null ? anchors?.[otherIndex]?.[0] : undefined
  const label = ev ? sideLabel(card, ev) : null
  const lang = ev ? languageOf(ev.source, ev.language) : null
  const head = ev
    ? [label!.isSys ? (/\bSAP\b/.test(ev.source ?? '') ? 'SAP' : 'System') : card.supplier_name, [docKind(ev.source), where(a, ev)].filter(Boolean).join(', '), lang]
        .filter(Boolean).join(' · ')
    : ''
  const english = ev?.translation ? ev.translation.split(' = ').pop() : null
  const original = a ? fileUrl(a) : ev?.doc ? `/api/file?path=${encodeURIComponent(ev.doc)}${ev.page ? `#page=${ev.page}` : ''}` : null

  return (
    <Dialog.Root open={index != null} onOpenChange={(o) => !o && onIndex(null)}>
      <Dialog.Portal>
        <Dialog.Overlay className="fade fixed inset-0 z-40 bg-[rgba(24,24,26,.28)]" />
        <Dialog.Content
          aria-describedby={undefined}
          className={cn(
            'sheet-up fixed z-50 overflow-y-auto bg-ground focus:outline-none',
            'inset-x-0 bottom-0 max-h-[88vh] rounded-t-[22px] px-6 pb-[max(36px,env(safe-area-inset-bottom))] pt-3 shadow-[0_-10px_40px_-16px_rgba(0,0,0,.25)]',
            'md:inset-x-auto md:bottom-auto md:left-1/2 md:top-[8vh] md:max-h-[84vh] md:w-[640px] md:-translate-x-1/2 md:rounded-2xl md:px-8 md:pb-8 md:pt-7 md:shadow-[0_20px_60px_-20px_rgba(0,0,0,.3)]',
          )}
        >
          <div className="mx-auto mb-5 h-[5px] w-9 rounded-full bg-hair md:hidden" aria-hidden="true" />
          {ev && (
            <>
              <Dialog.Title className="t-small font-normal text-ink-3">{head}</Dialog.Title>
              <div className="mt-3 overflow-hidden rounded-xl bg-soft p-3">
                {a ? (
                  <AnchorCrop key={`${index}-${a.doc}`} anchor={a} />
                ) : !ready ? (
                  <p className="t-small p-2 text-ink-3">Finding it in the documents…</p>
                ) : (
                  <p className="t-small whitespace-pre-wrap p-2 leading-[1.9] text-ink-2"><Marked text={tidy(ev.quote) || String(ev.value ?? '')} value={String(ev.value ?? '')} /></p>
                )}
              </div>
              {english ? (
                <p className="t-body mt-4 text-ink">{english}</p>
              ) : a && ev.quote ? (
                <p className="t-small mt-3 text-ink-3">{tidy(ev.quote)}</p>
              ) : null}

              {other && (
                <button
                  onClick={() => onIndex(otherIndex)}
                  className="t-body mt-7 grid w-full cursor-pointer grid-cols-[20px_1fr] gap-x-2.5 text-left text-ink-2 hover:text-ink focus-visible:outline-2 focus-visible:outline-accent"
                >
                  <span className="mt-1 text-ink-3">{sideLabel(card, other).isSys ? <Database size={16} /> : <FileText size={16} />}</span>
                  <span>
                    <span className="font-medium text-ink">{sideLabel(card, other).who} {splitValue(other.value).big}</span>{' '}
                    {splitValue(other.value).rest}
                  </span>
                  <span className="t-small col-start-2 text-ink-3">{cap([docKind(other.source), where(otherA, other)].filter(Boolean).join(', '))}</span>
                </button>
              )}

              <div className="mt-7 flex items-center gap-4">
                {original && (
                  <a href={original} target="_blank" rel="noreferrer" className="t-small shrink-0 text-accent hover:underline">Open original</a>
                )}
                <Dialog.Close className="t-body h-[52px] flex-1 cursor-pointer rounded-[14px] bg-soft font-medium text-ink hover:bg-hair focus-visible:outline-2 focus-visible:outline-accent">
                  Back
                </Dialog.Close>
              </div>
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

/** The quote with the value's numbers highlighted (when no page or row was found). */
function Marked({ text, value }: { text: string; value: string }) {
  const nums = new Set((value.match(/\d[\d,.]*\d|\d/g) ?? []).map(normTok))
  const parts: ReactNode[] = []
  let last = 0
  for (const m of text.matchAll(/\d[\d,.]*\d|\d/g)) {
    if (!nums.has(normTok(m[0]))) continue
    parts.push(text.slice(last, m.index), <mark key={m.index} className="rounded-[3px] bg-mark px-1 font-semibold text-[#111]">{m[0]}</mark>)
    last = (m.index ?? 0) + m[0].length
  }
  parts.push(text.slice(last))
  return <>{parts}</>
}

// ---------------------------------------------------------------- done

function DoneView(p: Props & { done: DoneInfo }) {
  const { done, state, phone } = p
  const live = useLive()
  const outcome = done.actionId ? live.undo[done.actionId] : undefined
  const [undoing, setUndoing] = useState(false)
  const next = p.order.find((c) => c.id !== done.card.id) ?? null
  const offer = done.offer > 0 && !outcome?.ok
  // No look-alikes open, but the same call made again: offer it as a routine on its own.
  const repeat = !offer && !outcome?.ok && done.repeat && done.repeat.n >= 2 ? done.repeat : null
  const who = shortName(done.card.supplier_name)

  const undo = async () => {
    if (!done.actionId) return
    setUndoing(true)
    const r = await undoAction(done.actionId)
    setUndoing(false)
    if (r.ok) p.onChanged()
  }

  const primary = offer || repeat
    ? { label: 'Show me how', onClick: () => { p.onClearDone(); p.onRoutines({ cardId: done.card.id, k: Date.now() }) } }
    : next
      ? { label: 'Next decision', onClick: () => { p.onFocus(next.id); p.onClearDone() } }
      : null
  const quiet = offer || repeat ? { label: 'Not now', onClick: p.onClearDone } : null

  return (
    <section className="rise" aria-live="polite">
      <div className="mb-6 grid h-10 w-10 place-items-center rounded-full bg-soft text-ink"><Check size={20} strokeWidth={1.75} /></div>
      <h1 className="t-big text-ink">{outcome?.ok ? 'Undone.' : `Done. ${done.text}`}</h1>
      <p className="t-body mt-6 text-ink-2">
        {outcome?.ok ? 'It is back on your list.' : done.sub}{' '}
        {done.actionId && !outcome && (
          <button onClick={undo} disabled={undoing} className="uline">{undoing ? 'Undoing…' : 'Undo'}</button>
        )}
        {outcome && !outcome.ok && <span className="t-small block pt-2 text-ink-3">Undo: {outcome.reason}</span>}
      </p>

      <div className="h-14" />
      {offer ? (
        <div>
          <p className="t-body text-ink">
            {plural(done.offer, 'more like this', 'more like this')}{who ? ` from ${who}` : ''}. Handle them the same way?
          </p>
          <p className="t-small dot-routine mt-7 text-routine">You'll see a test run first. It starts by asking: it prepares them, you send with one tap.</p>
        </div>
      ) : repeat ? (
        <div>
          <p className="t-body text-ink">
            That's the {ordinal(repeat.n)} time you {repeat.words}. Handle it like this from now on?
          </p>
          <p className="t-small dot-routine mt-7 text-routine">It will prepare them; you send with one tap.</p>
        </div>
      ) : next ? (
        <p className="t-body text-ink-2">Next: <span className="text-ink">{headlineOf(next, state)}</span></p>
      ) : (
        <p className="t-body text-ink-2">Nothing else needs you. <button className="uline" onClick={p.onClearDone}>See the day</button></p>
      )}

      {(primary || quiet) && (
        <div className={cn(phone ? 'fixed inset-x-0 bottom-0 z-20 bg-gradient-to-t from-ground from-70% to-ground/0 px-6 pb-[max(28px,env(safe-area-inset-bottom))] pt-8' : 'mt-10')}>
          <div className={cn(phone ? 'mx-auto grid max-w-[520px] justify-items-center gap-3.5' : 'flex items-center gap-6')}>
            {primary && (
              <button
                onClick={primary.onClick}
                autoFocus={!phone}
                className={cn(
                  't-body inline-flex cursor-pointer items-center justify-center rounded-xl bg-accent font-medium text-white shadow-[0_1px_2px_rgba(35,72,208,.25)] hover:bg-[#1d3db5] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                  phone ? 'h-[54px] w-full rounded-[14px]' : 'h-12 px-7',
                )}
              >
                {primary.label}
              </button>
            )}
            {quiet && (
              <button onClick={quiet.onClick} className={cn('cursor-pointer text-ink-2 hover:text-ink', phone ? 't-small' : 't-body')}>{quiet.label}</button>
            )}
          </div>
        </div>
      )}
    </section>
  )
}

// ---------------------------------------------------------------- all clear

function AllClear({
  state, nowMs, handled, onRoutines, onReview, onChanged, go,
}: { state: State; nowMs: number; handled: number; onRoutines: (r?: { cardId: string; k: number }) => void; onReview: Review; onChanged: () => void; go: (p: string) => void }) {
  const waiting = state.cards.filter((c) => (c.status === 'open' && c.routed_to_other) || c.status === 'escalated').length
  const live = useLive()
  const pending = pendingOf(live)
  const suggestion = live.suggestions[0] ?? null
  return (
    <section className="rise pt-[16vh] lg:pt-[10vh]">
      <h1 className="t-big text-ink">Nothing needs you.</h1>
      <p className="t-body mt-6 text-ink-2">{launchStatus(state, nowMs)}</p>
      <div className="t-small mt-10 grid gap-3.5 text-ink-2">
        <span>Watching {plural(state.shipments.length, 'shipment')}<button className="uline ml-1.5" onClick={() => go('/suppliers')}>see</button></span>
        {waiting > 0 && <span>{plural(waiting, 'decision')} with others<button className="uline ml-1.5" onClick={() => go('/plan')}>see</button></span>}
        {handled > 0 && (
          <RoutineLine links={[{ label: 'see', onClick: () => onRoutines() }, { label: 'undo', onClick: () => go('/plan#handled') }]}>
            {handled} handled by your routines {new Date(nowMs).getHours() < 11 ? 'overnight' : 'today'}
          </RoutineLine>
        )}
        {/* One routine waiting: its own line below names it. Several: one count. */}
        {pending.length > 0 && new Set(pending.map((x) => x.routine_id)).size > 1 && (
          <RoutineLine links={[{ label: 'review', onClick: () => onReview(pending) }]}>{pending.length} waiting for your OK</RoutineLine>
        )}
        <Automation state={state} onRoutines={onRoutines} onReview={onReview} onChanged={onChanged} className="grid gap-3.5" />
        {suggestion && (
          <RoutineLine links={[{ label: 'make it a routine', onClick: () => onRoutines({ cardId: suggestion.last_card_id, k: Date.now() }) }]}>
            You did ‘{suggestionLabel(suggestion, state)}’ {suggestion.count} times
          </RoutineLine>
        )}
      </div>
      {state.last_check_at && <p className="t-small mt-16 text-ink-3">Last checked the portal at {when(state.last_check_at, nowMs)}.</p>}
    </section>
  )
}

/**
 * Automation where the decision happens, one quiet line each: what a routine would do with the
 * focused decision, what routines prepared and wait to send, and routines drafted on a call.
 */
function Automation({
  state, card, onRoutines, onReview, onChanged, className,
}: { state: State; card?: Card; onRoutines: (r?: { cardId: string; k: number }) => void; onReview: Review; onChanged: () => void; className?: string }) {
  const live = useLive()
  const pending = pendingOf(live)
  const mine = card ? pending.filter((x) => x.card_id === card.id) : []
  const others = card ? pending.filter((x) => x.card_id !== card.id) : []
  const asking = new Set(mine.map((x) => x.routine_id))
  const would = card && card.status === 'open' ? (live.would[card.id] ?? []).filter((w) => !asking.has(w.routine_id)) : []
  const drafts = live.drafts
  // On All clear several routines fold into the count line above; a single one is named here.
  const all = groupProposals(card ? others : pending, state)
  const groups = !card && all.length > 1 ? [] : all
  if (!mine.length && !would.length && !groups.length && !drafts.length) return null
  return (
    <div className={cn('grid gap-3', className)}>
      {mine.length > 0 && (
        <RoutineLine links={[{ label: 'review', onClick: () => onReview(mine) }]}>
          Your routine ‘{mine[0].routine_name}’ prepared {mine.length === 1 ? 'this for you' : `${mine.length} steps for this`}
        </RoutineLine>
      )}
      {would.slice(0, 1).map((w) => <WouldLine key={w.routine_id} w={w} onChanged={onChanged} />)}
      <ProposalLines groups={groups} onReview={onReview} max={card ? 1 : 2} />
      {drafts.length > 0 && (
        <RoutineLine links={[{ label: 'review', onClick: () => onRoutines() }]}>
          {drafts.length === 1 ? <>Drafted from your call: ‘{drafts[0].routine.name}’</> : <>{drafts.length} routines drafted from your calls</>}
        </RoutineLine>
      )}
    </div>
  )
}

function FootLines({ handled, onRoutines, nowMs, center }: { handled: number; onRoutines: () => void; nowMs: number; center: boolean }) {
  if (!handled) return null
  return (
    <div className={cn('flex', center && 'justify-center')}>
      <RoutineLine links={[{ label: 'see', onClick: onRoutines }]}>
        {handled} handled by your routines {new Date(nowMs).getHours() < 11 ? 'overnight' : 'today'}
      </RoutineLine>
    </div>
  )
}

/** The short label of a suggestion: the latest case's list title, else its headline. */
function suggestionLabel(sg: { last_card_id: string; label?: string | null }, state: State): string {
  const c = state.cards.find((x) => x.id === sg.last_card_id)
  const t = c ? shortTitle(c, state) : tidy(sg.label).replace(/\.$/, '')
  return t.length > 60 ? `${t.slice(0, 57).replace(/\s+\S*$/, '')}…` : t
}

/** Everything behind a case, one tap away in the side drawer. */
function EvidenceLinks({ card, onDrawer, className }: { card: Card; onDrawer: Drawer; className?: string }) {
  if (!card.evidence.length && !card.trail.length) return null
  return (
    <p className={cn('t-small flex flex-wrap gap-x-5 gap-y-2 text-ink-3', className)}>
      {card.evidence.length > 0 && (
        <button onClick={() => onDrawer(card, 'evidence')} className="cursor-pointer hover:text-ink"><span className="border-b border-hair">All evidence · {card.evidence.length}</span></button>
      )}
      {card.trail.length > 0 && (
        <button onClick={() => onDrawer(card, 'trail')} className="cursor-pointer hover:text-ink"><span className="border-b border-hair">How we know</span></button>
      )}
    </p>
  )
}

// ---------------------------------------------------------------- a case that is not open any more

/** A decided or passed-on case, opened from Plan: where it stands, why, and Undo where it works. */
function CaseView({ card, state, nowMs, onChanged, onDrawer }: { card: Card; state: State; nowMs: number; onChanged: () => void; onDrawer: Drawer }) {
  const [source, setSource] = useState<number | null>(null)
  const anchors = useAnchors(card.id)
  const claim = claimOf(card)
  const routine = routineOf(state, card.id)
  const waiting = card.status === 'escalated'
  const actionId = routine ? routineActionOf(state, card.id) : actionIdOf(state.activity.find((a) => a.card === card.id))
  const undo = !waiting && card.status !== 'sent' ? actionId : null
  const facts = replyFacts(card)
  const who = (card.approver ?? '').split(' ').find((w) => !w.endsWith('.')) || 'the approver'
  const at = card.ui?.at ? ` at ${when(card.ui.at, nowMs)}` : ''
  return (
    <article className="rise" aria-label={headlineOf(card, state)}>
      <p className="t-small mb-4 text-ink-3">{card.supplier_name || card.part_name || 'Soony'}</p>
      <h1 className="t-big text-ink">{headlineOf(card, state)}</h1>

      <p className="t-body mt-6 flex flex-wrap items-baseline gap-x-2 text-ink-2">
        {waiting ? (
          <span className="text-ink">Waiting for {who}{card.ui?.at ? ` since ${when(card.ui.at, nowMs)}` : ''}.</span>
        ) : (
          <>
            <Check size={17} strokeWidth={1.75} className="translate-y-0.5 self-center text-ink" aria-hidden="true" />
            <span className="text-ink">{DONE_WORD[card.status] ?? 'Done'}{at}.</span>
            {routine ? <span className="t-small dot-routine text-routine">By your routine ‘{routine}’</span> : <span className="t-small text-ink-3">By you</span>}
          </>
        )}
        {undo && <UndoAction actionId={undo} onUndone={onChanged} />}
      </p>
      {waiting && card.reason && <p className="t-body mt-2 text-ink-2">{sentence(card.reason)}</p>}

      {facts && (
        <p className="t-body mt-6 text-ink">{facts.text}{facts.late ? <span className="text-late">, after launch.</span> : '.'}</p>
      )}
      {card.ui?.reply && !facts && (
        <p className="t-body mt-6 text-ink-2">
          <span className="text-ink-3">Reply from {card.ui.reply_from || 'the supplier'}: </span>{card.ui.reply}
        </p>
      )}

      <p className="t-body mt-6 text-ink-2">
        {claim ? (
          <ClaimLine claim={claim} late={card.level === 'red'} open={source} onOpen={setSource} />
        ) : (
          <Cited text={summaryLine(card)} anchors={anchors.items} onOpen={setSource} open={source} />
        )}
      </p>
      <EvidenceLinks card={card} onDrawer={onDrawer} className="mt-12" />
      <SourceSheet card={card} claim={claim} index={source} anchors={anchors.items} ready={anchors.ready} onIndex={setSource} />
    </article>
  )
}
