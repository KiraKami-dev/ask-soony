import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { type Anchor, AnchorList, CitedText, OpenAnchor, useAnchors } from '@/components/SourceAnchors'
import { fmtDay, fmtTime, tidy, type Card } from '@/lib/api'
import { cn } from '@/lib/utils'

export type DrawerView = { card: Card; tab: 'evidence' | 'trail' } | null

// All the evidence behind one decision: a sheet from the bottom on a phone, a side panel on a
// desk. Same type and ground as Today; the highlighter only on the source crops.
export function EvidenceDrawer({ view, onClose }: { view: DrawerView; onClose: () => void }) {
  const card = view?.card
  return (
    <Dialog.Root open={!!view} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fade fixed inset-0 z-40 bg-[rgba(24,24,26,.28)]" />
        {card && view && (
          <Dialog.Content
            className={cn(
              'sheet-up fixed z-50 flex flex-col bg-ground focus:outline-none',
              'inset-x-0 bottom-0 max-h-[92vh] rounded-t-[22px] shadow-[0_-10px_40px_-16px_rgba(0,0,0,.25)]',
              'md:inset-y-0 md:left-auto md:right-0 md:max-h-none md:w-[560px] md:rounded-none md:shadow-[-20px_0_60px_-30px_rgba(0,0,0,.3)]',
            )}
          >
            <div className="mx-auto mt-3 h-[5px] w-9 shrink-0 rounded-full bg-hair md:hidden" aria-hidden="true" />
            <div className="shrink-0 px-6 pb-2 pr-14 pt-5 md:px-8 md:pt-8">
              <p className="t-small text-ink-3">
                {[card.supplier_name, view.tab === 'evidence' ? 'evidence' : 'how we know'].filter(Boolean).join(' · ')}
              </p>
              <Dialog.Title className="t-body mt-2 font-medium text-ink">{tidy(card.headline)}</Dialog.Title>
              <Dialog.Description className="sr-only">Source documents and agent steps behind this decision</Dialog.Description>
            </div>
            <div className="scroll-thin flex-1 overflow-y-auto px-6 pb-[max(32px,env(safe-area-inset-bottom))] pt-4 md:px-8">
              {view.tab === 'evidence' ? <Evidence card={card} /> : <Trail card={card} />}
            </div>
            <Dialog.Close
              aria-label="Close"
              className="absolute right-4 top-4 grid h-10 w-10 cursor-pointer place-items-center rounded-full text-ink-3 hover:bg-soft hover:text-ink focus-visible:outline-2 focus-visible:outline-accent md:right-6 md:top-6"
            >
              <X size={20} strokeWidth={1.75} />
            </Dialog.Close>
          </Dialog.Content>
        )}
      </Dialog.Portal>
    </Dialog.Root>
  )
}

const SYSTEM = /system|sap|erp|plan|book stock/i
const PAPER = /paper|document|scan|advice|confirmation|delivery note|notice|invoice/i

function Evidence({ card }: { card: Card }) {
  const anchors = useAnchors(card.id)
  const [open, setOpen] = useState<Anchor | null>(null)
  if (!card.evidence.length) return <p className="t-body text-ink-2">No source documents attached to this decision yet.</p>
  // Lead with the contradiction: what the system says against what the paper says, each with its
  // source opened at the spot. Everything else follows as supporting evidence.
  const sys = card.evidence.findIndex((e) => SYSTEM.test(e.source ?? ''))
  const paper = card.evidence.findIndex((e, i) => i !== sys && PAPER.test(e.source ?? ''))
  const pair = sys >= 0 && paper >= 0 ? [sys, paper] : []
  const rest = card.evidence.map((_, i) => i).filter((i) => !pair.includes(i))
  const order = [...pair, ...rest]
  const all = order.flatMap((i) => anchors.items?.[i] ?? [])
  const point = card.summary || card.reason
  return (
    <div className="space-y-8">
      {point && (
        <div>
          <p className="t-body text-ink-2">
            <CitedText text={point} anchors={all} active={open} onOpen={setOpen} />
          </p>
          <OpenAnchor anchor={open} />
        </div>
      )}
      {pair.length > 0 && (
        <div className="space-y-6">
          <Side e={card.evidence[sys]} tone="system" anchors={anchors.items?.[sys]} ready={anchors.ready} />
          <Side e={card.evidence[paper]} tone="paper" anchors={anchors.items?.[paper]} ready={anchors.ready} />
        </div>
      )}
      {rest.length > 0 && (
        <div className="space-y-6">
          {pair.length > 0 && <p className="t-small border-t border-hair pt-6 text-ink-3">More evidence</p>}
          {rest.map((i) => (
            <EvidenceItem key={i} e={card.evidence[i]} anchors={anchors.items?.[i]} ready={anchors.ready} />
          ))}
        </div>
      )}
    </div>
  )
}

