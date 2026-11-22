import { Check, FlaskConical, Loader2, Maximize2, Play, Sparkles, Trash2, Wand2 } from 'lucide-react'
import { useState, type FormEvent, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet'
import { fmtTime } from '@/lib/api'
import { cn } from '@/lib/utils'
import { FlowGraph } from './FlowGraph'
import { ChannelIcon, RoutineFlow, StepIcon } from './RoutineFlow'
import { body, call, didLines, summarise, type Routine, type Run, type Spec } from './types'

// One routine: header with its switch and last-run line, the flow graph or the editable block
// list, and the change-and-review loop. A change (in words or by hand) is never saved straight
// away: it is tested on today's inbox first and the buyer applies it or keeps the old version.
// The same card shows a suggested routine (draft) before it is turned on.

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
      className={cn('relative h-5 w-9 shrink-0 cursor-pointer rounded-full transition-colors', on ? 'bg-ok' : 'bg-line')}
    >
      <span className={cn('absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-[left]', on ? 'left-[18px]' : 'left-0.5')} />
    </button>
  )
}

export function TestResult({ run, spec, title = "Test on today's inbox" }: { run: Run; spec?: Spec | null; title?: string }) {
  const lines = didLines(run, spec)
  return (
    <div className="rounded-lg bg-white p-3 text-sm ring-1 ring-line">
      <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-ink-3">
        <FlaskConical size={13} /> {title}
      </div>
      {!run.ok ? (
        <div className="mt-1.5">
          <p className="font-semibold text-stop">The test failed, so this version cannot be used.</p>
          <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap text-[12px] text-stop/90">{run.error}</pre>
        </div>
      ) : lines.length === 0 ? (
        <p className="mt-1.5 text-ink-2">Nothing in today's inbox matches. It waits for the next card like this.</p>
      ) : (
        <ul className="mt-1.5 space-y-1">
          {lines.map((l, i) => (
            <li key={i} className="flex items-start gap-2 text-ink">
              <span className="mt-0.5 shrink-0">
                {l.kind === 'send' ? <StepIcon step={{ do: 'carry_out' }} size={14} /> : l.kind === 'note' ? <StepIcon step={{ do: 'log' }} size={14} /> : <ChannelIcon channel={l.channel} size={14} />}
              </span>
              <span className="min-w-0">{l.text}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Segmented({ value, onChange }: { value: 'flow' | 'steps'; onChange: (v: 'flow' | 'steps') => void }) {
  return (
    <div role="tablist" aria-label="View" className="inline-flex rounded-lg bg-panel p-0.5 ring-1 ring-line">
      {(['flow', 'steps'] as const).map((v) => (
        <button
          key={v}
          role="tab"
          type="button"
          aria-selected={value === v}
          onClick={() => onChange(v)}
          className={cn(
            'cursor-pointer rounded-md px-2.5 py-0.5 text-[13px] font-semibold',
            value === v ? 'bg-white text-ink shadow-sm ring-1 ring-line' : 'text-ink-3 hover:text-ink',
          )}
        >
          {v === 'flow' ? 'Flow' : 'Steps'}
        </button>
      ))}
    </div>
  )
}

export function RoutineCard({
  routine, draft, onChanged,
}: {
  routine: Routine
  /** Set for a suggested routine that is not saved yet. */
  draft?: { preview: Run; onTurnOn: () => Promise<void>; onDiscard: () => void; onReplace: (r: Routine, preview: Run) => void }
  onChanged: () => void
}) {
  const [view, setView] = useState<'flow' | 'steps'>('flow')
  const [review, setReview] = useState<Review | null>(null)
  const [busy, setBusy] = useState<'edit' | 'preview' | 'apply' | 'run' | 'delete' | 'save' | null>(null)
  const [error, setError] = useState('')
  const [words, setWords] = useState('')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [expanded, setExpanded] = useState(false)

  const spec = routine.spec
  const working = review?.after ?? routine
  const shownView = review ? 'steps' : view
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

  const stats = (
    <p className="mt-0.5 text-sm text-ink-3">
      {routine.handled ? `Handled ${routine.handled} card${routine.handled === 1 ? '' : 's'}` : 'No cards handled yet'}
      {' · '}
      {lastRun ? (
        <>
          Last ran {fmtTime(lastRun.at)} · <span className={cn(!lastRun.ok && 'font-semibold text-stop')}>{summarise(lastRun)}</span>
        </>
      ) : 'Not run yet'}
    </p>
  )

  let content: ReactNode
  if (!spec) {
    content = (
      <>
        <p className="mt-2 text-[15px] text-ink-2">{routine.description}</p>
        <p className="mt-2 text-sm text-ink-3">An earlier routine, written as code. Ask the agent for a new one to see and change it as steps.</p>
      </>
    )
  } else {
    content = (
      <>
        <div className="mt-3 flex items-center justify-between gap-2">
          {review ? (
            <div className="text-xs font-semibold uppercase tracking-wide text-ok">Your change, before you apply it</div>
          ) : (
            <Segmented value={view} onChange={setView} />
          )}
          {shownView === 'flow' && (
            <Button size="sm" variant="ghost" className="h-7 px-2 text-[13px]" onClick={() => setExpanded(true)}>
              <Maximize2 size={13} /> Expand
            </Button>
          )}
        </div>
        <div className="mt-2">
          {shownView === 'flow' ? (
            <FlowGraph spec={spec} run={lastRun} />
          ) : (
            <RoutineFlow
              spec={working.spec ?? spec}
              before={review ? spec : undefined}
              onChange={tryEdit}
              disabled={!!busy}
            />
          )}
        </div>
        {review && review.after.name !== routine.name && (
          <p className="mt-2 text-sm text-ink-2">
            New name: <span className="text-ink-3 line-through">{routine.name}</span> {review.after.name}
          </p>
        )}
        {busy === 'preview' && <Working text="Testing the change on today's inbox…" />}
        {busy === 'edit' && <Working text="The agent is changing the routine…" />}

        {review ? (
          <div className="mt-3 space-y-3">
            {review.note && <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900 ring-1 ring-amber-200">{review.note}</p>}
            <TestResult run={review.preview} spec={review.after.spec} />
            <div className="flex flex-wrap gap-2">
              <Button size="sm" disabled={!review.preview.ok || !!busy} onClick={apply}>
                <Check size={14} /> {busy === 'apply' ? 'Applying…' : 'Apply change'}
              </Button>
              <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => { setReview(null); setError('') }}>Keep as is</Button>
            </div>
          </div>
        ) : (
          <form onSubmit={askInWords} className="mt-3 flex gap-2">
            <label className="relative min-w-0 flex-1">
              <span className="sr-only">Change it in your words</span>
              <Wand2 size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-3" />
              <input
                value={words}
                onChange={(e) => setWords(e.target.value)}
                disabled={!!busy}
                placeholder="Change it in your words, e.g. “also tell me on Teams”"
                className="h-9 w-full rounded-lg border border-line bg-white pl-8 pr-3 text-sm text-ink placeholder:text-ink-3 focus:border-ok focus:outline-none focus:ring-2 focus:ring-ok/20 disabled:opacity-60"
              />
            </label>
            <Button size="sm" variant="outline" type="submit" disabled={!words.trim() || !!busy}>Change</Button>
          </form>
        )}
      </>
    )
  }

  return (
    <section
      className={cn(
        'rounded-xl p-4',
        draft ? 'border-2 border-ok/50 bg-ok/5' : 'border border-line bg-white',
      )}
    >
      {draft && (
        <div className="mb-1 flex items-center gap-1.5 text-xs font-bold uppercase tracking-wide text-ok">
          <Sparkles size={13} /> Suggested routine
        </div>
      )}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[16px] font-bold leading-snug text-ink">{routine.name}</div>
          {draft ? <p className="mt-1 text-[15px] text-ink-2">{routine.description}</p> : stats}
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
      {error && <p className="mt-3 rounded-lg bg-stop/10 p-3 text-sm text-stop">{error}</p>}

      {draft && !review && (
        <div className="mt-3 space-y-3">
          <TestResult run={draft.preview} spec={routine.spec} title="Test run on today's inbox" />
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={!draft.preview.ok || !!busy} onClick={() => act('save', draft.onTurnOn)}>
              {busy === 'save' ? 'Turning on…' : 'Turn on'}
            </Button>
            <Button size="sm" variant="ghost" disabled={!!busy} onClick={draft.onDiscard}>Not now</Button>
          </div>
        </div>
      )}

      {!draft && !review && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-3">
          <Button size="sm" variant="outline" disabled={!!busy} onClick={() => act('run', () => call(`/api/routines/${routine.id}/run`))} title="Run it on the inbox now">
            {busy === 'run' ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />} Run now
          </Button>
          {confirmDelete ? (
            <span className="inline-flex items-center gap-1.5 text-sm text-ink-2">
              Delete this routine?
              <Button size="sm" variant="ghost" className="text-stop hover:text-stop" disabled={!!busy} onClick={() => act('delete', () => call(`/api/routines/${routine.id}`, {}, 'DELETE'))}>Delete</Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(false)}>Cancel</Button>
            </span>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(true)}><Trash2 size={14} /> Delete</Button>
          )}
        </div>
      )}

      <details className="mt-2">
        <summary className="cursor-pointer text-sm text-ink-3 hover:text-ink-2">Show the code</summary>
        <p className="mt-1 text-xs text-ink-3">What runs in the closed sandbox. It can only use the building blocks above.</p>
        <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-panel p-3 text-[12px] leading-snug ring-1 ring-line">{working.code}</pre>
      </details>

      {spec && (
        <Sheet open={expanded} onOpenChange={setExpanded}>
          <SheetContent className="max-w-[min(1280px,100vw)]">
            <div className="border-b border-line px-6 pb-4 pt-6 pr-14">
              <div className="text-xs font-semibold uppercase tracking-wide text-ink-3">{draft ? 'Suggested routine' : 'Routine'}</div>
              <SheetTitle className="mt-1 text-xl font-bold leading-snug">{routine.name}</SheetTitle>
              <SheetDescription className="mt-1 text-sm text-ink-2">{routine.description}</SheetDescription>
            </div>
            <div className="flex-1 overflow-auto px-6 py-6">
              <FlowGraph spec={spec} run={lastRun} wide />
              {lastRun && <div className="mt-6 max-w-[640px]"><TestResult run={lastRun} spec={spec} title={lastRun.dry ? "Test run on today's inbox" : `Last run at ${fmtTime(lastRun.at)}`} /></div>}
            </div>
          </SheetContent>
        </Sheet>
      )}
    </section>
  )
}

function Working({ text }: { text: string }) {
  return (
    <p className="mt-2 flex items-center gap-2 text-sm font-medium text-ok">
      <Loader2 size={14} className="animate-spin" /> {text}
    </p>
  )
}
