import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import { fmtDay, fmtNum, fmtTime, partName, tidy, type Card, type ClockEdition, type Level, type State } from '@/lib/api'

/** "Decide by" for your own decisions; "With Maren · due" once it was passed on. */
const dueWord = (c?: Card | null) => {
  if (!c || !(c.status === 'escalated' || c.routed_to_other)) return 'Decide by'
  const who = (c.approver ?? '').split(' ').find((w) => !w.endsWith('.')) || 'the approver'
  return `With ${who} · due`
}

// The launch timeline: shift hours to scale, nights squeezed, a thick truck line at launch and a
// "Too late" gutter past it. One lane per edition (packing bar, decide-by and runs-dry markers),
// one inbound lane (shipment ETAs, late goods stated on cards). Everything comes from
// state.clock, state.cards and state.shipments; nothing about the launch is written here.
export interface LaunchTimelineProps {
  state: State
  nowMs: number
  selectedId: string | null
  onSelect: (cardId: string) => void
  compact?: boolean
}

const TZ = 'Europe/Berlin'
const HOUR = 3_600_000
const NIGHT = 0.25 // a night hour gets a quarter of a shift hour's width
// The app's tokens (index.css): ink, hairlines, the one blue for the selection, red only for
// what misses the trucks.
const INK = 'var(--color-ink)'
const INK2 = 'var(--color-ink-2)'
const INK3 = 'var(--color-ink-3)'
const LINE = 'var(--color-hair)'
const GROUND = 'var(--color-ground)'
const NIGHT_FILL = 'var(--color-soft)'
const BAR = '#c9c9ce'
const OK = 'var(--color-accent)'
const STOP = 'var(--color-late)'
const SILENT = 'var(--color-ink-3)'
const OPEN = new Set(['open', 'escalated'])

const fmtDate = (ms: number) =>
  new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: TZ }).format(new Date(ms))
const fmtShort = (ms: number) =>
  new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', timeZone: TZ }).format(new Date(ms))
const ymd = (ms: number) => new Intl.DateTimeFormat('en-CA', { timeZone: TZ }).format(new Date(ms))
const ms = (iso?: string | null) => (iso ? new Date(iso).getTime() : NaN)
const ok = (n: number) => Number.isFinite(n)
const clip = (t: string, n: number) => (t.length > n ? `${t.slice(0, Math.max(n - 1, 1)).trimEnd()}…` : t)

/** Red only for what misses the trucks; everything else packs in quiet grey. */
const tone = (l: Level) => (l === 'red' ? STOP : INK3)

/** A short readable name for what a card is about: the part, else the start of its headline. */
function what(c: Card, cards: Card[], n = 34): string {
  const p = c.part_name ?? partName(c.part, cards)?.split(' – ')[0]
  if (p) return clip(p, n)
  return clip(tidy(c.headline).split(/[.;:]/)[0], n)
}

interface Seg { a: number; b: number; shift: boolean }

function useWidth() {
  const ref = useRef<HTMLDivElement>(null)
  const [w, setW] = useState(0)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    setW(el.clientWidth)
    const ro = new ResizeObserver(() => setW(el.clientWidth))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, w] as const
}

interface Tip { x: number; y: number; text: string }