const valueText = (v: Card['evidence'][number]['value']) => (typeof v === 'number' ? v.toLocaleString('en-GB') : tidy(String(v ?? '')))

/** One supporting evidence line: its value, the quote and translation, and where it was found. */
export function EvidenceItem({ e, anchors, ready }: { e: Card['evidence'][number]; anchors?: Anchor[]; ready: boolean }) {
  return (
    <section>
      <p className="t-small text-ink-3">{e.source && !/^evidence$/i.test(e.source) ? tidy(e.source) : 'Supporting note'}</p>
      {e.value != null && <p className="t-body mt-1 font-medium text-ink">{valueText(e.value)}</p>}
      {e.quote && (
        <blockquote className="t-small mt-2 border-l border-hair pl-3 text-ink-2">
          “{e.quote}”{e.language && <span className="ml-1.5 text-ink-3">{e.language}</span>}
        </blockquote>
      )}
      {e.translation && <p className="t-small mt-1.5 pl-3 text-ink-3">In English: {e.translation}</p>}
      {e.doc && !anchors?.length && (
        <a
          href={`/api/file?path=${encodeURIComponent(e.doc)}${e.page ? `#page=${e.page}` : ''}`}
          target="_blank"
          rel="noreferrer"
          className="t-small mt-2 inline-block text-accent hover:underline"
        >
          Open the document{e.page ? `, page ${e.page}` : ''}
        </a>
      )}
      <AnchorList anchors={anchors} ready={ready} />
    </section>
  )
}

export function Side({ e, tone, anchors, ready }: {
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
    <section>
      <p className="t-small text-ink-3">
        {tone === 'system' ? 'The system says' : 'The document says'}
        <span> · {e.source?.replace(/^(System|Paper) says\s*·\s*/i, '')}</span>
      </p>
      <p className="t-body mt-1 font-medium text-ink">{valueText(e.value)}</p>
      {e.quote && <blockquote className="t-small mt-2 border-l border-hair pl-3 text-ink-2">“{cited(String(e.quote))}”</blockquote>}
      {e.translation && <p className="t-small mt-1.5 pl-3 text-ink-3">In English: {e.translation}</p>}
      <OpenAnchor anchor={open} />
      {list.length === 0 && <AnchorList anchors={anchors} ready={ready} />}
    </section>
  )
}

export function Trail({ card }: { card: Card }) {
  if (!card.trail.length) return <p className="t-body text-ink-2">No agent steps recorded for this decision yet.</p>
  return (
    <ol className="space-y-4">
      {card.trail.map((t, i) => (
        <li key={i} className="t-small grid grid-cols-[8px_1fr] gap-x-3.5">
          <i className="mt-[7px] h-[5px] w-[5px] rounded-full bg-ink-3" aria-hidden="true" />
          <div>
            <p className="text-ink-3">
              {t.agent}
              {t.at && <span> · {fmtDay(t.at)} {fmtTime(t.at)}</span>}
            </p>
            <p className="mt-0.5 text-ink-2">{t.text}</p>
          </div>
        </li>
      ))}
    </ol>
  )
}
