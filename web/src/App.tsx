import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertsButton } from '@/components/AlertsButton'
import { EvidenceDrawer, type DrawerView } from '@/components/EvidenceDrawer'
import { History } from '@/components/History'
import { Launch } from '@/components/Launch'
import { Routines, RoutinesLive } from '@/components/Routines'
import { pendingOf, refreshNow, suggestionFor, useLive } from '@/components/routines/live'
import { MapLegend, SupplierList, SupplyMap, type MapSelection } from '@/components/SupplyMap'
import { Today, type DoneInfo } from '@/components/Today'
import { TopBar, type Page } from '@/components/TopBar'
import { DECIDED, DONE_WORD, fetchState, tidy, viaOf, type Card, type State } from '@/lib/api'
import { actionIdOf } from '@/lib/decisions'
import { firstNameOf, mattersToApprover, roleFromParam, savedRole, saveRole, type Role } from '@/lib/role'
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
  const [, setSearch] = useState(window.location.search)
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
  // Who uses this device: the approver lands on Launch, the buyer (and no choice) on Today.
  const [role, setRoleState] = useState<Role | null>(savedRole)
  const chooseRole = useCallback((r: Role | null) => { saveRole(r); setRoleState(r) }, [])
  // The approver's inbox: what reaches her (default) or every open case, as the buyer sees it.
  const [allCases, setAllCases] = useState(false)
  const roleRef = useRef(role)
  roleRef.current = role
  const landed = useRef(false)
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
  // Routines are the buyer's: the person who signs is not asked to build or approve them.
  const pending = role === 'approver' ? 0 : pendingOf(live).length + drafts
  const readyN = pendingOf(live).length

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
          const a = acts.find((x) => viaOf(x)?.kind === 'voice' || viaOf(x)?.kind === 'call') ?? acts[0]
          decidedHere.current.add(c.id)
          void finish(s, {
            card: c,
            text: a ? stop(tidy(a.text)) : `${DONE_WORD[c.status] ?? 'Done'}.`,
            sub: ({ voice: 'Decided on the call.', call: 'Decided on the phone.' } as Record<string, string>)[viaOf(a ?? { text: '', kind: '' })?.kind ?? ''] ?? 'Decided elsewhere.',
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
    if (roleRef.current === 'approver') { setDone({ ...d, offer: 0, k }); return }
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

  // First state: ?as=<name> (from a sign-off link) picks the role; "/" opens the role's page.
  useEffect(() => {
    if (!state || landed.current) return
    landed.current = true
    const q = new URLSearchParams(window.location.search)
    let r = role
    if (q.has('as')) {
      const named = roleFromParam(q.get('as'), { viewer: state.launch?.money?.viewer ?? state.viewer, approver: state.launch?.money?.approver })
      if (named) { r = named; chooseRole(named) }
      q.delete('as')
      const rest = q.toString()
      window.history.replaceState(null, '', window.location.pathname + (rest ? `?${rest}` : '') + window.location.hash)
      setSearch(window.location.search)
    }
    if (r === 'approver' && window.location.pathname === '/') {
      window.history.replaceState(null, '', '/launch')
      setPath('/launch')
    }
  }, [state, role, chooseRole])

  // The buyer sees every case; the person who signs only those that reach her.
  const allOpen = useMemo(() => (state ? todayOrder(state) : []), [state])
  const mine = useMemo(() => {
    if (!state) return []
    const m = state.launch?.money
    const priced = new Set((m?.pending_decisions ?? []).map((d) => d.card))
    return allOpen.filter((c) => mattersToApprover(c, m?.approver, priced))
  }, [state, allOpen])
  const today = role === 'approver' && !allCases ? mine : allOpen
  // Launch and the folded groups on Today open a case on Today.
  const openCase = useCallback((id: string) => {
    setDone(null)
    go(`/d/${encodeURIComponent(id)}`)
  }, [go])
  // Phone: the status line's "N decisions" opens Today's "All cases".
  const [allReq, setAllReq] = useState(0)

  // Old links: /plan is now /launch; /plan?card=<id> opens the case on Today.
  const oldPlan = path.startsWith('/plan')
  useEffect(() => {
    if (!oldPlan) return
    const card = new URLSearchParams(window.location.search).get('card')
    window.history.replaceState(null, '', card ? `/d/${encodeURIComponent(card)}` : window.location.hash === '#handled' ? '/' : '/launch')
    setPath(window.location.pathname); setSearch(window.location.search)
  }, [oldPlan])

  if (!state) {
    return <div className="grid h-full place-items-center text-[15px] text-ink-3">{error ? 'Cannot reach the server.' : 'Loading…'}</div>
  }

  const single = path.match(/^\/d\/(.+)$/)
  const page: Page | null = single || path === '/' ? 'today' : path.startsWith('/launch') ? 'launch' : path.startsWith('/suppliers') ? 'suppliers' : path.startsWith('/routines') ? 'routines' : path.startsWith('/history') ? 'history' : 'today'
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
          ? <button className="cursor-pointer hover:text-ink" onClick={() => { if (page !== 'today' || single) go('/'); setAllReq(Date.now()) }}>{open} {open === 1 ? 'decision' : 'decisions'}</button>
          : `${open} ${open === 1 ? 'decision' : 'decisions'}`
      ) : open > 0 ? `${open} ${open === 1 ? 'decision needs' : 'decisions need'} you`
        : readyN > 0 ? `${readyN} ready for your OK`
        : done ? 'nothing else due' : phone ? null : 'nothing needs you'}
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
      role={role}
      onRole={chooseRole}
      people={{ buyer: state.launch?.money?.viewer ?? state.viewer, approver: state.launch?.money?.approver ?? null }}
      checkedAt={state.watching ? state.last_check_at : null}
      nowMs={nowMs}
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
    // Passed on to the person who signs: hers to answer, first in her inbox.
    const approver = state.launch?.money?.approver
    const forMe = role === 'approver'
      ? state.cards.filter((c) => {
        const r = c.signoff_request
        return r?.kind === 'escalation' && !r.answer && !!approver && firstNameOf(r.to) === firstNameOf(approver)
      })
      : []
    return (
      <div className="flex min-h-full flex-col bg-ground">
        {bar}
        {role === 'approver' && (
          <Scope all={allCases} onAll={(v) => { setAllCases(v); setFocusId(null); if (single) go('/') }} mine={mine.length} total={allOpen.length} phone={narrow} />
        )}
        {forMe.length > 0 && <ForYourOk cards={forMe} nowMs={nowMs} phone={narrow} />}
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
          onOpenCase={openCase}
          allReq={allReq}
          onDrawer={(c, tab) => setDrawer({ card: c, tab })}
        />
        {overlays}
      </div>
    )
  }

  // ------------------------------------------------------------ History
  if (page === 'history') {
    return (
      <div className="flex min-h-full flex-col bg-ground">
        {bar}
        <History state={state} nowMs={nowMs} phone={narrow} onOpen={openCase} />
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

  // ------------------------------------------------------------ Launch: will we make the trucks?
  return (
    <div className="flex min-h-full flex-col bg-ground">
      {bar}
      <Launch state={state} nowMs={nowMs} phone={narrow} onOpen={openCase} role={role} />
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

/** Cases the buyer passed on for her OK: one click opens the sign-off page she knows from her phone. */
function ForYourOk({ cards, nowMs, phone }: { cards: Card[]; nowMs: number; phone: boolean }) {
  const [err, setErr] = useState<string | null>(null)
  const open = async (id: string) => {
    try {
      const r = await fetch(`/api/cards/${encodeURIComponent(id)}/signoff/open`)
      if (!r.ok) throw new Error()
      window.location.assign((await r.json()).path)
    } catch {
      setErr('This one was answered already.')
    }
  }
  return (
    <section aria-label="Waiting for your OK" className={phone ? 'mx-auto w-full max-w-[560px] px-7 pt-4' : 'mx-auto -mb-12 w-full max-w-[1440px] px-12 pt-16 xl:px-24'}>
      <h2 className="t-small text-ink-3">Waiting for your OK</h2>
      <ul className="mt-2 divide-y divide-hair border-y border-hair">
        {cards.map((c) => {
          const asked = c.signoff_request?.asked_at
          const min = asked ? Math.max(0, Math.round((nowMs - new Date(asked).getTime()) / 60000)) : null
          return (
            <li key={c.id}>
              <button onClick={() => void open(c.id)} className="flex w-full cursor-pointer items-baseline justify-between gap-4 py-3 text-left hover:bg-soft">
                <span className="min-w-0 text-ink">{tidy(c.headline)}</span>
                <span className="t-small shrink-0 text-accent">
                  Review{min != null && <span className="text-ink-3"> · asked {min < 60 ? `${min} min` : `${Math.floor(min / 60)} h`} ago</span>}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
      {err && <p className="t-small mt-2 text-late">{err}</p>}
    </section>
  )
}

/** The approver's two views of the inbox: what reaches her, or every open case. */
function Scope({ all, onAll, mine, total, phone }: { all: boolean; onAll: (v: boolean) => void; mine: number; total: number; phone: boolean }) {
  const tab = (on: boolean, label: string, v: boolean) => (
    <button
      onClick={() => onAll(v)}
      aria-pressed={on}
      className={on ? 'cursor-pointer rounded-full bg-ink px-3 py-1 text-white' : 'cursor-pointer rounded-full px-3 py-1 text-ink-3 hover:text-ink'}
    >
      {label}
    </button>
  )
  return (
    <div role="group" aria-label="Show" className={phone ? 't-small mx-auto flex w-full max-w-[560px] gap-1 px-7 pt-4' : 't-small mx-auto -mb-12 flex w-full max-w-[1440px] gap-1 px-12 pt-10 xl:px-[88px]'}>
      {tab(!all, `For you · ${mine}`, false)}
      {tab(all, `All open · ${total}`, true)}
    </div>
  )
}
