import { geoDistance, geoNaturalEarth1, geoPath, type GeoProjection } from 'd3-geo'
import { curveCatmullRom, line } from 'd3-shape'
import type { Feature, FeatureCollection, MultiPoint } from 'geojson'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { feature, mesh } from 'topojson-client'
import type { GeometryCollection, Topology } from 'topojson-specification'
import countries from 'world-atlas/countries-110m.json'
import { COLORS, LEVEL_RANK, type Leg, type Level, type MapNode, type State } from '@/lib/api'

const PAD_DEG = 6
// Inset: everything within this distance of the distribution center, drawn larger.
const INSET_RADIUS_KM = 1400
const EARTH_KM = 6371

export interface MapSelection { routeId?: string; nodeId?: string }

interface Props {
  state: State
  hoverRoutes: string[]
  selection: MapSelection
  onSelect: (s: MapSelection) => void
  pulseNode: string | null
}

const topo = countries as unknown as Topology
const landFC = feature(topo, topo.objects.countries as GeometryCollection) as FeatureCollection
const borders = mesh(topo, topo.objects.countries as GeometryCollection, (a, b) => a !== b)

function boxOf(pts: [number, number][], pad: number): Feature<MultiPoint> {
  const lons = pts.map((p) => p[0]), lats = pts.map((p) => p[1])
  const x0 = Math.min(...lons) - pad, x1 = Math.max(...lons) + pad
  const y0 = Math.max(-80, Math.min(...lats) - pad), y1 = Math.min(80, Math.max(...lats) + pad)
  return {
    type: 'Feature', properties: {},
    geometry: { type: 'MultiPoint', coordinates: [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [(x0 + x1) / 2, y0], [(x0 + x1) / 2, y1]] },
  }
}

function useSize() {
  const ref = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 0, h: 0 })
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setSize({ w: Math.round(e.contentRect.width), h: Math.round(e.contentRect.height) }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, size] as const
}

export function SupplyMap(props: Props) {
  const { state } = props
  const { nodes, legs } = state.map
  const [ref, { w, h }] = useSize()

  const dc = nodes.find((n) => n.kind === 'dc')
  const nearIds = useMemo(() => {
    if (!dc) return new Set<string>()
    return new Set(nodes.filter((n) => geoDistance([n.lon, n.lat], [dc.lon, dc.lat]) * EARTH_KM <= INSET_RADIUS_KM).map((n) => n.id))
  }, [nodes, dc])

  const inset = useMemo(() => {
    if (!w || !h || nearIds.size < 2) return null
    const pts = nodes.filter((n) => nearIds.has(n.id)).map((n) => [n.lon, n.lat] as [number, number])
    const b = geoPath(geoNaturalEarth1()).bounds(boxOf(pts, 1.2))
    const aspect = (b[1][1] - b[0][1]) / Math.max(1e-6, b[1][0] - b[0][0])
    const iw = Math.max(260, Math.round(w * 0.32))
    const ih = Math.round(Math.min(h * 0.45, Math.max(170, (iw - 36) * aspect + 50)))
    return { x: 14, y: h - ih - 14, w: iw, h: ih }
  }, [w, h, nodes, nearIds])

  // Main projection: every node and sea-lane point fits; the map fills the panel, and spare room
  // shows more of the world instead of white bands.
  const main = useMemo(() => {
    const pts: [number, number][] = nodes.map((n) => [n.lon, n.lat])
    legs.forEach((l) => l.path?.forEach((p) => pts.push([p[0], p[1]])))
    if (!pts.length || !w || !h) return null
    const top = 16, bottom = inset ? inset.h * 0.55 : 16
    return geoNaturalEarth1().fitExtent([[16, top], [w - 16, h - bottom]], boxOf(pts, PAD_DEG))
  }, [nodes, legs, w, h, inset])

  const insetProj = useMemo(() => {
    if (!inset) return null
    const pts = nodes.filter((n) => nearIds.has(n.id)).map((n) => [n.lon, n.lat] as [number, number])
    return geoNaturalEarth1().fitExtent([[18, 30], [inset.w - 18, inset.h - 14]], boxOf(pts, 1.2))
  }, [inset, nodes, nearIds])

  return (
    <div ref={ref} className="absolute inset-0">
      {!nodes.length ? (
        <div className="grid h-full place-items-center text-ink-3">Waiting for supplier and port master data…</div>
      ) : main && (
        <svg width={w} height={h} className="block select-none" role="img" aria-label="Supply chain map" onClick={() => props.onSelect({})}>
          <rect width={w} height={h} fill="#fff" />
          <Layer {...props} id="main" projection={main} w={w} h={h} labelSkip={inset ? nearIds : undefined} />
          {inset && insetProj && (
            <>
              <InsetFrame main={main} proj={insetProj} inset={inset} />
              <g transform={`translate(${inset.x},${inset.y})`}>
                <rect width={inset.w} height={inset.h} rx={10} fill="#fff" stroke="#cbd5e1" strokeWidth={1.5} />
                <svg width={inset.w} height={inset.h}>
                  <Layer {...props} id="inset" projection={insetProj} w={inset.w} h={inset.h} labelAll={nearIds} />
                </svg>
                <text x={12} y={20} fontSize={12} fontWeight={700} fill="#64748b" letterSpacing={0.6}>EUROPE</text>
              </g>
            </>
          )}
        </svg>
      )}
    </div>
  )
}

