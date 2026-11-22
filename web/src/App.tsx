import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertsButton } from '@/components/AlertsButton'
import { DecisionCard } from '@/components/DecisionCard'
import { EvidenceDrawer, type DrawerView } from '@/components/EvidenceDrawer'
import { AutomateOffer, Rail } from '@/components/Rail'
import { Routines } from '@/components/Routines'
import { StatusStrip } from '@/components/StatusStrip'
import { SupplyMap, type MapSelection } from '@/components/SupplyMap'
import { COLORS, DECIDED, fetchState, viaOf, type Card, type State } from '@/lib/api'

const POLL_MS = 5000

export default function App() {
  const [state, setState] = useState<State | null>(null)
  const [error, setError] = useState(false)
  const [nowMs, setNowMs] = useState(Date.now())
  const [selection, setSelection] = useState<MapSelection>({})
  const [hoverRoutes, setHoverRoutes] = useState<string[]>([])
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
  const offset = useRef(0)
  const [path, setPath] = useState(window.location.pathname)
  // Opened from a push alert (/d/<id>?talk=1): show the card as an incoming call.
  const [ringing, setRinging] = useState(new URLSearchParams(window.location.search).get('talk') === '1')
  const stopRinging = () => {
    setRinging(false)
    window.history.replaceState(null, '', window.location.pathname)
  }

  const load = useCallback(async () => {
    try {
      const s = await fetchState()
      offset.current = new Date(s.now).getTime() - Date.now()
      setNowMs(Date.now() + offset.current)
      setState(s)
      setError(false)

      // A new portal drop: pulse its place on the map and say the agents are reading it.
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
        if (done && !byRoutine(done) && !(await hasRoutine(done.id))) setJustDecided(done)
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

  const openDrawer = useCallback((card: Card, tab: 'evidence' | 'trail') => setDrawer({ card, tab }), [])
  const automateFrom = (card: Card) => { setAutomate({ cardId: card.id, k: Date.now() }); setJustDecided(null) }

  if (!state) {
    return (
      <div className="grid h-full place-items-center text-ink-3">{error ? 'Cannot reach the server.' : 'Loading…'}</div>
    )
  }
  const clock = nowMs

  const single = path.match(/^\/d\/(.+)$/)
  if (single) {
    const id = decodeURIComponent(single[1])
    const card = state.cards.find((c) => c.id === id)
    return (
      <div className="min-h-full bg-white">
        <main className="mx-auto max-w-xl space-y-4 px-4 py-5">
          <div className="flex items-center justify-between gap-3">
            <a href="/" className="text-sm font-semibold text-ok hover:underline">← Inbox</a>
            <div className="flex items-center gap-2">
              <Routines state={state} onChanged={load} request={automate} />
              <AlertsButton />
            </div>
          </div>
          {card ? (
            <>
              <DecisionCard
                card={card}
                cards={state.cards}
                nowMs={clock}
                direct
                onChanged={load}
                onDrawer={openDrawer}
                ringing={ringing && card.status === 'open'}
                onRingingDone={stopRinging}
              />
              {justDecided?.id === card.id && (
                <AutomateOffer card={justDecided} onYes={() => automateFrom(justDecided)} onNo={() => setJustDecided(null)} />
              )}
            </>
          ) : (
            <p className="text-ink-2">This decision is no longer open.</p>
          )}
        </main>
        <EvidenceDrawer view={drawer} onClose={() => setDrawer(null)} />
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col bg-white">
      <StatusStrip
        state={state}
        nowMs={clock}
        actions={<><Routines state={state} onChanged={load} request={automate} /><AlertsButton /></>}
      />
      <div className="grid min-h-0 flex-1 lg:grid-cols-[3fr_2fr]">
        <section className="relative hidden min-h-0 flex-col border-r border-line lg:flex">
          <div className="relative min-h-0 flex-1">
            <SupplyMap
              state={state}
              hoverRoutes={hoverRoutes}
              selection={selection}
              onSelect={setSelection}
              pulseNode={pulseNode}
            />
          </div>
          <Legend />
        </section>
        <aside className="min-h-0 overflow-y-auto bg-white px-4 py-5 lg:px-6">
          <Rail
            state={state}
            nowMs={clock}
            selection={selection}
            onClear={() => setSelection({})}
            fresh={fresh}
            decided={justDecided}
            onAutomate={automateFrom}
            onSkipAutomate={() => setJustDecided(null)}
            onChanged={load}
            onHover={setHoverRoutes}
            onDrawer={openDrawer}
          />
        </aside>
      </div>
      {toast && (
        <div className="toast-in fixed left-1/2 top-24 z-50 -translate-x-1/2 rounded-xl border border-line bg-white px-5 py-3 text-[15px] font-semibold shadow-lg">
          <span className="mr-2 inline-block h-2.5 w-2.5 animate-pulse rounded-full bg-ok" />
          {toast}
        </div>
      )}
      <EvidenceDrawer view={drawer} onClose={() => setDrawer(null)} />
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
