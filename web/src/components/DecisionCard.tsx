import { Check, ChevronRight, FileSearch, Phone, Workflow } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { COLORS, fmtDay, fmtNum, fmtTime, post, until, type Card } from '@/lib/api'
import { cn } from '@/lib/utils'

export interface Offer { cardId: string; type: string; card: Card }

interface Props {
  card: Card
  nowMs: number
  isNew?: boolean
  /** Opened from the deep link (/d/<id>): whoever it is routed to can act on it here. */
  direct?: boolean
  offer?: Offer | null
  onOffer: (o: Offer | null) => void
  onChanged: () => void
  onHover?: (routes: string[]) => void
  onDrawer: (card: Card, tab: 'evidence' | 'trail') => void
}

// A person's first name; a role ('Head of Procurement', 'DC site lead') stays whole.
const first = (s?: string) => (!s ? '' : /\s[a-z]/.test(s) ? s : (s.split(' ').find((w) => !w.endsWith('.')) ?? s))

export function DecisionCard({ card, nowMs, isNew, direct, offer, onOffer, onChanged, onHover, onDrawer }: Props) {
  const [sel, setSel] = useState(0)
  const [mode, setMode] = useState<'idle' | 'edit' | 'escalate'>('idle')
  const [text, setText] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const color = COLORS[card.level]
  const opt = card.options[sel]
  const due = until(card.deadline, nowMs)
  const other = card.routed_to_other || card.status === 'escalated'
  const who = first(card.approver) || 'the approver'
  const decided = !!offer && offer.cardId === card.id

  async function act(fn: () => Promise<unknown>) {
    setBusy(true)
    try { await fn() } finally { setBusy(false) }
  }
  const approve = () =>
    act(async () => {
      const r = await post<{ offer_trust: string | null }>(`/api/cards/${card.id}/approve`, {
        option: sel, text: mode === 'edit' ? text : undefined,
      })
      if (r.offer_trust) onOffer({ cardId: card.id, type: r.offer_trust, card })
      setMode('idle')
      onChanged()
    })
  const escalate = () =>
    act(async () => { await post(`/api/cards/${card.id}/escalate`, { note }); setMode('idle'); onChanged() })
  const dismiss = () => act(async () => { await post(`/api/cards/${card.id}/dismiss`); onChanged() })
  const trust = (m: 'allow_always' | 'ask') =>
    act(async () => { await post('/api/trust', { action_type: offer!.type, mode: m }); onOffer(null); onChanged() })

  return (
    <article
      onMouseEnter={() => onHover?.(card.routes)}
      onMouseLeave={() => onHover?.([])}
      className={cn('relative rounded-2xl border border-line bg-white p-5 pl-6', isNew && 'slide-in', other && 'bg-panel')}
    >
      <span className="absolute inset-y-0 left-0 w-1.5 rounded-l-2xl" style={{ background: color }} />

      <div className="flex flex-wrap items-center gap-x-2 text-sm text-ink-3">
        <span className="font-semibold text-ink-2">{card.supplier_name || card.part || 'Soony'}</span>
        {card.supplier_name && card.part && <span>· {card.part}</span>}
      </div>

      <h2 className="mt-1.5 text-[21px] font-bold leading-snug">{card.headline}</h2>

      <div className="mt-4 flex flex-wrap items-end gap-x-8 gap-y-2">
        {card.number != null && card.number > 0 && (
          <div>
            <div className="text-[40px] font-extrabold leading-none tabular-nums" style={{ color }}>{fmtNum(card.number)}</div>
            <div className="mt-1 text-sm font-medium text-ink-3">{card.number_label}</div>
          </div>
        )}
        {card.deadline && (
          <div>
            <div className={cn('text-[28px] font-extrabold leading-none tabular-nums', due?.urgent && 'text-stop')}>
              {fmtDay(card.deadline)} {fmtTime(card.deadline)}
            </div>
            <div className="mt-1 text-sm font-medium text-ink-3">decide by{due ? ` · ${due.text}` : ''}</div>
          </div>
        )}
      </div>

      {other && (
        <div className="mt-4 flex items-start gap-2 rounded-lg bg-white px-3 py-2 text-sm text-ink-2 ring-1 ring-line">
          <Phone size={16} className="mt-0.5 shrink-0 text-ink-3" />
          <div>
            <span className="font-semibold text-ink">
              {card.status === 'escalated'
                ? `Waiting for ${who}`
                : `With ${who}${card.called_at ? ` · called ${fmtTime(card.called_at)}` : ''}`}
            </span>
            {card.reason && <div>{card.reason}</div>}
          </div>
        </div>
      )}

      {(!other || direct) && !decided && opt && (
        <>
          <div className="mt-4 rounded-xl border-2 p-4" style={{ borderColor: `${color}55`, background: `${color}0d` }}>
            <div className="text-xs font-bold uppercase tracking-wide" style={{ color }}>
              {sel === 0 ? 'Recommended' : 'Option'}
            </div>
            <div className="mt-1 text-[17px] font-bold leading-snug">{opt.action}</div>
            <div className="mt-1 text-[15px] text-ink-2">{[opt.cost, opt.effect].filter(Boolean).join(' · ')}</div>
          </div>

          {card.options.length > 1 && (
            <ul className="mt-2 space-y-1">
              {card.options.map((o, i) =>
                i === sel ? null : (
                  <li key={i}>
                    <button
                      onClick={() => { setSel(i); setMode('idle') }}
                      className="flex w-full cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-left text-[15px] text-ink-2 hover:bg-panel"
                    >
                      <ChevronRight size={16} className="shrink-0 text-ink-3" />
                      <span className="font-medium">{o.action}</span>
                      <span className="ml-auto shrink-0 text-sm text-ink-3">{o.cost}</span>
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
              <p className="mt-1 text-[15px] text-ink-2">{card.escalation_brief}</p>
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

          <div className="mt-4 flex flex-wrap gap-2">
            <Button disabled={busy} onClick={approve}>
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
            <Button variant="ghost" disabled={busy} onClick={dismiss}>Dismiss</Button>
          </div>
        </>
      )}

      {decided && offer && (
        <div className="mt-4 rounded-xl border-2 border-ok/40 bg-ok/5 p-4">
          <div className="flex items-center gap-2 text-[15px] font-bold text-ok"><Check size={18} /> Approved</div>
          <p className="mt-2 text-[17px] font-semibold leading-snug">
            Send routine ETA chasers like this one automatically from now on?
          </p>
          <p className="mt-1 text-[15px] text-ink-2">You'll still approve anything that costs money.</p>
          <div className="mt-3 flex gap-2">
            <Button disabled={busy} onClick={() => trust('allow_always')}>Allow always</Button>
            <Button variant="outline" disabled={busy} onClick={() => trust('ask')}>Ask me each time</Button>
          </div>
        </div>
      )}

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
