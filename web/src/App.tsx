import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertsButton } from '@/components/AlertsButton'
import { DecisionList, visibleOrder } from '@/components/DecisionList'
import { DecisionPane, type Offer } from '@/components/DecisionPane'
import { EvidenceDrawer, type DrawerView } from '@/components/EvidenceDrawer'
import { LaunchTimeline } from '@/components/LaunchTimeline'
import { Routines } from '@/components/Routines'
import { pendingOf, useLive } from '@/components/routines/live'
import { SupplyMap, type MapSelection } from '@/components/SupplyMap'
import { Today, type DoneInfo } from '@/components/Today'
import { TopBar, type Page } from '@/components/TopBar'
import { COLORS, DECIDED, DONE_WORD, fetchState, tidy, viaOf, type Card, type State } from '@/lib/api'
import { actionIdOf, groupKey, sections } from '@/lib/decisions'
import { lookAlikes, todayOrder, trucksLeave } from '@/lib/today'

const POLL_MS = 5000

function useMedia(q: string) {
  const [on, setOn] = useState(() => window.matchMedia(q).matches)
  useEffect(() => {
    const m = window.matchMedia(q)
    const f = () => setOn(m.matches)
    m.addEventListener('change', f)
    return () => m.removeEventListener('change', f)
  }, [q])
  return on
}

