import { Check, Menu, UserRound } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { roleTitle, type Role } from '@/lib/role'
import { cn } from '@/lib/utils'

export type Page = 'today' | 'launch' | 'suppliers' | 'routines' | 'history'

export function Mark({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M2.5 8V2.5H8M16 2.5h5.5V8M21.5 16v5.5H16M8 21.5H2.5V16" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="square" />
      <rect x="6.5" y="9.5" width="11" height="5" rx="1" fill="currentColor" />
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
  /** Who uses this device (remembered); null: nobody chose, "/" is Today. */
  role?: Role | null
  onRole?: (r: Role | null) => void
  people?: { buyer?: string | null; approver?: string | null }
  /** The agents' last look at the portal: a quiet heartbeat by the mark. */
  checkedAt?: string | null
  nowMs?: number
}

interface RoleProps { role: Role | null; onRole: (r: Role | null) => void; people: { buyer?: string | null; approver?: string | null } }

/** The two roles this device can be, by title: "Head of Procurement · what needs a signature". */
function roleChoices({ people }: Pick<RoleProps, 'people'>): { role: Role; label: string; lands: string }[] {
  const out: { role: Role; label: string; lands: string }[] = []
  if (people.buyer) out.push({ role: 'buyer', label: roleTitle('buyer', people.buyer), lands: 'every case' })
  if (people.approver) out.push({ role: 'approver', label: roleTitle('approver', people.approver), lands: 'what needs a signature' })
  return out
}

type Link = { page: Page; label: string; path: string }
/**
 * The buyer works the whole inbox and builds the routines; the person who signs gets the launch,
 * the cases that reach her, and the record of what was decided. Nobody chose: the buyer's set.
 */
function linksFor(role: Role | null): Link[] {
  const history: Link = { page: 'history', label: 'History', path: '/history' }
  const suppliers: Link = { page: 'suppliers', label: 'Suppliers', path: '/suppliers' }
  if (role === 'approver') {
    return [{ page: 'launch', label: 'Launch', path: '/launch' }, { page: 'today', label: 'Inbox', path: '/' }, history, suppliers]
  }
  return [
    { page: 'today', label: 'Today', path: '/' },
    { page: 'launch', label: 'Launch', path: '/launch' },
    { page: 'routines', label: 'Routines', path: '/routines' },
    suppliers,
    history,
  ]
}

// One quiet line: the mark, where the launch stands, and where to go.
export function TopBar({ page, go, status, bell, phone, routinesBadge = 0, role = null, onRole, people = {}, checkedAt = null, nowMs = Date.now() }: Props) {
  const who = onRole ? { role, onRole, people } : null
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
        aria-label="Lighthouse, today"
      >
        <Mark />
        {!phone && <span className="font-medium">Lighthouse</span>}
      </a>
      {!phone && checkedAt && <Heartbeat at={checkedAt} nowMs={nowMs} />}
      <p className={cn('min-w-0 truncate text-center', phone && 'max-w-[calc(100%-88px)]')} aria-live="polite">{status}</p>
      <div className="absolute right-3 top-1/2 flex -translate-y-1/2 items-center gap-3 lg:right-6">
        {phone ? (
          <PhoneMenu page={page} go={go} bell={bell} badge={role === 'approver' ? 0 : routinesBadge} who={who} links={linksFor(role)} />
        ) : (
          <nav className="flex items-center gap-4" aria-label="Views">
            {linksFor(role).map((l) =>
              link(
                page === l.page,
                l.page === 'routines' && routinesBadge > 0 ? <>Routines<span className="ml-1 text-routine">· {routinesBadge}</span></> : l.label,
                () => go(l.path),
                l.page,
              ),
            )}
            {bell}
            {who && roleChoices(who).length > 0 && <RoleMenu {...who} />}
          </nav>
        )}
      </div>
    </header>
  )
}

function PhoneMenu({ page, go, bell, badge, who, links }: { page: Page | null; go: (p: string) => void; bell: ReactNode; badge: number; who: RoleProps | null; links: Link[] }) {
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
        {links.map((l) => (
          <div key={l.page}>
            {item(page === l.page, l.page === 'routines' && badge > 0 ? <>Routines<span className="ml-1.5 text-routine">· {badge}</span></> : l.label, () => go(l.path))}
          </div>
        ))}
        <div className="flex items-center justify-between px-3 pb-1 pt-2 text-[13px] text-ink-3">
          Launch alerts {bell}
        </div>
        {who && roleChoices(who).length > 0 && (
          <div className="mt-1 border-t border-hair px-1 pt-1.5">
            <p className="px-2 pb-1 pt-1 text-[13px] text-ink-3">On this phone</p>
            {roleChoices(who).map((c) => (
              <button
                key={c.role}
                onClick={() => { who.onRole(who.role === c.role ? null : c.role); setOpen(false) }}
                aria-pressed={who.role === c.role}
                className="flex w-full cursor-pointer items-center justify-between rounded-lg px-2 py-2 text-left text-[15px] text-ink-2 hover:bg-soft"
              >
                <span>{c.label} <span className="text-ink-3">· {c.lands}</span></span>
                {who.role === c.role && <Check size={16} className="text-ink" />}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

/** Desktop: a quiet person button; picks who uses this device ("/" then opens their page). */
function RoleMenu({ role, onRole, people }: RoleProps) {
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
  const name = role ? roleTitle(role, role === 'approver' ? people.approver : people.buyer) : null
  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        aria-label={name ? `Using this as ${name}` : 'Who is using this?'}
        aria-expanded={open}
        className="flex h-8 cursor-pointer items-center gap-1.5 rounded-full px-2 text-ink-3 hover:bg-soft hover:text-ink focus-visible:outline-2 focus-visible:outline-accent"
      >
        <UserRound size={16} />
        {name && <span>{name}</span>}
      </button>
      {open && (
        <div className="absolute right-0 top-10 z-30 w-64 rounded-2xl bg-ground p-1.5 shadow-[0_12px_40px_-12px_rgba(0,0,0,.28)]">
          <p className="px-3 pb-1 pt-2 text-[13px] text-ink-3">On this device</p>
          {roleChoices({ people }).map((c) => (
            <button
              key={c.role}
              onClick={() => { onRole(role === c.role ? null : c.role); setOpen(false) }}
              aria-pressed={role === c.role}
              className="flex w-full cursor-pointer items-center justify-between rounded-lg px-3 py-2 text-left text-ink-2 hover:bg-soft"
            >
              <span>{c.label} <span className="text-ink-3">· {c.lands}</span></span>
              {role === c.role && <Check size={15} className="text-ink" />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * The agents are watching: a filled dot while the last portal check is fresh, a hollow ring and
 * "Not checked since" when it is not. The words carry the meaning; the colour only adds to it.
 */
function Heartbeat({ at, nowMs }: { at: string; nowMs: number }) {
  const t = new Date(at)
  if (Number.isNaN(t.getTime())) return null
  const fresh = nowMs - t.getTime() < 30 * 60000
  const hm = t.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
  return (
    <span className="absolute left-[150px] top-1/2 hidden -translate-y-1/2 items-center gap-2 text-ink-3 xl:flex lg:left-[166px]">
      <span
        aria-hidden="true"
        className={cn('inline-block h-1.5 w-1.5 rounded-full', fresh ? 'bg-routine' : 'ring-1 ring-ink-3/50')}
      />
      {fresh ? `Watching · checked ${hm}` : `Not checked since ${hm}`}
    </span>
  )
}
