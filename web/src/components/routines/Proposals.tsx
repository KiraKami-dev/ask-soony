import * as Dialog from '@radix-ui/react-dialog'
import { Check } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { tidy, type Card, type State } from '@/lib/api'
import { headlineOf, plural, shortName } from '@/lib/today'
import { cn } from '@/lib/utils'
import { answerProposal, goTo, setEnabled, setMode } from './live'
import { modeOf, type Proposal, type StepKind, type WouldHandle } from './types'

// What routines in "Ask me" mode prepared, in the Today style: one quiet line per routine
// ("Your routine ‘X’ prepared 3 emails to PackRight · review") that opens a review sheet shaped
// like the email review of a decision: to, subject and the text per item, "Send all" plus
// "Send" or "Skip" per item. Nothing goes out before a tap. The server takes no edits on a
// proposal, so the text is shown as the routine wrote it and the sheet says so.

/** The bits of the state the wording needs; the Plan pane passes just its card. */
export type Ctx = Pick<State, 'cards'> & Partial<Pick<State, 'shipments' | 'map'>>

const KNOWN: StepKind[] = ['carry_out', 'notify', 'log', 'chase', 'call', 'ask_status']
const kindOf = (p: Proposal): StepKind => (KNOWN.includes(p.step?.do as StepKind) ? (p.step.do as StepKind) : 'carry_out')

export interface Mail {
  /** An email to an address: the item reads like the email review. */
  email: boolean
  kind: StepKind
  /** One line on what it does: "Chase once in the same thread". */
  what: string
  to: string
  toName: string
  subject: string
  text: string
  card?: Card
}

export function proposalMail(p: Proposal, ctx: Ctx): Mail {
  const kind = kindOf(p)
  const card = p.card_id ? ctx.cards.find((c) => c.id === p.card_id) : undefined
  const ship = p.shipment_id ? ctx.shipments?.find((s) => s.id === p.shipment_id) : undefined
  const supplier = card?.supplier_name || (ship ? ctx.map?.nodes.find((n) => n.id === ship.supplier_id)?.name : '') || ''
  const raw = String(p.preview_text ?? '').replace(/\r/g, '').trim()
  const lines = raw.split('\n')
  const head = lines[0] ?? ''
  const rest = lines.slice(1).join('\n').trim()
  const stepTo = String(p.step?.to ?? '')
  const base = { kind, card, toName: supplier, what: tidy(p.step?.text) || '' }

  if (kind === 'chase') {
    const m = head.match(/^Follow-up to (\S+) \(Re: (.*)\):$/)
    return { ...base, email: true, what: 'Chase once in the same thread', to: m?.[1] ?? stepTo, subject: m ? `Re: ${m[2]}` : '', text: m ? rest : raw }
  }
  if (kind === 'ask_status') {
    const m = head.match(/^Email to (\S+) \((.*)\):$/)
    return { ...base, email: true, what: 'Ask for the shipment status', to: m?.[1] ?? stepTo, subject: m?.[2] ?? '', text: m ? rest : raw }
  }
  if (kind === 'carry_out') {
    const opt = card?.options[Number(p.step?.option_index ?? 0)]
    const to = opt?.to ?? stepTo
    // "<action>\nTo <to>:\n<draft>" from the routine runner.
    const hasTo = /^To .*:$/.test(lines[1] ?? '')
    const text = hasTo ? lines.slice(2).join('\n').trim() : rest
    const subject = (opt as { subject?: string } | undefined)?.subject || (card ? headlineOf(card, ctx as State) : '')
    return {
      ...base, email: opt?.channel === 'email' && /@/.test(to), what: tidy(opt?.action) || head || base.what,
      to, subject, text: text || tidy(opt?.draft),
    }
  }
  if (kind === 'call') return { ...base, email: false, what: 'Call you', to: '', subject: '', text: raw }
  return { ...base, email: false, what: base.what || 'Tell you', to: '', subject: '', text: raw }
}

export interface Group { routineId: string; name: string; items: Proposal[]; line: string }