export default function App() {
  const [state, setState] = useState<State | null>(null)
  const [error, setError] = useState(false)
  const [nowMs, setNowMs] = useState(Date.now())
  const [path, setPath] = useState(window.location.pathname)
  const [toast, setToast] = useState<string | null>(null)
  const [pulseNode, setPulseNode] = useState<string | null>(null)
  const [routinesOpen, setRoutinesOpen] = useState(false)
  const [automate, setAutomate] = useState<{ cardId: string; k: number } | null>(null)
  const [drafts, setDrafts] = useState(0)
  // Today: the decision in focus, and the "Done" screen after one.
  const [focusId, setFocusId] = useState<string | null>(null)
  const [done, setDone] = useState<DoneInfo | null>(null)
  const decidedHere = useRef(new Set<string>())
  // Plan: the full list and pane.
  const [selectedId, setSelectedId] = useState<string | null>(() => new URLSearchParams(window.location.search).get('card'))
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [mapSel, setMapSel] = useState<MapSelection>({})
  const [drawer, setDrawer] = useState<DrawerView>(null)
  const [fresh, setFresh] = useState<Set<string>>(new Set())
  const seenEvents = useRef<Set<string> | null>(null)
  const seenCards = useRef<Set<string> | null>(null)
  const lastStatus = useRef<Map<string, string> | null>(null)
  const offset = useRef(0)
  const phone = !useMedia('(min-width: 1024px)')
  const narrow = useMedia('(max-width: 767px)')
  // Opened from a push alert (/d/<id>?talk=1): the card rings as an incoming call.
  const [ringing, setRinging] = useState(new URLSearchParams(window.location.search).get('talk') === '1')
  const stopRinging = () => {
    setRinging(false)
    window.history.replaceState(null, '', window.location.pathname)
  }
  const go = useCallback((to: string) => {
    window.history.pushState(null, '', to)
    setPath(window.location.pathname)
    const card = new URLSearchParams(window.location.search).get('card')
    if (card) setSelectedId(card)
    window.scrollTo(0, 0)
  }, [])

  const live = useLive()
  const pending = pendingOf(live).length + drafts

  const load = useCallback(async () => {
    try {
      const s = await fetchState()
      offset.current = new Date(s.now).getTime() - Date.now()
      setNowMs(Date.now() + offset.current)
      setState(s)
      setError(false)

      // A new portal drop: say the agents are reading it (and pulse its place on the map).
      const ev = new Set(s.events.map((e) => e.id))
      if (seenEvents.current) {
        const added = s.events.filter((e) => !seenEvents.current!.has(e.id))
        if (added.length) {
          const e = added[0]
          const name = (e.path || e.file || '').split('/').pop()
          setToast(`New file: ${name}. Your agents are reading it.`)
          setPulseNode(e.node ?? null)
          window.setTimeout(() => setToast(null), 5000)
          window.setTimeout(() => setPulseNode(null), 4500)
        }
      }
      seenEvents.current = ev

      const ids = new Set(s.cards.filter((c) => c.status === 'open').map((c) => c.id))
      if (seenCards.current) {
        const added = [...ids].filter((id) => !seenCards.current!.has(id))
        if (added.length) {
          setFresh((f) => new Set([...f, ...added]))
          window.setTimeout(() => setFresh((f) => new Set([...f].filter((x) => !added.includes(x)))), 2500)
        }
      }
      seenCards.current = ids

      // Decided elsewhere (on a call): show "Done" for it too, unless a routine did it.
      const prev = lastStatus.current
      if (prev) {
        const c = s.cards.find((x) => prev.get(x.id) === 'open' && DECIDED.includes(x.status) && !decidedHere.current.has(x.id))
        const acts = c ? s.activity.filter((a) => a.card === c.id) : []
        if (c && !acts.some((a) => viaOf(a)?.kind === 'routine')) {
          const a = acts.find((x) => viaOf(x)?.kind === 'voice') ?? acts[0]
          decidedHere.current.add(c.id)
          void finish(s, {
            card: c,
            text: a ? stop(tidy(a.text)) : `${DONE_WORD[c.status] ?? 'Done'}.`,
            sub: viaOf(a ?? { text: '', kind: '' })?.kind === 'voice' ? 'Decided on the call.' : 'Decided elsewhere.',
            actionId: actionIdOf(a),
          })
        }
      }
      lastStatus.current = new Map(s.cards.map((c) => [c.id, c.status]))
    } catch {
      setError(true)
    }
  }, [])

  async function finish(s: State, d: { card: Card; text: string; sub: string; actionId: string | null }) {
    const more = lookAlikes(d.card, s).length
    const offer = more > 0 && !(await hasRoutine(d.card.id)) ? more : 0
    setDone({ ...d, offer, k: Date.now() })
  }

  useEffect(() => {
    void load()
    const poll = window.setInterval(load, POLL_MS)
    const tick = window.setInterval(() => setNowMs(Date.now() + offset.current), 15000)
    const nav = () => setPath(window.location.pathname)
    window.addEventListener('popstate', nav)
    return () => { clearInterval(poll); clearInterval(tick); window.removeEventListener('popstate', nav) }
  }, [load])

  // Voice drafts waiting in Routines count towards "Your routines want to do N things".
  useEffect(() => {
    const get = async () => {
      try {
        const r = await fetch('/api/routines/drafts')
        if (r.ok) setDrafts(((await r.json()).drafts ?? []).filter((x: { routine?: { id?: string } }) => x?.routine?.id).length)
      } catch { /* optional */ }
    }
    void get()
    const t = window.setInterval(get, 30000)
    return () => clearInterval(t)
  }, [])

  const sec = useMemo(() => (state ? sections(state) : null), [state])
  const order = useMemo(() => (sec ? visibleOrder(sec, expanded) : []), [sec, expanded])
  const today = useMemo(() => (state ? todayOrder(state) : []), [state])

  if (!state || !sec) {
    return <div className="grid h-full place-items-center text-[15px] text-ink-3">{error ? 'Cannot reach the server.' : 'Loading…'}</div>
  }

  const single = path.match(/^\/d\/(.+)$/)
  const page: Page | null = single || path === '/' ? 'today' : path.startsWith('/plan') ? 'plan' : path.startsWith('/suppliers') ? 'suppliers' : 'today'
  const openRoutines = (request?: { cardId: string; k: number }) => {
    if (request) setAutomate(request)
    setRoutinesOpen(true)
  }
  const truck = trucksLeave(state, nowMs)
  const open = today.length
  const status = (
    <>
      {truck && <span className="font-medium text-ink">{truck}</span>}
      {truck && (open > 0 || !phone || done) && ' · '}
      {phone && open > 0 ? (
        page === 'today'
          ? <button className="cursor-pointer hover:text-ink" onClick={() => go('/plan')}>{open} {open === 1 ? 'decision' : 'decisions'}</button>
          : `${open} ${open === 1 ? 'decision' : 'decisions'}`
      ) : done && open === 0 ? 'nothing else due' : open > 0 ? `${open} ${open === 1 ? 'decision needs' : 'decisions need'} you` : phone ? null : 'nothing needs you'}
    </>
  )
  const bar = (
    <TopBar
      page={page}
      go={go}
      onRoutines={() => openRoutines()}
      status={status}
      bell={<AlertsButton />}
      phone={phone}
      routinesBadge={pending}
    />
  )
  const routines = (
    <Routines state={state} onChanged={load} request={automate} open={routinesOpen} onOpenChange={setRoutinesOpen} hideTrigger />
  )
  const overlays = (
    <>
      {toast && (
        <div className="toast-in t-small fixed left-1/2 top-[76px] z-50 -translate-x-1/2 rounded-full bg-ink px-4 py-2 text-white shadow-lg" role="status">
          {toast}
        </div>
      )}
      <EvidenceDrawer view={drawer} onClose={() => setDrawer(null)} />
      {routines}
    </>
  )

  // ------------------------------------------------------------ Today (/ and /d/<id>)
  if (page === 'today') {
    const pinned = single ? state.cards.find((c) => c.id === decodeURIComponent(single[1])) ?? null : null
    const pinnedOpen = pinned && pinned.status !== 'open' ? null : pinned
    const card = single
      ? pinnedOpen ?? (done ? null : today[0] ?? null)
      : today.find((c) => c.id === focusId) ?? today[0] ?? null
    return (
      <div className="flex min-h-full flex-col bg-ground">
        {bar}
        <Today
          state={state}
          nowMs={nowMs}
          phone={phone}
          card={card}
          order={today}
          onFocus={(id) => { setFocusId(id); if (single) go('/') }}
          done={done}
          onDone={(d) => { decidedHere.current.add(d.card.id); void finish(state, d) }}
          onClearDone={() => { setDone(null); if (single) go('/') }}
          onChanged={load}
          onRoutines={openRoutines}
          go={go}
          ringing={ringing && !!pinnedOpen && card?.id === pinnedOpen.id}
          onRingingDone={stopRinging}
          pending={pending}
        />
        {overlays}
      </div>
    )
  }

  // ------------------------------------------------------------ Suppliers
  if (page === 'suppliers') {
    return (
      <div className="flex h-full flex-col bg-ground">
        {bar}
        <div className="relative min-h-[420px] flex-1">
          <SupplyMap state={state} hoverRoutes={[]} selection={mapSel} onSelect={setMapSel} pulseNode={pulseNode} />
        </div>
        <Legend />
        {overlays}
      </div>
    )
  }

  // ------------------------------------------------------------ Plan: timeline and every decision
  const groupOf = (c: Card) => sec.needs.find((g) => g.cards.some((x) => x.id === c.id)) ?? null
  const selected = selectedId ? state.cards.find((c) => c.id === selectedId) ?? null : null
  const offerFor = (c: Card): Offer | null => {
    if (done?.card.id !== c.id || !done.offer) return null
    const k = groupKey(c)
    return {
      more: state.cards.filter((x) => x.id !== c.id && x.status === 'open' && groupKey(x) === k).length,
      onYes: () => { openRoutines({ cardId: c.id, k: Date.now() }); setDone(null) },
      onNext: () => setDone(null),
    }
  }
  const select = (id: string | null) => {
    setSelectedId(id)
    if (!id) return
    const g = sec.needs.find((x) => x.cards.length > 1 && x.lead.id !== id && x.cards.some((c) => c.id === id))
    if (g) setExpanded((e) => new Set([...e, g.key]))
  }
  const toggle = (key: string) =>
    setExpanded((e) => { const n = new Set(e); if (n.has(key)) n.delete(key); else n.add(key); return n })
  const openOnPhone = (id: string) => go(`/d/${encodeURIComponent(id)}`)
  const timeline = (
    <LaunchTimeline state={state} nowMs={nowMs} selectedId={selectedId} onSelect={(id) => (narrow ? openOnPhone(id) : select(id))} compact={narrow} />
  )
  const list = (
    <DecisionList
      state={state}
      nowMs={nowMs}
      sec={sec}
      selectedId={narrow ? null : selectedId}
      onSelect={narrow ? openOnPhone : select}
      expanded={expanded}
      onToggle={toggle}
      fresh={fresh}
    />
  )

  if (narrow) {
    return (
      <div className="flex min-h-full flex-col bg-ground">
        {bar}
        <div className="border-b border-hair px-3">{timeline}</div>
        <div className="px-1">{list}</div>
        {overlays}
      </div>
    )
  }
  const first = order[0] ?? null
  const shown = selected ?? (first ? state.cards.find((c) => c.id === first) ?? null : null)
  return (
    <div className="flex h-full flex-col bg-ground">
      {bar}
      <div className="shrink-0 border-b border-hair px-6">{timeline}</div>
      <div className="flex min-h-0 flex-1">
        <aside className="scroll-thin w-[372px] shrink-0 overflow-y-auto border-r border-hair px-2" aria-label="All decisions">{list}</aside>
        <main className="scroll-thin min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-[860px] px-8 py-8 xl:px-12">
            {shown ? (
              <DecisionPane
                key={shown.id}
                card={shown}
                state={state}
                nowMs={nowMs}
                group={groupOf(shown)}
                onChanged={load}
                onDrawer={(card, tab) => setDrawer({ card, tab })}
                offer={offerFor(shown)}
              />
            ) : (
              <p className="t-body pt-24 text-center text-ink-2">Nothing here yet.</p>
            )}
          </div>
        </main>
      </div>
      {overlays}
    </div>
  )
}

