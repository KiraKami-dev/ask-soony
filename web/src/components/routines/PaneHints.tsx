import { Loader2, Workflow } from 'lucide-react'
import { useState } from 'react'
import type { Card } from '@/lib/api'
import { cn } from '@/lib/utils'
import { pendingOf, setEnabled, setMode, useLive } from './live'
import { ProposalItem } from './Proposals'
import { modeOf, type WouldHandle } from './types'

// What the buyer's routines say about the card in the decision pane: a step one of them
// prepared and waits to be confirmed, or a routine that would handle this card if it were on.

export function PaneHints({ card, onChanged }: { card: Card; onChanged: () => void }) {
  const live = useLive()
  const proposals = pendingOf(live, card.id)
  const asking = new Set(proposals.map((p) => p.routine_id))
  const would = card.status === 'open' ? (live.would[card.id] ?? []).filter((w) => !asking.has(w.routine_id)) : []
  if (!proposals.length && !would.length) return null
  return (
    <div className="space-y-2">
      {proposals.map((p) => <ProposalItem key={p.id} p={p} inPane onDone={onChanged} />)}
      {would.map((w) => <WouldChip key={w.routine_id} w={w} onChanged={onChanged} />)}
    </div>
  )
}

function WouldChip({ w, onChanged }: { w: WouldHandle; onChanged: () => void }) {
  const [busy, setBusy] = useState<'on' | 'ask' | null>(null)
  const [error, setError] = useState('')
  const mode = modeOf(w)

  async function go(kind: 'on' | 'ask') {
    setBusy(kind); setError('')
    try {
      if (!w.enabled) await setEnabled(w.routine_id, true)
      await setMode(w.routine_id, kind === 'on' ? 'tell' : 'ask')
      onChanged()
    } catch (e) {
      const why = (e as Error).message
      setError(/404|not found/i.test(why) ? 'Not available on the server yet.' : why)
    } finally { setBusy(null) }
  }

  const text = !w.enabled
    ? <>Your routine ‘{w.routine_name}’ would handle this</>
    : mode === 'ask'
      ? <>Your routine ‘{w.routine_name}’ will ask you about this on the next check</>
      : <>Your routine ‘{w.routine_name}’ handles this on the next check</>

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg px-3 py-2 text-sm ring-1 ring-routine/25">
      <span className="inline-flex min-w-0 items-center gap-1.5 text-ink-2">
        <Workflow size={14} className="shrink-0 text-routine" />
        <span className="min-w-0">{text}</span>
      </span>
      <span className="ml-auto inline-flex items-center gap-1">
        {!w.enabled && (
          <ChipButton busy={busy === 'on'} disabled={!!busy} onClick={() => go('on')} strong>Turn it on</ChipButton>
        )}
        {(!w.enabled || mode !== 'ask') && (
          <ChipButton busy={busy === 'ask'} disabled={!!busy} onClick={() => go('ask')}>Let it ask me first</ChipButton>
        )}
      </span>
      {error && <span className="basis-full text-xs text-stop">{error}</span>}
    </div>
  )
}

function ChipButton({
  children, busy, disabled, onClick, strong,
}: { children: string; busy?: boolean; disabled?: boolean; onClick: () => void; strong?: boolean }) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        'inline-flex cursor-pointer items-center gap-1 whitespace-nowrap rounded-md px-2 py-1 text-[13px] font-semibold disabled:opacity-60',
        strong ? 'text-routine hover:bg-routine/10' : 'text-ink-2 hover:bg-panel hover:text-ink',
      )}
    >
      {busy && <Loader2 size={12} className="animate-spin" />} {children}
    </button>
  )
}
