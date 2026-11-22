import { fmtNum, type Card, type PricedDecision, type State } from '@/lib/api'
import { when } from '@/lib/decisions'
import { firstNameOf, type Role } from '@/lib/role'
import { plain } from '@/lib/today'
import { cn } from '@/lib/utils'

// Launch in the words of the person who signs: what is at risk, which decisions cost money and
// who holds them, and how much waits on others. One calm block under the verdict; only copies that
// miss the trucks are red. Amounts only as the options state them; otherwise "price pending".

interface Props {
  state: State
  nowMs: number
  phone: boolean
  role: Role | null
  onOpen: (cardId: string) => void
}

const money = (v: number, cur?: string | null) => `${cur ?? 'EUR'} ${fmtNum(v)}`

/** 'Ask Karton Nowak for a direct van, a split load or an earlier truck' -> 'Ask Karton Nowak for a direct van'. */
function shortLabel(d: PricedDecision): string {
  const t = plain(d.label, d.part_name).split(/,\s|;\s|\s+–\s/)[0].replace(/[.:]$/, '')
  const part = d.part_name?.replace(/\s*\(.*\)$/, '')
  return part && !t.toLowerCase().includes(part.toLowerCase()) ? `${t} (${part.toLowerCase()})` : t
}

/** "3 h", "40 min" */
function age(iso: string | null | undefined, nowMs: number): string | null {
  if (!iso) return null
  const min = Math.max(0, Math.round((nowMs - new Date(iso).getTime()) / 60000))
  if (!Number.isFinite(min)) return null
  return min < 60 ? `${min} min` : `${Math.floor(min / 60)} h`
}

export function LaunchBrief({ state, nowMs, phone, role, onOpen }: Props) {
  const m = state.launch?.money
  const approver = m?.approver ?? null
  const viewer = m?.viewer ?? state.viewer
  const me = role === 'approver' ? approver : viewer
  const isMe = (name?: string | null) => !!name && !!me && firstNameOf(name) === firstNameOf(me)

  const holderText = (d: PricedDecision) => {
    const since = d.since ? ` since ${when(d.since, nowMs)}` : ''
    if (d.holder_role === 'supplier') {
      const who = (d.holder ?? '').split(/\s/)[0] || 'the supplier'
      return `sent ${when(d.since, nowMs)}, waiting for ${who}`
    }
    return isMe(d.holder) ? `with you${since}` : `with ${firstNameOf(d.holder) || 'someone'}${since}`
  }

  // Waiting on others: sent and not answered, and decisions passed on to someone who is not me.
  const site = state.launch?.floor?.site ?? 'the site'
  const waiting = state.cards
    .filter((c) =>
      (c.status === 'sent' && !c.ui?.reply) ||
      ((c.status === 'escalated' || c.routed_to_other) && c.status !== 'sent' && !isMe(c.signoff ?? c.approver)),
    )
    .map((c) => ({ c, at: c.ui?.at ?? null }))
    .sort((a, b) => (a.at ? new Date(a.at).getTime() : Infinity) - (b.at ? new Date(b.at).getTime() : Infinity))
  const oldest = waiting[0]
  const whoOf = (c: Card) =>
    c.status === 'sent' ? (c.site_question ? site : c.supplier_name ?? 'a supplier') : firstNameOf(c.signoff ?? c.approver) || 'someone'

  const decisions = m?.pending_decisions ?? []
  const committed = m?.committed_today ?? []

  return (
    <section
      aria-label="At a glance"
      className={cn('mt-10 grid border-t border-hair pt-5', phone ? 'gap-y-7' : 'grid-cols-[minmax(0,0.7fr)_minmax(0,1.8fr)_minmax(0,0.8fr)] gap-x-12')}
    >
      <div>
        <h2 className="t-small text-ink-3">Copies at risk</h2>
        <ul className="t-small mt-2 space-y-1">
          {state.strip.map((s) => {
            const n = s.copies_at_risk ?? 0
            const misses = n > 0 && !!s.stops_at
            return (
              <li key={s.edition} className="flex items-baseline justify-between gap-4">
                <span className="text-ink-2">{s.label}</span>
                <span className={cn('tabular-nums', misses ? 'text-late' : n > 0 ? 'text-ink' : 'text-ink-3')}>{n > 0 ? fmtNum(n) : 'none'}</span>
              </li>
            )
          })}
        </ul>
      </div>

      <div className="min-w-0">
        <h2 className="t-small text-ink-3">Decisions with a price</h2>
        {!m ? (
          <p className="t-small mt-2 text-ink-3">Not known yet.</p>
        ) : decisions.length === 0 ? (
          <p className="t-small mt-2 text-ink-3">None in the air.</p>
        ) : (
          <ul className="t-small mt-2 space-y-1">
            {decisions.map((d) => (
              <li key={d.card} className="min-w-0">
                <a
                  href={`/d/${encodeURIComponent(d.card)}`}
                  onClick={(e) => { e.preventDefault(); onOpen(d.card) }}
                  className="text-ink hover:underline"
                >
                  {shortLabel(d)}
                </a>
                <span className="text-ink-3">
                  {' · '}{d.cost_value != null ? `about ${money(d.cost_value, d.currency)}` : 'price pending'}
                  {' · '}{holderText(d)}
                </span>
              </li>
            ))}
          </ul>
        )}
        {committed.length > 0 && (
          <p className="t-small mt-3 text-ink-2">
            About {committed.map((c) => money(c.value, c.currency)).join(' and ')} committed today
            <span className="text-ink-3">
              {' '}({m!.committed_items.length} {m!.committed_items.length === 1 ? 'order' : 'orders'})
            </span>
          </p>
        )}
      </div>

      <div>
        <h2 className="t-small text-ink-3">Waiting on others</h2>
        {waiting.length === 0 ? (
          <p className="t-small mt-2 text-ink-3">Nobody.</p>
        ) : (
          <p className="t-small mt-2 text-ink">
            {waiting.length} {waiting.length === 1 ? 'answer' : 'answers'}
            {oldest?.at && (
              <span className="block text-ink-3">oldest {age(oldest.at, nowMs)}, {whoOf(oldest.c)}</span>
            )}
          </p>
        )}
      </div>
    </section>
  )
}
