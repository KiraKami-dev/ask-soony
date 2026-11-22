import { Menu } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { cn } from '@/lib/utils'

export type Page = 'today' | 'plan' | 'suppliers'

export function Mark({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M2.5 8V2.5H8M16 2.5h5.5V8M21.5 16v5.5H16M8 21.5H2.5V16" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="square" />
      <rect x="6.5" y="9.5" width="11" height="5" rx="1" fill="var(--color-mark)" />
    </svg>
  )
}

interface Props {
  page: Page | null
  go: (path: string) => void
  onRoutines: () => void
  /** "Trucks leave in …" (bold) and what follows. */
  status: ReactNode
  bell: ReactNode
  phone: boolean
  routinesBadge?: number
}

const LINKS: { page: Page; label: string; path: string }[] = [
  { page: 'today', label: 'Today', path: '/' },
  { page: 'plan', label: 'Plan', path: '/plan' },
]

// One quiet line: the mark, where the launch stands, and where to go.
export function TopBar({ page, go, onRoutines, status, bell, phone, routinesBadge = 0 }: Props) {
  const link = (on: boolean, label: ReactNode, onClick: () => void, key: string) => (
    <button
      key={key}
      onClick={onClick}
      aria-current={on ? 'page' : undefined}
      className={cn(
        'cursor-pointer rounded-md px-1 py-1 focus-visible:outline-2 focus-visible:outline-accent',
        on ? 'text-ink' : 'text-ink-3 hover:text-ink',
      )}
    >
      {label}
    </button>
  )
  return (
    <header className="relative z-20 flex h-[72px] shrink-0 items-center justify-center px-4 text-[length:var(--small)] text-ink-2 lg:px-8">
      <a
        href="/"
        onClick={(e) => { e.preventDefault(); go('/') }}
        className="absolute left-4 top-1/2 flex -translate-y-1/2 items-center gap-2.5 text-ink lg:left-8"
        aria-label="Ask Soony, today"
      >
        <Mark />
        {!phone && <span className="font-medium">Ask Soony</span>}
      </a>
      <p className={cn('min-w-0 truncate text-center', phone && 'max-w-[calc(100%-88px)]')} aria-live="polite">{status}</p>
      <div className="absolute right-3 top-1/2 flex -translate-y-1/2 items-center gap-3 lg:right-6">
        {phone ? (
          <PhoneMenu page={page} go={go} onRoutines={onRoutines} bell={bell} badge={routinesBadge} />
        ) : (
          <nav className="flex items-center gap-4" aria-label="Views">
            {LINKS.map((l) => link(page === l.page, l.label, () => go(l.path), l.page))}
            {link(false, <>Routines{routinesBadge > 0 && <span className="ml-1 text-routine">· {routinesBadge}</span>}</>, onRoutines, 'routines')}
            {link(page === 'suppliers', 'Suppliers', () => go('/suppliers'), 'suppliers')}
            {bell}
          </nav>
        )}
      </div>
    </header>
  )
}

function PhoneMenu({ page, go, onRoutines, bell, badge }: { page: Page | null; go: (p: string) => void; onRoutines: () => void; bell: ReactNode; badge: number }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const off = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', off)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', off); document.removeEventListener('keydown', esc) }
  }, [open])
  const item = (on: boolean, label: ReactNode, onClick: () => void) => (
    <button
      onClick={() => { onClick(); setOpen(false) }}
      aria-current={on ? 'page' : undefined}
      className={cn('block w-full cursor-pointer rounded-lg px-3 py-2.5 text-left text-[17px] hover:bg-soft', on ? 'text-ink' : 'text-ink-2')}
    >
      {label}
    </button>
  )
  return (
    <div ref={ref} className="relative flex items-center">
      <button
        onClick={() => setOpen(!open)}
        aria-label="Menu"
        aria-expanded={open}
        className="relative grid h-10 w-10 cursor-pointer place-items-center rounded-full text-ink-2 hover:bg-soft focus-visible:outline-2 focus-visible:outline-accent"
      >
        <Menu size={20} />
        {badge > 0 && <span className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-routine" />}
      </button>
      <div className={cn('absolute right-0 top-12 z-30 w-56 rounded-2xl bg-ground p-1.5 shadow-[0_12px_40px_-12px_rgba(0,0,0,.28)]', !open && 'hidden')}>
        {item(page === 'today', 'Today', () => go('/'))}
        {item(page === 'plan', 'Plan', () => go('/plan'))}
        {item(false, <>Routines{badge > 0 && <span className="ml-1.5 text-routine">· {badge}</span>}</>, onRoutines)}
        {item(page === 'suppliers', 'Suppliers', () => go('/suppliers'))}
        <div className="flex items-center justify-between px-3 pb-1 pt-2 text-[13px] text-ink-3">
          Launch alerts {bell}
        </div>
      </div>
    </div>
  )
}
