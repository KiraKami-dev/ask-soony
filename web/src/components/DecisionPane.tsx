import { Check, ChevronRight, ExternalLink, FileSearch, MoreHorizontal, Phone, Sparkles, Workflow } from 'lucide-react'
import { useEffect, useRef, useState, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { EvidenceItem, Trail } from '@/components/EvidenceDrawer'
import { OpenAnchor, useAnchors, type Anchor } from '@/components/SourceAnchors'
import { Button } from '@/components/ui/button'
import { VoiceCall } from '@/components/VoiceCall'
import {
  DONE_WORD, costText, fmtNum, partName, post, sentence, tidy, until, type Card, type State,
} from '@/lib/api'
import {
  languageOf, mismatchPair, routineOf, sameIssueOn, sourceName, splitValue, when, type Group,
} from '@/lib/decisions'
import { cn } from '@/lib/utils'

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

export function DecisionPane({
  card, state, nowMs, group, direct, keys, onChanged, onDrawer, ringing, onRingingDone, offer,
}: Props) {
  const [sel, setSel] = useState(() => Math.max(0, card.options.findIndex((o) => o.recommended)))
  const [mode, setMode] = useState<'idle' | 'edit' | 'escalate'>('idle')
  const [text, setText] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [more, setMore] = useState(false)
  const moreRef = useRef<HTMLDivElement>(null)
  const anchors = useAnchors(card.id)
  // While the recommendation is out of view, the action bar says what Approve does.
  const recRef = useRef<HTMLElement>(null)
  const [recSeen, setRecSeen] = useState(true)
  useEffect(() => {
    const el = recRef.current
    if (!el) return
    const io = new IntersectionObserver(([e]) => setRecSeen(e.isIntersecting), { threshold: 0.4 })
    io.observe(el)
    return () => io.disconnect()
  }, [sel])

  const opt = card.options[sel]
  const due = until(card.deadline, nowMs)
  const other = card.routed_to_other || card.status === 'escalated'
  const closed = card.status !== 'open' && card.status !== 'escalated'
  const actionable = (!other || !!direct) && !closed && !!opt
  const who = first(card.approver) || 'the approver'
  const part = card.part_name || partName(card.part, state.cards)
  const pair = mismatchPair(card)
  const rest = card.evidence.map((_, i) => i).filter((i) => !pair || (i !== pair.sys && i !== pair.doc))
  const recommended = opt?.recommended || (sel === 0 && !card.options.some((o) => o.recommended))
  const routine = closed ? routineOf(state, card.id) : null
  const same = group ? sameIssueOn(group) : null

  useEffect(() => {
    if (!more) return
    const off = (e: MouseEvent) => { if (!moreRef.current?.contains(e.target as Node)) setMore(false) }
    document.addEventListener('mousedown', off)
    return () => document.removeEventListener('mousedown', off)
  }, [more])

  async function act(fn: () => Promise<unknown>) {
    setBusy(true)
    try { await fn() } finally { setBusy(false); setConfirm(false) }
  }
  const approve = () =>
    act(async () => {
      await post(`/api/cards/${card.id}/approve`, { option: sel, text: mode === 'edit' ? text : undefined })
      setMode('idle')
      onChanged()
    })
  const escalate = () =>
    act(async () => { await post(`/api/cards/${card.id}/escalate`, { note }); setMode('idle'); onChanged() })
  const dismiss = () => act(async () => { setMore(false); await post(`/api/cards/${card.id}/dismiss`); onChanged() })

  // Keyboard: a asks, Enter approves, Esc cancels. Never while typing.
  const approveRef = useRef(approve)
  approveRef.current = approve
  useEffect(() => {
    if (!keys) return
    const onKey = (e: KeyboardEvent) => {
      if (typing(e.target) || e.metaKey || e.ctrlKey || e.altKey) return
      if (confirm && e.key === 'Enter') { e.preventDefault(); void approveRef.current() }
      else if (confirm && e.key === 'Escape') { e.preventDefault(); setConfirm(false) }
      else if (!confirm && e.key.toLowerCase() === 'a' && actionable && mode === 'idle' && !busy) { e.preventDefault(); setConfirm(true) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [keys, confirm, actionable, mode, busy])

  return (
    <article className="pane-in space-y-6" aria-label={tidy(card.headline)}>
      {/* identity and time */}
      <div className="space-y-2">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-sm">
          <span className="font-semibold text-ink-2">{[card.supplier_name, part].filter(Boolean).join(' · ') || 'Soony'}</span>
          {card.deadline && !closed && (
            <span className={cn('font-semibold tabular-nums', card.level === 'red' && due?.urgent ? 'text-stop' : 'text-ink')}>
              Decide by {when(card.deadline, nowMs)}{due ? <span className="font-normal text-ink-3"> · {due.text}</span> : null}
            </span>
          )}
        </div>
        <h1 className="text-[24px] font-bold leading-tight tracking-tight sm:text-[28px]">{tidy(card.headline)}</h1>
        {same && <p className="text-sm font-semibold text-ink-2">Same issue on {same}.</p>}
      </div>

      {/* outcome so far */}
      {closed && (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-xl bg-panel px-4 py-3 text-[15px] ring-1 ring-line">
          <Check size={18} className={routine ? 'text-routine' : 'text-ok'} />
          <span className="font-semibold">{DONE_WORD[card.status] ?? 'Done'}</span>
          {card.ui?.at && <span className="text-ink-2">{when(card.ui.at, nowMs)}</span>}
          {routine && (
            <span className="inline-flex items-center gap-1 text-sm font-semibold text-routine">
              <Workflow size={14} /> By routine · {routine}
            </span>
          )}
        </div>
      )}
      {closed && offer && <AutomateOffer card={card} offer={offer} />}

      {other && (
        <div className="flex items-start gap-3 rounded-xl bg-panel px-4 py-3 text-[15px] text-ink-2 ring-1 ring-line">
          <Phone size={18} className="mt-0.5 shrink-0 text-ink-3" />
          <div>
            <div className="font-semibold text-ink">{card.status === 'escalated' ? `Waiting for ${who}` : `With ${who}`}</div>
            {card.reason && <div className="mt-0.5">{sentence(card.reason)}</div>}
          </div>
        </div>
      )}

      {/* the contradiction, with its source */}
      {pair ? (
        <Hero card={card} pair={pair} anchors={anchors.items} ready={anchors.ready} />
      ) : (
        card.number != null && card.number > 0 && (
          <div className="rounded-xl bg-panel p-5 ring-1 ring-line">
            <div className={cn('text-[44px] font-extrabold leading-none tabular-nums sm:text-[52px]', card.level === 'red' ? 'text-stop' : card.level === 'amber' ? 'text-warn' : 'text-ink')}>
              {fmtNum(card.number)}
            </div>
            <div className="mt-2 text-[16px] text-ink-2">{card.number_label}</div>
          </div>
        )
      )}
      {card.summary && <p className="text-[16px] leading-relaxed text-ink-2">{sentence(card.summary)}</p>}

      {(rest.length > 0 || card.trail.length > 0) && (
        <div className="divide-y divide-line border-y border-line">
          {rest.length > 0 && (
            <Fold title={`More evidence · ${rest.length}`}>
              <div className="space-y-3">
                {rest.map((i) => <EvidenceItem key={i} e={card.evidence[i]} anchors={anchors.items?.[i]} ready={anchors.ready} />)}
              </div>
            </Fold>
          )}
          {card.trail.length > 0 && (
            <Fold title="How we know">
              <Trail card={card} />
            </Fold>
          )}
        </div>
      )}

      {/* what to do */}
      {actionable && (
        <>
          <section ref={recRef} className="rounded-xl bg-panel p-5 ring-1 ring-line">
            <div className="text-xs font-bold uppercase tracking-wide text-ink-3">{recommended ? 'We recommend' : 'Your choice'}</div>
            <div className="mt-1.5 text-[20px] font-bold leading-snug">{sentence(opt.action)}</div>
            {opt.effect && <p className="mt-1 text-[16px] text-ink-2">{sentence(opt.effect)}</p>}
            <div className="mt-3 inline-flex rounded-md bg-white px-2.5 py-1 text-sm font-semibold ring-1 ring-line">{costText(opt.cost)}</div>
          </section>

          {card.options.length > 1 && (
            <ul className="-mt-3 space-y-0.5">
              {card.options.map((o, i) =>
                i === sel ? null : (
                  <li key={i}>
                    <button
                      onClick={() => { setSel(i); setMode('idle'); setConfirm(false) }}
                      className="flex w-full cursor-pointer items-start gap-2 rounded-lg px-3 py-2.5 text-left text-[15px] text-ink-2 hover:bg-panel"
                    >
                      <ChevronRight size={16} className="mt-0.5 shrink-0 text-ink-3" />
                      <span className="min-w-0 font-medium"><span className="text-ink-3">Or: </span>{sentence(o.action)}</span>
                      <span className="ml-auto shrink-0 pl-2 text-sm text-ink-3">{costText(o.cost)}</span>
                    </button>
                  </li>
                ),
              )}
            </ul>
          )}

          {mode === 'edit' && (
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={7}
              className="w-full rounded-xl border border-line p-4 text-[15px] leading-relaxed focus:outline-2 focus:outline-ok"
              aria-label="Edit draft"
            />
          )}
          {mode === 'escalate' && (
            <div className="rounded-xl border border-line p-4">
              <div className="text-xs font-semibold uppercase tracking-wide text-ink-3">Brief for {who}</div>
              <p className="mt-1 text-[15px] text-ink-2">{tidy(card.escalation_brief)}</p>
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Add one line (optional)"
                className="mt-3 h-10 w-full rounded-lg border border-line px-3 text-[15px] focus:outline-2 focus:outline-ok"
              />
              <div className="mt-3 flex gap-2">
                <Button size="sm" disabled={busy} onClick={escalate}>Send to {who}</Button>
                <Button size="sm" variant="ghost" onClick={() => setMode('idle')}>Cancel</Button>
              </div>
            </div>
          )}

          {/* The action bar stays in view while the evidence scrolls. */}
          <div className="sticky bottom-3 z-10 space-y-2 rounded-xl border border-line bg-white/95 p-3 shadow-[0_8px_24px_rgba(15,23,42,0.08)] backdrop-blur">
            {confirm && (
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg bg-ok/5 px-3 py-2.5 text-[15px] ring-1 ring-ok/30" role="alert">
                <span className="min-w-0 flex-1">
                  Press <kbd>Enter</kbd> to approve: <span className="font-semibold">{sentence(opt.action)}</span>{' '}
                  <span className="text-ink-2">({costText(opt.cost)})</span>
                </span>
                <span className="text-sm text-ink-3"><kbd>Esc</kbd> cancels</span>
              </div>
            )}
            {!confirm && !recSeen && (
              <p className="line-clamp-2 px-1 text-sm text-ink-2">
                <span className="text-ink-3">{recommended ? 'We recommend: ' : 'Your choice: '}</span>
                <span className="font-semibold text-ink">{sentence(opt.action)}</span> · {costText(opt.cost)}
              </p>
            )}
            <div className="flex flex-wrap items-center gap-2 max-md:flex-nowrap max-md:has-[ul]:flex-wrap">
              <Button disabled={busy} onClick={approve} className="px-6 max-md:flex-1 max-md:px-3">
                <Check size={18} /> {mode === 'edit' ? 'Approve edited' : 'Approve'}
                {keys && <kbd className="ml-1 border-white/40 bg-white/15 text-white">A</kbd>}
              </Button>
              <Voice card={card} onChanged={onChanged} ringing={ringing} onRingingDone={onRingingDone} />
              <Button
                variant="outline"
                className="max-md:hidden"
                disabled={!opt.draft && mode !== 'edit'}
                title={opt.draft ? undefined : 'This option has no draft to edit'}
                onClick={() => { setText(opt.draft ?? ''); setMode(mode === 'edit' ? 'idle' : 'edit') }}
              >
                Edit
              </Button>
              <Button variant="outline" className="max-md:hidden" onClick={() => setMode(mode === 'escalate' ? 'idle' : 'escalate')}>
                Escalate
              </Button>
              <div ref={moreRef} className="relative ml-auto">
                <Button variant="ghost" className="w-11 px-0" aria-label="More actions" onClick={() => setMore(!more)}>
                  <MoreHorizontal size={18} />
                </Button>
                {more && (
                  <div className="absolute bottom-12 right-0 z-20 w-60 rounded-xl border border-line bg-white p-1 shadow-lg">
                    {opt.draft && (
                      <MenuItem className="md:hidden" onClick={() => { setMore(false); setText(opt.draft ?? ''); setMode('edit') }}>Edit the draft</MenuItem>
                    )}
                    <MenuItem className="md:hidden" onClick={() => { setMore(false); setMode('escalate') }}>Escalate to {who}</MenuItem>
                    <MenuItem disabled={busy} onClick={dismiss} hint="Nothing to do; take it off the list">Dismiss</MenuItem>
                  </div>
                )}
              </div>
            </div>
          </div>
        </>
      )}

      {card.ui?.reply && (
        <section className="rounded-r-xl border-l-4 border-good bg-good/5 px-4 py-3">
          <div className="text-xs font-bold uppercase tracking-wide text-good">
            Reply from {card.ui.reply_from || 'supplier'}{card.ui.reply_at ? ` · ${when(card.ui.reply_at, nowMs)}` : ''}
          </div>
          <p className="mt-1 text-[16px] font-medium leading-snug">{card.ui.reply}</p>
          {card.ui.reply_text && (
            <details className="mt-2">
              <summary className="cursor-pointer text-sm text-ink-3">Original email</summary>
              <pre className="mt-1 whitespace-pre-wrap break-words font-sans text-sm text-ink-2">{card.ui.reply_text}</pre>
            </details>
          )}
        </section>
      )}

      {!actionable && !closed && <Voice card={card} onChanged={onChanged} ringing={ringing} onRingingDone={onRingingDone} />}

      <button
        onClick={() => onDrawer(card, 'evidence')}
        className="inline-flex cursor-pointer items-center gap-1.5 text-sm font-semibold text-ink-2 hover:text-ink md:hidden"
      >
        <FileSearch size={16} /> Show all evidence
      </button>
    </article>
  )
}

// VoiceCall brings its own "Talk it through" button and transcript; it sits in the action row,
// and takes the full width while ringing or once a transcript shows.
function Voice(p: { card: Card; onChanged: () => void; ringing?: boolean; onRingingDone?: () => void }) {
  return (
    <div
      className={cn(
        'min-w-0 [&>div]:mt-0 [&_ul]:max-h-56 [&_ul]:overflow-y-auto has-[ul]:basis-full',
        '[&_button]:h-11 [&_button]:text-[15px] max-md:[&_button]:px-3 md:[&_button]:px-5',
        p.ringing && 'order-first basis-full',
      )}
    >
      <VoiceCall card={p.card} onChanged={p.onChanged} ringing={p.ringing} onRingingDone={p.onRingingDone} />
    </div>
  )
}

function MenuItem({ children, hint, className, ...rest }: { children: ReactNode; hint?: string } & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button {...rest} className={cn('w-full cursor-pointer rounded-lg px-3 py-2 text-left text-[15px] font-semibold text-ink-2 hover:bg-panel', className)}>
      {children}
      {hint && <span className="block text-xs font-normal text-ink-3">{hint}</span>}
    </button>
  )
}

function Fold({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="group">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 py-3 text-sm font-semibold text-ink-2 hover:text-ink [&::-webkit-details-marker]:hidden">
        <ChevronRight size={16} className="transition-transform group-open:rotate-90" /> {title}
      </summary>
      <div className="pb-4">{children}</div>
    </details>
  )
}

/** After a decision by hand: offer to handle the next ones like it, in place. */
function AutomateOffer({ card, offer }: { card: Card; offer: Offer }) {
  const who = card.supplier_name
  return (
    <section className="slide-in rounded-xl border border-routine/30 bg-routine/5 p-5" aria-live="polite">
      <div className="text-[18px] font-bold leading-snug">
        Done.{' '}
        {offer.more > 0
          ? `${offer.more} more like this${who ? ` from ${who}` : ''}. Handle them the same way?`
          : 'Handle the next one like this on its own?'}
      </div>
      <p className="mt-1 text-[15px] text-ink-2">Money and plan changes still come to you.</p>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button onClick={offer.onYes} className="bg-routine hover:bg-[#6d28d9]"><Sparkles size={16} /> Show me the routine</Button>
        <Button variant="outline" onClick={offer.onNext}>Next decision</Button>
      </div>
    </section>
  )
}

// ---------------------------------------------------------------- hero

const where = (a: Anchor) =>
  a.kind === 'table' || a.kind === 'text' ? `row ${(a.row ?? 0) + 1}` : `p. ${a.page ?? 1}`
const docName = (doc: string) => (doc.split('/').pop() ?? doc).replace(/\.[a-z0-9]+$/i, '').replace(/_/g, ' ')

function Hero({
  card, pair, anchors, ready,
}: { card: Card; pair: { sys: number; doc: number }; anchors: Record<number, Anchor[]> | null; ready: boolean }) {
  const s = card.evidence[pair.sys]
  const d = card.evidence[pair.doc]
  const sysA = anchors?.[pair.sys] ?? []
  const docA = anchors?.[pair.doc] ?? []
  // The paper side opens right away; the system's row is one click away.
  const [pick, setPick] = useState<{ side: 'doc' | 'sys'; i: number } | null>(null)
  const active = pick ? (pick.side === 'doc' ? docA : sysA)[pick.i] : docA[0] ?? null
  const activeKey = pick ?? (docA[0] ? { side: 'doc' as const, i: 0 } : null)
  const tone = card.level === 'red' ? 'stop' : card.level === 'amber' ? 'warn' : null
  const lang = languageOf(d.source, d.language)
  const page = d.page ?? docA[0]?.page
  const docHead = `The document says${lang || page ? ` (${[lang, page ? `p. ${page}` : ''].filter(Boolean).join(', ')})` : ''}`
  const sources = [
    ...docA.map((a, i) => ({ side: 'doc' as const, i, a })),
    ...sysA.map((a, i) => ({ side: 'sys' as const, i, a })),
  ]

  return (
    <section className="space-y-3">
      <div className="grid items-stretch gap-3 md:grid-cols-[1fr_auto_1fr]">
        <Slab head="The system says" source={sourceName(s.source) || 'System'} value={s.value} />
        <div className="grid place-items-center text-[28px] font-bold text-ink-3 max-md:-my-2" aria-label="does not match">≠</div>
        <Slab head={docHead} source={sourceName(d.source) || 'Document'} value={d.value} tone={tone} />
      </div>

      {(d.quote || d.translation) && (
        <div className="space-y-1 px-1">
          {d.quote && <blockquote className="border-l-2 border-line pl-3 text-[15px] leading-relaxed text-ink">“{d.quote}”</blockquote>}
          {d.translation && <p className="pl-3.5 text-sm text-ink-2">In English: {d.translation}</p>}
        </div>
      )}

      {sources.length > 1 && (
        <div className="flex flex-wrap gap-1.5">
          {sources.map((x) => {
            const on = activeKey?.side === x.side && activeKey.i === x.i
            return (
              <button
                key={`${x.side}-${x.i}`}
                onClick={() => setPick({ side: x.side, i: x.i })}
                title={x.a.doc}
                className={cn(
                  'inline-flex max-w-full cursor-pointer items-center gap-1.5 rounded-full border px-3 py-1 text-sm',
                  on ? 'border-ink bg-ink font-semibold text-white' : 'border-line text-ink-2 hover:bg-panel',
                )}
              >
                <span className="shrink-0 font-semibold">{x.side === 'doc' ? 'Document' : 'System'}</span>
                <span className="truncate">{docName(x.a.doc)}</span>
                <span className={cn('shrink-0', on ? 'text-white/70' : 'text-ink-3')}>· {where(x.a)}</span>
              </button>
            )
          })}
        </div>
      )}
      {active ? (
        <OpenAnchor anchor={active} />
      ) : !ready ? (
        <p className="px-1 text-sm text-ink-3">Looking for this in the documents…</p>
      ) : d.doc ? (
        <a
          href={`/api/file?path=${encodeURIComponent(d.doc)}${d.page ? `#page=${d.page}` : ''}`}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1.5 px-1 text-sm font-semibold text-ok hover:underline"
        >
          Open the document <ExternalLink size={14} />
        </a>
      ) : null}
    </section>
  )
}

function Slab({ head, source, value, tone }: { head: string; source: string; value: Card['evidence'][number]['value']; tone?: 'stop' | 'warn' | null }) {
  const { big, rest } = splitValue(value)
  const long = big.length > 14
  return (
    <div
      className={cn(
        'min-w-0 rounded-xl p-5 ring-1',
        tone === 'stop' ? 'bg-stop/[0.05] ring-stop/25' : tone === 'warn' ? 'bg-warn/[0.06] ring-warn/25' : 'bg-white ring-line',
      )}
    >
      <div className={cn('text-xs font-bold uppercase tracking-wide', tone === 'stop' ? 'text-stop' : tone === 'warn' ? 'text-warn' : 'text-ink-3')}>
        {head}
      </div>
      <div className="mt-0.5 text-sm text-ink-2">{source}</div>
      <div
        className={cn(
          'mt-3 font-extrabold leading-none tabular-nums tracking-tight',
          long ? 'text-[28px] leading-tight' : 'text-[40px] sm:text-[48px]',
          tone === 'stop' ? 'text-stop' : tone === 'warn' ? 'text-warn' : 'text-ink',
        )}
      >
        {big}
      </div>
      {rest && <div className="mt-2 text-[16px] font-medium leading-snug text-ink-2">{rest}</div>}
    </div>
  )
}