/** Outline on the main map of the area the inset shows. */
function InsetFrame({ main, proj, inset }: { main: GeoProjection; proj: GeoProjection; inset: { w: number; h: number } }) {
  const corners = [[0, 0], [inset.w, 0], [inset.w, inset.h], [0, inset.h]]
    .map((c) => proj.invert?.(c as [number, number]))
    .map((g) => (g ? main(g as [number, number]) : null))
  if (corners.some((c) => !c)) return null
  const xs = corners.map((c) => c![0]), ys = corners.map((c) => c![1])
  return (
    <rect
      x={Math.min(...xs)} y={Math.min(...ys)} width={Math.max(...xs) - Math.min(...xs)} height={Math.max(...ys) - Math.min(...ys)}
      fill="none" stroke="#94a3b8" strokeWidth={1.2} strokeDasharray="3 3" rx={3} pointerEvents="none"
    />
  )
}

interface LayerProps extends Props {
  id: string
  projection: GeoProjection
  w: number
  h: number
  labelSkip?: Set<string>
  labelAll?: Set<string>
}

function Layer({ state, hoverRoutes, selection, onSelect, pulseNode, id, projection, w, h, labelSkip, labelAll }: LayerProps) {
  const { nodes, legs } = state.map
  const nodeById = useMemo(() => Object.fromEntries(nodes.map((n) => [n.id, n])), [nodes])
  const compact = !!labelAll
  const gp = geoPath(projection)

  const legPaths = useMemo(() => {
    const g = geoPath(projection)
    const smooth = line().curve(curveCatmullRom.alpha(0.5))
    const out: Record<string, string> = {}
    for (const l of legs) {
      const a = nodeById[l.origin], b = nodeById[l.destination]
      if (!a || !b) continue
      if (l.mode === 'sea' && l.path) {
        const pr = l.path.map((p) => projection(p as [number, number])).filter(Boolean) as [number, number][]
        out[l.id] = smooth(pr) ?? ''
      } else {
        out[l.id] = g({ type: 'LineString', coordinates: [[a.lon, a.lat], [b.lon, b.lat]] }) ?? ''
      }
    }
    return out
  }, [projection, legs, nodeById])

  const pathEls = useRef<Record<string, SVGPathElement | null>>({})
  const [dots, setDots] = useState<Record<string, { x: number; y: number }>>({})
  const shipKey = state.shipments.map((s) => `${s.id}:${s.progress.toFixed(3)}:${s.level}`).join('|')
  useEffect(() => {
    const next: Record<string, { x: number; y: number }> = {}
    for (const s of state.shipments) {
      const el = pathEls.current[s.route_id]
      if (!el || !s.departed) continue
      const p = el.getPointAtLength(el.getTotalLength() * s.progress)
      next[s.id] = { x: p.x, y: p.y }
    }
    setDots(next)
  }, [shipKey, legPaths, state.shipments])

  const nodeLevel = useMemo(() => {
    const out: Record<string, Level> = {}
    const dcId = nodes.find((n) => n.kind === 'dc')?.id
    for (const c of state.cards) {
      if (c.status !== 'open' && c.status !== 'escalated') continue
      const nid = c.supplier_id || dcId
      if (nid && LEVEL_RANK[c.level] >= LEVEL_RANK[out[nid] ?? 'blue']) out[nid] = c.level
    }
    return out
  }, [state.cards, nodes])

  const labelled = useMemo(
    () => nodes.filter((n) => (labelAll ? labelAll.has(n.id) : (n.kind === 'dc' || n.kind === 'supplier') && !labelSkip?.has(n.id))),
    [nodes, labelAll, labelSkip],
  )
  const labels = useMemo(() => placeLabels(labelled, nodes, projection, w, h, compact), [labelled, nodes, projection, w, h, compact])

  const hl = new Set(hoverRoutes)
  const dim = hl.size > 0 || !!selection.routeId
  const focus = (rid: string) => (hl.size ? hl.has(rid) : selection.routeId ? selection.routeId === rid : true)
  const hasShip = new Set(state.shipments.map((s) => s.route_id))
  const legById: Record<string, Leg> = Object.fromEntries(legs.map((l) => [l.id, l]))
  const feeders = [...new Set(state.shipments.map((s) => `${s.supplier_id}|${legById[s.route_id]?.origin}`))]
    .map((k) => k.split('|') as [string, string])
    .filter(([a, b]) => nodeById[a] && nodeById[b] && a !== b)
  const xy = (n: MapNode) => projection([n.lon, n.lat]) as [number, number]
  const k = compact ? 0.9 : 1

  return (
    <g>
      <path d={gp(landFC) ?? ''} fill="var(--color-land)" />
      <path d={gp(borders) ?? ''} fill="none" stroke="#fff" strokeWidth={0.7} />

      {feeders.map(([a, b]) => {
        const p = xy(nodeById[a]), q = xy(nodeById[b])
        return <line key={a + b} x1={p[0]} y1={p[1]} x2={q[0]} y2={q[1]} stroke="#94a3b8" strokeWidth={1.5} strokeDasharray="1 4" strokeLinecap="round" />
      })}

      {legs.map((l) => {
        const d = legPaths[l.id]
        if (!d) return null
        const level = state.route_level[l.id] ?? 'blue'
        const live = hasShip.has(l.id) || !!state.route_level[l.id]
        const color = live ? COLORS[level] : '#c3cad5'
        const on = focus(l.id)
        const selected = selection.routeId === l.id || hl.has(l.id)
        return (
          <g key={l.id} opacity={dim && !on ? 0.18 : 1} style={{ transition: 'opacity .2s' }}>
            <path
              ref={(el) => { pathEls.current[l.id] = el }}
              d={d} fill="none" stroke={color}
              strokeWidth={selected ? 4.5 : live ? 2.75 : 1.5}
              strokeLinecap="round" strokeDasharray={l.mode === 'sea' ? '7 6' : undefined}
              className={state.route_level[l.id] && level !== 'blue' ? 'route-recolour' : undefined}
              style={{ transition: 'stroke .8s, stroke-width .15s' }}
            />
            <path
              d={d} fill="none" stroke="transparent" strokeWidth={16} className="cursor-pointer"
              onClick={(e) => { e.stopPropagation(); onSelect(selection.routeId === l.id ? {} : { routeId: l.id }) }}
            >
              <title>{`${l.id} · ${nodeById[l.origin]?.name ?? l.origin} → ${nodeById[l.destination]?.name ?? l.destination} · ${l.mode}`}</title>
            </path>
          </g>
        )
      })}

      {state.shipments.map((s) => {
        const p = dots[s.id]
        if (!p) return null
        const c = COLORS[s.level]
        return (
          <g key={s.id} opacity={dim && !focus(s.route_id) ? 0.2 : 1} style={{ transition: 'opacity .2s' }}>
            {s.silent && <circle cx={p.x} cy={p.y} r={5} fill="none" stroke={c} strokeWidth={2} className="pulse-ring" />}
            <circle cx={p.x} cy={p.y} r={5 * k} fill={c} stroke="#fff" strokeWidth={1.5} style={{ transition: 'fill .8s' }}>
              <title>{`${s.id} · ${s.status}${s.silent_hours ? ` · silent ${s.silent_hours} h` : ''} · ETA ${s.eta ?? '?'}`}</title>
            </circle>
          </g>
        )
      })}

      {nodes.map((n) => {
        const [x, y] = xy(n)
        if (x < -20 || y < -20 || x > w + 20 || y > h + 20) return null
        const badge = state.badges[n.id]
        const lab = labels[n.id]
        const sel = selection.nodeId === n.id
        return (
          <g
            key={n.id}
            className={n.kind === 'supplier' ? 'cursor-pointer' : ''}
            onClick={(e) => { e.stopPropagation(); if (n.kind === 'supplier') onSelect(sel ? {} : { nodeId: n.id }) }}
          >
            {pulseNode === n.id && (
              <>
                <circle cx={x} cy={y} r={6} fill="none" stroke={COLORS.blue} strokeWidth={3} className="pulse-once" />
                <circle cx={x} cy={y} r={6} fill="none" stroke={COLORS.blue} strokeWidth={2} className="pulse-once delay" />
              </>
            )}
            {n.kind === 'dc' ? (
              <rect x={x - 6} y={y - 6} width={12} height={12} fill="#0f172a" stroke="#fff" strokeWidth={2} rx={2} />
            ) : n.kind === 'supplier' ? (
              <circle cx={x} cy={y} r={sel ? 8 : 6.5 * k} fill="#fff" stroke="#0f172a" strokeWidth={2.5} />
            ) : (
              <circle cx={x} cy={y} r={compact ? 3.5 : 3} fill="#94a3b8" stroke="#fff" strokeWidth={1}>
                <title>{n.name}</title>
              </circle>
            )}
            {lab && (
              <text
                x={lab.x} y={lab.y} textAnchor={lab.anchor} fontSize={lab.size}
                fontWeight={n.kind === 'port' ? 500 : n.kind === 'dc' ? 700 : 600}
                fill={n.kind === 'port' ? '#475569' : '#0f172a'} stroke="#fff" strokeWidth={4} paintOrder="stroke" pointerEvents="none"
              >
                {lab.text}
              </text>
            )}
            {badge ? (
              <g transform={`translate(${x + 9},${y - 10})`}>
                <circle r={9} fill={COLORS[nodeLevel[n.id] ?? 'amber']} stroke="#fff" strokeWidth={2} />
                <text textAnchor="middle" dy={4} fontSize={12} fontWeight={700} fill="#fff">{badge}</text>
              </g>
            ) : null}
          </g>
        )
      })}
      <desc>{id}</desc>
    </g>
  )
}

