import { CircleCheck, Loader2, Phone, Sparkles, Workflow } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet'
import { fmtDay, fmtTime, tidy, type State } from '@/lib/api'
import { pendingOf, refreshLive, useLive } from './routines/live'
import { ProposalItem } from './routines/Proposals'
import { RoutineCard } from './routines/RoutineCard'
import { body, call, type Routine, type Run } from './routines/types'

// Routines the agents wrote after a person decided something by hand: a recipe of conditions
// and steps, compiled to small Python and run in the Monty sandbox on every check
// (routines.py). Here they are reviewed, changed, switched on or off, and drafted from a decision.

interface VoiceDraft { card_id: string; instruction?: string; at?: string; via?: string; routine: Routine; preview?: Run }

const HIDDEN_KEY = 'ask-soony.hidden-routine-drafts'
function readHidden(): Set<string> {
  try { return new Set(JSON.parse(localStorage.getItem(HIDDEN_KEY) ?? '[]') as string[]) } catch { return new Set() }
}

const DECIDED: Record<string, string> = {
  approved: 'Approved', sent: 'Sent', escalated: 'Passed on', dismissed: 'Dismissed',
}

export function Routines({
  state, onChanged, request,
}: { state: State; onChanged: () => void; request?: { cardId: string; k: number } | null }) {
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<Routine[]>([])
  const [draft, setDraft] = useState<{ routine: Routine; preview: Run } | null>(null)
  const [writing, setWriting] = useState<string | null>(null)
  const [error, setError] = useState('')
  // Drafts the voice assistant wrote during a call; "Not now" hides one on this device.
  const [voice, setVoice] = useState<VoiceDraft[]>([])
  const [hidden, setHidden] = useState<Set<string>>(readHidden)
  const [voiceEdits, setVoiceEdits] = useState<Record<string, { routine: Routine; preview: Run }>>({})
  const tested = useRef(new Set<string>())

  const load = useCallback(async () => {
    try {
      const [r, d] = await Promise.all([fetch('/api/routines'), fetch('/api/routines/drafts')])
      if (r.ok) setItems((await r.json()).routines)
      if (d.ok) setVoice(((await d.json()).drafts ?? []).filter((x: VoiceDraft) => x?.routine?.id))
    } catch { /* offline for a moment: keep the last list */ }
    void refreshLive(0)
  }, [])
  // Proposals and "would handle" follow the app's own refresh of the state.
  useEffect(() => { void refreshLive() }, [state.now, state.last_check_at])
  const live = useLive()
  const proposals = pendingOf(live)
  useEffect(() => { void load() }, [load])
  useEffect(() => {
    void load()
    const t = window.setInterval(load, open ? 10000 : 30000)
    return () => clearInterval(t)
  }, [open, load])

  const waiting = voice.filter(
    (d) => !hidden.has(d.routine.id) && !items.some((r) => r.id === d.routine.id) && draft?.routine.id !== d.routine.id,
  )
  // Test each waiting voice draft once on today's inbox (its stored test may be hours old).
  useEffect(() => {
    if (!open) return
    for (const d of waiting) {
      const id = d.routine.id
      if (tested.current.has(id) || voiceEdits[id] || !d.routine.spec) continue
      tested.current.add(id)
      call<{ routine: Routine; preview: Run }>('/api/routines/preview', body(d.routine))
        .then((out) => setVoiceEdits((v) => (v[id] ? v : { ...v, [id]: { routine: { ...d.routine, code: out.routine.code }, preview: out.preview } })))
        .catch(() => { /* keep the stored test run */ })
    }
  }, [open, waiting, voiceEdits])
  function hideDraft(id: string) {
    const next = new Set(hidden).add(id)
    setHidden(next)
    try { localStorage.setItem(HIDDEN_KEY, JSON.stringify([...next])) } catch { /* private mode */ }
  }

  // Asked from the "automate this" offer: open and start drafting right away.
  useEffect(() => {
    if (!request) return
    setOpen(true)
    void suggest(request.cardId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request?.k])

  // Decisions made by hand; a card with a routine says so, the card behind an open draft is hidden.
  const automatedBy = new Map(items.filter((r) => r.created_from).map((r) => [r.created_from!, r.name]))
  const drafting = new Set([draft?.routine.created_from, ...waiting.map((d) => d.card_id)])
  const decided = state.cards.filter((c) => c.status in DECIDED && !drafting.has(c.id))
  const active = items.filter((r) => r.enabled).length
  const writingCard = state.cards.find((c) => c.id === writing)
  const forYou = waiting.length + proposals.length
  const cardHead = (id?: string | null) => (id ? tidy(state.cards.find((c) => c.id === id)?.headline) || undefined : undefined)

  async function suggest(cardId: string) {
    setWriting(cardId); setError(''); setDraft(null)
    try {
      setDraft(await call<{ routine: Routine; preview: Run }>('/api/routines/suggest', { card_id: cardId }))
    } catch (e) {
      setError(`The agent could not write a routine: ${(e as Error).message}.`)
    } finally { setWriting(null) }
  }
  async function turnOn() {
    if (!draft) return
    await call('/api/routines', body(draft.routine))
    setDraft(null)
    await load()
  }
  const changed = () => { void load(); onChanged() }

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        title={
          proposals.length ? `Routines: ${proposals.length} waiting for your OK`
            : waiting.length ? `Routines: ${waiting.length} draft${waiting.length === 1 ? '' : 's'} from your call waiting`
            : 'Routines: what the agents do on their own'
        }
        aria-label={forYou ? `Routines, ${forYou} waiting for you` : active ? `Routines, ${active} on` : 'Routines'}
        className="relative inline-flex h-9 cursor-pointer items-center gap-2 whitespace-nowrap rounded-full border border-line bg-white px-2.5 text-sm font-semibold text-ink-2 hover:bg-panel lg:px-3"
      >
        <Workflow size={16} className={active ? 'text-ok' : ''} />
        <span className="hidden lg:inline">Routines</span>
        {forYou > 0 ? (
          <span className="absolute -right-1 -top-1 grid h-4 min-w-4 place-items-center rounded-full bg-routine px-1 text-[10px] font-bold text-white ring-2 ring-white">{forYou}</span>
        ) : active > 0 && (
          <span className="absolute -right-1 -top-1 grid h-4 min-w-4 place-items-center rounded-full bg-white px-1 text-[10px] font-bold text-ok ring-1 ring-ok/40">{active}</span>
        )}
      </button>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent className="sm:max-w-[560px]">
          <div className="border-b border-line px-6 pb-4 pt-6 pr-14">
            <SheetTitle className="text-xl font-bold leading-snug">Routines</SheetTitle>
            <SheetDescription className="mt-1 text-sm text-ink-2">
              Written by an agent from your decisions. They run in a closed sandbox on every check. Anything that costs money or changes the plan still comes to you.
            </SheetDescription>
          </div>
          <div className="flex-1 space-y-4 overflow-y-auto px-6 py-5">
            {proposals.length > 0 && (
              <section className="space-y-2" aria-label="Waiting for your OK">
                <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-routine">
                  <CircleCheck size={13} /> Waiting for your OK · {proposals.length}
                </h3>
                {proposals.map((p) => (
                  <ProposalItem key={p.id} p={p} context={cardHead(p.card_id) ?? (p.shipment_id ? `Shipment ${p.shipment_id}` : undefined)} onDone={onChanged} />
                ))}
              </section>
            )}
            {waiting.length > 0 && (
              <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-ok">
                <Phone size={13} /> Drafted from your call
              </h3>
            )}
            {waiting.map((d) => {
              const id = d.routine.id
              const cur = voiceEdits[id] ?? { routine: d.routine, preview: d.preview ?? { at: d.at ?? '', ok: true, dry: true } }
              return (
                <div key={`voice-${id}`} className="space-y-2">
                  {d.instruction && <p className="text-sm text-ink-2">You said: “{d.instruction}”</p>}
                  <RoutineCard
                    routine={cur.routine}
                    onChanged={changed}
                    draft={{
                      preview: cur.preview,
                      onTurnOn: async () => { await call('/api/routines', body(cur.routine)); hideDraft(id); await load() },
                      onDiscard: () => hideDraft(id),
                      onReplace: (routine, preview) => setVoiceEdits((v) => ({ ...v, [id]: { routine, preview } })),
                    }}
                  />
                </div>
              )
            })}
            {writing && (
              <div className="rounded-xl border-2 border-dashed border-ok/40 bg-ok/5 p-4 text-[15px]">
                <div className="flex items-center gap-2 font-semibold text-ok">
                  <Loader2 size={16} className="animate-spin" /> The agent is writing a routine from your decision…
                </div>
                {writingCard && (
                  <p className="mt-1 text-ink-2">
                    “{writingCard.headline}”. It builds the steps, checks them in the sandbox and tests them on today's inbox before you see them. This takes up to half a minute.
                  </p>
                )}
              </div>
            )}
            {draft && (
              <RoutineCard
                key={`draft-${draft.routine.id}`}
                routine={draft.routine}
                onChanged={changed}
                draft={{
                  preview: draft.preview,
                  onTurnOn: turnOn,
                  onDiscard: () => setDraft(null),
                  onReplace: (routine, preview) => setDraft({ routine, preview }),
                }}
              />
            )}
            {error && <p className="rounded-lg bg-stop/10 p-3 text-sm text-stop">{error}</p>}

            {items.length > 0 && <h3 className="pt-1 text-xs font-semibold uppercase tracking-wide text-ink-3">Your routines</h3>}
            {items.length === 0 && !draft && !writing && (
              <p className="text-[15px] text-ink-2">No routines yet. Decide a card, then let the agent handle the next one like it.</p>
            )}
            {items.map((r) => <RoutineCard key={r.id} routine={r} onChanged={changed} />)}

            <h3 className="pt-3 text-xs font-semibold uppercase tracking-wide text-ink-3">Automate a decision you made</h3>
            {decided.length === 0 && (
              <p className="text-sm text-ink-3">{draft ? 'Review the suggestion above.' : 'Nothing decided yet.'}</p>
            )}
            <div className="space-y-2">
              {decided.map((c) => (
                <div key={c.id} className="flex items-center justify-between gap-3 rounded-lg border border-line px-3 py-2.5">
                  <div className="min-w-0">
                    <div className="line-clamp-2 text-[15px] font-medium leading-snug">{c.headline}</div>
                    <div className="mt-0.5 text-xs text-ink-3">
                      {DECIDED[c.status]}{c.ui?.at ? ` · ${fmtDay(c.ui.at)} ${fmtTime(c.ui.at)}` : ''}
                    </div>
                  </div>
                  {automatedBy.has(c.id) ? (
                    <span className="max-w-[45%] shrink-0 text-right text-sm font-semibold text-ok">Automated · {automatedBy.get(c.id)}</span>
                  ) : (
                    <Button size="sm" variant="outline" className="shrink-0" disabled={!!writing} onClick={() => suggest(c.id)}>
                      <Sparkles size={14} /> {writing === c.id ? 'Writing…' : 'Automate cards like this'}
                    </Button>
                  )}
                </div>
              ))}
            </div>
          </div>
        </SheetContent>
      </Sheet>
    </>
  )
}
