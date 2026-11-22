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
const fileUrl = (a: Anchor) =>
  `/api/file?path=${encodeURIComponent(a.doc)}${a.kind === 'pdf' && a.page ? `#page=${a.page}` : ''}`

export function AnchorList({ anchors, ready }: { anchors?: Anchor[]; ready: boolean }) {
  const [open, setOpen] = useState<number | null>(null)
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
  const box = useRef<HTMLDivElement | null>(null)
  const [loaded, setLoaded] = useState(false)
  useEffect(() => {
    if (loaded) box.current?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [loaded])
  const src = `/api/doc/page?path=${encodeURIComponent(a.doc)}&page=${a.page ?? 1}`
  return (
    <div className="max-h-[60vh] overflow-auto rounded bg-white">
      <div className="relative">
        <img src={src} alt={name(a.doc)} className="block w-full" onLoad={() => setLoaded(true)} />
        {loaded &&
          (a.boxes ?? []).map(([x0, y0, x1, y1], i) => (
            <div
              key={i}
              ref={i === 0 ? box : undefined}
              className="absolute rounded-sm bg-yellow-300/45 ring-2 ring-amber-500"
              style={{
                left: `${x0 * 100 - 0.4}%`,
                top: `${y0 * 100 - 0.3}%`,
                width: `${(x1 - x0) * 100 + 0.8}%`,
                height: `${(y1 - y0) * 100 + 0.6}%`,
              }}
            />
          ))}
      </div>
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
  useEffect(() => {
    hit.current?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [data])
  if (!data) return <p className="p-2 text-sm text-ink-3">Loading table…</p>
  const wanted = new Set(a.matched)
  const norm = (s: string) => s.replace(/(?<=\d)[,.' ](?=\d{3}(?!\d))/g, '').toUpperCase()
  return (
    <div className="max-h-[50vh] overflow-auto rounded bg-white">
      {a.sheet && <div className="px-2 pt-2 text-xs font-semibold text-ink-3">Sheet: {a.sheet}</div>}
      <table className="w-full border-collapse text-[13px] tabular-nums">
        <thead className="sticky top-0 bg-panel">
          <tr>{data.header.map((h, i) => <th key={i} className="whitespace-nowrap border-b border-line px-2 py-1 text-left font-semibold text-ink-2">{h}</th>)}</tr>
        </thead>
        <tbody>
          {data.rows.map((r) => (
            <tr key={r.row} ref={r.hit ? hit : undefined} className={r.hit ? 'bg-yellow-100' : ''}>
              {r.cells.map((c, i) => (
                <td
                  key={i}
                  className={cn(
                    'whitespace-nowrap border-b border-line px-2 py-1',
                    r.hit && [...wanted].some((w) => norm(c).includes(w)) && 'bg-yellow-300/70 font-bold',
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
  return <p className="whitespace-pre-wrap break-words rounded bg-yellow-100 p-2 font-mono text-[13px]">{a.snippet}</p>
}
