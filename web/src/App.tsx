import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertsButton } from '@/components/AlertsButton'
import { EvidenceDrawer, type DrawerView } from '@/components/EvidenceDrawer'
import { Plan } from '@/components/Plan'
import { Routines, RoutinesLive } from '@/components/Routines'
import { pendingOf, refreshNow, suggestionFor, useLive } from '@/components/routines/live'
import { MapLegend, SupplierList, SupplyMap, type MapSelection } from '@/components/SupplyMap'
import { Today, type DoneInfo } from '@/components/Today'
import { TopBar, type Page } from '@/components/TopBar'
import { DECIDED, DONE_WORD, fetchState, tidy, viaOf, type Card, type State } from '@/lib/api'
import { actionIdOf } from '@/lib/decisions'
import { lookAlikes, repeatWords, todayOrder, trucksLeave } from '@/lib/today'

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
  const [search, setSearch] = useState(window.location.search)
  const [toast, setToast] = useState<string | null>(null)
  const [pulseNode, setPulseNode] = useState<string | null>(null)
  const [drafts, setDrafts] = useState(0)
  // Today: the decision in focus, and the "Done" screen after one.
  const [focusId, setFocusId] = useState<string | null>(null)
  const [done, setDone] = useState<DoneInfo | null>(null)
  const decidedHere = useRef(new Set<string>())
  const [mapSel, setMapSel] = useState<MapSelection>({})
  const [drawer, setDrawer] = useState<DrawerView>(null)
  const seenEvents = useRef<Set<string> | null>(null)
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
    setSearch(window.location.search)
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
    const k = Date.now()
    const more = lookAlikes(d.card, s).length
    if (more > 0) {
      setDone({ ...d, offer: (await hasRoutine(d.card.id)) ? 0 : more, k })
      return
    }
    // Show Done at once; a repeat offer follows when the server counts this decision as a repeat.
    setDone({ ...d, offer: 0, k })
    await refreshNow()
    const sg = suggestionFor(d.card.id)
    if (!sg || sg.count < 2) return
    const repeat = { n: sg.count, words: repeatWords(sg.decision, sg.action, d.card) }
    setDone((cur) => (cur && cur.k === k ? { ...cur, repeat } : cur))
  }

  useEffect(() => {
    void load()
    const poll = window.setInterval(load, POLL_MS)
    const tick = window.setInterval(() => setNowMs(Date.now() + offset.current), 15000)
    const nav = () => { setPath(window.location.pathname); setSearch(window.location.search) }
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

  const today = useMemo(() => (state ? todayOrder(state) : []), [state])
  // Plan opens a case on Today, with a quiet way back.
  const openCase = useCallback((id: string) => {
    setDone(null)
    go(`/d/${encodeURIComponent(id)}?from=plan`)
  }, [go])

  // Old links (/plan?card=<id>) now open the case on Today.
  const oldCard = path.startsWith('/plan') ? new URLSearchParams(search).get('card') : null
  useEffect(() => {
    if (oldCard) window.history.replaceState(null, '', `/d/${encodeURIComponent(oldCard)}?from=plan`)
    if (oldCard) { setPath(window.location.pathname); setSearch(window.location.search) }
  }, [oldCard])

  if (!state) {
    return <div className="grid h-full place-items-center text-[15px] text-ink-3">{error ? 'Cannot reach the server.' : 'Loading…'}</div>
  }

  const single = path.match(/^\/d\/(.+)$/)
  const page: Page | null = single || path === '/' ? 'today' : path.startsWith('/plan') ? 'plan' : path.startsWith('/suppliers') ? 'suppliers' : path.startsWith('/routines') ? 'routines' : 'today'
  // Routines is its own page; "Show me how" lands there drafting from the decision.
  const openRoutines = (request?: { cardId: string; k: number }) =>
    go(request ? `/routines?draft=${encodeURIComponent(request.cardId)}` : '/routines')
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
  const routines = <RoutinesLive state={state} />
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
    // A case opened by link that is no longer open is shown as it stands (unless just decided here).
    const caseCard = pinned && !pinnedOpen && done?.card.id !== pinned.id ? pinned : null
    const card = single
      ? pinnedOpen ?? (done ? null : today[0] ?? null)
      : today.find((c) => c.id === focusId) ?? today[0] ?? null
    const fromPlan = !!single && new URLSearchParams(search).get('from') === 'plan'
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
          caseCard={caseCard}
          onBack={fromPlan ? () => go('/plan') : null}
          onDrawer={(c, tab) => setDrawer({ card: c, tab })}
        />
        {overlays}
      </div>
    )
  }

  // ------------------------------------------------------------ Routines
  if (page === 'routines') {
    return (
      <div className="flex min-h-full flex-col bg-ground">
        {bar}
        <Routines state={state} onChanged={load} phone={narrow} />
        {overlays}
      </div>
    )
  }

  // ------------------------------------------------------------ Suppliers
  if (page === 'suppliers') {
    const list = (
      <SupplierList state={state} nowMs={nowMs} selection={mapSel} onSelect={setMapSel} onOpen={(id) => go(`/d/${encodeURIComponent(id)}`)} />
    )
    if (narrow) {
      return (
        <div className="flex min-h-full flex-col bg-ground">
          {bar}
          <div className="relative h-[300px] shrink-0">
            <SupplyMap state={state} hoverRoutes={[]} selection={mapSel} onSelect={setMapSel} pulseNode={pulseNode} />
          </div>
          <MapLegend className="px-6 pt-3" />
          <div className="px-6 pb-16 pt-8">{list}</div>
          {overlays}
        </div>
      )
    }
    return (
      <div className="flex h-full flex-col bg-ground">
        {bar}
        <div className="flex min-h-0 flex-1">
          <div className="flex min-w-0 flex-1 flex-col pb-6 pl-6">
            <div className="relative min-h-[420px] flex-1">
              <SupplyMap state={state} hoverRoutes={[]} selection={mapSel} onSelect={setMapSel} pulseNode={pulseNode} />
            </div>
            <MapLegend className="pt-3" />
          </div>
          <aside className="scroll-thin w-[380px] shrink-0 overflow-y-auto px-10 pb-16 pt-6 xl:w-[420px]" aria-label="Suppliers">{list}</aside>
        </div>
        {overlays}
      </div>
    )
  }

  // ------------------------------------------------------------ Plan: the overview, no decisions
  return (
    <div className="flex min-h-full flex-col bg-ground">
      {bar}
      <Plan state={state} nowMs={nowMs} phone={narrow} onOpen={openCase} onChanged={load} />
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