export function LaunchTimeline({ state, nowMs, selectedId, onSelect, compact = false }: LaunchTimelineProps) {
  const [ref, width] = useWidth()
  const [tip, setTip] = useState<Tip | null>(null)
  const clock = state.clock
  const H = compact ? 116 : 176

  const model = useMemo((): Model | null => {
    if (!clock || !width) return null
    const launch = ms(clock.launch)
    const windows = clock.windows.map(([s, e]) => [ms(s), ms(e)] as [number, number]).filter(([s, e]) => ok(s) && ok(e))
    if (!ok(launch) || !windows.length) return null
    const start = Math.min(nowMs, windows[0][0])

    // Piecewise scale: shift hours at full width, gaps (nights) at NIGHT.
    const segs: Seg[] = []
    let t = start
    for (const [s, e] of windows) {
      if (e <= t) continue
      if (s > t) segs.push({ a: t, b: Math.min(s, launch), shift: false })
      const a = Math.max(s, t)
      const b = Math.min(e, launch)
      if (b > a) segs.push({ a, b, shift: true })
      t = Math.max(t, b)
      if (t >= launch) break
    }
    if (t < launch) segs.push({ a: t, b: launch, shift: false })
    const weight = (g: Seg, a = g.a, b = g.b) => ((b - a) / HOUR) * (g.shift ? 1 : NIGHT)
    const total = segs.reduce((n, g) => n + weight(g), 0) || 1

    const labelW = compact ? 74 : 112
    const gutterW = Math.round(Math.max(width * 0.12, compact ? 86 : 184))
    const x0 = labelW
    const x1 = width - gutterW
    const x = (tm: number) => {
      if (tm <= start) return x0
      if (tm >= launch) return x1
      let n = 0
      for (const g of segs) {
        if (tm >= g.b) n += weight(g)
        else { if (tm > g.a) n += weight(g, g.a, tm); break }
      }
      return x0 + ((x1 - x0) * n) / total
    }
    return { launch, windows, start, segs, x, x0, x1, gutterW }
  }, [clock, width, nowMs, compact])

  // Rows: tick row, one lane per edition, the inbound lane.
  const tickH = compact ? 20 : 28
  const laneH = compact ? 36 : 52
  const inboundH = compact ? 24 : 40
  const editions: ClockEdition[] = clock?.editions ?? []

  if (!clock) {
    return (
      <div ref={ref} style={{ height: H }} className="t-small grid place-items-center text-ink-3">
        No packing plan found in the portal yet
      </div>
    )
  }

  const hover = (e: React.MouseEvent | React.FocusEvent, text: string) => {
    const box = ref.current?.getBoundingClientRect()
    const r = (e.currentTarget as Element).getBoundingClientRect()
    if (box) setTip({ x: r.left + r.width / 2 - box.left, y: r.top - box.top, text })
  }
  const leave = () => setTip(null)

  const body = model && (() => {
    const { x, x0, x1, segs, launch, windows, start, gutterW } = model
    const lanesTop = tickH
    const lanesBottom = tickH + editions.length * laneH + inboundH
    const nowX = nowMs >= start && nowMs < launch ? x(nowMs) : nowMs >= launch ? x1 : null
    const open = state.cards.filter((c) => OPEN.has(c.status))
    const cardsBy = (id: string) => open.find((c) => c.id === id)
    const sel = (ids: string[]) => !!selectedId && ids.includes(selectedId)
    const pick = (ids: string[]) => {
      const i = selectedId ? ids.indexOf(selectedId) : -1
      onSelect(ids[(i + 1) % ids.length])
    }

    // ---- ticks
    const ticks: { t: number; label: string; day: boolean }[] = []
    for (const [s, e] of windows) {
      if (e <= start || s >= launch) continue
      const list = [s]
      let k = s + 6 * HOUR
      while (k < e - 3 * HOUR) { list.push(k); k += 6 * HOUR }
      const last = list[list.length - 1]
      if (e - last > 6 * HOUR) list.push(last + Math.round((e - last) / 2 / HOUR) * HOUR)
      if (e < launch) list.push(e)
      list.forEach((tm, i) => {
        if (tm < start) return
        const day = i === 0
        if (compact && !day) return
        ticks.push({ t: tm, label: day ? `${fmtDay(new Date(tm).toISOString())} ${fmtTime(new Date(tm).toISOString()).slice(0, 2)}` : fmtTime(new Date(tm).toISOString()).slice(0, 2), day })
      })
    }

    // ---- gutter items: late goods named on cards, then shipments a card links, both past launch
    type Late = { key: string; at: number; title: string; sub: string; cards: string[] }
    const late: Late[] = []
    for (const c of open) {
      const at = ms(c.arrives_at)
      if (!ok(at) || at < launch) continue
      late.push({ key: `c-${c.id}`, at, title: fmtDate(at), sub: `${c.number != null ? `${fmtNum(c.number)} ` : ''}${what(c, state.cards, 30)}`, cards: [c.id] })
    }
    let laterUnlinked = 0
    for (const s of state.shipments) {
      const at = etaMs(s.eta, windows)
      if (!ok(at) || at < launch) continue
      const linked = open.filter((c) => c.shipment === s.id)
      if (!linked.length) { laterUnlinked += 1; continue }
      if (late.some((l) => linked.some((c) => l.cards.includes(c.id)))) continue
      late.push({ key: `s-${s.id}`, at, title: fmtDate(at), sub: `${fmtNum(linked[0].number)} ${what(linked[0], state.cards, 30)}`, cards: linked.map((c) => c.id) })
    }
    late.sort((a, b) => a.at - b.at)
    const lateRowH = compact ? 22 : 38
    const lateMax = Math.max(1, Math.floor((lanesBottom - lanesTop - (compact ? 4 : 18)) / lateRowH))
    const lateShown = late.slice(0, lateMax)
    const lateMore = late.length - lateShown.length + laterUnlinked

    const gx = x1 + (compact ? 10 : 18)
    const truckLabel = `${compact ? '' : 'Trucks '}${fmtTime(new Date(launch).toISOString())}`

    return (
      <svg width={width} height={H} className="block select-none" role="img" aria-label="Launch timeline">

        {/* nights */}
        {segs.filter((g) => !g.shift).map((g) => {
          const a = x(g.a), b = x(g.b)
          return (
            <g key={`n-${g.a}`}>
              <rect x={a} y={lanesTop} width={Math.max(b - a, 0)} height={lanesBottom - lanesTop} fill={NIGHT_FILL} />
              {b - a >= 56 && !compact && (
                <text x={(a + b) / 2} y={lanesBottom - 8} textAnchor="middle" fontSize={12} fill={INK3}>night</text>
              )}
            </g>
          )
        })}

        {/* lane separators */}
        {editions.map((_, i) => (
          <line key={`sep-${i}`} x1={0} x2={width} y1={lanesTop + (i + 1) * laneH} y2={lanesTop + (i + 1) * laneH} stroke={LINE} />
        ))}
        <line x1={0} x2={width} y1={lanesTop} y2={lanesTop} stroke={LINE} />

        {/* ticks */}
        {ticks.map((tk) => {
          const tx = x(tk.t)
          const underNow = (!compact && nowX != null && Math.abs(tx - nowX) < 44) || tx > x1 - (compact ? 58 : 104)
          return (
            <g key={`t-${tk.t}`}>
              <line x1={tx} x2={tx} y1={lanesTop} y2={lanesBottom} stroke={LINE} />
              {!underNow && <text x={tx} y={tickH - 11} textAnchor={tx - x0 < 20 ? 'start' : 'middle'} fontSize={12} fill={tk.day ? INK2 : INK3}>{tk.label}</text>}
            </g>
          )
        })}

        {/* lane labels */}
        {editions.map((ed, i) => (
          <text key={`l-${ed.edition}`} x={0} y={lanesTop + i * laneH + (compact ? 22 : 32)} fontSize={13} fontWeight={500} fill={INK}>{ed.label}</text>
        ))}
        <text x={0} y={lanesTop + editions.length * laneH + (compact ? 19 : 20)} fontSize={13} fill={INK2}>Inbound</text>

        {/* edition lanes: bars under the past fade, words and markers above it */}
        {(['bars', 'top'] as const).map((layer) => (
          <g key={layer}>
            {layer === 'top' && nowX != null && nowX > x0 && (
              <rect x={x0} y={lanesTop} width={nowX - x0} height={lanesBottom - lanesTop} fill={GROUND} opacity={0.55} pointerEvents="none" />
            )}
            {editions.map((ed, i) => (
              <EditionLane
                key={ed.edition} layer={layer} ed={ed} top={lanesTop + i * laneH} compact={compact} model={model} nowMs={nowMs}
                cards={open.filter((c) => c.edition === ed.edition)} all={state.cards}
                selected={sel} pick={pick} hover={hover} leave={leave} cardsBy={cardsBy}
              />
            ))}
          </g>
        ))}

        {/* inbound lane */}
        <Inbound
          top={lanesTop + editions.length * laneH} h={inboundH} compact={compact} model={model}
          state={state} open={open} selected={sel} pick={pick} hover={hover} leave={leave}
        />

        {/* truck line and gutter */}
        <rect x={x1} y={lanesTop} width={gutterW} height={lanesBottom - lanesTop} fill={GROUND} />
        <line x1={x1} x2={x1} y1={lanesTop - 8} y2={lanesBottom} stroke={INK} strokeWidth={2} />
        <text x={x1 - 8} y={tickH - 11} textAnchor="end" fontSize={12} fontWeight={500} fill={INK}>{truckLabel}</text>
        <text x={gx} y={tickH - 11} fontSize={12} fontWeight={500} fill={STOP}>Too late</text>
        {lateShown.map((l, i) => {
          const y = lanesTop + (compact ? 14 : 20) + i * lateRowH
          const s = sel(l.cards)
          return (
            <g
              key={l.key} className="cursor-pointer" tabIndex={0} role="button"
              aria-label={`${l.sub}, arrives ${l.title}, after launch`}
              onClick={() => pick(l.cards)} onKeyDown={(e) => e.key === 'Enter' && pick(l.cards)}
              onMouseEnter={(e) => hover(e, `${l.sub}: arrives ${l.title}, after the trucks leave`)} onMouseLeave={leave}
              onFocus={(e) => hover(e, `${l.sub}: arrives ${l.title}`)} onBlur={leave}
            >
              <rect x={x1 + 2} y={y - 16} width={gutterW - 2} height={Math.max(lateRowH, 32)} fill="transparent" />
              {s && <circle cx={gx + 3} cy={y - 4} r={7} fill="none" stroke={OK} strokeWidth={1.5} />}
              <circle cx={gx + 3} cy={y - 4} r={3.5} fill={STOP} />
              <text x={gx + 14} y={y} fontSize={compact ? 12 : 13} fontWeight={500} fill={STOP}>{compact ? fmtShort(l.at) : l.title}</text>
              {!compact && <text x={gx + 14} y={y + 16} fontSize={12} fill={INK2}>{clip(l.sub, Math.floor((width - gx - 20) / 6.4))}</text>}
            </g>
          )
        })}
        {lateMore > 0 && (
          <text
            x={gx} y={lanesBottom - 6} fontSize={12} fill={INK3}
            onMouseEnter={(e) => hover(e, `${lateMore} more shipment${lateMore === 1 ? '' : 's'} due after launch, none tied to an open decision`)} onMouseLeave={leave}
          >
            +{lateMore} later{compact ? '' : ', not on a card'}
          </text>
        )}

        {/* now */}
        {nowX != null && (
          <g pointerEvents="none">
            <line x1={nowX} x2={nowX} y1={lanesTop - 8} y2={lanesBottom} stroke={INK} strokeWidth={1} />
            {!compact && <text x={nowX} y={tickH - 11} textAnchor="middle" fontSize={12} fontWeight={500} fill={INK}>now</text>}
          </g>
        )}
      </svg>
    )
  })()

  return (
    <div ref={ref} className="relative w-full overflow-visible" style={{ height: H }} onMouseLeave={leave}>
      {body}
      {tip && (
        <div
          className="t-small pointer-events-none absolute z-20 max-w-[340px] -translate-x-1/2 -translate-y-full truncate rounded-lg bg-ink px-3 py-1.5 text-white shadow-[0_8px_24px_-10px_rgba(0,0,0,.35)]"
          style={{ left: Math.min(Math.max(tip.x, 120), Math.max(width - 120, 120)), top: tip.y - 6 }}
        >
          {tip.text}
        </div>
      )}
    </div>
  )
}