/** "prepared 3 emails to PackRight" for one routine's proposals. */
function groupLine(items: Proposal[], ctx: Ctx): string {
  const mails = items.map((p) => proposalMail(p, ctx))
  const allEmail = mails.every((m) => m.email)
  const who = [...new Set(mails.map((m) => shortName(m.toName)).filter(Boolean))]
  const noun = allEmail ? 'email' : mails.every((m) => m.kind === 'call') ? 'call' : 'step'
  const what = items.length === 1 ? `an ${noun}`.replace(/^an s/, 'a s').replace(/^an c/, 'a c') : plural(items.length, noun)
  const to = who.length === 1 ? ` to ${who[0]}` : who.length > 1 ? ` to ${who.length} suppliers` : ''
  return `prepared ${what}${allEmail ? to : ''}`
}

/** Pending proposals by routine, most first. */
export function groupProposals(items: Proposal[], ctx: Ctx): Group[] {
  const by = new Map<string, Proposal[]>()
  for (const p of items) by.set(p.routine_id, [...(by.get(p.routine_id) ?? []), p])
  return [...by.entries()]
    .map(([routineId, ps]) => ({ routineId, name: ps[0].routine_name, items: ps, line: groupLine(ps, ctx) }))
    .sort((a, b) => b.items.length - a.items.length)
}

// ---------------------------------------------------------------- the quiet line

/** A green routine line with quiet links: "Your routine ‘X’ prepared 3 emails · review". */
export function RoutineLine({ children, links, className }: { children: ReactNode; links?: { label: string; onClick: () => void; busy?: boolean }[]; className?: string }) {
  return (
    <p className={cn('t-small text-ink-3', className)}>
      <span className="dot-routine text-routine">{children}</span>
      {links?.map((l) => (
        <span key={l.label}>
          {' · '}
          <button className="uline" onClick={l.onClick} disabled={l.busy}>{l.busy ? `${l.label}…` : l.label}</button>
        </span>
      ))}
    </p>
  )
}

/** One line per routine with something waiting; more than `max` folds into one count. */
export function ProposalLines({
  groups, onReview, max = 2, className,
}: { groups: Group[]; onReview: (items: Proposal[]) => void; max?: number; className?: string }) {
  if (!groups.length) return null
  if (groups.length > max) {
    const all = groups.flatMap((g) => g.items)
    return <RoutineLine className={className} links={[{ label: 'review', onClick: () => onReview(all) }]}>{all.length} waiting for your OK</RoutineLine>
  }
  return (
    <>
      {groups.map((g) => (
        <RoutineLine key={g.routineId} className={className} links={[{ label: 'review', onClick: () => onReview(g.items) }]}>
          Your routine ‘{g.name}’ {g.line}
        </RoutineLine>
      ))}
    </>
  )
}

// ---------------------------------------------------------------- would handle

/** "Your routine ‘X’ handles cases like this · let it · ask me first" on a focused decision. */
export function WouldLine({ w, onChanged, className }: { w: WouldHandle; onChanged: () => void; className?: string }) {
  const [busy, setBusy] = useState<'tell' | 'ask' | null>(null)
  const [done, setDone] = useState<'tell' | 'ask' | null>(null)
  const [error, setError] = useState('')
  const mode = modeOf(w)

  async function go(m: 'tell' | 'ask') {
    setBusy(m); setError('')
    try {
      if (!w.enabled) await setEnabled(w.routine_id, true)
      await setMode(w.routine_id, m)
      setDone(m)
      onChanged()
    } catch (e) {
      const why = (e as Error).message
      setError(/404|not found/i.test(why) ? 'That is not available on the server yet.' : `That did not go through: ${why}.`)
    } finally { setBusy(null) }
  }

  if (done) {
    return (
      <RoutineLine className={className}>
        ‘{w.routine_name}’ is on. {done === 'tell' ? 'It handles these and tells you.' : 'It prepares these; you send with one tap.'}
      </RoutineLine>
    )
  }
  const links = [
    ...(!w.enabled || mode === 'ask' ? [{ label: 'let it', onClick: () => go('tell'), busy: busy === 'tell' }] : []),
    ...(!w.enabled || mode !== 'ask' ? [{ label: 'ask me first', onClick: () => go('ask'), busy: busy === 'ask' }] : []),
  ]
  return (
    <div className={className}>
      <RoutineLine links={links}>
        Your routine ‘{w.routine_name}’ {w.enabled && mode === 'ask' ? 'prepares cases like this for you' : 'handles cases like this'}
      </RoutineLine>
      {error && <p className="t-small mt-1 text-late" role="alert">{error}</p>}
    </div>
  )
}

