import { Loader2 } from 'lucide-react'
import { useState, type FormEvent, type ReactNode } from 'react'
import { fmtTime, type State } from '@/lib/api'
import { cn } from '@/lib/utils'
import { TestResult, TrustControl } from './Diagram'
import { FlowGraph } from './FlowGraph'
import { setMode, useLive } from './live'
import { UndoAction } from './Undo'
import { body, call, lastUndoable, modeOf, summarise, type Mode, type Routine, type Run, type Spec } from './types'

// One routine in the Today style, one view, top to bottom: its name and what it did, how it works
// (the When / Only if / Then diagram that is also the editor: × to remove, drag to reorder, "Add
// step"), "Change it in your words" right under it, then how much it may do on its own. A change is never saved straight
// away: it is tested on today's inbox first and shown on the diagram as before/after, then the
// buyer applies it or keeps the old version. The same block shows a suggested routine (draft)
// before it is on, everywhere: in the sheet, after "Show me how" and from "How it decided".

export { TestResult, TrustControl }

interface Review { after: Routine; preview: Run; note: string }

export function Switch({ on, onChange, label }: { on: boolean; onChange: (on: boolean) => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      title={on ? 'On: runs on every check' : 'Off'}
      onClick={() => onChange(!on)}
      className="t-small inline-flex shrink-0 cursor-pointer items-center gap-2 text-ink-2 hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      {on ? 'On' : 'Off'}
      <span className={cn('relative h-[22px] w-[38px] rounded-full transition-colors', on ? 'bg-ink' : 'bg-hair')}>
        <span className={cn('absolute top-[3px] h-4 w-4 rounded-full bg-white shadow-sm transition-[left]', on ? 'left-[19px]' : 'left-[3px]')} />
      </span>
    </button>
  )
}

const primary = 't-small inline-flex h-10 cursor-pointer items-center justify-center rounded-xl bg-accent px-5 font-medium text-white shadow-[0_1px_2px_rgba(35,72,208,.25)] hover:bg-[#1d3db5] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-60'
const quiet = 't-small cursor-pointer text-ink-2 hover:text-ink disabled:opacity-60'

