import { Check, ChevronRight, FileSearch, MoreHorizontal, Phone, Workflow } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { VoiceCall } from '@/components/VoiceCall'
import {
  COLORS, DONE_WORD, costText, fmtDay, fmtNum, fmtTime, partName, post, sentence, tidy, until, type Card,
} from '@/lib/api'
import { cn } from '@/lib/utils'

interface Props {
  card: Card
  nowMs: number
  /** All cards, to give a part code a readable name. */
  cards?: Card[]
  isNew?: boolean
  /** Opened from the deep link (/d/<id>): whoever it is routed to can act on it here. */
  direct?: boolean
  onChanged: () => void
  onHover?: (routes: string[]) => void
  onDrawer: (card: Card, tab: 'evidence' | 'trail') => void
  ringing?: boolean
  onRingingDone?: () => void
}

// A person's first name; a role ('Head of Procurement', 'DC site lead') stays whole.
const first = (s?: string) => (!s ? '' : /\s[a-z]/.test(s) ? s : (s.split(' ').find((w) => !w.endsWith('.')) ?? s))

// Same split as the evidence drawer: what the system says against what a document says.
const SYSTEM = /system|sap|erp|plan|book stock/i
const PAPER = /paper|document|scan|advice|confirmation|delivery note|notice|invoice/i
const sourceName = (src: string | undefined, fallback: string) =>
  (src ?? '').replace(/^(System|Paper) says\s*·?\s*/i, '').trim() || fallback

function mismatch(card: Card) {
  const sys = card.evidence.findIndex((e) => SYSTEM.test(e.source ?? '') && e.value != null)
  const doc = card.evidence.findIndex((e, i) => i !== sys && PAPER.test(e.source ?? '') && e.value != null)
  if (sys < 0 || doc < 0) return null
  const s = card.evidence[sys]
  const d = card.evidence[doc]
  return {
    system: { label: sourceName(s.source, 'System'), value: tidy(String(s.value)) },
    doc: { label: sourceName(d.source, 'Document'), value: tidy(String(d.value)) },
  }
}