// ---------------------------------------------------------------- the review sheet

type ItemState = { s: 'busy' | 'sent' | 'skipped' } | { s: 'error'; why: string }
const at = () => new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })

/**
 * Review what routines prepared. `items` is a snapshot taken when the sheet opens, so a sent
 * item stays on screen with its time instead of vanishing when the server stops listing it.
 */
export function ProposalSheet({
  items, ctx, onClose, onChanged,
}: { items: Proposal[] | null; ctx: Ctx; onClose: () => void; onChanged: () => void }) {
  const open = !!items?.length
  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fade fixed inset-0 z-[60] bg-[rgba(24,24,26,.28)]" />
        <Dialog.Content
          aria-describedby={undefined}
          className={cn(
            'sheet-up fixed z-[70] overflow-y-auto bg-ground focus:outline-none',
            'inset-x-0 bottom-0 max-h-[92vh] rounded-t-[22px] px-6 pb-[max(28px,env(safe-area-inset-bottom))] pt-3 shadow-[0_-10px_40px_-16px_rgba(0,0,0,.25)]',
            'md:inset-x-auto md:bottom-auto md:left-1/2 md:top-[6vh] md:max-h-[88vh] md:w-[640px] md:-translate-x-1/2 md:rounded-2xl md:px-8 md:pb-8 md:pt-7 md:shadow-[0_20px_60px_-20px_rgba(0,0,0,.3)]',
          )}
        >
          <div className="mx-auto mb-5 h-[5px] w-9 rounded-full bg-hair md:hidden" aria-hidden="true" />
          {open && <ProposalReview key={items!.map((p) => p.id).join()} items={items!} ctx={ctx} onClose={onClose} onChanged={onChanged} />}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function ProposalReview({
  items, ctx, onClose, onChanged,
}: { items: Proposal[]; ctx: Ctx; onClose: () => void; onChanged: () => void }) {
  const [st, setSt] = useState<Record<string, ItemState & { at?: string }>>({})
  const [all, setAll] = useState<{ i: number; n: number } | null>(null)
  // "How it decided": the routine's own view on the Routines page.
  const show = (routineId: string) => { onClose(); goTo(`/routines?routine=${encodeURIComponent(routineId)}`) }
  const mails = items.map((p) => proposalMail(p, ctx))
  const names = [...new Set(items.map((p) => p.routine_name))]
  const allEmail = mails.every((m) => m.email)
  const open = items.filter((p) => !st[p.id] || st[p.id].s === 'error')
  const finished = open.length === 0
  const verb = (m: Mail) => (m.email ? 'Send' : m.kind === 'call' ? 'Call' : 'Do it')

  async function answer(p: Proposal, how: 'accept' | 'skip'): Promise<boolean> {
    setSt((s) => ({ ...s, [p.id]: { s: 'busy' } }))
    try {
      await answerProposal(p.id, how)
      setSt((s) => ({ ...s, [p.id]: { s: how === 'accept' ? 'sent' : 'skipped', at: at() } }))
      return true
    } catch (e) {
      const why = (e as Error).message
      setSt((s) => ({ ...s, [p.id]: { s: 'error', why: /404|not found|unknown proposal/i.test(why) ? 'This one is gone; the routine dropped it.' : `That did not go through: ${why}. Nothing was sent.` } }))
      return false
    }
  }
  async function sendAll() {
    const todo = open
    for (let i = 0; i < todo.length; i++) {
      setAll({ i: i + 1, n: todo.length })
      await answer(todo[i], 'accept')
    }
    setAll(null)
    onChanged()
  }

  return (
    <>
      <Dialog.Title className="t-small font-normal text-ink-3">
        {names.length === 1 ? `Your routine ‘${names[0]}’ prepared ${items.length === 1 ? 'this' : 'these'}` : 'Your routines prepared these'}. Nothing goes out until you press {allEmail ? 'Send' : 'it'}.
      </Dialog.Title>
      <p className="t-small mt-1 text-ink-3">
        {names.length === 1 && <><button className="uline" onClick={() => show(items[0].routine_id)}>How it decided</button> · </>}
        The routine sends its text as written; it cannot be edited here.
        {' '}To change it, change the routine.
      </p>

      <ol className="mt-8 space-y-12">
        {items.map((p, i) => {
          const m = mails[i]
          const s = st[p.id]
          return (
            <li key={p.id} aria-live="polite">
              <p className="t-small text-ink-3">
                {items.length > 1 && `${i + 1} of ${items.length} · `}{m.what}
                {names.length > 1 && <> · <span className="text-routine">‘{p.routine_name}’</span> <button className="uline" onClick={() => show(p.routine_id)}>how it decided</button></>}
              </p>
              {m.email ? (
                <dl className="t-small mt-3 grid grid-cols-[64px_minmax(0,1fr)] gap-y-2 text-ink-2">
                  <dt className="text-ink-3">To</dt>
                  <dd className="min-w-0 break-words text-ink">{m.toName ? `${m.toName} · ` : ''}<span className="text-ink-2">{m.to}</span></dd>
                  {m.subject && <><dt className="text-ink-3">Subject</dt><dd className="min-w-0 break-words text-ink">{m.subject}</dd></>}
                </dl>
              ) : m.card ? (
                <p className="t-small mt-3 text-ink-2">About: <span className="text-ink">{headlineOf(m.card, ctx as State)}</span></p>
              ) : null}
              {m.text && <MailText text={m.text} muted={s?.s === 'skipped'} />}
              <div className="t-body mt-4 flex flex-wrap items-center gap-6">
                {s?.s === 'sent' ? (
                  <span className="dot-routine t-small text-routine">{m.email ? 'Sent' : 'Done'} at {s.at}</span>
                ) : s?.s === 'skipped' ? (
                  <span className="t-small text-ink-3">Skipped. Nothing was sent.</span>
                ) : (
                  <>
                    <button disabled={(!!s && s.s === 'busy') || !!all} onClick={() => void answer(p, 'accept').then((ok) => ok && onChanged())} className="cursor-pointer font-medium text-accent hover:underline disabled:opacity-60">
                      {s?.s === 'busy' ? 'Sending…' : verb(m)}
                    </button>
                    <button disabled={(!!s && s.s === 'busy') || !!all} onClick={() => void answer(p, 'skip')} className="cursor-pointer text-ink-2 hover:text-ink disabled:opacity-60">Skip</button>
                  </>
                )}
              </div>
              {s?.s === 'error' && <p className="t-small mt-2 text-late" role="alert">{s.why}</p>}
            </li>
          )
        })}
      </ol>

      <div className="sticky -bottom-8 -mx-6 mt-10 flex items-center gap-6 bg-ground px-6 pb-8 pt-4 shadow-[0_-12px_16px_-12px_rgba(0,0,0,.08)] md:-mx-8 md:px-8">
        {finished ? (
          <button onClick={onClose} autoFocus className="t-body inline-flex h-12 cursor-pointer items-center justify-center gap-2 rounded-xl bg-soft px-7 font-medium text-ink hover:bg-hair focus-visible:outline-2 focus-visible:outline-accent">
            <Check size={18} strokeWidth={1.75} /> Done
          </button>
        ) : (
          <>
            <button
              onClick={sendAll}
              disabled={!!all}
              className="t-body inline-flex h-12 cursor-pointer items-center justify-center rounded-xl bg-accent px-7 font-medium text-white shadow-[0_1px_2px_rgba(35,72,208,.25)] hover:bg-[#1d3db5] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-60"
            >
              {all ? `Sending ${all.i} of ${all.n}…` : open.length === 1 ? (allEmail ? 'Send' : verb(mails[items.indexOf(open[0])])) : `${allEmail ? 'Send' : 'Do'} all ${open.length}`}
            </button>
            <Dialog.Close className="t-body cursor-pointer text-ink-2 hover:text-ink">Cancel</Dialog.Close>
          </>
        )}
      </div>
    </>
  )
}

/** The email text, folded after a few lines when several are reviewed together. */
function MailText({ text, muted }: { text: string; muted?: boolean }) {
  const [all, setAll] = useState(false)
  const long = text.split('\n').length > 7 || text.length > 420
  return (
    <div className="mt-4 rounded-xl bg-soft p-4">
      <p className={cn('t-body whitespace-pre-wrap break-words leading-relaxed', muted ? 'text-ink-3' : 'text-ink', long && !all && 'line-clamp-5')}>{text}</p>
      {long && <button className="t-small uline mt-2" onClick={() => setAll(!all)}>{all ? 'Show less' : 'Read all of it'}</button>}
    </div>
  )
}
