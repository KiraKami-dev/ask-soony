import { geoNaturalEarth1, geoPath } from 'd3-geo'
import { curveCatmullRom, line } from 'd3-shape'
import type { Feature, FeatureCollection, MultiPoint } from 'geojson'
import { useEffect, useMemo, useRef, useState } from 'react'
import { feature, mesh } from 'topojson-client'
import type { GeometryCollection, Topology } from 'topojson-specification'
import countries from 'world-atlas/countries-110m.json'
import { COLORS, LEVEL_RANK, type Level, type MapNode, type State } from '@/lib/api'

const W = 1000
const PAD_DEG = 9

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

export function SupplyMap({ state, hoverRoutes, selection, onSelect, pulseNode }: Props) {
  const { nodes, legs } = state.map
  const nodeById = useMemo(() => Object.fromEntries(nodes.map((n) => [n.id, n])), [nodes])

  // Projection fitted to every node and sea-lane point in the data, with a margin.
  const geo = useMemo(() => {
    const pts: [number, number][] = nodes.map((n) => [n.lon, n.lat])
    legs.forEach((l) => l.path?.forEach((p) => pts.push([p[0], p[1]])))
    if (!pts.length) return null
    const lons = pts.map((p) => p[0]), lats = pts.map((p) => p[1])
    const x0 = Math.min(...lons) - PAD_DEG, x1 = Math.max(...lons) + PAD_DEG
    const y0 = Math.max(-80, Math.min(...lats) - PAD_DEG), y1 = Math.min(80, Math.max(...lats) + PAD_DEG)
    const box: Feature<MultiPoint> = {
      type: 'Feature', properties: {},
      geometry: { type: 'MultiPoint', coordinates: [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [(x0 + x1) / 2, y0], [(x0 + x1) / 2, y1]] },
    }
    const projection = geoNaturalEarth1().fitExtent([[0, 0], [W, W]], box)
    const b = geoPath(projection).bounds(box)
    return { projection, vb: { x: b[0][0], y: b[0][1], w: b[1][0] - b[0][0], h: b[1][1] - b[0][1] } }
  }, [nodes, legs])

  const pathEls = useRef<Record<string, SVGPathElement | null>>({})
  const [dots, setDots] = useState<Record<string, { x: number; y: number }>>({})

  const legPaths = useMemo(() => {
    if (!geo) return {}
    const gp = geoPath(geo.projection)
    const smooth = line().curve(curveCatmullRom.alpha(0.5))
    const out: Record<string, string> = {}
    for (const l of legs) {
      const a = nodeById[l.origin], b = nodeById[l.destination]
      if (!a || !b) continue
      if (l.mode === 'sea' && l.path) {
        const pr = l.path.map((p) => geo.projection(p as [number, number])).filter(Boolean) as [number, number][]
        out[l.id] = smooth(pr) ?? ''
      } else {
        out[l.id] = gp({ type: 'LineString', coordinates: [[a.lon, a.lat], [b.lon, b.lat]] }) ?? ''
      }
    }
    return out
  }, [geo, legs, nodeById])

  const shipKey = state.shipments.map((s) => `${s.id}:${s.progress.toFixed(3)}:${s.level}`).join('|')
  useEffect(() => {
    const next: Record<string, { x: number; y: number }> = {}
    for (const s of state.shipments) {
      const el = pathEls.current[s.route_id]
      if (!el || !s.departed) continue
      const len = el.getTotalLength()
      const p = el.getPointAtLength(len * s.progress)
      next[s.id] = { x: p.x, y: p.y }
    }
    setDots(next)
  }, [shipKey, legPaths, state.shipments])

  // Worst open-card level per node, for the badge colour.
  const nodeLevel = useMemo(() => {
    const out: Record<string, Level> = {}
    const dc = nodes.find((n) => n.kind === 'dc')?.id
    for (const c of state.cards) {
      if (c.status !== 'open' && c.status !== 'escalated') continue
      const id = c.supplier_id || dc
      if (id && LEVEL_RANK[c.level] >= LEVEL_RANK[out[id] ?? 'blue']) out[id] = c.level
    }
    return out
  }, [state.cards, nodes])

  const labels = useMemo(() => (geo ? placeLabels(nodes, geo.projection) : {}), [nodes, geo])

  if (!geo) {
    return <div className="grid h-full place-items-center text-ink-3">Waiting for supplier and port master data…</div>
  }
  const { projection, vb } = geo
  const gp = geoPath(projection)
  const hl = new Set(hoverRoutes)
  const dim = hl.size > 0 || !!selection.routeId
  const focus = (id: string) => (hl.size ? hl.has(id) : selection.routeId ? selection.routeId === id : true)
  const hasShip = new Set(state.shipments.map((s) => s.route_id))
  const legById = Object.fromEntries(legs.map((l) => [l.id, l]))
  const feeders = [...new Set(state.shipments.map((s) => `${s.supplier_id}|${legById[s.route_id]?.origin}`))]
    .map((k) => k.split('|') as [string, string])
    .filter(([a, b]) => nodeById[a] && nodeById[b] && a !== b)
  const xy = (n: MapNode) => projection([n.lon, n.lat]) as [number, number]

  return (
    <svg
      viewBox={`${vb.x - 10} ${vb.y - 10} ${vb.w + 20} ${vb.h + 20}`}
      className="h-auto w-full select-none"
      role="img"
      aria-label="Supply chain map"
      onClick={() => onSelect({})}
    >
      <rect x={vb.x - 10} y={vb.y - 10} width={vb.w + 20} height={vb.h + 20} fill="#fff" />
      <path d={gp(landFC) ?? ''} fill="var(--color-land)" />
      <path d={gp(borders) ?? ''} fill="none" stroke="#fff" strokeWidth={0.7} />

      {/* supplier to the port or site its shipments leave from */}
      {feeders.map(([a, b]) => {
        const p = xy(nodeById[a]), q = xy(nodeById[b])
        return <line key={a + b} x1={p[0]} y1={p[1]} x2={q[0]} y2={q[1]} stroke="#94a3b8" strokeWidth={1.5} strokeDasharray="1 4" strokeLinecap="round" />
      })}

      {/* legs */}
      {legs.map((l) => {
        const d = legPaths[l.id]
        if (!d) return null
        const level = state.route_level[l.id] ?? 'blue'
        const used = hasShip.has(l.id)
        const color = used || state.route_level[l.id] ? COLORS[level] : '#c3cad5'
        const on = focus(l.id)
        const selected = selection.routeId === l.id || hl.has(l.id)
        return (
          <g key={l.id} opacity={dim && !on ? 0.18 : 1} style={{ transition: 'opacity .2s' }}>
            <path
              ref={(el) => { pathEls.current[l.id] = el }}
              d={d}
              fill="none"
              stroke={color}
              strokeWidth={selected ? 4.5 : used || state.route_level[l.id] ? 2.5 : 1.5}
              strokeLinecap="round"
              strokeDasharray={l.mode === 'sea' ? '7 6' : undefined}
              style={{ transition: 'stroke .5s, stroke-width .15s' }}
            />
            <path
              d={d}
              fill="none"
              stroke="transparent"
              strokeWidth={16}
              className="cursor-pointer"
              onClick={(e) => { e.stopPropagation(); onSelect(selection.routeId === l.id ? {} : { routeId: l.id }) }}
            >
              <title>{`${l.id} · ${l.origin} → ${l.destination} · ${l.mode}`}</title>
            </path>
          </g>
        )
      })}

      {/* shipments */}
      {state.shipments.map((s) => {
        const p = dots[s.id]
        if (!p) return null
        const c = COLORS[s.level]
        const on = focus(s.route_id)
        return (
          <g key={s.id} opacity={dim && !on ? 0.2 : 1} style={{ transition: 'opacity .2s' }}>
            {s.silent && <circle cx={p.x} cy={p.y} r={5} fill="none" stroke={c} strokeWidth={2} className="pulse-ring" />}
            <circle cx={p.x} cy={p.y} r={5} fill={c} stroke="#fff" strokeWidth={1.5} style={{ transition: 'fill .5s' }}>
              <title>{`${s.id} · ${s.status}${s.silent_hours ? ` · silent ${s.silent_hours} h` : ''} · ETA ${s.eta ?? '?'}`}</title>
            </circle>
          </g>
        )
      })}

      {/* nodes */}
      {nodes.map((n) => {
        const [x, y] = xy(n)
        const badge = state.badges[n.id]
        const lab = labels[n.id]
        const sel = selection.nodeId === n.id
        return (
          <g
            key={n.id}
            className={n.kind === 'supplier' ? 'cursor-pointer' : ''}
            onClick={(e) => { e.stopPropagation(); if (n.kind === 'supplier') onSelect(sel ? {} : { nodeId: n.id }) }}
          >
            {pulseNode === n.id && <circle cx={x} cy={y} r={6} fill="none" stroke={COLORS.blue} strokeWidth={3} className="pulse-once" />}
            {n.kind === 'dc' ? (
              <rect x={x - 6} y={y - 6} width={12} height={12} fill="#0f172a" stroke="#fff" strokeWidth={2} rx={2} />
            ) : n.kind === 'supplier' ? (
              <circle cx={x} cy={y} r={sel ? 8 : 6.5} fill="#fff" stroke="#0f172a" strokeWidth={2.5} />
            ) : (
              <circle cx={x} cy={y} r={3} fill="#94a3b8" stroke="#fff" strokeWidth={1}>
                <title>{n.name}</title>
              </circle>
            )}
            {lab && (
              <text
                x={lab.x} y={lab.y} textAnchor={lab.anchor} fontSize={13} fontWeight={n.kind === 'dc' ? 700 : 600}
                fill="#0f172a" stroke="#fff" strokeWidth={4} paintOrder="stroke" pointerEvents="none"
              >
                {n.kind === 'dc' ? shortDc(n.name) : n.name}
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
    </svg>
  )
}

const shortDc = (name: string) => name.replace(/^.*?Distribution Center/i, 'Distribution Center').replace(/^Soony /, '')

/** Pick a free spot for each label: below, above, right, left, trying the next on overlap. */
function placeLabels(nodes: MapNode[], projection: (p: [number, number]) => [number, number] | null) {
  const out: Record<string, { x: number; y: number; anchor: 'start' | 'middle' | 'end' }> = {}
  const boxes: { x0: number; x1: number; y0: number; y1: number }[] = []
  const order = [...nodes].filter((n) => n.kind === 'dc' || n.kind === 'supplier').sort((a) => (a.kind === 'dc' ? -1 : 1))
  const pts = nodes.map((n) => projection([n.lon, n.lat]) as [number, number])
  for (const n of order) {
    const p = projection([n.lon, n.lat])
    if (!p) continue
    const text = n.kind === 'dc' ? shortDc(n.name) : n.name
    const w = text.length * 7.4
    const tries: [number, number, 'start' | 'middle' | 'end'][] = [
      [0, 24, 'middle'], [0, -18, 'middle'], [14, 4, 'start'], [-14, 4, 'end'],
      [0, 42, 'middle'], [0, -36, 'middle'], [14, 22, 'start'], [-14, 22, 'end'], [14, -14, 'start'], [-14, -14, 'end'],
    ]
    let chosen = tries[0]
    for (const t of tries) {
      const x0 = t[2] === 'middle' ? p[0] + t[0] - w / 2 : t[2] === 'start' ? p[0] + t[0] : p[0] + t[0] - w
      const b = { x0, x1: x0 + w, y0: p[1] + t[1] - 12, y1: p[1] + t[1] + 4 }
      const hitsLabel = boxes.some((o) => b.x0 < o.x1 && b.x1 > o.x0 && b.y0 < o.y1 && b.y1 > o.y0)
      const hitsNode = pts.some((q) => q[0] > b.x0 - 6 && q[0] < b.x1 + 6 && q[1] > b.y0 - 6 && q[1] < b.y1 + 6)
      chosen = t
      if (!hitsLabel && !hitsNode) break
    }
    const x0 = chosen[2] === 'middle' ? p[0] + chosen[0] - w / 2 : chosen[2] === 'start' ? p[0] + chosen[0] : p[0] + chosen[0] - w
    boxes.push({ x0, x1: x0 + w, y0: p[1] + chosen[1] - 12, y1: p[1] + chosen[1] + 4 })
    out[n.id] = { x: p[0] + chosen[0], y: p[1] + chosen[1], anchor: chosen[2] }
  }
  return out
}
