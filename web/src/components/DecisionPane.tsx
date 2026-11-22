import { Check, ChevronRight, Database, FileText } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { EvidenceItem, Trail } from '@/components/EvidenceDrawer'
import { AnchorCrop, NUM_OR_ID, fileUrl, normTok, useAnchors, type Anchor } from '@/components/SourceAnchors'
import { VoiceCall } from '@/components/VoiceCall'
import { DONE_WORD, post, sentence, tidy, type Card, type Evidence, type State } from '@/lib/api'
import { languageOf, replyFacts, routineActionOf, routineOf, sameIssueOn, when, type Group } from '@/lib/decisions'
import {
  actionText, claimOf, costLine, costsMoney, docKind, forToday, headlineOf, recommendedIndex, shortName, summaryLine, type Claim,
} from '@/lib/today'
import { cn } from '@/lib/utils'
import { PaneHints } from './routines/PaneHints'
import { UndoAction } from './routines/Undo'

export interface Offer {
  /** Open cards left that look like the one just decided. */
  more: number
  onYes: () => void
  onNext: () => void
}

interface Props {
  card: Card
  state: State
  nowMs: number
  group?: Group | null
  /** Opened from the deep link (/d/<id>): whoever it is routed to can act on it here. */
  direct?: boolean
  /** Listen for a / Enter / Esc (the desktop pane). */
  keys?: boolean
  onChanged: () => void
  onDrawer: (card: Card, tab: 'evidence' | 'trail') => void
  ringing?: boolean
  onRingingDone?: () => void
  offer?: Offer | null
}

const first = (s?: string) => (!s ? '' : /\s[a-z]/.test(s) ? s : (s.split(' ').find((w) => !w.endsWith('.')) ?? s))
const typing = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))
const lower = (t: string) => (t ? t[0].toLowerCase() + t.slice(1) : t)

