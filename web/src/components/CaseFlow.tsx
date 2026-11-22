import { FileInput, Inbox, ScanText, Scale, Send, ShieldCheck, TrendingDown } from 'lucide-react'
import { Fragment, useState, type ReactNode } from 'react'
import { fmtNum, tidy, type Card, type Evidence, type State } from '@/lib/api'
import { languageOf, sourceName, splitValue, when } from '@/lib/decisions'
import { firstNameOf } from '@/lib/role'
import { plain } from '@/lib/today'
import { cn } from '@/lib/utils'
import { Link, NODE, NodeHead } from './routines/FlowGraph'

// "How we got here": the agents' steps behind one case as a small flow, the same canvas and node
// look as a routine's diagram. Every node is read from the case itself (its trail, evidence,
// impact, approval rule, options and status); a step the data does not have is left out, never
// made up. Done steps are quiet, the step the case waits at is marked, what comes after is
// dashed. One column, top to bottom; folded to one line on a phone.

type Tone = 'done' | 'now' | 'next'
interface Step { key: string; icon: ReactNode; kicker: string; title: string; text?: string; at?: string | null; tone: Tone; late?: boolean }

const SYS = /system|sap|erp|plan|book stock/i
const short = (s: string, n = 90) => (s.length > n ? `${s.slice(0, n - 1).replace(/\s+\S*$/, '')}…` : s)
const big = (v?: string | number) => splitValue(v).big

function stepsOf(card: Card, state: State, nowMs: number): Step[] {
  const trail = (re: RegExp) => card.trail.find((t) => re.test(t.agent ?? ''))
  const sys: Evidence | undefined = card.evidence.find((e) => SYS.test(e.source ?? '') && e.value != null)
  const paper: Evidence | undefined = card.evidence.find((e) => e !== sys && !SYS.test(e.source ?? '') && e.value != null)
  const out: Step[] = []
  const icon = (I: typeof Inbox) => <I size={14} strokeWidth={1.75} />

  const found = trail(/watch/i)
  if (found || paper?.doc) {
    const file = (paper?.doc ?? '').split('/').pop()
    out.push({ key: 'source', icon: icon(FileInput), kicker: 'Portal', title: 'New document', text: file || short(tidy(found?.text)), at: found?.at, tone: 'done' })
  }
  const read = trail(/paper|read|recogn|vision/i)
  if (read || paper) {
    const lang = languageOf(paper?.source, paper?.language)
    out.push({
      key: 'read', icon: icon(ScanText), kicker: 'Reader', title: lang && lang !== 'English' ? `Read it (${lang})` : 'Read it',
      text: paper ? short(tidy(String(paper.value)), 80) : short(tidy(read?.text)), at: read?.at, tone: 'done',
    })
  }
  if (sys && paper) {
    out.push({
      key: 'verify', icon: icon(Scale), kicker: 'Verifier', title: 'System and paper disagree',
      text: `${sourceName(sys.source)} ${big(sys.value)} · paper ${big(paper.value)}`, tone: 'done', late: true,
    })
  }
  const impact = trail(/impact|predict/i)
  if (card.number != null || impact) {
    const ed = state.strip.find((s) => s.edition === card.edition)?.label
    const dry = card.runs_dry ? `${ed ? `${ed} ` : ''}runs dry ${when(card.runs_dry, nowMs)}` : ''
    out.push({
      key: 'impact', icon: icon(TrendingDown), kicker: 'Prediction',
      title: card.number != null ? `${fmtNum(card.number)} ${card.number_label || 'at risk'}` : 'Impact worked out',
      text: dry || short(tidy(impact?.text)), at: impact?.at, tone: 'done', late: card.level === 'red',
    })
  }
  const signer = firstNameOf(card.signoff)
  out.push({
    key: 'rule', icon: icon(ShieldCheck), kicker: 'Approval rule',
    title: signer ? `${signer} signs` : card.level === 'red' ? 'A person decides' : 'The buyer decides',
    text: signer ? 'It costs money: above the buyer’s limit in the policy' : card.level === 'red' ? 'Launch at risk: never automatic' : 'Within the buyer’s limit',
    tone: 'done',
  })

  const decided = !['open', 'escalated'].includes(card.status)
  const holder = card.status === 'escalated' ? firstNameOf(card.approver) || 'the approver' : null
  out.push({
    key: 'decide', icon: icon(Inbox), kicker: 'Decision',
    title: decided ? 'Decided' : holder ? `Waiting for ${holder}` : !card.deadline ? 'Needs a decision' : new Date(card.deadline).getTime() <= nowMs ? 'Decide now' : `Decide by ${when(card.deadline, nowMs)}`,
    text: decided ? undefined : holder ? 'Passed on with the recommendation' : 'Here, now',
    at: decided ? card.ui?.at : null, tone: decided ? 'done' : 'now',
  })

  const rec = card.options.find((o) => o.recommended) ?? card.options[0]
  if (rec || card.commitment_line) {
    const sent = ['sent', 'approved'].includes(card.status)
    out.push({
      key: 'act', icon: icon(Send), kicker: sent ? 'Done' : 'Then',
      title: sent ? (card.status === 'sent' ? 'Sent' : 'Carried out') : short(plain(rec?.action, card.part_name), 60) || 'Act on it',
      text: sent ? short(tidy(card.commitment_line ?? card.ui?.note ?? '')) || undefined : rec?.cost ? tidy(rec.cost) : undefined,
      tone: sent ? 'done' : 'next',
    })
  }
  return out
}

