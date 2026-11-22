import { ExternalLink, FileText, Image as ImageIcon, Table } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { cn } from '@/lib/utils'

// Where an evidence line was found in the portal documents (anchors.py): the PDF page or scan
// with the matched words highlighted, or the spreadsheet row. Opens inline, original one tap away.

export interface Anchor {
  doc: string
  kind: 'pdf' | 'image' | 'table' | 'text'
  page?: number
  row?: number
  sheet?: string
  score: number
  matched: string[]
  boxes?: [number, number, number, number][]
  snippet?: string
  source?: 'text' | 'ocr'
}
type Items = Record<number, Anchor[]>

export function useAnchors(cardId: string): { items: Items | null; ready: boolean } {
  const [items, setItems] = useState<Items | null>(null)
  const [ready, setReady] = useState(true)
  useEffect(() => {
    let stop = false
    let timer: number | undefined
    const load = async () => {
      try {
        const r = await fetch(`/api/anchors/${encodeURIComponent(cardId)}`)
        if (!r.ok) return
        const d = (await r.json()) as { ready: boolean; items: { evidence: number; anchors: Anchor[] }[] }
        if (stop) return
        setItems(Object.fromEntries(d.items.map((x) => [x.evidence, x.anchors])))
        setReady(d.ready)
        if (!d.ready) timer = window.setTimeout(load, 4000) // index still building
      } catch {
        /* sources are optional */
      }
    }
    void load()
    return () => {
      stop = true
      clearTimeout(timer)
    }
  }, [cardId])
  return { items, ready }
}

const name = (doc: string) => doc.split('/').pop() ?? doc
export const fileUrl = (a: Anchor) =>
  `/api/file?path=${encodeURIComponent(a.doc)}${a.kind === 'pdf' && a.page ? `#page=${a.page}` : ''}`

export function AnchorList({
  anchors, ready, defaultOpen = null,
}: { anchors?: Anchor[]; ready: boolean; defaultOpen?: number | null }) {
  const [open, setOpen] = useState<number | null>(defaultOpen)
  if (!anchors) return null
  if (!anchors.length) {
    return ready ? null : <p className="mt-3 text-sm text-ink-3">Looking for this in the documents…</p>
  }
  return (
    <div className="mt-3">
      <div className="text-xs font-semibold uppercase tracking-wide text-ink-3">Found in</div>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {anchors.map((a, i) => {
          const Icon = a.kind === 'table' ? Table : a.kind === 'image' ? ImageIcon : FileText
          const where = a.kind === 'table' || a.kind === 'text' ? `row ${(a.row ?? 0) + 1}` : `page ${a.page}`
          return (
            <button
              key={i}
              onClick={() => setOpen(open === i ? null : i)}
              className={cn(
                'inline-flex max-w-full cursor-pointer items-center gap-1.5 rounded-full border px-3 py-1 text-sm',
                open === i ? 'border-ok bg-ok/10 font-semibold text-ok' : 'border-line text-ink-2 hover:bg-panel',
              )}
              title={a.doc}
            >
              <Icon size={14} className="shrink-0" />
              <span className="truncate">{name(a.doc)}</span>
              <span className="shrink-0 text-ink-3">· {where}{a.source === 'ocr' ? ' · scan' : ''}</span>
            </button>
          )
        })}
      </div>
      {open != null && anchors[open] && <AnchorView anchor={anchors[open]} />}
    </div>
  )
}

/** Just the highlighted crop (page, scan, table row or text), no links: for the source sheet. */
export function AnchorCrop({ anchor: a }: { anchor: Anchor }) {
  return a.kind === 'table' ? <TableView a={a} /> : a.kind === 'text' ? <TextView a={a} /> : <PageView a={a} />
}

function AnchorView({ anchor: a }: { anchor: Anchor }) {
  return (
    <div className="mt-3 rounded-lg border border-line bg-panel p-2">
      {a.kind === 'table' ? <TableView a={a} /> : a.kind === 'text' ? <TextView a={a} /> : <PageView a={a} />}
      <a
        href={fileUrl(a)}
        target="_blank"
        rel="noreferrer"
        className="mt-2 inline-flex items-center gap-1.5 px-1 text-sm font-semibold text-ok hover:underline"
      >
        Open original <ExternalLink size={14} />
      </a>
    </div>
  )
}