export function RoutineCard({
  routine, draft, onChanged, big, ctx,
}: {
  routine: Routine
  /** Today's cases, for the diagram's "left after this line" counts. */
  ctx?: State | null
  /** Shown on its own (after "Show me how", from "How it decided"): the name as the headline. */
  big?: boolean
  /** Set for a suggested routine that is not saved yet. */
  draft?: { preview: Run; onTurnOn: (mode: Mode) => Promise<void>; onDiscard: () => void; onReplace: (r: Routine, preview: Run) => void }
  onChanged: () => void
}) {
  const [review, setReview] = useState<Review | null>(null)
  const [busy, setBusy] = useState<'edit' | 'preview' | 'apply' | 'run' | 'delete' | 'save' | null>(null)
  const [error, setError] = useState('')
  const [words, setWords] = useState('')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [confirmRun, setConfirmRun] = useState(false)
  // Shown right away; the server's answer (or a failure) settles it. A draft starts by asking.
  const [mode, setModeLocal] = useState<Mode | null>(draft ? 'ask' : null)
  const shownMode = mode ?? modeOf(routine)
  async function changeMode(m: Mode) {
    setModeLocal(m); setError('')
    if (draft) return
    try { await setMode(routine.id, m); onChanged() } catch (e) {
      setModeLocal(null)
      const why = (e as Error).message
      setError(/404|not found/i.test(why) ? 'Trust levels are not available on the server yet.' : `Could not change how it acts: ${why}.`)
    }
  }

  const live = useLive()
  const would = Object.values(live.would).filter((ws) => ws.some((w) => w.routine_id === routine.id)).length
  const undoable = draft ? null : lastUndoable(routine.runs)
  const spec = routine.spec
  const working = review?.after ?? routine
  const lastRun = review?.preview ?? draft?.preview ?? routine.runs?.[0]

  async function tryEdit(next: Spec) {
    setBusy('preview'); setError('')
    try {
      const out = await call<{ routine: Routine; preview: Run }>('/api/routines/preview', body({ ...working, spec: next }))
      setReview({ after: { ...working, ...out.routine, spec: next }, preview: out.preview, note: '' })
    } catch (e) {
      setError(`That change does not work: ${(e as Error).message}.`)
    } finally { setBusy(null) }
  }
  async function askInWords(e: FormEvent) {
    e.preventDefault()
    const instruction = words.trim()
    if (!instruction || busy) return
    setBusy('edit'); setError('')
    try {
      const out = await call<{ after: Routine; note: string; preview: Run }>('/api/routines/edit', { routine: body(working), instruction })
      setReview({ after: { ...working, ...out.after }, preview: out.preview, note: out.note })
      setWords('')
    } catch (e) {
      setError(`The agent could not change it: ${(e as Error).message}.`)
    } finally { setBusy(null) }
  }
  async function apply() {
    if (!review) return
    if (draft) { draft.onReplace(review.after, review.preview); setReview(null); return }
    setBusy('apply'); setError('')
    try {
      await call('/api/routines', body(review.after))
      setReview(null); onChanged()
    } catch (e) {
      setError(`Could not save the change: ${(e as Error).message}.`)
    } finally { setBusy(null) }
  }
  async function act(kind: 'run' | 'delete' | 'save', fn: () => Promise<unknown>) {
    setBusy(kind); setError('')
    try { await fn(); onChanged() } catch (e) { setError((e as Error).message) } finally { setBusy(null) }
  }

  const ran = routine.runs?.find((x) => !x.dry)
  const dot = <span aria-hidden className="hidden sm:inline">·</span>
  const stats = (
    <div className="t-small mt-1.5 flex flex-col gap-y-0.5 text-ink-3 sm:flex-row sm:flex-wrap sm:items-baseline sm:gap-x-2">
      {routine.handled ? <span className="dot-routine text-routine">Handled {routine.handled} case{routine.handled === 1 ? '' : 's'}</span> : <span>Nothing handled yet</span>}
      {dot}
      {ran ? (
        summarise(ran) === 'Nothing to do' && ran.ok ? (
          <span>{routine.enabled ? `Watching · checked ${fmtTime(ran.at)}, nothing to do right now` : `Off · last ran ${fmtTime(ran.at)}`}</span>
        ) : (
          <span>Last ran {fmtTime(ran.at)}: <span className={cn(!ran.ok ? 'text-late' : 'text-ink-2')}>{summarise(ran).replace(/^./, (ch) => ch.toLowerCase())}</span></span>
        )
      ) : <span>{routine.enabled ? 'Starting: checks within a minute' : 'Off'}</span>}
      {would > 0 && <>{dot}<span className="text-ink-2">Would handle {would} open case{would === 1 ? '' : 's'} now</span></>}
      {undoable && <>{dot}<UndoAction actionId={undoable.id} onUndone={onChanged} label="Undo its last step" /></>}
    </div>
  )

  const trust = (
    <section aria-label="How much it may do on its own" className="mt-10 max-w-[620px]">
      <p className="t-small mb-3 font-medium text-ink-3">How much it may do on its own</p>
      <TrustControl
        value={shownMode}
        onChange={changeMode}
        disabled={!!busy}
        dim={!draft && !routine.enabled}
        note={
          draft && shownMode === 'ask' ? 'Every routine starts here. Move it up once you trust what it prepares.'
          : !draft && !routine.enabled ? 'Off. Switch it on to let it act.'
          : undefined
        }
      />
    </section>
  )

  let content: ReactNode
  if (!spec) {
    content = (
      <>
        <p className="t-body mt-5 text-ink-2">{routine.description}</p>
        <p className="t-small mt-2 text-ink-3">An earlier routine, written as code. Ask the agent for a new one to see and change it as steps.</p>
        {trust}
      </>
    )
  } else {
    content = (
      <>
        {review ? (
          <div className="rise mt-8 flex flex-wrap items-center gap-x-6 gap-y-3 rounded-2xl bg-soft px-5 py-4" role="status">
            <div className="min-w-0 flex-1 basis-[280px]">
              <p className="t-small font-medium text-ink">Your change, tested on today's inbox. Nothing is saved yet.</p>
              <p className="t-small mt-0.5 flex flex-wrap items-center gap-x-4 text-ink-3">
                <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-[3px] border border-accent/50 bg-accent/10" />New or changed</span>
                <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-[3px] border border-dashed border-ink-3" />Removed</span>
              </p>
            </div>
            <div className="flex items-center gap-5">
              <button className={quiet} disabled={!!busy} onClick={() => { setReview(null); setError('') }}>Keep as is</button>
              <button className={primary} disabled={!review.preview.ok || !!busy} onClick={apply}>
                {busy === 'apply' ? 'Applying…' : draft ? 'Use this version' : 'Apply change'}
              </button>
            </div>
          </div>
        ) : (
          <p className="t-small mt-8 font-medium text-ink-3">How it works</p>
        )}
        <div className={cn('mt-5 transition-opacity', busy === 'preview' && 'opacity-60')}>
          <FlowGraph
            wide
            spec={working.spec ?? spec}
            before={review ? spec : undefined}
            run={lastRun}
            onChange={tryEdit}
            disabled={!!busy}
            ctx={ctx}
          />
        </div>
        {review && review.after.name !== routine.name && (
          <p className="t-small mt-3 text-ink-2">
            New name: <span className="text-ink-3 line-through">{routine.name}</span> <span className="text-ink">{review.after.name}</span>
          </p>
        )}
        {busy === 'preview' && <Working text="Testing the change on today's inbox…" />}
        {busy === 'edit' && <Working text="The agent is changing the routine…" />}

        {review ? (
          <div className="mt-6 space-y-3">
            {review.note && <p className="t-small text-ink-2">{review.note}</p>}
            <TestResult run={review.preview} spec={review.after.spec} title="What this version would do on today's inbox" />
          </div>
        ) : (
          <form onSubmit={askInWords} className="mt-6 flex items-center gap-2 rounded-xl border border-hair bg-white py-1 pl-3.5 pr-1 focus-within:border-ink-3/40">
            <label className="min-w-0 flex-1">
              <span className="sr-only">Change it in your words</span>
              <input
                value={words}
                onChange={(e) => setWords(e.target.value)}
                disabled={!!busy}
                placeholder="Say what to change, like “call me too”"
                className="t-small h-9 w-full bg-transparent text-ink outline-none placeholder:text-ink-3 disabled:opacity-60"
              />
            </label>
            <button type="submit" disabled={!words.trim() || !!busy} className="t-small h-9 shrink-0 cursor-pointer rounded-lg px-3 font-medium text-ink hover:bg-soft disabled:cursor-default disabled:text-ink-3 disabled:hover:bg-transparent">Change</button>
          </form>
        )}
        {!review && trust}
      </>
    )
  }

  return (
    <section aria-label={routine.name}>
      {draft && <p className="t-small mb-1.5 font-medium text-ink-3">Suggested routine, not on yet</p>}
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          {big ? <h2 className="t-big text-ink">{routine.name}</h2> : <h3 className="t-body font-medium text-ink">{routine.name}</h3>}
          {draft ? <p className={cn('mt-1 text-ink-2', big ? 't-body mt-3' : 't-small')}>{routine.description}</p> : stats}
        </div>
        {!draft && (
          <Switch
            on={routine.enabled}
            label={`${routine.name}: ${routine.enabled ? 'on' : 'off'}`}
            onChange={(on) => act('save', () => call(`/api/routines/${routine.id}/toggle`, { enabled: on }))}
          />
        )}
      </div>

      {content}
      {error && <p className="t-small mt-3 text-late" role="alert">{error}</p>}

      {draft && !review && (
        <div className="mt-8 space-y-6">
          <TestResult run={draft.preview} spec={routine.spec} title="Test run on today's inbox" />
          <div className="flex flex-wrap items-center gap-6">
            <button className={primary} disabled={!draft.preview.ok || !!busy} onClick={() => act('save', () => draft.onTurnOn(shownMode))}>
              {busy === 'save' ? 'Turning on…' : shownMode === 'ask' ? 'Turn on, ask me first' : 'Turn on'}
            </button>
            <button className={quiet} disabled={!!busy} onClick={draft.onDiscard}>Not now</button>
          </div>
        </div>
      )}

      <div className="t-small mt-6 flex flex-wrap items-center gap-x-5 gap-y-2 text-ink-3">
        {!draft && !review && (
          <>
            {confirmRun ? (
              <span className="inline-flex items-center gap-3">
                <span className="text-ink-2">It acts for real: messages go out. Run it now?</span>
                <button className="cursor-pointer font-medium text-ink hover:underline" disabled={!!busy}
                  onClick={() => { setConfirmRun(false); act('run', () => call(`/api/routines/${routine.id}/run`)) }}>Run it</button>
                <button className={quiet} onClick={() => setConfirmRun(false)}>Cancel</button>
              </span>
            ) : (
              <button className={quiet} disabled={!!busy} title="Run it on the inbox now"
                onClick={() => (shownMode === 'ask' ? act('run', () => call(`/api/routines/${routine.id}/run`)) : setConfirmRun(true))}>
                {busy === 'run' ? 'Running…' : 'Run now'}
              </button>
            )}
            {confirmDelete ? (
              <span className="inline-flex items-center gap-3">
                <span className="text-ink-2">Delete this routine?</span>
                <button className="cursor-pointer font-medium text-ink hover:underline" disabled={!!busy} onClick={() => act('delete', () => call(`/api/routines/${routine.id}`, {}, 'DELETE'))}>Delete</button>
                <button className={quiet} onClick={() => setConfirmDelete(false)}>Cancel</button>
              </span>
            ) : (
              <button className={quiet} onClick={() => setConfirmDelete(true)}>Delete</button>
            )}
          </>
        )}
        <details className="group basis-full">
          <summary className="w-fit cursor-pointer list-none text-ink-3 hover:text-ink-2">
            <span className="group-open:hidden">Show the code</span><span className="hidden group-open:inline">Hide the code</span>
          </summary>
          <p className="mt-2 text-ink-3">What runs in the closed sandbox. It can only use the building blocks above.</p>
          <pre className="mt-2 max-h-72 overflow-auto rounded-xl bg-soft p-4 text-[12px] leading-snug text-ink-2">{working.code}</pre>
        </details>
      </div>
    </section>
  )
}

function Working({ text }: { text: string }) {
  return (
    <p className="t-small mt-3 flex items-center gap-2 text-ink-2">
      <Loader2 size={14} className="animate-spin text-ink-3" /> {text}
    </p>
  )
}
