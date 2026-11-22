import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertsButton } from '@/components/AlertsButton'
import { DecisionList, visibleOrder } from '@/components/DecisionList'
import { DecisionPane, type Offer } from '@/components/DecisionPane'
import { EvidenceDrawer, type DrawerView } from '@/components/EvidenceDrawer'
import { Header, VerdictLine, type View } from '@/components/Header'
import { LaunchTimeline } from '@/components/LaunchTimeline'
import { Routines } from '@/components/Routines'
import { SupplyMap, type MapSelection } from '@/components/SupplyMap'
import { COLORS, DECIDED, ago, fetchState, viaOf, type Card, type State } from '@/lib/api'
import { groupKey, sections } from '@/lib/decisions'

const POLL_MS = 5000

function usePhone() {
  const q = '(max-width: 767px)'
  const [phone, setPhone] = useState(() => window.matchMedia(q).matches)
  useEffect(() => {
    const m = window.matchMedia(q)
    const on = () => setPhone(m.matches)
    m.addEventListener('change', on)
    return () => m.removeEventListener('change', on)
  }, [])
  return phone
}

const typing = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))

export default function App() {
  const [state, setState] = useState<State | null>(null)
  const [error, setError] = useState(false)
  const [nowMs, setNowMs] = useState(Date.now())
  const [view, setView] = useState<View>('launch')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [mapSel, setMapSel] = useState<MapSelection>({})
  const [drawer, setDrawer] = useState<DrawerView>(null)
  const [toast, setToast] = useState<string | null>(null)
  const [pulseNode, setPulseNode] = useState<string | null>(null)
  const [fresh, setFresh] = useState<Set<string>>(new Set())
  const seenEvents = useRef<Set<string> | null>(null)
  const seenCards = useRef<Set<string> | null>(null)
  // A card that was just decided (button or voice): offer to automate the next one like it.
  const lastStatus = useRef<Map<string, string> | null>(null)
  const [justDecided, setJustDecided] = useState<Card | null>(null)
  const [automate, setAutomate] = useState<{ cardId: string; k: number } | null>(null)
  // A decision without an offer moves the selection on to the next one.
  const [advance, setAdvance] = useState<{ id: string; k: number } | null>(null)
  const offset = useRef(0)
  const phone = usePhone()
  const [path, setPath] = useState(window.location.pathname)
  // Opened from a push alert (/d/<id>?talk=1): show the card as an incoming call.
  const [ringing, setRinging] = useState(new URLSearchParams(window.location.search).get('talk') === '1')
  const stopRinging = () => {
    setRinging(false)
    window.history.replaceState(null, '', window.location.pathname)
  }
  const go = (to: string) => {
    window.history.pushState(null, '', to)
    setPath(to)
    window.scrollTo(0, 0)
  }

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
          setToast(`New: ${name}. Agent reading…`)
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

      const prev = lastStatus.current
      if (prev) {
        const done = s.cards.find((c) => prev.get(c.id) === 'open' && DECIDED.includes(c.status))
        // Not when a routine made the decision, nor when a routine already covers this card.
        const byRoutine = (c: Card) => s.activity.some((a) => a.card === c.id && viaOf(a)?.kind === 'routine')
        if (done) {
          if (!byRoutine(done) && !(await hasRoutine(done.id))) setJustDecided(done)
          else setAdvance({ id: done.id, k: Date.now() })
        }
      }
      lastStatus.current = new Map(s.cards.map((c) => [c.id, c.status]))
    } catch {
      setError(true)
    }
  }, [])

  useEffect(() => {
    load()
    const poll = window.setInterval(load, POLL_MS)
    const tick = window.setInterval(() => setNowMs(Date.now() + offset.current), 15000)
    const nav = () => setPath(window.location.pathname)
    window.addEventListener('popstate', nav)
    return () => { clearInterval(poll); clearInterval(tick); window.removeEventListener('popstate', nav) }
  }, [load])

  const sec = useMemo(() => (state ? sections(state) : null), [state])
  const order = useMemo(() => (sec ? visibleOrder(sec, expanded) : []), [sec, expanded])
  const needsOrder = useMemo(() => (sec ? sec.needs.flatMap((g) => g.cards.map((c) => c.id)) : []), [sec])
  const lastOrder = useRef<string[]>([])
  const touched = useRef(false)

  const select = useCallback((id: string | null) => {
    touched.current = true
    setSelectedId(id)
    if (!id || !sec) return
    // A card hidden in a closed group (from the timeline, say): open its group.
    const g = sec.needs.find((x) => x.cards.length > 1 && x.lead.id !== id && x.cards.some((c) => c.id === id))
    if (g) setExpanded((e) => new Set([...e, g.key]))
    window.setTimeout(() => document.getElementById(`row-${id}`)?.scrollIntoView({ block: 'nearest' }), 0)
  }, [sec])

  // Keep the selection across polls; if its card is gone, take the one that moved into its place.
  useEffect(() => {
    if (!state) return
    const exists = selectedId && state.cards.some((c) => c.id === selectedId)
    if (!exists) {
      if (selectedId) {
        const i = lastOrder.current.indexOf(selectedId)
        const next = lastOrder.current.slice(i + 1).find((id) => order.includes(id)) ?? order[order.length - 1] ?? null
        setSelectedId(next)
      } else if (!touched.current && order.length && needsOrder.length) {
        setSelectedId(order[0])
      }
    }
    lastOrder.current = order
  }, [state, order, needsOrder, selectedId])

  const nextOpen = useCallback((from: string) => {
    const i = lastOrder.current.indexOf(from)
    const after = lastOrder.current.slice(i + 1).find((id) => needsOrder.includes(id) && id !== from)
    return after ?? needsOrder.find((id) => id !== from) ?? null
  }, [needsOrder])

  useEffect(() => {
    if (advance && advance.id === selectedId) select(nextOpen(advance.id))
    // only when a new decision lands
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [advance])

  // j / k / arrows walk the list.
  useEffect(() => {
    if (phone || view !== 'launch' || path !== '/') return
    const onKey = (e: KeyboardEvent) => {
      if (typing(e.target) || e.metaKey || e.ctrlKey || e.altKey || drawer) return
      const k = e.key
      const down = k === 'j' || k === 'J' || k === 'ArrowDown'
      const up = k === 'k' || k === 'K' || k === 'ArrowUp'
      if (!down && !up) return
      e.preventDefault()
      const i = selectedId ? order.indexOf(selectedId) : -1
      const n = i < 0 ? 0 : Math.min(order.length - 1, Math.max(0, i + (down ? 1 : -1)))
      if (order[n]) select(order[n])
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [phone, view, path, order, selectedId, select, drawer])

  const openDrawer = useCallback((card: Card, tab: 'evidence' | 'trail') => setDrawer({ card, tab }), [])
  const automateFrom = (card: Card) => { setAutomate({ cardId: card.id, k: Date.now() }); setJustDecided(null) }

  if (!state || !sec) {
    return <div className="grid h-full place-items-center text-ink-3">{error ? 'Cannot reach the server.' : 'Loading…'}</div>
  }

  const groupOf = (c: Card) => sec.needs.find((g) => g.cards.some((x) => x.id === c.id)) ?? null
  const offerFor = (c: Card): Offer | null => {
    if (justDecided?.id !== c.id) return null
    const k = groupKey(c)
    return {
      more: state.cards.filter((x) => x.id !== c.id && x.status === 'open' && groupKey(x) === k).length,
      onYes: () => automateFrom(c),
      onNext: () => { setJustDecided(null); select(nextOpen(c.id)) },
    }
  }
  const routines = <Routines state={state} onChanged={load} request={automate} />

  // ------------------------------------------------------------ one decision (/d/<id>)
  const single = path.match(/^\/d\/(.+)$/)
  if (single) {
    const id = decodeURIComponent(single[1])
    const card = state.cards.find((c) => c.id === id)
    return (
      <div className="min-h-full bg-white">
        <div className="sticky top-0 z-20 flex h-14 items-center justify-between gap-3 border-b border-line bg-white/95 px-4 backdrop-blur">
          <button onClick={() => go('/')} className="cursor-pointer text-sm font-semibold text-ok hover:underline">← All decisions</button>
          <div className="flex items-center gap-2">{routines}<AlertsButton /></div>
        </div>
        <main className="mx-auto max-w-[900px] px-4 py-6 sm:px-8">
          {card ? (
            <DecisionPane
              key={card.id}
              card={card}
              state={state}
              nowMs={nowMs}
              group={groupOf(card)}
              direct
              onChanged={load}
              onDrawer={openDrawer}
              ringing={ringing && card.status === 'open'}
              onRingingDone={stopRinging}
              offer={offerFor(card) && { ...offerFor(card)!, onNext: () => { setJustDecided(null); const n = nextOpen(card.id); go(n ? `/d/${encodeURIComponent(n)}` : '/') } }}
            />
          ) : (
            <p className="text-ink-2">This decision is no longer open.</p>
          )}
        </main>
        <EvidenceDrawer view={drawer} onClose={() => setDrawer(null)} />
      </div>
    )
  }

  const selected = selectedId ? state.cards.find((c) => c.id === selectedId) ?? null : null
  const openOnPhone = (id: string) => go(`/d/${encodeURIComponent(id)}`)
  const timeline = (
    <LaunchTimeline
      state={state}
      nowMs={nowMs}
      selectedId={selectedId}
      onSelect={(id) => (phone ? openOnPhone(id) : select(id))}
      compact={phone}
    />
  )
  const header = (
    <Header state={state} nowMs={nowMs} view={view} onView={setView} routines={routines} bell={<AlertsButton />} compact={phone} />
  )
  const toggle = (key: string) =>
    setExpanded((e) => { const n = new Set(e); if (n.has(key)) n.delete(key); else n.add(key); return n })
  const list = (
    <DecisionList
      state={state}
      nowMs={nowMs}
      sec={sec}
      selectedId={phone ? null : selectedId}
      onSelect={phone ? openOnPhone : select}
      expanded={expanded}
      onToggle={toggle}
      fresh={fresh}
    />
  )
  const suppliers = (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="relative min-h-[420px] flex-1">
        <SupplyMap state={state} hoverRoutes={[]} selection={mapSel} onSelect={setMapSel} pulseNode={pulseNode} />
      </div>
      <Legend />
    </div>
  )
  const overlays = (
    <>
      {toast && (
        <div className="toast-in fixed left-1/2 top-20 z-50 -translate-x-1/2 rounded-xl border border-line bg-white px-5 py-3 text-[15px] font-semibold shadow-lg">
          <span className="mr-2 inline-block h-2.5 w-2.5 animate-pulse rounded-full bg-ok" />
          {toast}
        </div>
      )}
      <EvidenceDrawer view={drawer} onClose={() => setDrawer(null)} />
    </>
  )

  // ------------------------------------------------------------ phone
  if (phone) {
    return (
      <div className="flex min-h-full flex-col bg-white">
        <div className="sticky top-0 z-20">{header}</div>
        {view === 'launch' ? (
          <>
            <VerdictLine state={state} nowMs={nowMs} />
            <div className="border-b border-line">{timeline}</div>
            <div className="px-1">{list}</div>
          </>
        ) : suppliers}
        {overlays}
      </div>
    )
  }

  // ------------------------------------------------------------ desktop
  const openCount = sec.needs.length
  return (
    <div className="flex h-full flex-col bg-white">
      {header}
      {view === 'launch' ? (
        <>
          <div className="shrink-0 border-b border-line">{timeline}</div>
          <div className="flex min-h-0 flex-1">
            <aside className="scroll-thin w-[372px] shrink-0 overflow-y-auto border-r border-line px-2" aria-label="Decisions">
              {list}
            </aside>
            <main className="scroll-thin min-w-0 flex-1 overflow-y-auto">
              <div className="mx-auto max-w-[900px] px-8 py-8 xl:px-12">
                {selected ? (
                  <DecisionPane
                    key={selected.id}
                    card={selected}
                    state={state}
                    nowMs={nowMs}
                    group={groupOf(selected)}
                    keys
                    onChanged={load}
                    onDrawer={openDrawer}
                    offer={offerFor(selected)}
                  />
                ) : (
                  <div className="grid min-h-[40vh] place-items-center text-center">
                    {openCount === 0 ? (
                      <p className="max-w-md text-[16px] leading-relaxed text-ink-2">
                        <span className="block text-[20px] font-bold text-ink">Nothing needs you.</span>
                        Agents are watching {state.shipments.length} shipments; last check {ago(state.last_check_at, nowMs)}.
                      </p>
                    ) : (
                      <p className="text-[16px] text-ink-2">Pick a decision, or press <kbd>J</kbd>.</p>
                    )}
                  </div>
                )}
              </div>
            </main>
          </div>
        </>
      ) : suppliers}
      {overlays}
    </div>
  )
}

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
      <svg width="26" height="6"><line x1="1" y1="3" x2="25" y2="3" stroke="#475569" strokeWidth="2.5" strokeDasharray={dash ? '6 4' : undefined} strokeLinecap="round" /></svg>
      {label}
    </span>
  )
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1 border-t border-line px-5 py-3 text-sm text-ink-2">
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
