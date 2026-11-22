import { Check, Loader2, Workflow } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { tidy } from '@/lib/api'
import { cn } from '@/lib/utils'
import { answerProposal } from './live'
import { StepIcon } from './RoutineFlow'
import type { Proposal, StepKind } from './types'

// One thing a routine in "ask me" mode prepared: what it would do, the text it would send, and
// one tap to do it or skip it. In the sheet it names the card; in the pane it names the routine.

const KNOWN: StepKind[] = ['carry_out', 'notify', 'log', 'chase', 'call', 'ask_status']
const kindOf = (p: Proposal): StepKind => (KNOWN.includes(p.step?.do as StepKind) ? (p.step.do as StepKind) : 'carry_out')

/** "Chase once in the same thread", from the step, with a fallback per kind. */
function proposalWhat(p: Proposal): string {
  const t = tidy(p.step?.text)
  if (t) return t
  const k = kindOf(p)
  return k === 'chase' ? 'Chase once in the same thread'
    : k === 'call' ? 'Call you'
    : k === 'ask_status' ? 'Ask the supplier for a status'
    : k === 'notify' ? 'Tell you'
    : 'Send the prepared message'
}

export function ProposalItem({
  p, context, inPane, onDone,
}: { p: Proposal; context?: string; inPane?: boolean; onDone?: () => void }) {
  const [busy, setBusy] = useState<'accept' | 'skip' | null>(null)
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)
  const kind = kindOf(p)
  const what = proposalWhat(p)

  async function answer(how: 'accept' | 'skip') {
    setBusy(how); setError('')
    try {
      await answerProposal(p.id, how)
      if (how === 'accept') setDone(true)
      onDone?.()
    } catch (e) {
      const why = (e as Error).message
      setError(/404|not found/i.test(why) ? 'This suggestion is gone; the routine may have dropped it.' : `Could not do it: ${why}.`)
    } finally { setBusy(null) }
  }

  return (
    <div className={cn('rounded-xl border border-routine/30 bg-routine/5 p-3.5', inPane && 'p-4')} aria-live="polite">
      <div className="flex items-start gap-3">
        <span className={cn('grid h-8 w-8 shrink-0 place-items-center rounded-lg', kind === 'call' ? 'bg-routine' : 'bg-white ring-1 ring-routine/25')}>
          <StepIcon step={{ do: kind, channel: p.step?.channel }} size={15} className={kind === 'call' ? 'text-white' : 'text-routine'} />
        </span>
        <div className="min-w-0 flex-1">
          {inPane ? (
            <div className="text-[15px] leading-snug text-ink">
              <span className="text-ink-2">Your routine ‘{p.routine_name}’ suggests: </span>
              <span className="font-semibold">{what}</span>
            </div>
          ) : (
            <>
              <div className="flex items-center gap-1 text-xs font-semibold text-routine">
                <Workflow size={12} className="shrink-0" /> <span className="truncate">{p.routine_name}</span>
              </div>
              <div className="mt-0.5 text-[15px] font-semibold leading-snug text-ink">{what}</div>
              {context && <div className="mt-0.5 line-clamp-1 text-sm text-ink-3">{context}</div>}
            </>
          )}
          {p.preview_text && (
            <blockquote className="mt-2 line-clamp-4 whitespace-pre-line border-l-2 border-routine/30 pl-2.5 text-sm leading-relaxed text-ink-2">
              {p.preview_text}
            </blockquote>
          )}
          {error && <p className="mt-2 text-sm text-stop">{error}</p>}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {done ? (
              <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-routine"><Check size={15} /> Done</span>
            ) : (
              <>
                <Button size="sm" className="bg-routine hover:bg-routine/90" disabled={!!busy} onClick={() => answer('accept')}>
                  {busy === 'accept' ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} Do it
                </Button>
                <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => answer('skip')}>
                  {busy === 'skip' ? 'Skipping…' : 'Skip'}
                </Button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
