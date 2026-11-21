import { ExternalLink } from 'lucide-react'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet'
import { fmtDay, fmtTime, type Card } from '@/lib/api'

export type DrawerView = { card: Card; tab: 'evidence' | 'trail' } | null

export function EvidenceDrawer({ view, onClose }: { view: DrawerView; onClose: () => void }) {
  const card = view?.card
  return (
    <Sheet open={!!view} onOpenChange={(o) => !o && onClose()}>
      {card && view && (
        <SheetContent>
          <div className="border-b border-line px-6 pb-4 pt-6 pr-14">
            <div className="text-xs font-semibold uppercase tracking-wide text-ink-3">
              {view.tab === 'evidence' ? 'Evidence' : 'Agent trail'}
            </div>
            <SheetTitle className="mt-1 text-xl font-bold leading-snug">{card.headline}</SheetTitle>
            <SheetDescription className="sr-only">Source documents and agent steps behind this decision</SheetDescription>
          </div>
          <div className="flex-1 space-y-4 overflow-y-auto px-6 py-5">
            {view.tab === 'evidence' ? <Evidence card={card} /> : <Trail card={card} />}
          </div>
        </SheetContent>
      )}
    </Sheet>
  )
}

function Evidence({ card }: { card: Card }) {
  if (!card.evidence.length) return <p className="text-ink-2">No source documents attached to this decision yet.</p>
  return (
    <>
      <p className="text-sm text-ink-2">Both sides are shown. The agent does not pick one.</p>
      {card.evidence.map((e, i) => (
        <section key={i} className="rounded-xl border border-line p-4">
          <div className="flex items-baseline justify-between gap-3">
            <div className="text-[15px] font-semibold">{e.source}</div>
            {e.value != null && <div className="text-xl font-extrabold tabular-nums">{typeof e.value === 'number' ? e.value.toLocaleString('en-GB') : e.value}</div>}
          </div>
          {e.quote && (
            <blockquote className="mt-3 border-l-4 border-line pl-3 text-[15px] text-ink">
              “{e.quote}”
              {e.language && <span className="ml-2 text-xs uppercase text-ink-3">{e.language}</span>}
            </blockquote>
          )}
          {e.translation && <p className="mt-2 text-sm text-ink-2">Translation: {e.translation}</p>}
          {e.doc && (
            <a
              href={`/api/file?path=${encodeURIComponent(e.doc)}`}
              target="_blank"
              rel="noreferrer"
              className="mt-3 inline-flex items-center gap-1.5 text-sm font-semibold text-ok hover:underline"
            >
              Open source document{e.page ? `, page ${e.page}` : ''} <ExternalLink size={14} />
            </a>
          )}
        </section>
      ))}
    </>
  )
}

function Trail({ card }: { card: Card }) {
  if (!card.trail.length) return <p className="text-ink-2">No agent steps recorded for this decision yet.</p>
  return (
    <ol className="space-y-4">
      {card.trail.map((t, i) => (
        <li key={i} className="relative border-l-2 border-line pl-4">
          <span className="absolute -left-[5px] top-1.5 h-2 w-2 rounded-full bg-ok" />
          <div className="text-sm font-semibold">
            {t.agent}
            {t.at && <span className="ml-2 font-normal text-ink-3">{fmtDay(t.at)} {fmtTime(t.at)}</span>}
          </div>
          <div className="text-[15px] text-ink-2">{t.text}</div>
        </li>
      ))}
    </ol>
  )
}