// One decision in Plan: Today's decision (headline, the contradiction with tappable sources, the
// recommendation, one blue button), then the Plan-only parts as quiet folds below a hairline.
export function DecisionPane({
  card, state, nowMs, group, direct, keys, onChanged, onDrawer, ringing, onRingingDone, offer,
}: Props) {
  const [sel, setSel] = useState(() => recommendedIndex(card))
  const [text, setText] = useState(() => card.options[recommendedIndex(card)]?.draft ?? '')
  const [editing, setEditing] = useState(false)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [confirm, setConfirm] = useState(false)
  // The source opened under the sentence, as on Today: closed until a citation is tapped.
  const [src, setSrc] = useState<number | null>(null)
  const [linesEl, setLinesEl] = useState<HTMLDivElement | null>(null)
  const anchors = useAnchors(card.id)

  const opt = card.options[sel]
  const other = card.routed_to_other || card.status === 'escalated'
  const closed = card.status !== 'open' && card.status !== 'escalated'
  const actionable = (!other || !!direct) && !closed && !!opt
  const who = first(card.approver) || 'the approver'
  const claim = claimOf(card)
  const today = !closed ? forToday(card, state, nowMs) : null
  const recommended = sel === recommendedIndex(card)
  const routine = closed ? routineOf(state, card.id) : null
  const undoId = routine ? routineActionOf(state, card.id) : null
  const facts = replyFacts(card)
  const same = group ? sameIssueOn(group) : null
  const late = card.level === 'red'
  const emails = !!opt && opt.channel === 'email' && /@/.test(opt.to ?? '')
  const money = !!opt && costsMoney(card, sel)
  const edited = editing && text.trim() !== (opt?.draft ?? '').trim()
  const shown = src

  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError('')
    try { await fn(); onChanged() } catch (e) {
      setError(`That did not go through: ${(e as Error).message}. Nothing was sent.`)
    } finally { setBusy(false); setConfirm(false) }
  }
  const approve = () =>
    act(() => post(`/api/cards/${encodeURIComponent(card.id)}/approve`, edited ? { option: sel, text } : { option: sel }))
  const escalate = () => act(() => post(`/api/cards/${encodeURIComponent(card.id)}/escalate`, { note }))
  const dismiss = () => act(() => post(`/api/cards/${encodeURIComponent(card.id)}/dismiss`))
  // An email or money is said out loud first: one more press sends it.
  const onApprove = () => (confirm ? void approve() : emails || money ? setConfirm(true) : void approve())
  const pick = (i: number) => { setSel(i); setText(card.options[i]?.draft ?? ''); setEditing(false); setConfirm(false) }

  // Keyboard: a asks, Enter approves, Esc cancels. Never while typing.
  const approveRef = useRef(approve)
  approveRef.current = approve
  useEffect(() => {
    if (!keys) return
    const onKey = (e: KeyboardEvent) => {
      if (typing(e.target) || e.metaKey || e.ctrlKey || e.altKey) return
      if (confirm && e.key === 'Enter') { e.preventDefault(); void approveRef.current() }
      else if (confirm && e.key === 'Escape') { e.preventDefault(); setConfirm(false) }
      else if (!confirm && e.key.toLowerCase() === 'a' && actionable && !busy) { e.preventDefault(); setConfirm(true) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [keys, confirm, actionable, busy])

  const decideBy = !closed && card.deadline
    ? new Date(card.deadline).getTime() <= nowMs ? 'decide now' : `decide by ${when(card.deadline, nowMs)}`
    : ''
  const meta = [card.supplier_name || card.part_name, decideBy].filter(Boolean).join(' · ') || 'Soony'

  return (
    <article className="pane-in" aria-label={headlineOf(card, state)}>
      <p className="t-small mb-4 text-ink-3">{meta}</p>
      <h1 className="t-big text-ink">{headlineOf(card, state)}</h1>
      {same && <p className="t-small mt-3 text-ink-3">Same issue on {same}.</p>}

      {/* where it stands */}
      {closed && (
        <p className="t-body mt-6 flex flex-wrap items-baseline gap-x-2 text-ink-2">
          <Check size={17} strokeWidth={1.75} className="translate-y-0.5 self-center text-ink" />
          <span className="text-ink">{DONE_WORD[card.status] ?? 'Done'}{card.ui?.at ? ` ${when(card.ui.at, nowMs)}` : ''}.</span>
          {routine && <span className="t-small dot-routine text-routine">By your routine {routine}</span>}
          {undoId && <UndoAction actionId={undoId} onUndone={onChanged} />}
        </p>
      )}
      {closed && offer && <AutomateOffer card={card} offer={offer} />}
      {other && (
        <p className="t-body mt-6 text-ink-2">
          <span className="text-ink">{card.status === 'escalated' ? `Waiting for ${who}.` : `With ${who}.`}</span>
          {card.reason && <> {sentence(card.reason)}</>}
        </p>
      )}

      <div className="mt-6 empty:hidden"><PaneHints card={card} onChanged={onChanged} /></div>

      {facts && (
        <p className="t-body mt-6 text-ink">
          {facts.text}{facts.late ? <span className="text-late">, after launch.</span> : '.'}
        </p>
      )}

      {/* the contradiction, with its sources */}
      <p className="t-body mt-6 text-ink-2">
        {claim ? (
          <ClaimLine claim={claim} late={late} open={shown} onOpen={(i) => setSrc(shown === i ? null : i)} />
        ) : (
          <Cited text={summaryLine(card)} anchors={anchors.items} open={shown} onOpen={(i) => setSrc(shown === i ? null : i)} />
        )}
      </p>
      {shown != null && card.evidence[shown] && (
        <Source card={card} ev={card.evidence[shown]} anchor={anchors.items?.[shown]?.[0]} ready={anchors.ready} onClose={() => setSrc(null)} />
      )}
      {today && (
        <p className="t-body mt-4 text-ink-2">For today: <span className="font-medium text-ink">{today.bold}</span>{today.rest}</p>
      )}

      {/* what to do */}
      {actionable && (
        <>
          <div className="mt-12">
            <p className="t-small mb-1.5 text-ink-3">{recommended ? 'We recommend' : 'Your choice'}</p>
            <p className="t-body text-ink">{actionText(card, sel)}</p>
            <p className="t-body text-ink-2">{costLine(card, sel)}{opt.effect ? ` ${sentence(opt.effect)}` : ''}</p>
          </div>

          {card.options.length > 1 && (
            <ul className="mt-5" aria-label="Other options">
              {card.options.map((_, i) =>
                i === sel ? null : (
                  <li key={i}>
                    <button
                      onClick={() => pick(i)}
                      className="t-body block w-full cursor-pointer py-1.5 text-left text-ink-2 hover:text-ink focus-visible:outline-2 focus-visible:outline-accent"
                    >
                      <span className="text-ink-3">Or </span>{lower(actionText(card, i))}{' '}
                      <span className="t-small text-ink-3">{costLine(card, i)}</span>
                    </button>
                  </li>
                ),
              )}
            </ul>
          )}

          <div className="mt-8">
            {confirm && (
              <p className="t-small mb-3 text-ink-2" role="alert">
                {emails ? <>This email goes to <span className="text-ink">{opt.to}</span>{edited ? ' with your text' : ''}. </> : null}
                Approve: <span className="text-ink">{actionText(card, sel).replace(/\.$/, '')}</span> ({costLine(card, sel).replace(/\.$/, '')})
                {keys && <span className="text-ink-3"> · <kbd>Enter</kbd> confirms, <kbd>Esc</kbd> cancels</span>}
              </p>
            )}
            {error && <p className="t-small mb-3 text-late" role="alert">{error}</p>}
            <div className="flex flex-wrap items-center gap-6">
              <button
                onClick={onApprove}
                disabled={busy}
                className={cn(
                  't-body inline-flex h-12 cursor-pointer items-center justify-center rounded-xl px-7 font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-60',
                  ringing ? 'bg-soft text-ink hover:bg-hair' : 'bg-accent text-white shadow-[0_1px_2px_rgba(35,72,208,.25)] hover:bg-[#1d3db5]',
                )}
              >
                {busy ? 'Sending…' : confirm ? (emails ? 'Yes, send it' : 'Yes, approve') : edited ? 'Approve edited' : 'Approve'}
              </button>
              <div className={cn('t-body flex items-center gap-6', ringing && 'order-first w-full flex-col items-start gap-4 pb-2')}>
                <VoiceCall card={card} onChanged={onChanged} ringing={ringing} onRingingDone={onRingingDone} quiet linesEl={linesEl} />
                {confirm && <button onClick={() => setConfirm(false)} className="cursor-pointer text-ink-2 hover:text-ink">Cancel</button>}
              </div>
            </div>
          </div>
        </>
      )}
      <div ref={setLinesEl} />

      {!actionable && !closed && (
        <div className="t-body mt-8"><VoiceCall card={card} onChanged={onChanged} ringing={ringing} onRingingDone={onRingingDone} quiet linesEl={linesEl} /></div>
      )}

      {card.ui?.reply && (
        <section className="mt-12">
          <p className="t-small text-ink-3">
            Reply from {card.ui.reply_from || 'the supplier'}{card.ui.reply_at ? ` · ${when(card.ui.reply_at, nowMs)}` : ''}
          </p>
          <p className="t-body mt-1.5 text-ink">{card.ui.reply}</p>
          {card.ui.reply_text && (
            <details className="group mt-2">
              <summary className="t-small cursor-pointer list-none text-ink-3 hover:text-ink [&::-webkit-details-marker]:hidden">
                <span className="border-b border-hair">Original email</span>
              </summary>
              <pre className="t-small mt-2 whitespace-pre-wrap break-words font-sans text-ink-2">{card.ui.reply_text}</pre>
            </details>
          )}
        </section>
      )}

      {/* Plan only: everything behind it, folded */}
      <div className="mt-14 border-t border-hair">
        {card.evidence.length > 0 && (
          <Fold title="All evidence" n={card.evidence.length}>
            <div className="space-y-7">
              {claim && card.summary && <p className="t-body text-ink-2">{sentence(card.summary)}</p>}
              {card.evidence.map((e, i) => <EvidenceItem key={i} e={e} anchors={anchors.items?.[i]} ready={anchors.ready} />)}
              <button onClick={() => onDrawer(card, 'evidence')} className="t-small cursor-pointer text-ink-3 hover:text-ink">
                <span className="border-b border-hair">Open beside the list</span>
              </button>
            </div>
          </Fold>
        )}
        {card.trail.length > 0 && (
          <Fold title="How we know" n={card.trail.length}>
            <Trail card={card} />
          </Fold>
        )}
        {actionable && opt.draft && (
          <Fold title={emails ? 'Edit the email' : 'Edit the message'} onToggle={setEditing}>
            {emails && (
              <p className="t-small mb-2 text-ink-3">To {card.supplier_name ? `${shortName(card.supplier_name)} · ` : ''}{opt.to}</p>
            )}
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={8}
              className="t-body w-full resize-y rounded-xl bg-soft p-4 leading-relaxed text-ink focus:outline-2 focus:outline-accent"
              aria-label="Edit the message"
            />
            <p className="t-small mt-2 text-ink-3">
              {edited ? 'Approve above sends your text.' : 'Change the text, then approve above.'}
              {edited && <button onClick={() => setText(opt.draft ?? '')} className="uline ml-2">Reset</button>}
            </p>
          </Fold>
        )}
        {actionable && (
          <Fold title={`Escalate to ${who}`}>
            <p className="t-small text-ink-3">They get this brief:</p>
            <p className="t-small mt-1 text-ink-2">{tidy(card.escalation_brief)}</p>
            <div className="mt-4 flex flex-wrap items-center gap-4">
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Add one line (optional)"
                className="t-small h-10 min-w-0 flex-1 rounded-lg bg-soft px-3 focus:outline-2 focus:outline-accent"
                aria-label={`Note for ${who}`}
              />
              <button disabled={busy} onClick={escalate} className="t-small cursor-pointer font-medium text-accent hover:underline disabled:opacity-60">
                Send to {who}
              </button>
            </div>
          </Fold>
        )}
        {actionable && (
          <p className="t-small border-b border-hair py-4 text-ink-3">
            Nothing to do?{' '}
            <button disabled={busy} onClick={dismiss} className="uline">Dismiss it</button>
          </p>
        )}
      </div>
    </article>
  )
}

function Fold({ title, n, children, onToggle }: { title: string; n?: number; children: ReactNode; onToggle?: (open: boolean) => void }) {
  return (
    <details className="group border-b border-hair" onToggle={(e) => onToggle?.((e.currentTarget as HTMLDetailsElement).open)}>
      <summary className="t-small flex cursor-pointer list-none items-center gap-2 py-4 text-ink-2 hover:text-ink focus-visible:outline-2 focus-visible:outline-accent [&::-webkit-details-marker]:hidden">
        <ChevronRight size={15} strokeWidth={1.75} className="text-ink-3 transition-transform group-open:rotate-90" />
        {title}{n != null && <span className="text-ink-3">· {n}</span>}
      </summary>
      <div className="fade pb-8 pl-[23px] pt-1">{children}</div>
    </details>
  )
}

/** After a decision by hand: offer to handle the next ones like it, in place. */
function AutomateOffer({ card, offer }: { card: Card; offer: Offer }) {
  const who = shortName(card.supplier_name)
  return (
    <section className="rise mt-10" aria-live="polite">
      <p className="t-body text-ink">
        {offer.more > 0
          ? `${offer.more} more like this${who ? ` from ${who}` : ''}. Handle them the same way?`
          : 'Handle the next one like this on its own?'}
      </p>
      <p className="t-small dot-routine mt-3 text-routine">You'll see a test run first. Anything that costs money still comes to you.</p>
      <div className="mt-6 flex items-center gap-6">
        <button
          onClick={offer.onYes}
          className="t-body inline-flex h-12 cursor-pointer items-center rounded-xl bg-accent px-7 font-medium text-white hover:bg-[#1d3db5] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Show me how
        </button>
        <button onClick={offer.onNext} className="t-body cursor-pointer text-ink-2 hover:text-ink">Not now</button>
      </div>
    </section>
  )
}

// ---------------------------------------------------------------- the sentence and its sources

function ClaimLine({ claim, late, open, onOpen }: { claim: Claim; late: boolean; open: number | null; onOpen: (i: number) => void }) {
  return (
    <>
      {claim.pre}
      <button className="cite" aria-expanded={open === claim.sys.index} onClick={() => onOpen(claim.sys.index)}>
        {claim.sys.value}<Database aria-hidden="true" />
      </button>
      {claim.mid}
      <button className={cn('cite', late && 'late')} aria-expanded={open === claim.doc.index} onClick={() => onOpen(claim.doc.index)}>
        {claim.doc.value}<FileText aria-hidden="true" />
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
      <button key={m.index} className="cite" aria-expanded={open === hit.i} onClick={() => onOpen(hit.i)}>{m[0]}</button>,
    )
    last = (m.index ?? 0) + m[0].length
  }
  parts.push(text.slice(last))
  return <>{parts}</>
}

const SYS = /system|sap|erp/i
const where = (a?: Anchor, ev?: Evidence) =>
  a ? (a.kind === 'table' || a.kind === 'text' ? `row ${(a.row ?? 0) + 1}` : `page ${a.page ?? 1}`) : ev?.page ? `page ${ev.page}` : ''

/** The source of one side, opened in place: the crop with the highlighter, then in English. */
function Source({ card, ev, anchor: a, ready, onClose }: { card: Card; ev: Evidence; anchor?: Anchor; ready: boolean; onClose: () => void }) {
  const isSys = SYS.test(ev.source ?? '')
  const head = [
    isSys ? (/\bSAP\b/.test(ev.source ?? '') ? 'SAP' : 'System') : card.supplier_name,
    [docKind(ev.source), where(a, ev)].filter(Boolean).join(', '),
    languageOf(ev.source, ev.language),
  ].filter(Boolean).join(' · ')
  const english = ev.translation ? ev.translation.split(' = ').pop() : null
  const original = a ? fileUrl(a) : ev.doc ? `/api/file?path=${encodeURIComponent(ev.doc)}${ev.page ? `#page=${ev.page}` : ''}` : null
  return (
    <section className="fade mt-5" aria-label="Source">
      <p className="t-small text-ink-3">{head}</p>
      <div className="mt-2 overflow-hidden rounded-xl bg-soft p-3">
        {a ? (
          <AnchorCrop key={a.doc} anchor={a} />
        ) : !ready ? (
          <p className="t-small p-2 text-ink-3">Finding it in the documents…</p>
        ) : (
          <p className="t-small whitespace-pre-wrap p-2 leading-[1.9] text-ink-2"><Marked text={tidy(ev.quote) || String(ev.value ?? '')} value={String(ev.value ?? '')} /></p>
        )}
      </div>
      {english && <p className="t-body mt-3 text-ink">{english}</p>}
      <p className="t-small mt-3 flex gap-5">
        {original && <a href={original} target="_blank" rel="noreferrer" className="text-accent hover:underline">Open original</a>}
        <button onClick={onClose} className="cursor-pointer text-ink-3 hover:text-ink">Close</button>
      </p>
    </section>
  )
}

/** The quote with the value's numbers highlighted (when no page or row was found). */
function Marked({ text, value }: { text: string; value: string }) {
  const nums = new Set((value.match(/\d[\d,.]*\d|\d/g) ?? []).map(normTok))
  const parts: ReactNode[] = []
  let last = 0
  for (const m of text.matchAll(/\d[\d,.]*\d|\d/g)) {
    if (!nums.has(normTok(m[0]))) continue
    parts.push(text.slice(last, m.index), <mark key={m.index} className="rounded-[3px] bg-mark px-1 font-medium text-[#111]">{m[0]}</mark>)
    last = (m.index ?? 0) + m[0].length
  }
  parts.push(text.slice(last))
  return <>{parts}</>
}
