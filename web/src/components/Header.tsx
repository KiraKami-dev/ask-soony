import { Menu } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ago } from '@/lib/api'
import type { State } from '@/lib/api'
import { launchAt, span, verdict } from '@/lib/decisions'
import { cn } from '@/lib/utils'

export type View = 'launch' | 'suppliers'

interface Props {
  state: State
  nowMs: number
  view: View
  onView: (v: View) => void
  /** The Routines button (opens its sheet), shown as a tab. */
  routines: ReactNode
  bell: ReactNode
  compact?: boolean
}

// One row: who is watching, one sentence on the launch, the time left and where to go.
export function Header({ state, nowMs, view, onView, routines, bell, compact }: Props) {
  const bits = verdict(state, nowMs)
  return (
    <header className="flex h-16 shrink-0 items-center gap-3 border-b border-line bg-white px-4 lg:gap-6 lg:px-6">
      <Brand state={state} nowMs={nowMs} compact={compact} />
      {!compact && (
        <p className="min-w-0 flex-1 text-balance text-center text-[16px] font-semibold leading-snug text-ink" aria-live="polite">
          {bits.map((b, i) => (
            <span key={i} className={b.tone === 'stop' ? 'text-stop' : b.tone === 'warn' ? 'text-warn' : undefined}>{b.text}</span>
          ))}
        </p>
      )}
      <div className={cn('flex shrink-0 items-center gap-3', compact && 'ml-auto')}>
        <Countdown state={state} nowMs={nowMs} />
        {compact ? (
          <PhoneMenu view={view} onView={onView} routines={routines} />
        ) : (
          <nav className="flex items-center gap-0.5 rounded-lg bg-panel p-0.5 ring-1 ring-line" aria-label="Views">
            <Tab on={view === 'launch'} onClick={() => onView('launch')}>Launch</Tab>
            <span className="tab-routines contents">{routines}</span>
            <Tab on={view === 'suppliers'} onClick={() => onView('suppliers')}>Suppliers</Tab>
          </nav>
        )}
        {bell}
      </div>
    </header>
  )
}

/** The phone header's sentence, under the header row. */
export function VerdictLine({ state, nowMs }: { state: State; nowMs: number }) {
  return (
    <p className="border-b border-line bg-white px-4 py-2.5 text-[15px] font-semibold leading-snug">
      {verdict(state, nowMs).map((b, i) => (
        <span key={i} className={b.tone === 'stop' ? 'text-stop' : b.tone === 'warn' ? 'text-warn' : undefined}>{b.text}</span>
      ))}
    </p>
  )
}

function Brand({ state, nowMs, compact }: { state: State; nowMs: number; compact?: boolean }) {
  return (
    <div className="flex shrink-0 items-center gap-2.5">
      <div className="grid h-8 w-8 place-items-center rounded-lg bg-ink text-sm font-extrabold text-white">S</div>
      <div className="leading-tight">
        <div className="text-[15px] font-bold">Ask Soony</div>
        <div className={cn('flex items-center gap-1.5 whitespace-nowrap text-xs text-ink-3', compact && 'hidden')} title="Agents check the customer portal for new files">
          <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-ok" />
          Watching · checked {ago(state.last_check_at, nowMs)}
        </div>
      </div>
    </div>
  )
}

function Countdown({ state, nowMs }: { state: State; nowMs: number }) {
  const at = launchAt(state)
  if (!at) return null
  const left = new Date(at).getTime() - nowMs
  return (
    <div className="whitespace-nowrap text-right leading-tight" title="Trucks leave Hamburg for retail">
      <div className="text-xs font-medium text-ink-3">{left > 0 ? 'Trucks in' : 'Trucks'}</div>
      <div className="text-[16px] font-bold tabular-nums">{left > 0 ? span(left) : 'have left'}</div>
    </div>
  )
}

function Tab({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      onClick={onClick}
      aria-current={on ? 'page' : undefined}
      className={cn(
        'h-8 cursor-pointer rounded-md px-3 text-sm font-semibold',
        on ? 'bg-white text-ink shadow-sm ring-1 ring-line' : 'text-ink-2 hover:text-ink',
      )}
    >
      {children}
    </button>
  )
}

function PhoneMenu({ view, onView, routines }: { view: View; onView: (v: View) => void; routines: ReactNode }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const off = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', off)
    return () => document.removeEventListener('mousedown', off)
  }, [open])
  const item = (v: View, label: string) => (
    <button
      onClick={() => { onView(v); setOpen(false) }}
      className={cn('block w-full cursor-pointer rounded-lg px-3 py-2.5 text-left text-[15px] font-semibold hover:bg-panel', view === v ? 'text-ink' : 'text-ink-2')}
    >
      {label}
    </button>
  )
  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        aria-label="Menu"
        aria-expanded={open}
        className="grid h-9 w-9 cursor-pointer place-items-center rounded-full border border-line bg-white text-ink-2 hover:bg-panel"
      >
        <Menu size={18} />
      </button>
      {/* Kept mounted so the Routines sheet survives the menu closing. */}
      <div className={cn('absolute right-0 top-11 z-30 w-52 rounded-xl border border-line bg-white p-1 shadow-lg', !open && 'hidden')}>
        {item('launch', 'Launch')}
        <div className="tab-routines-menu px-1 py-1" onClick={() => setOpen(false)}>{routines}</div>
        {item('suppliers', 'Suppliers')}
      </div>
    </div>
  )
}
