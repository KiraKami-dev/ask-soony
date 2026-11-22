import { useState } from 'react'
import { cn } from '@/lib/utils'
import { undoAction, useLive } from './live'

// "Undo" for something a routine did on its own. The server decides whether it still can (an
// email that went out cannot be unsent); its reason shows in place of the link.

export function UndoAction({
  actionId, onUndone, className, label = 'Undo',
}: { actionId: string; onUndone?: () => void; className?: string; label?: string }) {
  const live = useLive()
  const [busy, setBusy] = useState(false)
  const outcome = live.undo[actionId]

  if (outcome?.ok) return <span className={cn('t-small text-ink-3', className)}>Undone</span>
  return (
    <span className={cn('t-small inline-flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5', className)}>
      {!outcome && (
        <button
          type="button"
          disabled={busy}
          onClick={async (e) => {
            e.stopPropagation()
            setBusy(true)
            const out = await undoAction(actionId)
            setBusy(false)
            if (out.ok) onUndone?.()
          }}
          onKeyDown={(e) => e.stopPropagation()}
          className="uline disabled:opacity-60"
        >
          {busy ? 'Undoing…' : label}
        </button>
      )}
      {outcome && !outcome.ok && <span className="text-ink-2">{outcome.reason}</span>}
    </span>
  )
}