export function DecisionCard({
  card, nowMs, cards, isNew, direct, onChanged, onHover, onDrawer, ringing, onRingingDone,
}: Props) {
  const [sel, setSel] = useState(() => Math.max(0, card.options.findIndex((o) => o.recommended)))
  const [mode, setMode] = useState<'idle' | 'edit' | 'escalate'>('idle')
  const [text, setText] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [more, setMore] = useState(false)
  const moreRef = useRef<HTMLDivElement>(null)
  const color = COLORS[card.level]
  const opt = card.options[sel]
  const due = until(card.deadline, nowMs)
  const other = card.routed_to_other || card.status === 'escalated'
  const closed = card.status !== 'open' && card.status !== 'escalated'
  const who = first(card.approver) || 'the approver'
  const part = partName(card.part, cards ?? [card])
  const diff = mismatch(card)

  useEffect(() => {
    if (!more) return
    const off = (e: MouseEvent) => { if (!moreRef.current?.contains(e.target as Node)) setMore(false) }
    document.addEventListener('mousedown', off)
    return () => document.removeEventListener('mousedown', off)
  }, [more])

  async function act(fn: () => Promise<unknown>) {
    setBusy(true)
    try { await fn() } finally { setBusy(false) }
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

  return (
    <article
      id={`card-${card.id}`}
      onMouseEnter={() => onHover?.(card.routes)}
      onMouseLeave={() => onHover?.([])}
      className={cn('relative scroll-mt-4 rounded-2xl border border-line bg-white p-4 pl-5 sm:p-5 sm:pl-6', isNew && 'slide-in', other && 'bg-panel')}
    >
      <span className="absolute inset-y-0 left-0 w-1.5 rounded-l-2xl" style={{ background: color }} />

      <div className="flex min-w-0 items-center gap-x-1.5 text-sm text-ink-3">
        <span className="shrink-0 font-semibold text-ink-2">{card.supplier_name || part || 'Soony'}</span>
        {card.supplier_name && part && <span className="truncate" title={part}>· {part}</span>}
      </div>

      <h2 className="mt-1.5 text-[19px] font-bold leading-snug sm:text-[21px]">{tidy(card.headline)}</h2>

      <div className="mt-4 flex flex-wrap items-end gap-x-8 gap-y-3">
        {card.number != null && card.number > 0 && (
          <div>
            <div className="text-[34px] font-extrabold leading-none tabular-nums sm:text-[40px]" style={{ color }}>{fmtNum(card.number)}</div>
            <div className="mt-1 text-sm font-medium text-ink-3">{card.number_label}</div>
          </div>
        )}
        {card.deadline && !closed && (
          <div>
            <div className={cn('text-[24px] font-extrabold leading-none tabular-nums sm:text-[28px]', due?.urgent && 'text-stop')}>
              {fmtDay(card.deadline)} {fmtTime(card.deadline)}
            </div>
            <div className="mt-1 text-sm font-medium text-ink-3">Decide by{due ? ` · ${due.text}` : ''}</div>
          </div>
        )}
      </div>

      {diff && (
        <button
          onClick={() => onDrawer(card, 'evidence')}
          className="group mt-4 block w-full cursor-pointer rounded-lg border border-line px-3 py-2 text-left text-sm hover:bg-panel"
          title="Open the evidence"
        >
          <span className="grid grid-cols-2 gap-3">
            <Says head="System" label={diff.system.label} value={diff.system.value} />
            <Says head="Document" label={diff.doc.label} value={diff.doc.value} strong />
          </span>
          <span className="mt-1.5 inline-flex items-center gap-1 text-xs font-semibold text-ok group-hover:underline">
            They do not match · see the documents <ChevronRight size={13} />
          </span>
        </button>
      )}

      {other && (
        <div className="mt-4 flex items-start gap-2 rounded-lg bg-white px-3 py-2 text-sm text-ink-2 ring-1 ring-line">
          <Phone size={16} className="mt-0.5 shrink-0 text-ink-3" />
          <div>
            <span className="font-semibold text-ink">
              {card.status === 'escalated'
                ? `Waiting for ${who}`
                : `With ${who}${card.called_at ? ` · called ${fmtTime(card.called_at)}` : ''}`}
            </span>
            {card.reason && <div>{sentence(card.reason)}</div>}
          </div>
        </div>
      )}

      {closed && (
        <div className="mt-4 flex items-center gap-2 rounded-lg bg-ok/10 px-3 py-2 text-[15px] font-semibold text-ok">
          <Check size={18} /> {DONE_WORD[card.status] ?? 'Done'}
          {card.ui?.at && <span className="font-normal text-ink-2">· {fmtDay(card.ui.at)} {fmtTime(card.ui.at)}</span>}
        </div>
      )}

      {(!other || direct) && !closed && opt && (
        <>
          <div className="mt-4 rounded-xl border-2 p-4" style={{ borderColor: `${color}55`, background: `${color}0d` }}>
            <div className="text-xs font-bold uppercase tracking-wide" style={{ color }}>
              {opt.recommended || (sel === 0 && !card.options.some((o) => o.recommended)) ? 'We recommend' : 'Your choice'}
            </div>
            <div className="mt-1 text-[17px] font-bold leading-snug">{sentence(opt.action)}</div>
            {opt.effect && <div className="mt-1 text-[15px] text-ink-2">{sentence(opt.effect)}</div>}
            <div className="mt-2 inline-flex rounded-md bg-white/80 px-2 py-0.5 text-sm font-semibold text-ink ring-1 ring-line">
              {costText(opt.cost)}
            </div>
          </div>

          {card.options.length > 1 && (
            <ul className="mt-2 space-y-0.5">
              {card.options.map((o, i) =>
                i === sel ? null : (
                  <li key={i}>
                    <button
                      onClick={() => { setSel(i); setMode('idle') }}
                      className="flex w-full cursor-pointer items-start gap-2 rounded-lg px-3 py-2 text-left text-[15px] text-ink-2 hover:bg-panel"
                    >
                      <ChevronRight size={16} className="mt-0.5 shrink-0 text-ink-3" />
                      <span className="min-w-0 font-medium">
                        <span className="text-ink-3">Or: </span>{sentence(o.action)}
                      </span>
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
              rows={5}
              className="mt-3 w-full rounded-lg border border-line p-3 text-[15px] focus:outline-2 focus:outline-ok"
              aria-label="Edit draft"
            />
          )}
          {mode === 'escalate' && (
            <div className="mt-3 rounded-lg border border-line p-3">
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

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button disabled={busy} onClick={approve} className="w-full px-6 sm:w-auto">
              <Check size={18} /> {mode === 'edit' ? 'Approve edited' : 'Approve'}
            </Button>
            <Button
              variant="outline"
              disabled={!opt.draft && mode !== 'edit'}
              title={opt.draft ? undefined : 'This option has no draft to edit'}
              onClick={() => { setText(opt.draft ?? ''); setMode(mode === 'edit' ? 'idle' : 'edit') }}
            >
              Edit
            </Button>
            <Button variant="outline" onClick={() => setMode(mode === 'escalate' ? 'idle' : 'escalate')}>Escalate</Button>
            <div ref={moreRef} className="relative ml-auto">
              <Button variant="ghost" className="w-11 px-0" aria-label="More actions" onClick={() => setMore(!more)}>
                <MoreHorizontal size={18} />
              </Button>
              {more && (
                <div className="absolute right-0 top-12 z-20 w-56 rounded-xl border border-line bg-white p-1 shadow-lg">
                  <button
                    disabled={busy}
                    onClick={dismiss}
                    className="w-full cursor-pointer rounded-lg px-3 py-2 text-left text-[15px] font-semibold text-ink-2 hover:bg-panel"
                  >
                    Dismiss
                    <span className="block text-xs font-normal text-ink-3">Nothing to do; take it off the list</span>
                  </button>
                </div>
              )}
            </div>
          </div>
        </>
      )}

      {card.ui?.reply && (
        <div className="mt-4 rounded-xl border-2 border-ok/30 bg-ok/5 p-4">
          <div className="text-xs font-bold uppercase tracking-wide text-ok">
            Reply from {card.ui.reply_from || 'supplier'}
            {card.ui.reply_at ? ` · ${fmtDay(card.ui.reply_at)} ${fmtTime(card.ui.reply_at)}` : ''}
          </div>
          <p className="mt-1 text-[15px] font-medium leading-snug">{card.ui.reply}</p>
          {card.ui.reply_text && (
            <details className="mt-2">
              <summary className="cursor-pointer text-sm text-ink-3">Original email</summary>
              <pre className="mt-1 whitespace-pre-wrap break-words font-sans text-sm text-ink-2">{card.ui.reply_text}</pre>
            </details>
          )}
        </div>
      )}

      {!closed && <VoiceCall card={card} onChanged={onChanged} ringing={ringing} onRingingDone={onRingingDone} />}

      <div className="mt-4 flex gap-5 border-t border-line pt-3 text-sm font-semibold text-ink-2">
        <button className="inline-flex cursor-pointer items-center gap-1.5 hover:text-ink" onClick={() => onDrawer(card, 'evidence')}>
          <FileSearch size={16} /> Evidence
        </button>
        <button className="inline-flex cursor-pointer items-center gap-1.5 hover:text-ink" onClick={() => onDrawer(card, 'trail')}>
          <Workflow size={16} /> Agent trail
        </button>
      </div>
    </article>
  )
}

function Says({ head, label, value, strong }: { head: string; label: string; value: string; strong?: boolean }) {
  return (
    <span className="block min-w-0 leading-snug">
      <span className="block truncate text-xs text-ink-3" title={label}>
        <span className="font-semibold uppercase tracking-wide">{head}</span>
        {label !== head && ` · ${label}`}
      </span>
      <span className={cn('mt-0.5 block font-semibold', strong ? 'text-stop' : 'text-ink')}>{value}</span>
    </span>
  )
}
