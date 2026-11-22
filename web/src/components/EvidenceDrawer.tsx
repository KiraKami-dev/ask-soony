import { ExternalLink } from 'lucide-react'
import { useEffect, useState } from 'react'
import { type Anchor, AnchorList, CitedText, OpenAnchor, useAnchors } from '@/components/SourceAnchors'
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

const SYSTEM = /system|sap|erp|plan|book stock/i
const PAPER = /paper|document|scan|advice|confirmation|delivery note|notice|invoice/i

function Evidence({ card }: { card: Card }) {
  const anchors = useAnchors(card.id)
  if (!card.evidence.length) return <p className="text-ink-2">No source documents attached to this decision yet.</p>
  // Lead with the contradiction: what the system says against what the paper says, each with its
  // source opened at the spot. Everything else follows as supporting evidence.
  const sys = card.evidence.findIndex((e) => SYSTEM.test(e.source ?? ''))
  const paper = card.evidence.findIndex((e, i) => i !== sys && PAPER.test(e.source ?? ''))
  const pair = sys >= 0 && paper >= 0 ? [sys, paper] : []
  const rest = card.evidence.map((e, i) => [e, i] as const).filter(([, i]) => !pair.includes(i))
  const order = [...pair, ...rest.map(([, i]) => i)]
  const all = order.flatMap((i) => anchors.items?.[i] ?? [])
  const [open, setOpen] = useState<Anchor | null>(null)
  const point = card.summary || card.reason
  return (
    <>
      {point && (
        <div>
          <p className="text-[17px] leading-relaxed text-ink">
            <CitedText text={point} anchors={all} active={open} onOpen={setOpen} />
          </p>
          <OpenAnchor anchor={open} />
        </div>
      )}
      {pair.length > 0 && (
        <div className="space-y-2">
          <Side e={card.evidence[sys]} tone="system" anchors={anchors.items?.[sys]} ready={anchors.ready} />
          <div className="flex items-center gap-2 px-1 text-sm font-bold text-stop">
            <span className="h-px flex-1 bg-stop/30" /> does not match <span className="h-px flex-1 bg-stop/30" />
          </div>
          <Side e={card.evidence[paper]} tone="paper" anchors={anchors.items?.[paper]} ready={anchors.ready} />
        </div>
      )}
      {rest.length > 0 && (
        <>
          {pair.length > 0 && <div className="pt-2 text-xs font-semibold uppercase tracking-wide text-ink-3">More evidence</div>}
          {rest.map(([e, i]) => (
            <section key={i} className="rounded-xl border border-line p-4">
              <div className="flex items-baseline justify-between gap-3">
                <div className="text-[15px] font-semibold">{e.source}</div>
                {e.value != null && String(e.value).length <= 24 && <div className="text-xl font-extrabold tabular-nums">{typeof e.value === 'number' ? e.value.toLocaleString('en-GB') : e.value}</div>}
              </div>
              {e.value != null && String(e.value).length > 24 && <p className="mt-1.5 text-[15px] font-semibold text-ink">{e.value}</p>}
              {e.quote && (
                <blockquote className="mt-3 border-l-4 border-line pl-3 text-[15px] text-ink">
                  “{e.quote}”
                  {e.language && <span className="ml-2 text-xs uppercase text-ink-3">{e.language}</span>}
                </blockquote>
              )}
              {e.translation && <p className="mt-2 text-sm text-ink-2">In English: {e.translation}</p>}
              {e.doc && !anchors.items?.[i]?.length && (
                <a
                  href={`/api/file?path=${encodeURIComponent(e.doc)}${e.page ? `#page=${e.page}` : ''}`}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-3 inline-flex items-center gap-1.5 text-sm font-semibold text-ok hover:underline"
                >
                  Open source document{e.page ? `, page ${e.page}` : ''} <ExternalLink size={14} />
                </a>
              )}
              <AnchorList anchors={anchors.items?.[i]} ready={anchors.ready} />
            </section>
          ))}
        </>
      )}
    </>
  )
}

function Side({ e, tone, anchors, ready }: {
  e: Card['evidence'][number]; tone: 'system' | 'paper'; anchors?: Anchor[]; ready: boolean
}) {
  const [open, setOpen] = useState<Anchor | null>(null)
  const list = anchors ?? []
  const first = list[0]
  useEffect(() => {
    if (tone === 'paper' && first) setOpen((o) => o ?? first) // show the document right away
  }, [tone, first])
  const cited = (t: string) => <CitedText text={t} anchors={list} active={open} onOpen={setOpen} />
  return (
    <section className={tone === 'system' ? 'rounded-xl border border-line bg-panel p-4' : 'rounded-xl border-2 border-stop/40 bg-white p-4'}>
      <div className="text-xs font-bold uppercase tracking-wide text-ink-3">{tone === 'system' ? 'The system says' : 'The document says'}</div>
      <div className="mt-1 text-[22px] font-extrabold leading-tight tabular-nums">{e.value}</div>
      <div className="mt-1 text-sm text-ink-2">{e.source?.replace(/^(System|Paper) says\s*·\s*/i, '')}</div>
      {e.quote && <blockquote className="mt-2 border-l-4 border-line pl-3 text-sm leading-relaxed text-ink">“{cited(String(e.quote))}”</blockquote>}
      {e.translation && <p className="mt-1.5 text-sm text-ink-2">In English: {e.translation}</p>}
      <OpenAnchor anchor={open} />
      {list.length === 0 && <AnchorList anchors={anchors} ready={ready} />}
    </section>
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
