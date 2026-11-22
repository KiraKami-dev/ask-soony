import { useState } from 'react'
import { tidy, viaOf, type ActivityItem, type State } from '@/lib/api'
import { firstNameOf } from '@/lib/role'
import { cn } from '@/lib/utils'

// What was decided, by whom and how, newest first: the record the person who signs reads back.
// "Everything" adds the files the agents read. Rows that belong to a case open it.

interface Props {
  state: State
  nowMs: number
  phone: boolean
  onOpen: (cardId: string) => void
}

const day = (iso: string) => new Date(iso).toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' })
const time = (iso: string) => new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })

function who(a: ActivityItem, viewer: string): string {
  const v = viaOf(a)
  if (v?.kind === 'routine') return `routine “${v.name}”`
  const by = firstNameOf(a.by) || (a.kind === 'read' ? '' : firstNameOf(viewer))
  const how = v?.kind === 'voice' ? 'on a call' : v?.kind === 'call' ? 'on the phone' : ''
  return [by, how].filter(Boolean).join(', ')
}

export function History({ state, nowMs, phone, onOpen }: Props) {
  const [all, setAll] = useState(false)
  const items = state.activity.filter((a) => a.at && (all || a.kind !== 'read'))
  const today = day(new Date(nowMs).toISOString())
  const groups: { day: string; rows: ActivityItem[] }[] = []
  for (const a of items) {
    const d = day(a.at!)
    if (groups[groups.length - 1]?.day !== d) groups.push({ day: d, rows: [] })
    groups[groups.length - 1].rows.push(a)
  }
  const viewer = state.launch?.money?.viewer ?? state.viewer

  return (
    <div className="flex-1">
      <div className={cn('mx-auto w-full max-w-[760px]', phone ? 'px-6 pb-10 pt-6' : 'px-8 pb-16 pt-14')}>
        <h1 className="t-big text-ink">History</h1>
        <p className="t-small mt-2 text-ink-3">What was decided, by whom and how. Newest first.</p>
        <div className="t-small mt-6 flex gap-1" role="group" aria-label="Show">
          {[{ on: !all, label: 'Decisions', set: false }, { on: all, label: 'Everything', set: true }].map((b) => (
            <button
              key={b.label}
              onClick={() => setAll(b.set)}
              aria-pressed={b.on}
              className={cn('cursor-pointer rounded-full px-3 py-1', b.on ? 'bg-ink text-white' : 'text-ink-3 hover:text-ink')}
            >
              {b.label}
            </button>
          ))}
        </div>

        {groups.length === 0 && <p className="t-small mt-10 text-ink-3">Nothing decided yet.</p>}
        {groups.map((g) => (
          <section key={g.day} className="mt-10">
            <h2 className="t-small border-b border-hair pb-2 text-ink-3">{g.day === today ? 'Today' : g.day}</h2>
            <ul>
              {g.rows.map((a, i) => {
                const by = who(a, viewer)
                const body = (
                  <>
                    <span className="t-small w-12 shrink-0 tabular-nums text-ink-3">{time(a.at!)}</span>
                    <span className="min-w-0 flex-1">
                      <span className={cn('block', a.kind === 'read' ? 'text-ink-3' : 'text-ink')}>{tidy(a.text)}</span>
                      {(by || a.detail) && (
                        <span className="t-small block text-ink-3">
                          {by}{by && a.detail ? ' · ' : ''}{a.detail ? tidy(a.detail) : ''}
                        </span>
                      )}
                    </span>
                  </>
                )
                return (
                  <li key={a.id ?? `${a.at}-${i}`} className="border-b border-hair">
                    {a.card ? (
                      <button onClick={() => onOpen(a.card!)} className="flex w-full cursor-pointer gap-4 py-3 text-left hover:bg-soft">
                        {body}
                      </button>
                    ) : (
                      <div className="flex gap-4 py-3">{body}</div>
                    )}
                  </li>
                )
              })}
            </ul>
          </section>
        ))}
      </div>
    </div>
  )
}