const stop = (t: string) => (/[.!?]$/.test(t) ? t : `${t}.`)

// A routine already written from this card: do not offer to automate it again.
async function hasRoutine(cardId: string): Promise<boolean> {
  try {
    const r = await fetch('/api/routines')
    if (!r.ok) return false
    const { routines } = (await r.json()) as { routines: { created_from?: string }[] }
    return routines.some((x) => x.created_from === cardId)
  } catch {
    return false
  }
}

function Legend() {
  const sw = (c: string, label: string) => (
    <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full" style={{ background: c }} />{label}</span>
  )
  const line = (dash: boolean, label: string) => (
    <span className="inline-flex items-center gap-1.5">
      <svg width="26" height="6"><line x1="1" y1="3" x2="25" y2="3" stroke="#5d5d63" strokeWidth="2.5" strokeDasharray={dash ? '6 4' : undefined} strokeLinecap="round" /></svg>
      {label}
    </span>
  )
  return (
    <div className="t-small flex flex-wrap items-center gap-x-5 gap-y-1 px-5 py-3 text-ink-2">
      {line(false, 'Air / truck')}
      {line(true, 'Sea')}
      <span className="h-4 w-px bg-line" />
      {sw(COLORS.blue, 'On track')}
      {sw(COLORS.amber, 'At risk')}
      {sw(COLORS.red, 'Stops a line')}
      {sw(COLORS.grey, 'Silent')}
    </div>
  )
}