/** One step, in the same box as a routine's diagram: icon tile, kicker, title, detail. */
function Node({ s, nowMs }: { s: Step; nowMs: number }) {
  return (
    <div
      className={cn(
        'min-w-0 pb-3',
        s.tone === 'next' ? 'rounded-xl border border-dashed border-ink-3/40 bg-ground/70' : NODE,
        s.tone === 'now' && 'border-accent/45 shadow-[0_0_0_3px_color-mix(in_srgb,var(--color-accent)_9%,transparent)]',
      )}
    >
      <NodeHead
        icon={s.icon}
        kicker={s.kicker}
        title={s.title}
        accent={s.tone === 'now'}
        right={s.at ? <span className="t-small shrink-0 tabular-nums text-ink-3">{when(s.at, nowMs)}</span> : undefined}
      />
      {s.text && <p className={cn('t-small mt-1.5 line-clamp-2 pl-[52px] pr-3.5', s.late ? 'text-late' : 'text-ink-3')}>{s.text}</p>}
    </div>
  )
}

const CANVAS = {
  backgroundImage: 'radial-gradient(circle, color-mix(in srgb, var(--color-ink-3) 22%, transparent) 1px, transparent 1.2px)',
  backgroundSize: '18px 18px',
}

export function CaseFlow({ card, state, nowMs, phone, className }: { card: Card; state: State; nowMs: number; phone?: boolean; className?: string }) {
  const steps = stepsOf(card, state, nowMs)
  // Phone: folded to one line so the decision stays in reach; one tap shows the steps.
  const [open, setOpen] = useState(!phone)
  if (steps.length < 3) return null
  if (!open) {
    return (
      <section aria-label="How we got here" className={className}>
        <button onClick={() => setOpen(true)} className="t-small cursor-pointer text-ink-3 hover:text-ink">
          <span className="border-b border-hair">How we got here · {steps.length} steps</span>
        </button>
      </section>
    )
  }

  // One column, top to bottom, in the order the agents worked: no turns to follow.
  const body = (
    <ol className="grid">
      {steps.map((s, i) => (
        <Fragment key={s.key}>
          {i > 0 && <li aria-hidden="true"><Link dashed={s.tone === 'next'} /></li>}
          <li><Node s={s} nowMs={nowMs} /></li>
        </Fragment>
      ))}
    </ol>
  )

  return (
    <section aria-label="How we got here" className={className}>
      <h2 className="t-small text-ink-3">How we got here</h2>
      <div className="mt-3 rounded-2xl border border-hair p-3 sm:p-4" style={CANVAS}>{body}</div>
    </section>
  )
}
