import { Loader2, Undo2 } from 'lucide-react'
import { useState } from 'react'
import { cn } from '@/lib/utils'
import { undoAction, useLive } from './live'

// "Undo" for something a routine did on its own. The server decides whether it still can (an
// email that went out cannot be unsent); its reason shows in place of the button.

export function UndoAction({
  actionId, onUndone, className,
}: { actionId: string; onUndone?: () => void; className?: string }) {
  const live = useLive()
  const [busy, setBusy] = useState(false)
  const outcome = live.undo[actionId]

  if (outcome?.ok) {
    return <span className={cn('inline-flex items-center gap-1 text-xs font-semibold text-ink-3', className)}><Undo2 size={12} /> Undone</span>
  }
  return (
    <span className={cn('inline-flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5', className)}>
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
          className="inline-flex cursor-pointer items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-semibold text-ink-2 ring-1 ring-line hover:bg-white hover:text-ink disabled:opacity-60"
        >
          {busy ? <Loader2 size={12} className="animate-spin" /> : <Undo2 size={12} />} Undo
        </button>
      )}
      {outcome && !outcome.ok && <span className="text-xs font-medium text-stop">{outcome.reason}</span>}
    </span>
  )
}