function PageView({ a }: { a: Anchor }) {
  // Zoomed in on the highlighted values (with some context), full page one tap away. No
  // scrollIntoView: it would scroll the whole drawer away from the point being made.
  const [size, setSize] = useState<{ w: number; h: number } | null>(null)
  const [full, setFull] = useState(false)
  const boxes = a.boxes ?? []
  const src = `/api/doc/page?path=${encodeURIComponent(a.doc)}&page=${a.page ?? 1}`
  let crop = { x0: 0, y0: 0, x1: 1, y1: 1 }
  if (boxes.length && !full) {
    const x0 = Math.min(...boxes.map((b) => b[0])), x1 = Math.max(...boxes.map((b) => b[2]))
    const y0 = Math.min(...boxes.map((b) => b[1])), y1 = Math.max(...boxes.map((b) => b[3]))
    const cx = (x0 + x1) / 2, w = Math.max(0.5, x1 - x0 + 0.12)
    const left = Math.min(Math.max(0, cx - w / 2), 1 - Math.min(1, w))
    crop = { x0: left, x1: Math.min(1, left + w), y0: Math.max(0, y0 - 0.05), y1: Math.min(1, y1 + 0.05) }
  }
  const cw = crop.x1 - crop.x0, ch = crop.y1 - crop.y0
  return (
    <div>
      <div
        className="relative overflow-hidden rounded bg-white"
        style={size ? { aspectRatio: `${cw * size.w} / ${ch * size.h}` } : { minHeight: 120 }}
      >
        <div
          className="absolute"
          style={{ width: `${100 / cw}%`, height: `${100 / ch}%`, left: `${(-crop.x0 / cw) * 100}%`, top: `${(-crop.y0 / ch) * 100}%` }}
        >
          <img
            src={src}
            alt={name(a.doc)}
            className="block h-full w-full"
            onLoad={(e) => setSize({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
          />
          {size &&
            boxes.map(([x0, y0, x1, y1], i) => (
              <div
                key={i}
                className="absolute rounded-[2px] bg-mark/60 mix-blend-multiply"
                style={{
                  left: `${x0 * 100 - 0.3}%`,
                  top: `${y0 * 100 - 0.25}%`,
                  width: `${(x1 - x0) * 100 + 0.6}%`,
                  height: `${(y1 - y0) * 100 + 0.5}%`,
                }}
              />
            ))}
        </div>
      </div>
      {boxes.length > 0 && (
        <button className="mt-2 cursor-pointer px-1 text-[13px] text-ink-3 hover:text-ink" onClick={() => setFull(!full)}>
          {full ? 'Zoom to the values' : 'Show full page'}
        </button>
      )}
    </div>
  )
}

function TableView({ a }: { a: Anchor }) {
  const [data, setData] = useState<{ header: string[]; rows: { row: number; cells: string[]; hit: boolean }[] } | null>(null)
  const hit = useRef<HTMLTableRowElement | null>(null)
  useEffect(() => {
    const q = new URLSearchParams({ path: a.doc, row: String(a.row ?? 0), sheet: a.sheet ?? '' })
    fetch(`/api/doc/table?${q}`).then((r) => (r.ok ? r.json() : null)).then(setData).catch(() => setData(null))
  }, [a.doc, a.row, a.sheet])
  const box = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const el = hit.current, wrap = box.current
    if (el && wrap) wrap.scrollTop = el.offsetTop - wrap.clientHeight / 2
  }, [data])
  if (!data) return <p className="p-2 text-sm text-ink-3">Loading table…</p>
  const wanted = new Set(a.matched)
  const norm = (s: string) => s.replace(/(?<=\d)[,.' ](?=\d{3}(?!\d))/g, '').toUpperCase()
  return (
    <div ref={box} className="relative max-h-[40vh] overflow-auto rounded bg-white">
      {a.sheet && <div className="px-2 pt-2 text-xs font-semibold text-ink-3">Sheet: {a.sheet}</div>}
      <table className="w-full border-collapse text-[13px] tabular-nums">
        <thead className="sticky top-0 bg-panel">
          <tr>{data.header.map((h, i) => <th key={i} className="whitespace-nowrap border-b border-line px-2 py-1 text-left font-semibold text-ink-2">{h}</th>)}</tr>
        </thead>
        <tbody>
          {data.rows.map((r) => (
            <tr key={r.row} ref={r.hit ? hit : undefined} className={r.hit ? 'bg-mark/25' : ''}>
              {r.cells.map((c, i) => (
                <td
                  key={i}
                  className={cn(
                    'whitespace-nowrap border-b border-line px-2 py-1',
                    r.hit && [...wanted].some((w) => norm(c).includes(w)) && 'bg-mark font-semibold text-ink',
                  )}
                >
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function TextView({ a }: { a: Anchor }) {
  return <p className="whitespace-pre-wrap break-words rounded bg-mark/30 p-2 font-mono text-[13px]">{a.snippet}</p>
}

// ---------------------------------------------------------------- inline citations

export const NUM_OR_ID = /[A-Za-z]*\d[\d,.' ]*\d(?:[-_/][A-Za-z0-9]+)*|[A-Za-z]+[-_]?\d{3,}[A-Za-z0-9-]*/g
export const normTok = (s: string) => s.replace(/(?<=\d)[,.'  ](?=\d{3}(?!\d))/g, '').toUpperCase()

// 'purchase_orders_2025H2-2026.csv' -> 'purchase orders': the words of the file name, no ids or dates
const label = (doc: string) =>
  name(doc).replace(/\.[a-z0-9]+$/i, '').split(/[_\s-]+/).filter((w) => w && !/\d/.test(w)).join(' ').toLowerCase() || name(doc)
const tag = (a: Anchor) =>
  a.kind === 'table' || a.kind === 'text'
    ? `${label(a.doc)} · row ${(a.row ?? 0) + 1}`
    : `${a.kind === 'image' || a.source === 'ocr' ? 'scan' : 'PDF'} · p. ${a.page ?? 1}`

/**
 * Text with every number or id that the agent found in a source turned into a citation chip.
 * `anchors` are tried in order, so pass the preferred source first.
 */
export function CitedText({
  text, anchors, active, onOpen, className,
}: {
  text: string
  anchors: Anchor[]
  active?: Anchor | null
  onOpen: (a: Anchor | null) => void
  className?: string
}) {
  const parts: (string | { raw: string; a: Anchor })[] = []
  let last = 0
  for (const m of text.matchAll(NUM_OR_ID)) {
    const a = anchors.find((x) => x.matched.includes(normTok(m[0])))
    if (!a) continue
    parts.push(text.slice(last, m.index), { raw: m[0], a })
    last = (m.index ?? 0) + m[0].length
  }
  parts.push(text.slice(last))
  return (
    <span className={className}>
      {parts.map((p, i) =>
        typeof p === 'string' ? (
          <span key={i}>{p}</span>
        ) : (
          <button
            key={i}
            onClick={() => onOpen(active === p.a ? null : p.a)}
            title={`Found in ${p.a.doc}`}
            className={cn(
              'mx-0.5 inline-flex cursor-pointer items-baseline gap-1 rounded-md px-1.5 align-baseline font-bold underline decoration-dotted underline-offset-4',
              active === p.a ? 'bg-yellow-300/70 text-ink' : 'bg-yellow-100 text-ink hover:bg-yellow-200',
            )}
          >
            {p.raw}
            <span className="whitespace-nowrap text-[11px] font-semibold text-ink-3 no-underline">{tag(p.a)}</span>
          </button>
        ),
      )}
    </span>
  )
}

export function OpenAnchor({ anchor }: { anchor: Anchor | null }) {
  return anchor ? <AnchorView anchor={anchor} /> : null
}
