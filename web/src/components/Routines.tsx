import { Cog, Play, Sparkles, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet'
import { fmtDay, fmtTime, post, type State } from '@/lib/api'
import { cn } from '@/lib/utils'

// Routines the agents wrote after a person decided something by hand: small Python run in the
// Monty sandbox on every check (routines.py). Here they are reviewed, switched on or off, and
// new ones drafted from a decision.

interface Step { do: string; card?: string; option?: string; text?: string; status?: string; dry?: boolean }
interface Run { at: string; ok: boolean; dry?: boolean; result?: unknown; error?: string; log?: string[]; did?: Step[] }
interface Routine {
  id: string; name: string; description: string; code: string; enabled: boolean
  created_from?: string; created_at?: string; runs?: Run[]
}

export function Routines({
  state, onChanged, request,
}: { state: State; onChanged: () => void; request?: { cardId: string; k: number } | null }) {
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<Routine[]>([])
  const [draft, setDraft] = useState<{ routine: Routine; preview: Run } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    const r = await fetch('/api/routines')
    if (r.ok) setItems((await r.json()).routines)
  }, [])
  useEffect(() => { void load() }, [load])
  useEffect(() => {
    if (!open) return
    void load()
    const t = window.setInterval(load, 10000)
    return () => clearInterval(t)
  }, [open, load])

  // Asked from the "automate this" offer: open and start drafting right away.
  useEffect(() => {
    if (!request) return
    setOpen(true)
    void suggest(request.cardId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request?.k])

  // Decisions not automated yet; a card with a routine (or an open draft) is not offered again.
  const automatedBy = new Map(items.filter((r) => r.created_from).map((r) => [r.created_from!, r.name]))
  const decided = state.cards.filter(
    (c) => ['approved', 'sent', 'escalated', 'dismissed'].includes(c.status) && draft?.routine.created_from !== c.id,
  )
  const active = items.filter((r) => r.enabled).length

  async function suggest(cardId: string) {
    setBusy(cardId); setError(''); setDraft(null)
    try {
      setDraft(await post<{ routine: Routine; preview: Run }>('/api/routines/suggest', { card_id: cardId }))
    } catch (e) {
      setError(`Could not draft a routine: ${(e as Error).message}`)
    } finally { setBusy(null) }
  }
  async function save() {
    if (!draft) return
    await post('/api/routines', draft.routine)
    setDraft(null); await load()
  }
  async function toggle(r: Routine) { await post(`/api/routines/${r.id}/toggle`, { enabled: !r.enabled }); await load() }
  async function runNow(r: Routine) { setBusy(r.id); await post(`/api/routines/${r.id}/run`); await load(); setBusy(null); onChanged() }
  async function remove(r: Routine) { await fetch(`/api/routines/${r.id}`, { method: 'DELETE' }); await load() }

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        title="Routines: what the agents do on their own"
        aria-label="Routines"
        className="relative inline-flex h-9 cursor-pointer items-center gap-2 whitespace-nowrap rounded-full border border-line bg-white px-2.5 text-sm font-semibold text-ink-2 hover:bg-panel 2xl:px-3.5"
      >
        <Cog size={16} className={active ? 'text-ok' : ''} />
        <span className="hidden 2xl:inline">Routines</span>
        {active > 0 && (
          <span className="absolute -right-1 -top-1 grid h-4 min-w-4 place-items-center rounded-full bg-ok px-1 text-[10px] font-bold text-white">{active}</span>
        )}
      </button>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent>
          <div className="border-b border-line px-6 pb-4 pt-6 pr-14">
            <div className="text-xs font-semibold uppercase tracking-wide text-ink-3">Routines</div>
            <SheetTitle className="mt-1 text-xl font-bold leading-snug">What the agents do on their own</SheetTitle>
            <SheetDescription className="mt-1 text-sm text-ink-2">
              Written by an agent from a decision you made, run in a closed sandbox on every check. Anything that costs money still comes to you.
            </SheetDescription>
          </div>
          <div className="flex-1 space-y-4 overflow-y-auto px-6 py-5">
            {busy && state.cards.some((c) => c.id === busy) && (
              <div className="rounded-xl border-2 border-dashed border-ok/40 bg-ok/5 p-4 text-[15px]">
                <div className="flex items-center gap-2 font-semibold text-ok">
                  <Sparkles size={16} className="animate-pulse" /> The agent is writing a routine…
                </div>
                <p className="mt-1 text-ink-2">
                  From your decision on “{state.cards.find((c) => c.id === busy)?.headline}”. It checks the code in the sandbox and runs a test on the inbox before you see it.
                </p>
              </div>
            )}
            {draft && <DraftView draft={draft} onSave={save} onDiscard={() => setDraft(null)} />}
            {error && <p className="rounded-lg bg-stop/10 p-3 text-sm text-stop">{error}</p>}

            {items.length === 0 && !draft && (
              <p className="text-[15px] text-ink-2">No routines yet. Decide a card, then let the agent automate the next one like it.</p>
            )}
            {items.map((r) => (
              <section key={r.id} className={cn('rounded-xl border p-4', r.enabled ? 'border-ok/40' : 'border-line bg-panel')}>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-[16px] font-bold">{r.name}</div>
                    <p className="mt-1 text-[15px] text-ink-2">{r.description}</p>
                  </div>
                  <button
                    onClick={() => toggle(r)}
                    className={cn('shrink-0 cursor-pointer rounded-full px-3 py-1 text-sm font-semibold', r.enabled ? 'bg-ok text-white' : 'border border-line text-ink-2')}
                  >
                    {r.enabled ? 'On' : 'Off'}
                  </button>
                </div>
                <LastRun run={r.runs?.[0]} />
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <Button size="sm" variant="outline" disabled={busy === r.id} onClick={() => runNow(r)}><Play size={14} /> Run now</Button>
                  <Button size="sm" variant="ghost" onClick={() => remove(r)}><Trash2 size={14} /> Delete</Button>
                </div>
                <details className="mt-2">
                  <summary className="cursor-pointer text-sm text-ink-3">Show the code</summary>
                  <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-panel p-3 text-[12px] leading-snug">{r.code}</pre>
                </details>
              </section>
            ))}

            <div className="pt-2 text-xs font-semibold uppercase tracking-wide text-ink-3">Automate a decision you made</div>
            {decided.length === 0 && <p className="text-sm text-ink-3">{draft ? 'Review the suggestion above.' : 'Nothing decided yet.'}</p>}
            {decided.map((c) => (
              <div key={c.id} className="flex items-center justify-between gap-3 rounded-lg border border-line px-3 py-2">
                <div className="min-w-0">
                  <div className="truncate text-[15px] font-medium">{c.headline}</div>
                  <div className="text-xs text-ink-3">{c.status}{c.ui?.at ? ` · ${fmtDay(c.ui.at)} ${fmtTime(c.ui.at)}` : ''}</div>
                </div>
                {automatedBy.has(c.id) ? (
                  <span className="shrink-0 text-sm font-semibold text-ok">Automated · {automatedBy.get(c.id)}</span>
                ) : (
                  <Button size="sm" variant="outline" disabled={!!busy} onClick={() => suggest(c.id)}>
                    <Sparkles size={14} /> {busy === c.id ? 'Writing…' : 'Automate cards like this'}
                  </Button>
                )}
              </div>
            ))}
          </div>
        </SheetContent>
      </Sheet>
    </>
  )
}

