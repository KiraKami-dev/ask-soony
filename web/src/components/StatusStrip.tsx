import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { COLORS, ago, fmtDay, fmtNum, fmtTime, partName, slackText, type State, type StripItem } from '@/lib/api'

export function StatusStrip({ state, nowMs, actions }: { state: State; nowMs: number; actions?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-3 border-b border-line bg-white px-4 py-3 lg:flex-nowrap lg:px-6">
      <div className="flex items-center gap-2.5">
        <div className="grid h-8 w-8 place-items-center rounded-lg bg-ink text-sm font-extrabold text-white">S</div>
        <div className="leading-tight">
          <div className="text-[15px] font-bold">Ask Soony</div>
          <div className="flex items-center gap-1.5 whitespace-nowrap text-xs text-ink-3" title="Agents check the customer portal for new files">
            <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-ok" />
            Watching · checked {ago(state.last_check_at, nowMs)}
          </div>
        </div>
      </div>
      <div className="order-last grid w-full grid-cols-2 gap-2 lg:order-none lg:flex lg:w-auto lg:min-w-0 lg:flex-1 lg:gap-3">
        {state.strip.map((s) => <Edition key={s.edition} s={s} state={state} />)}
      </div>
      <div className="ml-auto flex items-center gap-3 lg:ml-0">
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
    </header>
  )
}

function Edition({ s, state }: { s: StripItem; state: State }) {
  const color = COLORS[s.level]
  const onTrack = s.level === 'blue'
  const bf = s.breaks_first
  const card = bf ? state.cards.find((c) => c.id === bf.card) : undefined
  // A readable name for what breaks first; never a bare part code.
  const what = bf ? (partName(bf.text, state.cards)?.split(' – ')[0] ?? card?.headline) : undefined
  const jump = () => document.getElementById(`card-${bf?.card}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })

  return (
    <div className={cn('flex min-w-0 flex-col gap-1.5 rounded-xl border border-line px-3 py-2 lg:flex-row lg:items-center lg:gap-5 lg:px-4', bf ? 'lg:flex-[3]' : 'lg:flex-[2]')}>
      <div className="min-w-0 lg:shrink-0">
        <div className="text-sm font-semibold text-ink-2">{s.label}</div>
        <div className="mt-0.5 flex items-center gap-1.5 text-[15px] font-bold leading-tight" style={{ color }}>
          <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: color }} />
          <span className="truncate lg:whitespace-nowrap">{s.status}</span>
        </div>
      </div>
      <div className="flex items-baseline gap-1.5 lg:block">
        <div className="text-xl font-extrabold leading-none tabular-nums lg:text-[28px]">{fmtNum(s.copies_at_risk)}</div>
        <div className="text-xs font-medium text-ink-3 lg:mt-1">at risk</div>
      </div>
      <div className="hidden lg:block">
        <div className="text-[28px] font-extrabold leading-none tabular-nums">{slackText(s.slack_hours, onTrack, s.copies_at_risk)}</div>
        <div className="mt-1 text-xs font-medium text-ink-3">slack</div>
      </div>
      <div className="hidden min-w-0 flex-1 xl:block">
        <div className="text-xs font-medium text-ink-3">Breaks first</div>
        {bf && what ? (
          <button onClick={jump} className="block w-full cursor-pointer truncate text-left text-sm font-semibold hover:underline" title={what}>
            {what}
          </button>
        ) : (
          <div className="text-sm font-semibold">{bf ? 'See the open decision' : 'Nothing at risk'}</div>
        )}
        {bf?.stops_at ? (
          <div className="truncate text-sm text-ink-2">
            Line stops <span className="font-bold" style={{ color }}>{fmtDay(bf.stops_at)} {fmtTime(bf.stops_at)}</span>
          </div>
        ) : bf?.deadline ? (
          <div className="truncate text-sm text-ink-2">Decide by {fmtDay(bf.deadline)} {fmtTime(bf.deadline)}</div>
        ) : null}
      </div>
    </div>
  )
}