const shortDc = (name: string) => name.replace(/^.*?Distribution Center/i, 'Distribution Center').replace(/^Soony /, '')
const labelText = (n: MapNode) => (n.kind === 'dc' ? shortDc(n.name) : n.name)

/** Pick a free spot for each label: below, above, right, left, trying the next on overlap. */
function placeLabels(labelled: MapNode[], all: MapNode[], projection: GeoProjection, w: number, h: number, compact: boolean) {
  const out: Record<string, { x: number; y: number; anchor: 'start' | 'middle' | 'end'; size: number; text: string }> = {}
  const boxes: { x0: number; x1: number; y0: number; y1: number }[] = []
  const rank = (n: MapNode) => (n.kind === 'dc' ? 0 : n.kind === 'supplier' ? 1 : 2)
  const order = [...labelled].sort((a, b) => rank(a) - rank(b))
  const pts = all.map((n) => projection([n.lon, n.lat]) as [number, number])
  for (const n of order) {
    const p = projection([n.lon, n.lat])
    if (!p) continue
    const size = n.kind === 'port' ? 11.5 : compact ? 12.5 : 13
    const text = labelText(n)
    const wd = text.length * size * 0.57
    const tries: [number, number, 'start' | 'middle' | 'end'][] = [
      [0, 22, 'middle'], [0, -14, 'middle'], [12, 4, 'start'], [-12, 4, 'end'],
      [12, 18, 'start'], [-12, 18, 'end'], [12, -10, 'start'], [-12, -10, 'end'], [0, 38, 'middle'], [0, -30, 'middle'],
    ]
    let chosen: (typeof tries)[number] | null = null
    for (const t of tries) {
      const x0 = t[2] === 'middle' ? p[0] + t[0] - wd / 2 : t[2] === 'start' ? p[0] + t[0] : p[0] + t[0] - wd
      const b = { x0, x1: x0 + wd, y0: p[1] + t[1] - size, y1: p[1] + t[1] + 3 }
      const inside = b.x0 > 2 && b.x1 < w - 2 && b.y0 > 2 && b.y1 < h - 2
      const hitsLabel = boxes.some((o) => b.x0 < o.x1 && b.x1 > o.x0 && b.y0 < o.y1 && b.y1 > o.y0)
      const hitsNode = pts.some((q) => q[0] > b.x0 - 5 && q[0] < b.x1 + 5 && q[1] > b.y0 - 5 && q[1] < b.y1 + 5)
      if (inside && !hitsLabel && !hitsNode) { chosen = t; break }
      if (!chosen && inside && !hitsLabel) chosen = t
    }
    // A port next to a labelled supplier or the DC is the same town: its label adds nothing.
    if (n.kind === 'port' && order.some((o) => o.kind !== 'port' && Math.hypot(...(projection([o.lon, o.lat]) as [number, number]).map((v, i) => v - p[i]) as [number, number]) < 24)) continue
    if (!chosen) {
      if (n.kind === 'port') continue // a port label that does not fit is dropped, never overlapped
      chosen = tries[0]
    }
    const x0 = chosen[2] === 'middle' ? p[0] + chosen[0] - wd / 2 : chosen[2] === 'start' ? p[0] + chosen[0] : p[0] + chosen[0] - wd
    boxes.push({ x0, x1: x0 + wd, y0: p[1] + chosen[1] - size, y1: p[1] + chosen[1] + 3 })
    out[n.id] = { x: p[0] + chosen[0], y: p[1] + chosen[1], anchor: chosen[2], size, text }
  }
  return out
}