/** A shipment ETA in ms. Date-only ETAs ("2026-11-22") land at the first shift of that day, or
 *  midday when no shift runs on it. */
function etaMs(eta: string | undefined, windows: [number, number][]): number {
  if (!eta) return NaN
  if (/^\d{4}-\d{2}-\d{2}$/.test(eta)) {
    const w = windows.find(([s]) => ymd(s) === eta)
    return w ? w[0] : ms(`${eta}T12:00:00Z`)
  }
  return ms(eta)
}

interface Model {
  launch: number; windows: [number, number][]; start: number; segs: Seg[]
  x: (t: number) => number; x0: number; x1: number; gutterW: number
}

interface Shared {
  compact: boolean
  model: Model
  selected: (ids: string[]) => boolean
  pick: (ids: string[]) => void
  hover: (e: React.MouseEvent | React.FocusEvent, text: string) => void
  leave: () => void
}

interface Marker { key: string; x: number; kind: 'decide' | 'dry'; at: number; cards: Card[] }

function EditionLane({
  layer, ed, top, cards, all, nowMs, cardsBy, compact, model, selected, pick, hover, leave,
}: Shared & {
  layer: 'bars' | 'top'; ed: ClockEdition; top: number; cards: Card[]; all: Card[]; nowMs: number
  cardsBy: (id: string) => Card | undefined
}) {
  const { x, x0, x1, segs, launch, start } = model
  const barY = top + (compact ? 21 : 27)
  const barH = compact ? 5 : 6
  const midY = barY + barH / 2
  const finish = ms(ed.finishes_at)
  const stop = ms(ed.stops_at)
  const end = ok(stop) ? stop : ok(finish) ? finish : launch
  const colour = ed.level === 'red' ? INK3 : BAR
  const shifts = segs.filter((g) => g.shift)
  const pieces = (a: number, b: number) =>
    shifts.map((g) => [Math.max(g.a, a), Math.min(g.b, b)] as const).filter(([p, q]) => q > p)

  // Status words at the end of the bar.
  let status: string
  let statusColour = INK2
  if (ok(stop) && stop < launch) {
    status = `${stop <= nowMs ? 'stopped' : 'stops'} ${fmtDay(ed.stops_at)} ${fmtTime(ed.stops_at)} · ${fmtNum(ed.copies_at_risk)} short`
    statusColour = STOP
  } else if (ok(finish)) {
    const spare = ed.slack_hours
    status = `makes the trucks${spare != null && spare > 0 ? ` · ${spare < 1 ? `${Math.round(spare * 60)} min` : `${Math.round(spare * 10) / 10} h`} spare` : ''}`
  } else {
    status = `runs out of time${ed.short_of_time ? ` · ${fmtNum(ed.short_of_time)} unpacked` : ''}`
    statusColour = STOP
  }
  if (compact) status = status.replace(/^(stops|stopped) \w+ /, '$1 ').replace('makes the trucks', 'on time')
  const endX = x(end)
  const approxW = status.length * (compact ? 6.4 : 7)
  const statusRight = endX + approxW > x1 - 6
  const statusX = statusRight ? x1 - 8 : Math.max(endX + 2, x0)

  // Markers: decide-by diamonds and runs-dry triangles, clustered when they sit on top of each other.
  const raw: Marker[] = []
  for (const c of cards) {
    const d = ms(c.deadline)
    if (ok(d) && d < launch) raw.push({ key: `d-${c.id}`, x: x(Math.max(d, start)), kind: 'decide', at: d, cards: [c] })
    const r = ms(c.runs_dry)
    if (ok(r) && r < launch) raw.push({ key: `r-${c.id}`, x: x(Math.max(r, start)), kind: 'dry', at: r, cards: [c] })
  }
  const markers: Marker[] = []
  for (const m of raw.sort((a, b) => a.x - b.x)) {
    const near = markers.find((n) => n.kind === m.kind && Math.abs(n.x - m.x) < 14)
    if (near) near.cards.push(...m.cards)
    else markers.push({ ...m, cards: [...m.cards] })
  }
  const first = ed.breaks_first_card ? cardsBy(ed.breaks_first_card) : undefined

  if (layer === 'bars') return (
    <g>
      {/* packing bar, inside shifts only */}
      <g
        onMouseEnter={(e) => hover(e, `${ed.label}: ${fmtNum(ed.remaining)} left at ${fmtNum(ed.rate_per_hour)}/h${ed.packed_as_of ? `, counted ${fmtDay(ed.packed_as_of)} ${fmtTime(ed.packed_as_of)} and projected at plan rate` : ''}`)}
        onMouseLeave={leave}
      >
        {pieces(start, end).map(([a, b]) => (
          <rect key={`b-${a}`} x={x(a)} y={barY} width={Math.max(x(b) - x(a), 1)} height={barH} rx={barH / 2} fill={colour} />
        ))}
        {/* stopped: a light tint and one thin red line, no hatching */}
        {ok(stop) && stop < launch && pieces(stop, launch).map(([a, b]) => (
          <g key={`h-${a}`}>
            <rect x={x(a)} y={barY - 3} width={Math.max(x(b) - x(a), 1)} height={barH + 6} fill={STOP} opacity={0.06} />
            <line x1={x(a)} x2={x(b)} y1={midY} y2={midY} stroke={STOP} strokeWidth={1.5} />
          </g>
        ))}
        {ok(stop) && stop < launch && <line x1={x(stop)} x2={x(stop)} y1={barY - 5} y2={barY + barH + 5} stroke={STOP} strokeWidth={1.5} />}
        {ok(finish) && !ok(stop) && finish < launch && (
          <line x1={x(finish)} x2={x1} y1={midY} y2={midY} stroke={INK3} strokeWidth={1} strokeDasharray="2 3" />
        )}
      </g>
    </g>
  )

  return (
    <g>
      <text x={statusX} y={barY - (compact ? 5 : 8)} textAnchor={statusRight ? 'end' : 'start'} fontSize={compact ? 12 : 13} fontWeight={500} fill={statusColour}>
        {status}
      </text>

      {markers.map((m) => {
        const ids = m.cards.map((c) => c.id)
        const isSel = selected(ids)
        const urgent = m.kind === 'dry'
        const fill = urgent ? STOP : INK
        const when = `${fmtDay(new Date(m.at).toISOString())} ${fmtTime(new Date(m.at).toISOString())}`
        const lead = m.cards[0]
        const text = m.kind === 'decide'
          ? `${dueWord(lead)} ${when} · ${m.cards.length > 1 ? `${m.cards.length} decisions, first: ` : ''}${tidy(lead.headline)}`
          : `Runs dry ${when} · ${tidy(lead.headline)}`
        const isFirst = !compact && m.kind === 'decide' && !!first && ids.includes(first.id)
        const s = compact ? 5 : 5.5
        return (
          <g
            key={m.key} className="cursor-pointer" tabIndex={0} role="button" aria-label={text}
            onClick={() => pick(ids)} onKeyDown={(e) => e.key === 'Enter' && pick(ids)}
            onMouseEnter={(e) => hover(e, text)} onMouseLeave={leave} onFocus={(e) => hover(e, text)} onBlur={leave}
          >
            <rect x={m.x - 16} y={midY - 16} width={32} height={32} fill="transparent" />
            {isSel && <circle cx={m.x} cy={midY} r={s + 5} fill={GROUND} stroke={OK} strokeWidth={1.5} />}
            {m.kind === 'decide' ? (
              <path d={`M${m.x} ${midY - s} L${m.x + s} ${midY} L${m.x} ${midY + s} L${m.x - s} ${midY} Z`} fill={fill} stroke={GROUND} strokeWidth={1.5} />
            ) : (
              <path d={`M${m.x - s} ${midY - s + 1} L${m.x + s} ${midY - s + 1} L${m.x} ${midY + s} Z`} fill={STOP} stroke={GROUND} strokeWidth={1.5} />
            )}
            {m.cards.length > 1 && !compact && (
              <g>
                <text x={m.x} y={midY + s + 13} textAnchor="middle" fontSize={11} fontWeight={500} fill={INK2} stroke={GROUND} strokeWidth={3} paintOrder="stroke">{m.cards.length}</text>
              </g>
            )}
            {isFirst && (
              <text
                x={Math.min(Math.max(m.x, x0 + 4), x1 - 8)} y={barY + barH + 18}
                textAnchor={m.x < x0 + 60 ? 'start' : m.x > x1 - 160 ? 'end' : 'middle'} fontSize={12} fill={INK2}
              >
                {`${dueWord(first)} ${fmtTime(new Date(m.at).toISOString())} · ${what(first, all, 28)}`}
              </text>
            )}
          </g>
        )
      })}
    </g>
  )
}

