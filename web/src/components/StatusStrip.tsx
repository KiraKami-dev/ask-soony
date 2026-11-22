import type { ReactNode } from 'react'
import { COLORS, ago, fmtDay, fmtNum, fmtTime, type State } from '@/lib/api'

export function StatusStrip({ state, nowMs, actions }: { state: State; nowMs: number; actions?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-stretch gap-x-2 border-b border-line bg-white px-5 py-3 lg:px-6">
      <div className="mr-4 flex items-center gap-2.5 self-center">
        <div className="grid h-8 w-8 place-items-center rounded-lg bg-ink text-sm font-extrabold text-white">S</div>
        <div className="leading-tight">
          <div className="text-[15px] font-bold">Ask Soony</div>
          <div className="text-xs text-ink-3">Decision inbox</div>
        </div>
      </div>
      <div className="flex flex-1 flex-wrap gap-3">
        {state.strip.map((s) => (
          <div key={s.edition} className="flex min-w-[300px] flex-1 items-center gap-5 rounded-xl border border-line px-4 py-2">
            <div className="min-w-[110px]">
              <div className="text-sm font-semibold text-ink-2">{s.label}</div>
              <div className="mt-0.5 flex items-center gap-1.5 text-[15px] font-bold" style={{ color: COLORS[s.level] }}>
                <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: COLORS[s.level] }} />
                {s.status}
              </div>
            </div>
            <Stat value={fmtNum(s.copies_at_risk)} label="copies at risk" />
            <Stat value={s.slack_hours == null ? '–' : `${s.slack_hours} h`} label="slack" />
            <div className="min-w-0 flex-1">
              <div className="text-xs font-medium text-ink-3">Breaks first</div>
              <div className="truncate text-sm font-semibold" title={s.breaks_first?.text}>
                {s.breaks_first ? s.breaks_first.text : 'Nothing at risk'}
              </div>
              {s.breaks_first?.stops_at ? (
                <div className="text-sm text-ink-2">
                  Line stops <span className="font-bold" style={{ color: COLORS[s.level] }}>{fmtDay(s.breaks_first.stops_at)} {fmtTime(s.breaks_first.stops_at)}</span>
                  {s.breaks_first.deadline && <> · decide by {fmtDay(s.breaks_first.deadline)} {fmtTime(s.breaks_first.deadline)}</>}
                </div>
              ) : s.breaks_first?.deadline ? (
                <div className="text-sm text-ink-2">Decide by {fmtDay(s.breaks_first.deadline)} {fmtTime(s.breaks_first.deadline)}</div>
              ) : null}
            </div>
          </div>
        ))}
      </div>
      <div className="ml-auto self-center pl-3 text-right text-xs text-ink-3">
        <div>Last portal drop</div>
        <div className="text-sm font-semibold text-ink-2">{ago(state.last_drop_at, nowMs)}</div>
      </div>
      {actions && <div className="flex items-center gap-2 self-center pl-3">{actions}</div>}
    </header>
  )
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div>
      <div className="text-[28px] font-extrabold leading-none tabular-nums">{value}</div>
      <div className="mt-1 text-xs font-medium text-ink-3">{label}</div>
    </div>
  )
}
