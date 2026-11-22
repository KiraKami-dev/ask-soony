import * as Dialog from '@radix-ui/react-dialog'
import { Check, Database, FileText } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { AnchorCrop, NUM_OR_ID, fileUrl, normTok, useAnchors, type Anchor } from '@/components/SourceAnchors'
import { VoiceCall, type Prepared, type ScreenWhat } from '@/components/VoiceCall'
import { pendingOf, undoAction, useLive } from '@/components/routines/live'
import { UndoAction } from '@/components/routines/Undo'
import { ProposalLines, ProposalSheet, RoutineLine, WouldLine, groupProposals } from '@/components/routines/Proposals'
import type { Proposal } from '@/components/routines/types'
import { DONE_WORD, fmtNum, fmtTime, post, sentence, tidy, type Card, type Evidence, type State, viaOf } from '@/lib/api'
import { actionIdOf, languageOf, replyFacts, routineActionOf, routineOf, sections, splitValue, when, type Handled } from '@/lib/decisions'
import {
  actionText, claimOf, costLine, costsMoney, docKind, forToday, headlineOf, launchStatus, ordinal, plural,
  recommendedIndex, routineCount, shortName, shortTitle, summaryLine, type Claim,
} from '@/lib/today'
import { resendSignoff, sentSub, signoffLine, signoffOpen } from '@/lib/signoff'
import { CaseFlow } from '@/components/CaseFlow'
import { savedRole } from '@/lib/role'
import { cn } from '@/lib/utils'

// The Sunday flow: one decision at a time, the source one tap away, then "Done" and what's next.

export interface DoneInfo {
  card: Card
  text: string
  sub: string
  actionId: string | null
  /** Open look-alikes to offer a routine for; 0 when none (or a routine exists). */
  offer: number
  /**
   * No open look-alikes, but this is the n-th time the same kind of case was decided the same
   * way (GET /api/routines/suggestions): offer to make it a routine. `words` is what was done.
   */
  repeat?: { n: number; words: string } | null
  k: number
}

/** Which part of the evidence drawer to open. */
type Drawer = (card: Card, tab: 'evidence' | 'trail') => void

interface Props {
  state: State
  nowMs: number
  phone: boolean
  card: Card | null
  order: Card[]
  onFocus: (id: string) => void
  done: DoneInfo | null
  onDone: (d: { card: Card; text: string; sub: string; actionId: string | null }) => void
  onClearDone: () => void
  onChanged: () => void
  onRoutines: (request?: { cardId: string; k: number }) => void
  go: (path: string) => void
  ringing?: boolean
  onRingingDone?: () => void
  /** Routine proposals waiting for an OK plus drafts from calls (the live store is read here). */
  pending: number
  /** A case opened by link (/d/<id>) that is no longer open: shown as it stands, not decided. */
  caseCard?: Card | null
  /** Opens any case (open, passed on or closed) on Today: /d/<id>. */
  onOpenCase: (cardId: string) => void
  /** Phone: a change opens "All cases" (the status line's "N decisions"). */
  allReq?: number
  onDrawer: Drawer
}

/** Opens the review sheet for what routines prepared. */
type Review = (items: Proposal[]) => void

/** What the primary button will do. */
type Choice = { kind: 'option'; i: number } | { kind: 'escalate' } | { kind: 'dismiss' }
const sameChoice = (a: Choice, b: Choice) => a.kind === b.kind && (a.kind !== 'option' || a.i === (b as { i: number }).i)
/** The recommended option; without options, pass it on (or, with nobody to pass it to, dismiss). */
const defaultChoice = (card: Card): Choice =>
  card.options.length ? { kind: 'option', i: recommendedIndex(card) } : card.approver ? { kind: 'escalate' } : { kind: 'dismiss' }

const typing = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))

export function Today(p: Props) {
  const { state, nowMs, phone, card, done } = p
  const handled = routineCount(state, nowMs)
  const [review, setReview] = useState<Proposal[] | null>(null)
  // A live call holds its decision on screen: Done shows after the call ends, not mid-sentence.
  const [call, setCall] = useState<Card | null>(null)
  const callCard = call ? state.cards.find((c) => c.id === call.id) ?? call : null
  const focusCard = callCard ?? card
  const view = callCard ? 'decide' : done ? 'done' : p.caseCard ? 'case' : card ? 'decide' : 'clear'

  // The folded groups below "Needs you": which are open. Phone: all of them sit behind "All cases".
  const [openGroups, setOpenGroups] = useState<Set<GroupKey>>(new Set())
  // Phone: the list of cases shows under the decision straight away; "hide" folds it.
  const [allCases, setAllCases] = useState(true)
  const allRef = useRef<HTMLDivElement>(null)
  const reveal = (g?: GroupKey) => {
    if (g) setOpenGroups((o) => new Set(o).add(g))
    if (phone) setAllCases(true)
    window.setTimeout(() => (g ? document.getElementById(`group-${g}`) : allRef.current)?.scrollIntoView({ block: 'start', behavior: 'smooth' }), 50)
  }
  const allReq = p.allReq ?? 0
  // Desktop: the list keeps its scroll position; only bring the selected row into view if needed.
  const asideRef = useRef<HTMLElement>(null)
  const selectedId = p.caseCard?.id ?? card?.id
  useEffect(() => {
    const id = window.requestAnimationFrame(() =>
      asideRef.current?.querySelector('[aria-current="true"]')?.scrollIntoView({ block: 'nearest' }))
    return () => window.cancelAnimationFrame(id)
  }, [selectedId])
  useEffect(() => { if (allReq) reveal() }, [allReq]) // eslint-disable-line react-hooks/exhaustive-deps
  const toggleGroup = (g: GroupKey) =>
    setOpenGroups((o) => { const n = new Set(o); if (n.has(g)) n.delete(g); else n.add(g); return n })
  const activeId = p.caseCard?.id ?? (view === 'decide' ? focusCard?.id : null) ?? null
  const ready = (
    <ReadyList state={state} onReview={setReview} />
  )
  const groups = (
    <CaseGroups state={state} nowMs={nowMs} phone={phone} open={openGroups} onToggle={toggleGroup} activeId={activeId} onOpen={p.onOpenCase} onChanged={p.onChanged} />
  )

  const foot = <FootLines handled={handled} onRoutines={() => p.onRoutines()} nowMs={nowMs} center={!phone} />

  const body =
    view === 'done' ? (
      <DoneView key={`done-${done!.k}`} {...p} done={done!} />
    ) : view === 'case' ? (
      <>
        <CaseView key={`case-${p.caseCard!.id}`} card={p.caseCard!} state={state} nowMs={nowMs} onChanged={p.onChanged} onDrawer={p.onDrawer} />
        <CaseFlow card={p.caseCard!} state={state} nowMs={nowMs} phone={phone} className={phone ? 'mt-10' : 'mt-14'} onDoc={() => p.onDrawer(p.caseCard!, 'evidence')} />
      </>
    ) : view === 'decide' ? (
      <>
        <Focus
          key={focusCard!.id}
          {...p}
          card={focusCard!}
          onReview={setReview}
          onCall={(live) => setCall(live ? focusCard : null)}
          doneText={done && done.card.id === focusCard!.id ? done.text : undefined}
        />
        <CaseFlow key={`flow-${focusCard!.id}`} card={focusCard!} state={state} nowMs={nowMs} phone={phone} className={phone ? 'mt-10' : 'mt-14'} onDoc={() => p.onDrawer(focusCard!, 'evidence')} />
      </>
    ) : (
      <AllClear state={state} nowMs={nowMs} handled={handled} onRoutines={p.onRoutines} onReview={setReview} onChanged={p.onChanged} go={p.go} onReveal={reveal} />
    )
  const sheet = (
    <ProposalSheet items={review} ctx={state} onClose={() => setReview(null)} onChanged={p.onChanged} />
  )

  if (phone) {
    return (
      <main className="mx-auto w-full max-w-[560px] px-7 pb-[220px] pt-5">
        {body}
        {view === 'decide' && handled > 0 && <div className="mt-14">{foot}</div>}
        <div ref={allRef} className="mt-16 scroll-mt-20">
          {allCases ? (
            <div className="fade space-y-8">
              <div className="flex items-baseline justify-between">
                <p className="t-small text-ink-3">All cases</p>
                <button onClick={() => setAllCases(false)} className="t-small cursor-pointer text-ink-3 hover:text-ink">hide</button>
              </div>
              {ready}
              {p.order.length > 0 && <QuietList {...p} onFocus={(id) => { p.onFocus(id); setAllCases(false); window.scrollTo(0, 0) }} />}
              {groups}
            </div>
          ) : (
            <button onClick={() => setAllCases(true)} className="t-small cursor-pointer text-ink-3 hover:text-ink">
              <span className="border-b border-hair">All cases</span>
            </button>
          )}
        </div>
        {sheet}
      </main>
    )
  }
  // Desktop: a very quiet list on the left, the decision in a 640 px column.
  return (
    <div className="relative flex-1">
      <div className="mx-auto grid w-full max-w-[1440px] grid-cols-[240px_minmax(0,640px)] gap-x-16 px-12 pb-32 pt-24 xl:grid-cols-[300px_640px] xl:gap-x-[124px] xl:px-24">
        <aside ref={asideRef} aria-label="Cases" className="sticky top-6 -mx-4 max-h-[calc(100vh-48px)] space-y-10 self-start overflow-y-auto px-4 pb-6 [scrollbar-width:thin]">
          {ready}
          {p.order.length > 0 && <QuietList {...p} card={p.caseCard ? null : p.card} />}
          {groups}
          {!phone && p.order.length > 0 && <p className="t-small text-ink-3"><kbd>⌘</kbd><kbd>↵</kbd> approve · <kbd>O</kbd> more options · <kbd>J</kbd> <kbd>K</kbd> move</p>}
        </aside>
        <main className="relative min-w-0">{body}</main>
      </div>
      {view !== 'clear' && view !== 'case' && <div className="fixed inset-x-0 bottom-8 z-10 flex justify-center">{foot}</div>}
      {sheet}
    </div>
  )
}