function Inbound({
  top, h, state, open, compact, model, selected, pick, hover, leave,
}: Shared & { top: number; h: number; state: State; open: Card[] }) {
  const { x, x0, launch, start, windows } = model
  const y = top + h / 2 - (compact ? 0 : 2)
  const r = compact ? 3.5 : 4
  const due: string[] = []
  const dots: { key: string; x: number; colour: string; hollow: boolean; cards: string[]; text: string }[] = []
  for (const s of state.shipments) {
    const at = etaMs(s.eta, windows)
    if (!ok(at) || at >= launch) continue
    const linked = open.filter((c) => c.shipment === s.id).map((c) => c.id)
    if (at <= start) { due.push(`${s.id} (${s.status.replace(/_/g, ' ')}, ETA ${s.eta})`); continue }
    dots.push({
      key: `s-${s.id}`, x: x(at), colour: s.silent ? SILENT : tone(s.level),
      hollow: s.silent, cards: linked,
      text: `${s.id} · ${s.mode} · ${s.status.replace(/_/g, ' ')} · ETA ${s.eta}${s.silent ? ` · silent ${s.silent_hours ?? ''} h` : ''}`,
    })
  }
  for (const c of open) {
    const at = ms(c.arrives_at)
    if (!ok(at) || at >= launch || at <= start) continue
    dots.push({ key: `c-${c.id}`, x: x(at), colour: tone(c.level), hollow: false, cards: [c.id], text: `Arrives ${fmtDay(c.arrives_at)} ${fmtTime(c.arrives_at)} · ${tidy(c.headline)}` })
  }
  // Dots that share a spot stack upwards a little, so none hides another.
  const placed: { x: number; n: number }[] = []
  return (
    <g>
      {due.length > 0 && !compact && (
        <g onMouseEnter={(e) => hover(e, `ETA passed, not received: ${due.join(', ')}`)} onMouseLeave={leave}>
          <rect x={0} y={top + 24} width={x0 - 8} height={18} fill="transparent" />
          <text x={0} y={top + 34} fontSize={12} fill={INK3}>{due.length} ETA passed</text>
        </g>
      )}
      {dots.map((d) => {
        const p = placed.find((q) => Math.abs(q.x - d.x) < r * 2)
        const n = p ? ++p.n : 0
        if (!p) placed.push({ x: d.x, n: 0 })
        const cy = y - Math.min(n, 2) * (r + 1)
        const cx = d.x + (n > 2 ? (n - 2) * (r * 2) : 0)
        const clickable = d.cards.length > 0
        return (
          <g
            key={d.key} className={clickable ? 'cursor-pointer' : undefined}
            onClick={clickable ? () => pick(d.cards) : undefined}
            onMouseEnter={(e) => hover(e, d.text)} onMouseLeave={leave}
          >
            <rect x={cx - 16} y={cy - 16} width={32} height={32} fill="transparent" />
            {clickable && selected(d.cards) && <circle cx={cx} cy={cy} r={r + 4} fill="none" stroke={OK} strokeWidth={1.5} />}
            <circle cx={cx} cy={cy} r={r} fill={d.hollow ? GROUND : d.colour} stroke={d.hollow ? INK2 : GROUND} strokeWidth={d.hollow ? 1.5 : 1} />
          </g>
        )
      })}
    </g>
  )
}