function DraftView({ draft, onSave, onDiscard }: { draft: { routine: Routine; preview: Run }; onSave: () => void; onDiscard: () => void }) {
  const { routine: r, preview: p } = draft
  return (
    <section className="rounded-xl border-2 border-ok/50 bg-ok/5 p-4">
      <div className="text-xs font-bold uppercase tracking-wide text-ok">Suggested routine</div>
      <div className="mt-1 text-[17px] font-bold">{r.name}</div>
      <p className="mt-1 text-[15px] text-ink-2">{r.description}</p>
      <div className="mt-3 rounded-lg bg-white p-3 text-sm ring-1 ring-line">
        <div className="font-semibold">{p.ok ? 'Test run on the inbox right now' : 'The sandbox rejected it'}</div>
        {p.ok ? <Steps run={p} empty="Nothing in the inbox matches right now; it waits for the next card like this." /> : (
          <pre className="mt-1 whitespace-pre-wrap text-[12px] text-stop">{p.error}</pre>
        )}
      </div>
      <details className="mt-2">
        <summary className="cursor-pointer text-sm text-ink-3">Show the code</summary>
        <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-white p-3 text-[12px] leading-snug ring-1 ring-line">{r.code}</pre>
      </details>
      <div className="mt-3 flex gap-2">
        <Button size="sm" disabled={!p.ok} onClick={onSave}>Turn on</Button>
        <Button size="sm" variant="ghost" onClick={onDiscard}>Not now</Button>
      </div>
    </section>
  )
}

function Steps({ run, empty }: { run: Run; empty: string }) {
  const did = run.did ?? []
  if (!did.length) return <p className="mt-1 text-ink-3">{empty}</p>
  return (
    <ul className="mt-1 space-y-1">
      {did.map((s, i) => (
        <li key={i}>
          {s.do === 'carry_out'
            ? `${s.dry ? 'Would carry out' : 'Carried out'}: ${s.option}${s.status ? ` (${s.status})` : ''}`
            : `${s.dry ? 'Would tell you' : 'Told you'}: ${s.text}`}
        </li>
      ))}
    </ul>
  )
}

function LastRun({ run }: { run?: Run }) {
  if (!run) return <p className="mt-2 text-sm text-ink-3">Not run yet.</p>
  return (
    <div className="mt-2 text-sm">
      <span className={cn('font-semibold', run.ok ? 'text-ink-2' : 'text-stop')}>
        Last run {fmtTime(run.at)}{run.ok ? '' : ': failed'}
      </span>
      {run.ok ? <Steps run={run} empty=" Nothing to do." /> : <pre className="mt-1 whitespace-pre-wrap text-[12px] text-stop">{run.error}</pre>}
    </div>
  )
}