// ---------------------------------------------------------------- left list

/** Key badges are chrome: show them only to someone who has used the keyboard on this device. */
function useKeyboardUser(): boolean {
  const read = () => { try { return localStorage.getItem('ask-keys') === '1' } catch { return false } }
  const [on, setOn] = useState(read)
  useEffect(() => {
    if (on) return
    const onKey = (e: KeyboardEvent) => {
      if (/^[jkJK1-9]$/.test(e.key) && !(e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement)) {
        try { localStorage.setItem('ask-keys', '1') } catch { /* private mode: this session only */ }
        setOn(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [on])
  return on
}

/** Steps routines prepared in "Ask me" mode: they wait for a look, so they lead the list. */
function ReadyList({ state, onReview }: { state: State; onReview: Review }) {
  const live = useLive()
  const pending = pendingOf(live)
  if (!pending.length) return null
  // One row per routine: several steps of the same kind read as one thing to look at.
  const byRoutine: { id: string; name: string; items: typeof pending }[] = []
  for (const x of pending) {
    const g = byRoutine.find((r) => r.id === x.routine_id)
    if (g) g.items.push(x)
    else byRoutine.push({ id: x.routine_id, name: x.routine_name, items: [x] })
  }
  const title = (x: (typeof pending)[number]) => {
    const card = x.card_id ? state.cards.find((c) => c.id === x.card_id) : undefined
    return card ? shortTitle(card, state) : x.step.text || x.routine_name
  }
  return (
    <div>
      <p className="t-small mb-3 text-ink-3">
        Ready for your OK · {pending.length}
        {pending.length > 1 && (
          <button onClick={() => onReview(pending)} className="uline ml-2 cursor-pointer">review all</button>
        )}
      </p>
      <ul className="-mx-3.5">
        {byRoutine.map((g) => (
          <li key={g.id}>
            <button
              onClick={() => onReview(g.items)}
              className="t-small grid w-full cursor-pointer grid-cols-[8px_1fr] items-baseline gap-3.5 rounded-[10px] px-3.5 py-3 text-left text-ink-2 hover:text-ink focus-visible:outline-2 focus-visible:outline-accent"
            >
              <i className="h-[7px] w-[7px] -translate-y-px rounded-full bg-routine" />
              <span className="min-w-0">
                <span className="line-clamp-2">{g.name}</span>
                <span className="block text-ink-3">
                  {g.items.length === 1 ? title(g.items[0]) : `${g.items.length} prepared · ${title(g.items[0])} and more`}
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** The row a card sits in: look-alikes (same group, or supplier + kind + title shape) share one. */
const rowKey = (c: Card, state: State) =>
  c.group ?? `${c.supplier_id ?? c.supplier_name}|${c.kind}|${shortTitle(c, state).replace(/\d[\d,.]*/g, '#')}`

/** One entry per row, in list order: the row's first card and how many it holds. */
function rowsOf(order: Card[], state: State): { c: Card; n: number; key: string }[] {
  const seen = new Map<string, { c: Card; n: number; key: string }>()
  for (const c of order) {
    const key = rowKey(c, state)
    const e = seen.get(key)
    if (e) e.n += 1
    else seen.set(key, { c, n: 1, key })
  }
  return [...seen.values()]
}

function QuietList({ order, card, onFocus, state, nowMs }: Props) {
  // One line per problem: look-alikes fold into their first card.
  const rows = rowsOf(order, state)
  const cardKey = card ? rowKey(card, state) : null
  // Five by default; all when asked, or when the case in focus sits further down (j/k, links).
  const [all, setAll] = useState(false)
  const focusAt = rows.findIndex((r) => r.key === cardKey)
  // Once the focus opened the list, it stays open: collapsing it would make everything below jump.
  useEffect(() => { if (focusAt >= 5) setAll(true) }, [focusAt])
  const open = all || focusAt >= 5
  const shown = open ? rows : rows.slice(0, 5)
  const more = rows.slice(5).reduce((n, r) => n + r.n, 0)
  const listRef = useRef<HTMLUListElement>(null)
  useEffect(() => {
    listRef.current?.querySelector('[aria-current="true"]')?.scrollIntoView({ block: 'nearest' })
  }, [card?.id, open])
  return (
    <div>
      <p className="t-small mb-3 text-ink-3">Needs you</p>
      <ul ref={listRef} className="-mx-3.5">
        {shown.map(({ c, n }) => {
          const on = cardKey != null && rowKey(c, state) === cardKey
          return (
            <li key={c.id}>
              <button
                onClick={() => onFocus(c.id)}
                aria-current={on ? 'true' : undefined}
                className={cn(
                  't-small grid w-full cursor-pointer grid-cols-[8px_1fr_auto] items-baseline gap-3.5 rounded-[10px] px-3.5 py-3 text-left focus-visible:outline-2 focus-visible:outline-accent',
                  on ? 'bg-soft font-medium text-ink' : 'text-ink-2 hover:text-ink',
                )}
              >
                <i className={cn('h-[7px] w-[7px] -translate-y-px rounded-full', on ? 'bg-accent' : 'bg-ink-3')} />
                <span className="min-w-0">
                  <span className="line-clamp-2">{shortTitle(c, state)}</span>
                  {c.follow_up && <span className="block font-normal text-ink-3">half done</span>}
                  {n > 1 && <span className="block font-normal text-ink-3">{n} like this, one decision each</span>}
                </span>
                <span className="font-normal text-ink-3">{c.deadline ? when(c.deadline, nowMs) : ''}</span>
              </button>
            </li>
          )
        })}
      </ul>
      {more > 0 && (
        <button onClick={() => setAll(!open)} className="t-small mt-2 cursor-pointer text-ink-3 hover:text-ink">
          {open ? 'Show fewer' : `+${more} more`}
        </button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------- the folded groups

type GroupKey = 'waiting' | 'handled' | 'done'
const firstWord = (s?: string) => (s ?? '').split(' ').find((w) => !w.endsWith('.')) || ''

/**
 * Below "Needs you": what waits on others, what routines handled and what you decided. Folded
 * with a count; a row opens the case in focus (read-only once it is closed).
 */
function CaseGroups({
  state, nowMs, phone, open, onToggle, activeId, onOpen, onChanged,
}: {
  state: State; nowMs: number; phone: boolean; open: Set<GroupKey>; onToggle: (g: GroupKey) => void
  activeId: string | null; onOpen: (id: string) => void; onChanged: () => void
}) {
  const sec = useMemo(() => sections(state), [state])
  const row = { nowMs, activeId, onOpen }
  const groups: { key: GroupKey; title: string; n: number; routine?: boolean; rows: ReactNode }[] = [
    {
      key: 'waiting', title: 'Waiting on others', n: sec.waiting.length,
      rows: sec.waiting.map((c) => {
        // Where the sign-off link went (or that it reached nobody); Send again while it waits.
        const reached = signoffLine(c, nowMs)
        const again = c.status === 'escalated' && (signoffOpen(c) || !c.signoff_request)
        return (
          <CaseRow key={c.id} {...row} card={c} title={shortTitle(c, state)} dot="hollow"
            sub={reached ? [reached] : [`${c.status === 'escalated' ? 'Waiting for' : 'With'} ${firstWord(c.approver) || 'the approver'}`, c.ui?.at ? `since ${when(c.ui.at, nowMs)}` : '']}
            undo={again ? <SendAgain cardId={c.id} onSent={onChanged} /> : null} />
        )
      }),
    },
    {
      key: 'handled', title: 'Handled for you', n: sec.handled.length, routine: true,
      rows: sec.handled.map((h) => <HandledCase key={h.key} h={h} state={state} {...row} onChanged={onChanged} />),
    },
    {
      key: 'done', title: savedRole() === 'approver' ? 'Decided' : 'Decided by you', n: sec.done.length,
      rows: sec.done.map((c) => {
        // A sent email cannot be taken back, so no Undo for it.
        const undo = c.status !== 'sent' ? actionIdOf(state.activity.find((x) => x.card === c.id)) : null
        const phone = state.activity.some((x) => x.card === c.id && viaOf(x)?.kind === 'call') ? 'On the phone' : ''
        return (
          <CaseRow key={c.id} {...row} card={c} title={shortTitle(c, state)} dot="quiet"
            sub={signoffLine(c, nowMs) && c.signoff_request?.kind === 'escalation' ? [signoffLine(c, nowMs)!, phone] : [DONE_WORD[c.status] ?? 'Done', phone, c.ui?.at ? when(c.ui.at, nowMs) : '', signoffLine(c, nowMs) ?? '']}
            undo={undo ? <UndoAction actionId={undo} onUndone={onChanged} /> : null} />
        )
      }),
    },
  ]
  const shown = groups.filter((g) => g.n > 0)
  if (!shown.length) return null
  return (
    <div className={cn('space-y-5', phone && 'space-y-6')}>
      {shown.map((g) => {
        const on = open.has(g.key)
        return (
          <section key={g.key} id={`group-${g.key}`} className="scroll-mt-24">
            <button
              onClick={() => onToggle(g.key)}
              aria-expanded={on}
              className="t-small flex w-full cursor-pointer items-baseline gap-2 text-left text-ink-3 hover:text-ink focus-visible:outline-2 focus-visible:outline-accent"
            >
              <span className={cn(g.routine && 'dot-routine text-routine')}>{g.title} · {g.n}</span>
              <span className="border-b border-hair">{on ? 'hide' : 'show'}</span>
            </button>
            {on && <ul className="fade -mx-3.5 mt-2">{g.rows}</ul>}
          </section>
        )
      })}
    </div>
  )
}

type CaseRowBase = { nowMs: number; activeId: string | null; onOpen: (id: string) => void }

/** One quiet case line: dot, title, where it stands; a div with role=button so it can hold Undo. */
/** A quiet link on a waiting row: a fresh sign-off link to the approver (never sent on its own). */
function SendAgain({ cardId, onSent }: { cardId: string; onSent: () => void }) {
  const [state, setState] = useState<'idle' | 'busy' | 'failed'>('idle')
  const go = async () => {
    setState('busy')
    try { await resendSignoff(cardId); setState('idle'); onSent() } catch { setState('failed') }
  }
  return (
    <button onClick={() => void go()} disabled={state === 'busy'} className="uline cursor-pointer text-ink-3 hover:text-ink disabled:opacity-60">
      {state === 'busy' ? 'Sending…' : state === 'failed' ? 'Did not go out, try again' : 'Send again'}
    </button>
  )
}

function CaseRow({
  card, title, dot, sub, undo, routine, activeId, onOpen,
}: CaseRowBase & { card: Card; title: string; dot: 'hollow' | 'quiet' | 'routine'; sub: string[]; undo?: ReactNode; routine?: string }) {
  const on = activeId === card.id
  return (
    <li>
      <div
        role="button"
        tabIndex={0}
        onClick={() => onOpen(card.id)}
        onKeyDown={(e) => { if (e.key === 'Enter' && e.target === e.currentTarget) { e.preventDefault(); onOpen(card.id) } }}
        aria-current={on ? 'true' : undefined}
        className={cn(
          't-small grid w-full cursor-pointer grid-cols-[8px_1fr] items-baseline gap-3.5 rounded-[10px] px-3.5 py-2.5 text-left focus-visible:outline-2 focus-visible:outline-accent',
          on ? 'bg-soft text-ink' : 'text-ink-2 hover:text-ink',
        )}
      >
        <i aria-hidden="true" className={cn('h-[7px] w-[7px] -translate-y-px rounded-full', on ? 'bg-accent' : dot === 'routine' ? 'bg-routine' : 'bg-ground ring-1 ring-ink-3/50')} />
        <span className="min-w-0">
          <span className={cn('line-clamp-2', on && 'font-medium')}>{title}</span>
          <span className="flex flex-wrap items-center gap-x-2 text-ink-3">
            {routine && <span className="truncate text-routine">{routine}</span>}
            {sub.filter(Boolean).map((x, i) => <span key={i} className="flex items-center gap-x-2">{(i > 0 || routine) && <span aria-hidden="true">·</span>}{x}</span>)}
            {undo && <><span aria-hidden="true">·</span><span onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>{undo}</span></>}
          </span>
        </span>
      </div>
    </li>
  )
}

function HandledCase({ h, state, nowMs, activeId, onOpen, onChanged }: CaseRowBase & { h: Handled; state: State; onChanged: () => void }) {
  const undo = h.actionId && h.status !== 'sent' ? <UndoAction actionId={h.actionId} onUndone={onChanged} /> : null
  const who = h.routine ? `‘${h.routine}’` : 'A routine'
  const sub = [(h.status && DONE_WORD[h.status]) || 'Done', h.at ? when(h.at, nowMs) : '']
  if (h.card) {
    return <CaseRow card={h.card} title={shortTitle(h.card, state)} dot="routine" routine={who} sub={sub} undo={undo} nowMs={nowMs} activeId={activeId} onOpen={onOpen} />
  }
  // Routine work without a case on screen: shown, not opened.
  return (
    <li>
      <div className="t-small grid grid-cols-[8px_1fr] items-baseline gap-3.5 px-3.5 py-2.5 text-ink-2">
        <i aria-hidden="true" className="h-[7px] w-[7px] -translate-y-px rounded-full bg-routine" />
        <span className="min-w-0">
          <span className="line-clamp-2">{tidy(h.text)}</span>
          <span className="flex flex-wrap items-center gap-x-2 text-ink-3">
            <span className="truncate text-routine">{who}</span>
            {h.at && <><span aria-hidden="true">·</span>{when(h.at, nowMs)}</>}
            {undo && <><span aria-hidden="true">·</span>{undo}</>}
          </span>
        </span>
      </div>
    </li>
  )
}

// ---------------------------------------------------------------- one decision

function Focus(p: Props & {
  card: Card
  onReview: Review
  /** A call started or ended on this decision. */
  onCall: (live: boolean) => void
  /** Decided while a call is still live: shown in place of the buttons. */
  doneText?: string
}) {
  const { card, state, nowMs, phone } = p
  // What will happen: one of the card's options, passing it on, or dismissing it. The recommended
  // option is preselected; any "Or …" line takes its place.
  const [choice, setChoice] = useState<Choice>(() => defaultChoice(card))
  const sel = choice.kind === 'option' ? choice.i : -1
  const setSel = (i: number) => setChoice({ kind: 'option', i })
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [others, setOthers] = useState(false)
  const [note, setNote] = useState('')
  const [source, setSource] = useState<number | null>(null)
  // What the voice assistant prepared and reads out; "yes" or a tap confirms it.
  const [prepared, setPrepared] = useState<Prepared | null>(null)
  const [screenEvent, setScreenEvent] = useState<{ k: number; text: string } | null>(null)
  const [onCall, setOnCall] = useState(false)
  // An email to a supplier is shown before it goes out: it cannot be taken back.
  const [review, setReview] = useState(false)
  const [draft, setDraft] = useState('')
  const [mail, setMail] = useState<MailHead>({ subject: '', to: '', cc: [] })
  const [linesEl, setLinesEl] = useState<HTMLDivElement | null>(null)
  const anchors = useAnchors(card.id)
  const claim = claimOf(card)
  const today = forToday(card, state, nowMs)
  const opt = sel >= 0 ? card.options[sel] : undefined
  const recommended = sel >= 0 && sel === recommendedIndex(card)
  const money = opt ? costsMoney(card, sel) : false
  const emails = !!opt && opt.channel === 'email' && /@/.test(opt.to ?? '')
  const late = card.level === 'red'

  async function act(fn: () => Promise<{ text: string; sub: string; actionId: string | null }>) {
    setBusy(true); setError('')
    try {
      const out = await fn()
      p.onDone({ card, ...out })
      p.onChanged()
      setPrepared(null)
      // Decided with a tap while the assistant is on the line: tell it, so it does not decide again.
      if (onCall) setScreenEvent({ k: Date.now(), text: `The person decided on screen: ${out.text} It is done; do not call decide. Confirm in one short sentence and say goodbye.` })
    } catch (e) {
      setError(`That did not go through: ${(e as Error).message}. Nothing was sent.`)
    } finally { setBusy(false); setConfirm(false) }
  }
  const approve = (text?: string, head?: MailHead, force?: boolean) =>
    act(async () => {
      const edited = text != null && text.trim() !== (opt?.draft ?? '').trim()
      const changes = head
        ? {
            ...(head.subject.trim() && head.subject.trim() !== defaultSubject ? { subject: head.subject.trim() } : {}),
            ...(head.to && head.to !== (opt?.to ?? '') ? { to: head.to } : {}),
            ...(head.cc.length ? { cc: head.cc } : {}),
          }
        : {}
      const r = await post<{ action?: { id?: string; status?: string; to?: string; channels?: string[] } }>(
        `/api/cards/${encodeURIComponent(card.id)}/approve`,
        { option: sel, ...(edited ? { text } : {}), ...changes, ...(force ? { force: true } : {}) },
      )
      const status = r.action?.status
      const at = new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
      const sent = status === 'sent'
      const sub = sent && opt?.channel === 'floor'
        ? `Sent to ${opt.to} at ${at}. Their answer replaces the system's number.`
        : sent
        ? `Sent to ${head?.to && head.to !== opt?.to ? head.to : shortName(card.supplier_name) || opt?.to}${head?.cc.length ? `, copy to ${head.cc.length}` : ''} at ${at}. You'll see the reply here.`
        : opt?.channel === 'notify' && opt.to ? sentSub(opt.to, r.action?.channels) : 'Recorded.'
      // A sent email cannot be undone, so no Undo is offered for it.
      return { text: doneFrom(card, sel, status), sub, actionId: sent ? null : r.action?.id ?? null }
    })
  const defaultSubject = (opt as { subject?: string } | undefined)?.subject || headlineOf(card, state)
  const openReview = () => {
    setDraft(opt?.draft ?? '')
    setMail({ subject: defaultSubject, to: opt?.to ?? '', cc: [] })
    setReview(true)
  }

  // The voice assistant prepared a decision: show it. An email opens its review sheet.
  const onPrepared = (pr: Prepared | null) => {
    setPrepared(pr)
    if (!pr) { setReview(false); return }
    const o = pr.decision === 'approve' ? card.options[pr.option_index] : undefined
    if (!o) return
    setSel(pr.option_index); setOthers(false); setConfirm(false)
    if (o.channel === 'email' && /@/.test(o.to ?? '')) { setSource(null); setDraft(o.draft ?? ''); setReview(true) }
  }
  // show_on_screen from the assistant: the source side, the options or the email.
  const onScreen = (what: ScreenWhat): boolean => {
    if (what === 'options') { setSource(null); setReview(false); setOthers(true); return true }
    if (what === 'email') {
      if (!emails) return false
      setSource(null); openReview(); return true
    }
    const isSys = (e: Evidence) => SYS.test(e.source ?? '')
    const i = claim
      ? (what === 'system' ? claim.sys.index : claim.doc.index)
      : card.evidence.findIndex((e) => (what === 'system' ? isSys(e) : !isSys(e)))
    if (i < 0) return false
    setReview(false); setSource(i); return true
  }
  const voicePrepared = !!prepared && !p.doneText
  const emailPrepared = voicePrepared && prepared!.decision === 'approve' && emails && sel === prepared!.option_index
  // The person who signs is using this device: she approves herself instead of asking herself.
  const iSign = savedRole() === 'approver' && !!card.signoff && firstWord(card.signoff) === firstWord(state.launch?.money?.approver ?? '')
  const signer = card.signoff && !iSign ? firstWord(card.signoff) : ''
  const escalate = (text: string = note, option?: number) =>
    act(async () => {
      const r = await post<{ id?: string; channels?: string[] }>(`/api/cards/${encodeURIComponent(card.id)}/escalate`, { note: text, ...(option != null ? { option } : {}) })
      return { text: `Passed on to ${card.approver || 'the approver'}.`, sub: sentSub(signer || firstWord(card.approver ?? '') || 'the approver', r?.channels), actionId: r?.id ?? null }
    })
  const dismiss = () =>
    act(async () => {
      const r = await post<{ id?: string }>(`/api/cards/${encodeURIComponent(card.id)}/dismiss`)
      return { text: 'Taken off the list.', sub: 'Nothing was sent.', actionId: r?.id ?? null }
    })
  // Above the viewer's limit: the primary step is asking the person who signs, with our recommendation.
  const askSigner = () => {
    const o = choice.kind === 'option' ? card.options[choice.i] : null
    void escalate([o ? `Please approve: ${o.action}${o.cost ? ` (${o.cost})` : ''}.` : '', note].filter(Boolean).join(' '), choice.kind === 'option' ? choice.i : undefined)
  }
  const onApprove = () =>
    signer && choice.kind === 'option' ? askSigner() : emails ? openReview() : money ? setConfirm(true) : void approve()
  /** The primary button, following the choice. */
  const go = () => (choice.kind === 'escalate' ? void escalate() : choice.kind === 'dismiss' ? void dismiss() : onApprove())
  const choose = (c: Choice) => { setChoice(c); setOthers(false); setConfirm(false); setError('') }
  const choices: Choice[] = [
    // A half-done decision does not offer what was already done; indices stay the server's.
    ...card.options.flatMap((o, i): Choice[] => (o.done ? [] : [{ kind: 'option', i }])),
    { kind: 'escalate' }, { kind: 'dismiss' },
  ]
  const chosenKey = Math.max(0, choices.findIndex((c) => sameChoice(c, choice)))
  const keys = useKeyboardUser()

  // Keyboard (desktop): j/k walk, a asks, Enter approves, Esc cancels.
  const ref = useRef({ approve, confirm, busy, emails, openReview, go, choose, choices, choice })
  ref.current = { approve, confirm, busy, emails, openReview, go, choose, choices, choice }
  useEffect(() => {
    if (phone) return
    const onKey = (e: KeyboardEvent) => {
      if (typing(e.target) || source != null || review) return
      const k = e.key
      // ⌘↵ / Ctrl+↵: the main button, whatever it says now; a second press confirms money.
      if ((e.metaKey || e.ctrlKey) && k === 'Enter' && !ref.current.busy) {
        e.preventDefault()
        if (ref.current.confirm) void ref.current.approve()
        else ref.current.go()
        return
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return
      if (ref.current.confirm && k === 'Enter') { e.preventDefault(); void ref.current.approve() }
      else if (k === 'o' || k === 'O') { e.preventDefault(); setOthers((o) => !o) }
      else if (k === 'Escape') { setConfirm(false); setOthers(false) }
      else if ((k === 'a' || k === 'A') && !ref.current.busy) {
        e.preventDefault()
        if (ref.current.choice.kind === 'option' && !opt) return
        ref.current.go()
      }
      else if (/^[1-9]$/.test(k)) {
        const c = ref.current.choices[Number(k) - 1]
        if (c) { e.preventDefault(); ref.current.choose(c) }
      }
      else if (k === 'j' || k === 'k' || k === 'ArrowDown' || k === 'ArrowUp') {
        // Row by row, as the list shows them: a folded group of look-alikes is one step.
        const rows = rowsOf(p.order, state)
        const i = rows.findIndex((r) => r.key === rowKey(card, state))
        const n = rows[Math.min(rows.length - 1, Math.max(0, i + (k === 'j' || k === 'ArrowDown' ? 1 : -1)))]?.c
        if (n && n.id !== card.id) { e.preventDefault(); p.onFocus(n.id) }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [phone, source, review, opt, card.id, p])

  const decideBy = card.deadline ? (new Date(card.deadline).getTime() <= nowMs ? 'decide now' : `decide by ${when(card.deadline, nowMs)}`) : ''
  const meta = [card.supplier_name || card.part_name, decideBy].filter(Boolean).join(' · ')

  const primaryLabel =
    choice.kind === 'escalate' ? 'Pass it on' : choice.kind === 'dismiss' ? 'Dismiss'
      : signer ? `Ask ${signer} to approve` : emails ? 'Review email' : 'Approve'
  const quietPrimary = choice.kind === 'dismiss'
  const actions = (opt || choice.kind !== 'option') && (
    <div className={cn(phone ? 'fixed inset-x-0 bottom-0 z-20 bg-gradient-to-t from-ground from-[calc(100%-20px)] to-ground/0 px-6 pb-[max(28px,env(safe-area-inset-bottom))] pt-8' : 'mt-8')}>
      <div className={cn(phone && 'mx-auto max-w-[520px]')}>
        {signer && choice.kind === 'option' && (
          <p className={cn('t-small mb-3 text-ink-2', phone && 'text-center')}>
            {signer} signs this: it is above your limit in the approval policy. We send it with our recommendation.
          </p>
        )}
        {confirm && (
          <p className={cn('t-small mb-3 text-ink-2', phone && 'text-center')} role="alert">
            Approve: <span className="text-ink">{actionText(card, sel).replace(/\.$/, '')}</span> ({costLine(card, sel).replace(/\.$/, '')})
            {!phone && <span className="text-ink-3"> · <kbd>Enter</kbd> confirms</span>}
          </p>
        )}
        {error && <p className="t-small mb-3 text-late" role="alert">{error}</p>}
        <div className={cn(phone ? 'grid justify-items-center gap-3.5' : 'flex flex-wrap items-center gap-6', p.ringing && phone && 'gap-5')}>
          {p.doneText ? (
            <p className="t-body text-ink" role="status"><Check size={18} strokeWidth={1.75} className="mr-2 inline -translate-y-px" />Done. {p.doneText}</p>
          ) : <button
            onClick={confirm ? () => approve() : go}
            disabled={busy}
            className={cn(
              'inline-flex cursor-pointer items-center justify-center rounded-xl font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-60 t-body',
              // While the alert rings, Answer is the one blue button; dismissing is never blue.
              p.ringing || quietPrimary ? 'bg-soft text-ink hover:bg-hair' : 'bg-accent text-white shadow-[0_1px_2px_rgba(35,72,208,.25)] hover:bg-[#1d3db5]',
              phone ? 'h-[54px] w-full rounded-[14px]' : 'h-12 px-7',
            )}
          >
            {busy ? (choice.kind === 'dismiss' ? 'Dismissing…' : 'Sending…') : confirm ? 'Yes, approve' : primaryLabel}
            {!phone && keys && !busy && <span className="ml-3 text-[13px] font-normal opacity-70">⌘↵</span>}
          </button>}
          <div className={cn(
            'flex items-center',
            // Phone: two equal, quiet buttons under the main one, big enough for a thumb; the
            // other options first (they are about the decision), then talking it through.
            phone && !p.ringing
              ? 't-small grid w-full grid-cols-2 gap-2.5 [&>button]:h-11 [&>button]:w-full [&>button]:justify-center [&>button]:rounded-xl [&>button]:bg-soft [&>button]:text-ink [&>button:first-child]:order-2'
              : phone ? 't-small gap-7' : 't-body gap-6',
            p.ringing && 'order-first w-full flex-col gap-4 pb-2 lg:items-start',
          )}>
            <VoiceCall
              card={card}
              onChanged={p.onChanged}
              ringing={p.ringing}
              onRingingDone={p.onRingingDone}
              quiet
              linesEl={linesEl}
              onLive={(l) => { setOnCall(l); p.onCall(l) }}
              onPrepared={onPrepared}
              onScreen={onScreen}
              screenEvent={screenEvent}
            />
            {p.doneText ? null : confirm ? (
              <button onClick={() => setConfirm(false)} className="cursor-pointer text-ink-2 hover:text-ink">Cancel</button>
            ) : (
              <button onClick={() => setOthers(!others)} aria-expanded={others} className="cursor-pointer text-ink-2 hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent">
                {others ? 'Fewer options' : `Other options · ${choices.length - 1}`}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )

  return (
    <article className="rise" aria-label={headlineOf(card, state)}>
      <p className="t-small mb-4 text-ink-3">{meta}</p>
      <h1 className="t-big text-ink">{headlineOf(card, state)}</h1>
      {card.signoff_request?.answer?.decision === 'declined' && (
        <p className="t-body mt-4 text-ink" role="status">{signoffLine(card, nowMs)}. It is back with you.</p>
      )}

      <p className="t-body mt-6 text-ink-2">
        {claim ? (
          <ClaimLine claim={claim} late={late} open={source} onOpen={setSource} />
        ) : (
          <Cited text={summaryLine(card)} anchors={anchors.items} onOpen={setSource} open={source} />
        )}
      </p>
      {today && (
        <p className="t-body mt-4 text-ink-2">For today: <span className="font-medium text-ink">{today.bold}</span>{today.rest}</p>
      )}

      {(opt || choice.kind !== 'option') && (
        <div className={cn(phone ? 'mt-9' : 'mt-12')}>
          {card.follow_up && <HalfDone card={card} state={state} />}
          <p className="t-small mb-1.5 text-ink-3">
            {recommended ? 'We recommend' : 'You chose'}
            {!recommended && card.options.length > 0 && (
              <> · <button onClick={() => choose({ kind: 'option', i: recommendedIndex(card) })} className="uline">back to our recommendation</button></>
            )}
          </p>
          {opt ? (
            // The chosen option is itself clickable (same as the button) and shows its key, so the
            // numbers 1-9 line up with the options below.
            <button
              onClick={go}
              disabled={busy}
              className="group grid w-full cursor-pointer grid-cols-[minmax(0,1fr)_auto] items-baseline gap-4 text-left focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent"
            >
              <span>
                <span className="t-body block text-ink group-hover:underline group-hover:decoration-hair group-hover:underline-offset-4">
                  {actionText(card, sel)}{phone && <span className="text-ink-2"> {costLine(card, sel)}</span>}
                </span>
                {!phone && (
                  <span className="t-body block text-ink-2">{costLine(card, sel)}{opt.effect ? ` ${cap(tidy(opt.effect))}.`.replace(/\.\.$/, '.') : ''}</span>
                )}
              </span>
              {!phone && keys && choices.length > 1 && chosenKey < 9 && <kbd className="t-small">{chosenKey + 1}</kbd>}
            </button>
          ) : choice.kind === 'escalate' ? (
            <>
              <p className="t-body text-ink">Pass it on to {card.approver || 'the approver'}.</p>
              <p className="t-small mt-2 text-ink-3">They get this brief:</p>
              <p className="t-small mt-1 text-ink-2">{tidy(card.escalation_brief)}</p>
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Add one line (optional)"
                className="t-small mt-3 h-10 w-full rounded-lg bg-soft px-3 focus:outline-2 focus:outline-accent"
                aria-label="Note for the approver"
              />
            </>
          ) : (
            <>
              <p className="t-body text-ink">Dismiss: nothing to do.</p>
              <p className="t-body text-ink-2">It leaves your list. Nothing is sent.</p>
            </>
          )}
        </div>
      )}

      {others && (
        <div className="fade mt-8 space-y-1" aria-label="Other options">
          {choices.map((c, n) => {
            if (sameChoice(c, choice)) return null
            const label = c.kind === 'option' ? (
              <>{actionText(card, c.i).replace(/^./, (x) => x.toLowerCase())}{' '}<span className="t-small text-ink-3">{costLine(card, c.i)}</span></>
            ) : c.kind === 'escalate' ? <>pass it on to {card.approver || 'the approver'}</> : <>dismiss: nothing to do</>
            return (
              <button
                key={n}
                onClick={() => choose(c)}
                className="t-body grid w-full cursor-pointer grid-cols-[minmax(0,1fr)_auto] items-baseline gap-4 py-2 text-left text-ink-2 hover:text-ink focus-visible:outline-2 focus-visible:outline-accent"
              >
                <span><span className="text-ink-3">Or </span>{label}</span>
                {!phone && keys && n < 9 && <kbd className="t-small">{n + 1}</kbd>}
              </button>
            )
          })}
          <EvidenceLinks card={card} onDrawer={p.onDrawer} className="pt-2" />
        </div>
      )}

      {voicePrepared && !emailPrepared && (
        <div className="fade mt-10" role="status">
          <p className="t-small text-ink-3">On the call</p>
          <p className="t-body mt-1 text-ink">{prepared!.summary}</p>
          <p className="t-small mt-1 text-ink-2">Say ‘yes’ to go ahead{prepared!.decision === 'approve' && opt ? <>, or <button className="uline" onClick={onApprove}>approve here</button></> : null}.</p>
        </div>
      )}
      <div ref={setLinesEl} />
      {phone && <Automation state={state} card={card} onRoutines={p.onRoutines} onReview={p.onReview} onChanged={p.onChanged} className="mt-10" />}
      {actions}
      {!phone && <Automation state={state} card={card} onRoutines={p.onRoutines} onReview={p.onReview} onChanged={p.onChanged} className="mt-12" />}

      <SourceSheet card={card} claim={claim} index={source} anchors={anchors.items} ready={anchors.ready} onIndex={setSource} />
      {opt && (
        <EmailReview
          open={review}
          onOpenChange={setReview}
          cardId={card.id}
          head={mail}
          onHead={setMail}
          supplier={card.supplier_name ?? ''}
          draft={draft}
          onDraft={setDraft}
          cost={money ? costLine(card, sel).replace(/\.$/, '') : null}
          busy={busy}
          error={error}
          voice={emailPrepared}
          onSend={(force) => { setReview(false); void approve(draft, mail, force) }}
        />
      )}
    </article>
  )
}

/** "Half done: corrected the goods receipt at 05:40. Still 5,900 Collector's copies short." */
function HalfDone({ card, state }: { card: Card; state: State }) {
  const fu = card.follow_up!
  const done = tidy(fu.done).replace(/\.$/, '')
  const label = state.strip.find((s) => s.edition === card.edition)?.label
  return (
    <p className="t-body mb-6 text-ink-2">
      Half done: {done ? done[0].toLowerCase() + done.slice(1) : 'one step'}{fu.at ? ` at ${fmtTime(fu.at)}` : ''}.
      {fu.still_short ? ` Still ${fmtNum(fu.still_short)}${label ? ` ${label}` : ''} copies short.` : ''}
    </p>
  )
}

// ---------------------------------------------------------------- email review

/** Recipient, copy and subject of an email under review. */
interface MailHead { subject: string; to: string; cc: string[] }
interface Contact { email: string; name: string; source: string }

/** The supplier's known contacts (profile, people who replied): the only addresses a draft may use. */
function useContacts(cardId: string, open: boolean): Contact[] {
  const [list, setList] = useState<Contact[]>([])
  useEffect(() => {
    if (!open) return
    let live = true
    fetch(`/api/cards/${encodeURIComponent(cardId)}/contacts`)
      .then((r) => (r.ok ? r.json() : { contacts: [] }))
      .then((j) => live && setList(j.contacts ?? []))
      .catch(() => {})
    return () => { live = false }
  }, [cardId, open])
  return list
}

interface Sent { at: string; kind: string; to: string; subject: string; signed?: string | null }
/** Emails that already went to this supplier about this card, from the app or by hand. */
function useRecentSends(cardId: string, open: boolean): Sent[] {
  const [list, setList] = useState<Sent[]>([])
  useEffect(() => {
    if (!open) return
    let live = true
    fetch(`/api/cards/${encodeURIComponent(cardId)}/recent_sends`)
      .then((r) => (r.ok ? r.json() : { sends: [] }))
      .then((j) => live && setList(j.sends ?? []))
      .catch(() => {})
    return () => { live = false }
  }, [cardId, open])
  return list
}

function EmailReview({
  open, onOpenChange, cardId, head, onHead, supplier, draft, onDraft, cost, busy, error, onSend, voice,
}: {
  /** Prepared on a live call: a spoken yes works as well as the button. */
  voice?: boolean
  open: boolean; onOpenChange: (o: boolean) => void; cardId: string; head: MailHead; onHead: (h: MailHead) => void
  supplier: string
  draft: string; onDraft: (t: string) => void; cost: string | null; busy: boolean; error: string; onSend: (force?: boolean) => void
}) {
  const contacts = useContacts(cardId, open)
  const earlier = useRecentSends(cardId, open)
  const last = earlier[earlier.length - 1]
  const label = (c: Contact) => (c.name ? `${c.name} · ${c.email}` : c.email)
  const toContact = contacts.find((c) => c.email === head.to)
  const others = contacts.filter((c) => c.email !== head.to)
  const [copying, setCopying] = useState(false)
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fade fixed inset-0 z-40 bg-[rgba(24,24,26,.28)]" />
        <Dialog.Content
          aria-describedby={undefined}
          onKeyDown={(e) => {
            // ⌘↵ sends, as in any mail program (the same check as the button).
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && !busy && draft.trim()) { e.preventDefault(); onSend(!!last) }
          }}
          className={cn(
            'sheet-up fixed z-50 overflow-y-auto bg-ground focus:outline-none',
            'inset-x-0 bottom-0 max-h-[92vh] rounded-t-[22px] px-6 pb-[max(28px,env(safe-area-inset-bottom))] pt-3 shadow-[0_-10px_40px_-16px_rgba(0,0,0,.25)]',
            'md:inset-x-auto md:bottom-auto md:left-1/2 md:top-[8vh] md:max-h-[84vh] md:w-[640px] md:-translate-x-1/2 md:rounded-2xl md:px-8 md:pb-8 md:pt-7 md:shadow-[0_20px_60px_-20px_rgba(0,0,0,.3)]',
          )}
        >
          <div className="mx-auto mb-5 h-[5px] w-9 rounded-full bg-hair md:hidden" aria-hidden="true" />
          <Dialog.Title className="t-small font-normal text-ink-3">{voice ? 'Say ‘yes’ or tap Send. Nothing goes out before.' : 'This email goes out when you press Send'}</Dialog.Title>
          <div className="t-small mt-4 grid grid-cols-[64px_minmax(0,1fr)] items-baseline gap-y-3 text-ink-2">
            <label htmlFor="email-to" className="text-ink-3">To</label>
            <div className="min-w-0">
              {contacts.length > 1 ? (
                <select
                  id="email-to"
                  value={head.to}
                  onChange={(e) => onHead({ ...head, to: e.target.value, cc: head.cc.filter((c) => c !== e.target.value) })}
                  className="w-full max-w-full cursor-pointer truncate rounded-lg bg-soft px-2 py-1.5 text-ink focus:outline-2 focus:outline-accent"
                >
                  {contacts.map((c) => <option key={c.email} value={c.email}>{label(c)}</option>)}
                </select>
              ) : (
                <span id="email-to" className="break-words text-ink">
                  {toContact?.name || supplier ? `${toContact?.name || supplier} · ` : ''}<span className="text-ink-2">{head.to}</span>
                </span>
              )}
              {toContact && <span className="mt-0.5 block text-ink-3">Known from the {toContact.source}</span>}
            </div>
            {(head.cc.length > 0 || copying) && (
              <>
                <span className="text-ink-3">Copy</span>
                <div className="flex min-w-0 flex-wrap gap-2">
                  {others.map((c) => {
                    const on = head.cc.includes(c.email)
                    return (
                      <button
                        key={c.email}
                        type="button"
                        aria-pressed={on}
                        onClick={() => onHead({ ...head, cc: on ? head.cc.filter((x) => x !== c.email) : [...head.cc, c.email] })}
                        className={cn('cursor-pointer rounded-full px-3 py-1', on ? 'bg-ink text-white' : 'bg-soft text-ink-2 hover:text-ink')}
                        title={`Known from the ${c.source}`}
                      >
                        {c.name || c.email}
                      </button>
                    )
                  })}
                </div>
              </>
            )}
            <label htmlFor="email-subject" className="text-ink-3">Subject</label>
            <input
              id="email-subject"
              value={head.subject}
              onChange={(e) => onHead({ ...head, subject: e.target.value })}
              className="w-full min-w-0 rounded-lg bg-soft px-2 py-1.5 text-ink focus:outline-2 focus:outline-accent"
            />
          </div>
          {!copying && head.cc.length === 0 && (
            <p className="t-small mt-2 pl-[64px] text-ink-3">
              {others.length > 0 ? (
                <button type="button" className="uline cursor-pointer" onClick={() => setCopying(true)}>Add a copy</button>
              ) : (
                'Only known contacts of this supplier can receive it.'
              )}
            </p>
          )}
          <label htmlFor="email-draft" className="sr-only">Email text</label>
          <textarea
            id="email-draft"
            value={draft}
            onChange={(e) => onDraft(e.target.value)}
            rows={10}
            className="t-body mt-4 w-full resize-y rounded-xl bg-soft p-4 leading-relaxed text-ink focus:outline-2 focus:outline-accent"
          />
          {last && (
            <p className="t-small mt-3 text-ink" role="alert">
              Already sent {last.kind === 'sent_outside' ? 'from the shared mailbox' : 'from here'} at{' '}
              {new Date(last.at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}
              {last.signed ? ` by ${last.signed}` : ''}: <span className="text-ink-2">“{last.subject}”</span>.
              {earlier.length > 1 ? ` ${earlier.length} emails on this case lately.` : ''} Sending again may order twice.
            </p>
          )}
          {cost && <p className="t-small mt-3 text-ink-2">This commits money: <span className="text-ink">{cost}</span>.</p>}
          {error && <p className="t-small mt-3 text-late" role="alert">{error}</p>}
          <div className="mt-6 flex items-center gap-6">
            <button
              onClick={() => onSend(!!last)}
              disabled={busy || !draft.trim()}
              className="t-body inline-flex h-12 cursor-pointer items-center justify-center rounded-xl bg-accent px-7 font-medium text-white hover:bg-[#1d3db5] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-60"
            >
              {busy ? 'Sending…' : last ? 'Send anyway' : 'Send email'}<span className="ml-3 hidden text-[13px] font-normal opacity-70 md:inline">⌘↵</span>
            </button>
            <Dialog.Close className="t-body cursor-pointer text-ink-2 hover:text-ink">Cancel</Dialog.Close>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

const cap = (t: string) => (t ? t[0].toUpperCase() + t.slice(1) : t)

function doneFrom(card: Card, i: number, status?: string): string {
  const a = actionText(card, i)
  const m = a.match(/^(\w+)\s+(.*)$/)
  const PAST: Record<string, string> = {
    ask: 'Asked', tell: 'Told', order: 'Ordered', send: 'Sent', book: 'Booked', collect: 'Collected', call: 'Called',
    move: 'Moved', release: 'Released', request: 'Requested', confirm: 'Confirmed', correct: 'Corrected', pay: 'Paid',
    accept: 'Accepted', cancel: 'Cancelled', expedite: 'Expedited', reserve: 'Reserved', notify: 'Notified', fly: 'Flew',
  }
  const past = m && PAST[m[1].toLowerCase()]
  // Short names and no order numbers: "Asked PackRight to make the order to rev C."
  const calm = (t: string) =>
    (card.supplier_name ? t.split(card.supplier_name).join(shortName(card.supplier_name)) : t).replace(/\bPO \d{6,}/g, 'the order')
  if (past) return calm(`${past} ${m![2]}`)
  return calm(status === 'sent' ? `Sent: ${a}` : `Approved: ${a}`)
}

const DbIcon = () => <Database aria-hidden="true" />
const DocIcon = () => <FileText aria-hidden="true" />

/** A source link inside a sentence: a span with button semantics, because a <button> cannot
 * wrap across lines like text (a long value would break out and leave the full stop alone). */
function Cite({ className, expanded, onClick, children }: { className?: string; expanded: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <span
      role="button"
      tabIndex={0}
      className={cn('cite', className)}
      aria-expanded={expanded}
      aria-haspopup="dialog"
      onClick={onClick}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick() } }}
    >
      {children}
    </span>
  )
}

function ClaimLine({ claim, late, open, onOpen }: { claim: Claim; late: boolean; open: number | null; onOpen: (i: number) => void }) {
  return (
    <>
      {claim.pre}
      <Cite expanded={open === claim.sys.index} onClick={() => onOpen(claim.sys.index)}>
        {claim.sys.value}<DbIcon />
      </Cite>
      {claim.mid}
      <Cite className={late ? 'late' : undefined} expanded={open === claim.doc.index} onClick={() => onOpen(claim.doc.index)}>
        {claim.doc.value}<DocIcon />
      </Cite>
      {claim.post}
    </>
  )
}

/** A sentence whose numbers, when found in a source, open that source. */
function Cited({ text, anchors, onOpen, open }: { text: string; anchors: Record<number, Anchor[]> | null; onOpen: (i: number) => void; open: number | null }) {
  if (!text) return null
  const flat = Object.entries(anchors ?? {}).flatMap(([i, as]) => as.map((a) => ({ i: Number(i), a })))
  const parts: ReactNode[] = []
  let last = 0
  for (const m of text.matchAll(NUM_OR_ID)) {
    const hit = flat.find((x) => x.a.matched.includes(normTok(m[0])))
    if (!hit || !/\d/.test(m[0])) continue
    parts.push(text.slice(last, m.index))
    parts.push(
      <Cite key={m.index} expanded={open === hit.i} onClick={() => onOpen(hit.i)}>
        {m[0]}
      </Cite>,
    )
    last = (m.index ?? 0) + m[0].length
  }
  parts.push(text.slice(last))
  return <>{parts}</>
}

// ---------------------------------------------------------------- the source sheet

const SYS = /system|sap|erp/i
function sideLabel(card: Card, ev: Evidence): { who: string; isSys: boolean } {
  const isSys = SYS.test(ev.source ?? '')
  if (isSys) return { who: /\bSAP\b/.test(ev.source ?? '') ? 'SAP says' : 'The system says', isSys }
  const kind = docKind(ev.source) || 'the document'
  const owner = ev.doc?.startsWith('suppliers/') && card.supplier_name ? `${shortName(card.supplier_name)}'s ` : 'The '
  return { who: `${owner}${kind} says`.replace(/^The the/, 'The'), isSys }
}
const where = (a?: Anchor, ev?: Evidence) =>
  a ? (a.kind === 'table' || a.kind === 'text' ? `row ${(a.row ?? 0) + 1}` : `page ${a.page ?? 1}`) : ev?.page ? `page ${ev.page}` : ''

function SourceSheet({
  card, claim, index, anchors, ready, onIndex,
}: { card: Card; claim: Claim | null; index: number | null; anchors: Record<number, Anchor[]> | null; ready: boolean; onIndex: (i: number | null) => void }) {
  const ev = index != null ? card.evidence[index] : null
  const a = index != null ? anchors?.[index]?.[0] : undefined
  const otherIndex = claim && index != null ? (index === claim.sys.index ? claim.doc.index : index === claim.doc.index ? claim.sys.index : null) : null
  const other = otherIndex != null ? card.evidence[otherIndex] : null
  const otherA = otherIndex != null ? anchors?.[otherIndex]?.[0] : undefined
  const label = ev ? sideLabel(card, ev) : null
  const lang = ev ? languageOf(ev.source, ev.language) : null
  const head = ev
    ? [label!.isSys ? (/\bSAP\b/.test(ev.source ?? '') ? 'SAP' : 'System') : card.supplier_name, [docKind(ev.source), where(a, ev)].filter(Boolean).join(', '), lang]
        .filter(Boolean).join(' · ')
    : ''
  const english = ev?.translation ? ev.translation.split(' = ').pop() : null
  const original = a ? fileUrl(a) : ev?.doc ? `/api/file?path=${encodeURIComponent(ev.doc)}${ev.page ? `#page=${ev.page}` : ''}` : null

  return (
    <Dialog.Root open={index != null} onOpenChange={(o) => !o && onIndex(null)}>
      <Dialog.Portal>
        <Dialog.Overlay className="fade fixed inset-0 z-40 bg-[rgba(24,24,26,.28)]" />
        <Dialog.Content
          aria-describedby={undefined}
          className={cn(
            'sheet-up fixed z-50 overflow-y-auto bg-ground focus:outline-none',
            'inset-x-0 bottom-0 max-h-[88vh] rounded-t-[22px] px-6 pb-[max(36px,env(safe-area-inset-bottom))] pt-3 shadow-[0_-10px_40px_-16px_rgba(0,0,0,.25)]',
            'md:inset-x-auto md:bottom-auto md:left-1/2 md:top-[8vh] md:max-h-[84vh] md:w-[640px] md:-translate-x-1/2 md:rounded-2xl md:px-8 md:pb-8 md:pt-7 md:shadow-[0_20px_60px_-20px_rgba(0,0,0,.3)]',
          )}
        >
          <div className="mx-auto mb-5 h-[5px] w-9 rounded-full bg-hair md:hidden" aria-hidden="true" />
          {ev && (
            <>
              <Dialog.Title className="t-small font-normal text-ink-3">{head}</Dialog.Title>
              <div className="mt-3 overflow-hidden rounded-xl bg-soft p-3">
                {a ? (
                  <AnchorCrop key={`${index}-${a.doc}`} anchor={a} />
                ) : !ready ? (
                  <p className="t-small p-2 text-ink-3">Finding it in the documents…</p>
                ) : (
                  <p className="t-small whitespace-pre-wrap p-2 leading-[1.9] text-ink-2"><Marked text={tidy(ev.quote) || String(ev.value ?? '')} value={String(ev.value ?? '')} /></p>
                )}
              </div>
              {english ? (
                <p className="t-body mt-4 text-ink">{english}</p>
              ) : a && ev.quote ? (
                <p className="t-small mt-3 text-ink-3">{tidy(ev.quote)}</p>
              ) : null}

              {other && (
                <button
                  onClick={() => onIndex(otherIndex)}
                  className="t-body mt-7 grid w-full cursor-pointer grid-cols-[20px_1fr] gap-x-2.5 text-left text-ink-2 hover:text-ink focus-visible:outline-2 focus-visible:outline-accent"
                >
                  <span className="mt-1 text-ink-3">{sideLabel(card, other).isSys ? <Database size={16} /> : <FileText size={16} />}</span>
                  <span>
                    <span className="font-medium text-ink">{sideLabel(card, other).who} {splitValue(other.value).big}</span>{' '}
                    {splitValue(other.value).rest}
                  </span>
                  <span className="t-small col-start-2 text-ink-3">{cap([docKind(other.source), where(otherA, other)].filter(Boolean).join(', '))}</span>
                </button>
              )}

              <div className="mt-7 flex items-center gap-4">
                {original && (
                  <a href={original} target="_blank" rel="noreferrer" className="t-small shrink-0 text-accent hover:underline">Open original</a>
                )}
                <Dialog.Close className="t-body h-[52px] flex-1 cursor-pointer rounded-[14px] bg-soft font-medium text-ink hover:bg-hair focus-visible:outline-2 focus-visible:outline-accent">
                  Back
                </Dialog.Close>
              </div>
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

/** The quote with the value's numbers highlighted (when no page or row was found). */
function Marked({ text, value }: { text: string; value: string }) {
  const nums = new Set((value.match(/\d[\d,.]*\d|\d/g) ?? []).map(normTok))
  const parts: ReactNode[] = []
  let last = 0
  for (const m of text.matchAll(/\d[\d,.]*\d|\d/g)) {
    if (!nums.has(normTok(m[0]))) continue
    parts.push(text.slice(last, m.index), <mark key={m.index} className="rounded-[3px] bg-mark px-1 font-semibold text-[#111]">{m[0]}</mark>)
    last = (m.index ?? 0) + m[0].length
  }
  parts.push(text.slice(last))
  return <>{parts}</>
}

// ---------------------------------------------------------------- done

function DoneView(p: Props & { done: DoneInfo }) {
  const { done, state, phone } = p
  const live = useLive()
  const outcome = done.actionId ? live.undo[done.actionId] : undefined
  const [undoing, setUndoing] = useState(false)
  const next = p.order.find((c) => c.id !== done.card.id) ?? null
  const offer = done.offer > 0 && !outcome?.ok
  // No look-alikes open, but the same call made again: offer it as a routine on its own.
  const repeat = !offer && !outcome?.ok && done.repeat && done.repeat.n >= 2 ? done.repeat : null
  const who = shortName(done.card.supplier_name)

  const undo = async () => {
    if (!done.actionId) return
    setUndoing(true)
    const r = await undoAction(done.actionId)
    setUndoing(false)
    if (r.ok) p.onChanged()
  }

  const primary = offer || repeat
    ? { label: 'Show me how', onClick: () => { p.onClearDone(); p.onRoutines({ cardId: done.card.id, k: Date.now() }) } }
    : next
      ? { label: 'Next decision', onClick: () => { p.onFocus(next.id); p.onClearDone() } }
      : null
  const quiet = offer || repeat ? { label: 'Not now', onClick: p.onClearDone } : null

  return (
    <section className="rise" aria-live="polite">
      <div className="mb-6 grid h-10 w-10 place-items-center rounded-full bg-soft text-ink"><Check size={20} strokeWidth={1.75} /></div>
      <h1 className="t-big text-ink">{outcome?.ok ? 'Undone.' : `Done. ${done.text}`}</h1>
      <p className="t-body mt-6 text-ink-2">
        {outcome?.ok ? 'It is back on your list.' : done.sub}{' '}
        {done.actionId && !outcome && (
          <button onClick={undo} disabled={undoing} className="uline">{undoing ? 'Undoing…' : 'Undo'}</button>
        )}
        {outcome && !outcome.ok && <span className="t-small block pt-2 text-ink-3">Undo: {outcome.reason}</span>}
      </p>

      <div className="h-14" />
      {offer ? (
        <div>
          <p className="t-body text-ink">
            {plural(done.offer, 'more like this', 'more like this')}{who ? ` from ${who}` : ''}. Handle them the same way?
          </p>
          <p className="t-small dot-routine mt-7 text-routine">You'll see a test run first. It starts by asking: it prepares them, you send with one tap.</p>
        </div>
      ) : repeat ? (
        <div>
          <p className="t-body text-ink">
            That's the {ordinal(repeat.n)} time you {repeat.words}. Handle it like this from now on?
          </p>
          <p className="t-small dot-routine mt-7 text-routine">It will prepare them; you send with one tap.</p>
        </div>
      ) : next ? (
        <p className="t-body text-ink-2">Next: <span className="text-ink">{headlineOf(next, state)}</span></p>
      ) : (
        <p className="t-body text-ink-2">Nothing else needs you. <button className="uline" onClick={p.onClearDone}>See the day</button></p>
      )}

      {(primary || quiet) && (
        <div className={cn(phone ? 'fixed inset-x-0 bottom-0 z-20 bg-gradient-to-t from-ground from-[calc(100%-20px)] to-ground/0 px-6 pb-[max(28px,env(safe-area-inset-bottom))] pt-8' : 'mt-10')}>
          <div className={cn(phone ? 'mx-auto grid max-w-[520px] justify-items-center gap-3.5' : 'flex items-center gap-6')}>
            {primary && (
              <button
                onClick={primary.onClick}
                autoFocus={!phone}
                className={cn(
                  't-body inline-flex cursor-pointer items-center justify-center rounded-xl bg-accent font-medium text-white shadow-[0_1px_2px_rgba(35,72,208,.25)] hover:bg-[#1d3db5] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                  phone ? 'h-[54px] w-full rounded-[14px]' : 'h-12 px-7',
                )}
              >
                {primary.label}
              </button>
            )}
            {quiet && (
              <button onClick={quiet.onClick} className={cn('cursor-pointer text-ink-2 hover:text-ink', phone ? 't-small' : 't-body')}>{quiet.label}</button>
            )}
          </div>
        </div>
      )}
    </section>
  )
}

// ---------------------------------------------------------------- all clear

function AllClear({
  state, nowMs, handled, onRoutines, onReview, onChanged, go, onReveal,
}: { state: State; nowMs: number; handled: number; onRoutines: (r?: { cardId: string; k: number }) => void; onReview: Review; onChanged: () => void; go: (p: string) => void; onReveal: (g: GroupKey) => void }) {
  const waiting = state.cards.filter((c) => (c.status === 'open' && c.routed_to_other) || c.status === 'escalated').length
  const live = useLive()
  const pending = pendingOf(live)
  const suggestion = live.suggestions[0] ?? null
  return (
    <section className="rise pt-[16vh] md:pt-0">
      {pending.length > 0 ? (
        // Prepared by routines in "Ask me" mode: nothing goes out without a look, so this is the
        // decision now, not a footnote.
        <>
          <h1 className="t-big text-ink">
            {pending.length === 1 ? 'One step is' : `${pending.length} steps are`} ready for your OK.
          </h1>
          <p className="t-body mt-6 text-ink-2">
            Your routines prepared {pending.length === 1 ? 'it' : 'them'}. Nothing goes out before you look. {launchStatus(state, nowMs)}
          </p>
          <button
            onClick={() => onReview(pending)}
            className="t-body mt-8 inline-flex h-12 cursor-pointer items-center justify-center rounded-xl bg-accent px-7 font-medium text-white hover:bg-[#1d3db5] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            Review {pending.length === 1 ? 'it' : `all ${pending.length}`}
          </button>
        </>
      ) : (
        <>
          <h1 className="t-big text-ink">Nothing needs you.</h1>
          <p className="t-body mt-6 text-ink-2">{launchStatus(state, nowMs)}</p>
        </>
      )}
      <div className="t-small mt-10 grid gap-3.5 text-ink-2">
        <span>Watching {plural(state.shipments.length, 'shipment')}<button className="uline ml-1.5" onClick={() => go('/suppliers')}>see</button></span>
        {waiting > 0 && <span>{plural(waiting, 'decision')} with others<button className="uline ml-1.5" onClick={() => onReveal('waiting')}>see</button></span>}
        {handled > 0 && (
          <RoutineLine links={[{ label: 'see', onClick: () => onRoutines() }, { label: 'undo', onClick: () => onReveal('handled') }]}>
            {handled} handled by your routines {new Date(nowMs).getHours() < 11 ? 'overnight' : 'today'}
          </RoutineLine>
        )}
        {/* One routine waiting: its own line below names it. Several: one count. */}
        {pending.length === 0 && <Automation state={state} onRoutines={onRoutines} onReview={onReview} onChanged={onChanged} className="grid gap-3.5" />}
        {suggestion && (
          <RoutineLine links={[{ label: 'make it a routine', onClick: () => onRoutines({ cardId: suggestion.last_card_id, k: Date.now() }) }]}>
            You did ‘{suggestionLabel(suggestion, state)}’ {suggestion.count} times
          </RoutineLine>
        )}
      </div>
      {state.last_check_at && <p className="t-small mt-16 text-ink-3">Last checked the portal at {when(state.last_check_at, nowMs)}.</p>}
    </section>
  )
}

/**
 * Automation where the decision happens, one quiet line each: what a routine would do with the
 * focused decision, what routines prepared and wait to send, and routines drafted on a call.
 */
function Automation({
  state, card, onRoutines, onReview, onChanged, className,
}: { state: State; card?: Card; onRoutines: (r?: { cardId: string; k: number }) => void; onReview: Review; onChanged: () => void; className?: string }) {
  const live = useLive()
  const pending = pendingOf(live)
  const mine = card ? pending.filter((x) => x.card_id === card.id) : []
  const others = card ? pending.filter((x) => x.card_id !== card.id) : []
  const asking = new Set(mine.map((x) => x.routine_id))
  const would = card && card.status === 'open' ? (live.would[card.id] ?? []).filter((w) => !asking.has(w.routine_id)) : []
  const drafts = live.drafts
  // On All clear several routines fold into the count line above; a single one is named here.
  const all = groupProposals(card ? others : pending, state)
  const groups = !card && all.length > 1 ? [] : all
  if (!mine.length && !would.length && !groups.length && !drafts.length) return null
  return (
    <div className={cn('grid gap-3', className)}>
      {mine.length > 0 && (
        <RoutineLine links={[{ label: 'review', onClick: () => onReview(mine) }]}>
          Your routine ‘{mine[0].routine_name}’ prepared {mine.length === 1 ? 'this for you' : `${mine.length} steps for this`}
        </RoutineLine>
      )}
      {would.slice(0, 1).map((w) => <WouldLine key={w.routine_id} w={w} onChanged={onChanged} />)}
      <ProposalLines groups={groups} onReview={onReview} max={card ? 1 : 2} />
      {drafts.length > 0 && (
        <RoutineLine links={[{ label: 'review', onClick: () => onRoutines() }]}>
          {drafts.length === 1 ? <>Drafted from your call: ‘{drafts[0].routine.name}’</> : <>{drafts.length} routines drafted from your calls</>}
        </RoutineLine>
      )}
    </div>
  )
}

function FootLines({ handled, onRoutines, nowMs, center }: { handled: number; onRoutines: () => void; nowMs: number; center: boolean }) {
  if (!handled) return null
  return (
    <div className={cn('flex', center && 'justify-center')}>
      <RoutineLine links={[{ label: 'see', onClick: onRoutines }]}>
        {handled} handled by your routines {new Date(nowMs).getHours() < 11 ? 'overnight' : 'today'}
      </RoutineLine>
    </div>
  )
}

/** The short label of a suggestion: the latest case's list title, else its headline. */
function suggestionLabel(sg: { last_card_id: string; label?: string | null }, state: State): string {
  const c = state.cards.find((x) => x.id === sg.last_card_id)
  const t = c ? shortTitle(c, state) : tidy(sg.label).replace(/\.$/, '')
  return t.length > 60 ? `${t.slice(0, 57).replace(/\s+\S*$/, '')}…` : t
}

/** Everything behind a case, one tap away in the side drawer. */
function EvidenceLinks({ card, onDrawer, className }: { card: Card; onDrawer: Drawer; className?: string }) {
  if (!card.evidence.length && !card.trail.length) return null
  return (
    <p className={cn('t-small flex flex-wrap gap-x-5 gap-y-2 text-ink-3', className)}>
      {card.evidence.length > 0 && (
        <button onClick={() => onDrawer(card, 'evidence')} className="cursor-pointer hover:text-ink"><span className="border-b border-hair">All evidence · {card.evidence.length}</span></button>
      )}
      {card.trail.length > 0 && (
        <button onClick={() => onDrawer(card, 'trail')} className="cursor-pointer hover:text-ink"><span className="border-b border-hair">How we know</span></button>
      )}
    </p>
  )
}

// ---------------------------------------------------------------- a case that is not open any more

/** A decided or passed-on case, opened from the list or Launch: where it stands, why, and Undo where it works. */
function CaseView({ card, state, nowMs, onChanged, onDrawer }: { card: Card; state: State; nowMs: number; onChanged: () => void; onDrawer: Drawer }) {
  const [source, setSource] = useState<number | null>(null)
  const anchors = useAnchors(card.id)
  const claim = claimOf(card)
  const routine = routineOf(state, card.id)
  const waiting = card.status === 'escalated'
  const actionId = routine ? routineActionOf(state, card.id) : actionIdOf(state.activity.find((a) => a.card === card.id))
  const undo = !waiting && card.status !== 'sent' && card.status !== 'answered' ? actionId : null
  const facts = replyFacts(card)
  const who = (card.approver ?? '').split(' ').find((w) => !w.endsWith('.')) || 'the approver'
  const at = card.ui?.at ? ` at ${when(card.ui.at, nowMs)}` : ''
  // Approved by the person it was passed on to, through their sign-off link.
  const signed = card.signoff_request?.kind === 'escalation' && card.signoff_request.answer?.decision === 'approved'
  return (
    <article className="rise" aria-label={headlineOf(card, state)}>
      <p className="t-small mb-4 text-ink-3">{card.supplier_name || card.part_name || 'Soony'}</p>
      <h1 className="t-big text-ink">{headlineOf(card, state)}</h1>

      <p className="t-body mt-6 flex flex-wrap items-baseline gap-x-2 text-ink-2">
        {waiting ? (
          <span className="text-ink">
            Waiting for {who}{card.ui?.at ? ` since ${when(card.ui.at, nowMs)}` : ''}.
            {signoffLine(card, nowMs) && <span className="text-ink-2"> {signoffLine(card, nowMs)}.</span>}
            {(signoffOpen(card) || !card.signoff_request) && <> <SendAgain cardId={card.id} onSent={onChanged} /></>}
          </span>
        ) : (
          <>
            <Check size={17} strokeWidth={1.75} className="translate-y-0.5 self-center text-ink" aria-hidden="true" />
            <span className="text-ink">{DONE_WORD[card.status] ?? 'Done'}{at}.</span>
            {routine ? <span className="t-small dot-routine text-routine">By your routine ‘{routine}’</span>
              : signed ? <span className="t-small text-ink-3">{signoffLine(card, nowMs)}</span>
              : <span className="t-small text-ink-3">By you{card.signoff_request ? ` · ${signoffLine(card, nowMs)}` : ''}</span>}
          </>
        )}
        {undo && <UndoAction actionId={undo} onUndone={onChanged} />}
      </p>
      {card.commitment_line && <p className="t-small mt-2 text-ink-3">{card.commitment_line}</p>}
      {waiting && card.reason && <p className="t-body mt-2 text-ink-2">{sentence(card.reason)}</p>}

      {facts && (
        <p className="t-body mt-6 text-ink">{facts.text}{facts.late ? <span className="text-late">, after launch.</span> : '.'}</p>
      )}
      {card.ui?.reply && !facts && (
        <p className="t-body mt-6 text-ink-2">
          <span className="text-ink-3">Reply from {card.ui.reply_from || 'the supplier'}: </span>{card.ui.reply}
        </p>
      )}

      <p className="t-body mt-6 text-ink-2">
        {claim ? (
          <ClaimLine claim={claim} late={card.level === 'red'} open={source} onOpen={setSource} />
        ) : (
          <Cited text={summaryLine(card)} anchors={anchors.items} onOpen={setSource} open={source} />
        )}
      </p>
      <EvidenceLinks card={card} onDrawer={onDrawer} className="mt-12" />
      <SourceSheet card={card} claim={claim} index={source} anchors={anchors.items} ready={anchors.ready} onIndex={setSource} />
    </article>
  )
}
