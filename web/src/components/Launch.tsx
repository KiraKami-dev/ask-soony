import { type ReactNode, useEffect, useState } from 'react'
import { LaunchTimeline } from '@/components/LaunchTimeline'
import { fmtNum, type Card, type Inbound, type LaunchPart, type State, type StripItem } from '@/lib/api'
import { launchAt, when } from '@/lib/decisions'
import { cn } from '@/lib/utils'

// Launch: "Will we make Sunday 20:00, and what breaks first?" One honest sentence, the timeline,
// then per edition how far packing is and per part whether it lasts. No case list here: a case
// opens on Today (/d/<id>). Every number comes from /api/state (strip, clock, launch); a number
// the data does not state reads "unknown".

interface Props {
  state: State
  nowMs: number
  phone: boolean
  /** Opens a case on Today. */
  onOpen: (cardId: string) => void
}

const OPEN = new Set(['open', 'escalated'])
const t = (iso?: string | null) => (iso ? new Date(iso).getTime() : NaN)
const hours = (h: number) => `${Math.round(h * 10) / 10} h`
const Unknown = () => <span className="text-ink-3">unknown</span>
const firstName = (s?: string) => (s ?? '').split(' ').find((w) => !w.endsWith('.')) || ''

/** Parts of one edition with the cases that address the short ones. */
function partsOf(state: State, edition: string): LaunchPart[] {
  return (state.launch?.parts ?? []).filter((p) => p.editions.includes(edition))
}

const runsShort = (p: LaunchPart) => (p.short ?? 0) > 0
/** Breaks first on top: timed shortfalls by time, then shortfalls without a time, then unknown, then covered. */
function byBreak(a: LaunchPart, b: LaunchPart): number {
  const rank = (p: LaunchPart) => (runsShort(p) ? (p.runs_dry ? 0 : 1) : p.short == null ? 2 : 3)
  return rank(a) - rank(b) || (t(a.runs_dry) || 0) - (t(b.runs_dry) || 0) || (b.short ?? 0) - (a.short ?? 0)
}

/** The live cases that would fix this edition: those on its short parts, plus what breaks first. */
function fixesFor(state: State, s: StripItem): Card[] {
  const ids = new Set(partsOf(state, s.edition).filter(runsShort).flatMap((p) => p.cards))
  if (s.breaks_first?.card) ids.add(s.breaks_first.card)
  return state.cards.filter((c) => ids.has(c.id) && OPEN.has(c.status))
}

type Bit = { text: string; late?: boolean }

const floorOf = (state: State, edition: string) => state.launch?.floor?.editions?.[edition]

/** What the counts behind the verdict are worth: stale counts first, then a site's count that
 * came in well below plan ("it likely stood still"). */
function countBits(state: State, nowMs: number): Bit[] {
  const eds = state.strip.map((s) => floorOf(state, s.edition)).filter((e): e is NonNullable<typeof e> => !!e)
  const out: Bit[] = []
  const stale = eds.filter((e) => e.stale)
  if (stale.length) {
    const h = Math.floor(Math.max(...stale.map((e) => e.age_hours)))
    const all = stale.length === eds.length
    out.push({ text: all ? `Packing counts are ${h} h old.` : `The ${stale.map((e) => e.label).join(' and ')} count is ${h} h old.`, late: true })
  }
  for (const e of eds) {
    const b = e.below_plan
    if (!e.counted.reported || !b?.stood_still) continue
    if (out.length) out.push({ text: ' ' })
    out.push({ text: e.line.startsWith('the ') ? `The ${e.label} ${e.line.slice(4)} packed ` : `${e.label} packed ` }, { text: `${fmtNum(b.copies)} fewer`, late: true },
      { text: ` than planned since ${when(b.since, nowMs)}: it likely stood still.` })
  }
  return out
}

/** One sentence per edition, in the order the editions stop. */
function verdictBits(state: State, nowMs: number): Bit[] {
  if (!state.strip.length) return [{ text: 'No packing plan yet, so whether we make the trucks is unknown.' }]
  const order = [...state.strip].sort((a, b) => (t(a.stops_at) || Infinity) - (t(b.stops_at) || Infinity))
  const out: Bit[] = countBits(state, nowMs)
  for (const s of order) {
    if (out.length) out.push({ text: ' ' })
    const stops = s.stops_at
    const short = s.copies_at_risk
    if (stops) {
      const past = t(stops) <= nowMs
      const n = fixesFor(state, s).length
      out.push({ text: `${s.label} ${past ? 'stopped' : 'stops'} ${when(stops, nowMs)}`, late: true })
      if (n === 0) {
        out.push({ text: past ? ' and stays ' : ', ' }, { text: `${fmtNum(short)} short`, late: true }, { text: '.' })
      } else {
        out.push({ text: ', ' }, { text: `${fmtNum(short)} short`, late: true })
        // Say who holds the decisions: yours, or passed on and waiting for someone else.
        const fixes = fixesFor(state, s)
        const theirs = fixes.filter((c) => c.status === 'escalated' || c.routed_to_other)
        const mine = fixes.length - theirs.length
        const who = [...new Set(theirs.map((c) => firstName(c.approver ?? c.signoff ?? '') || 'someone'))]
        const since = theirs[0]?.ui?.at ? ` since ${when(theirs[0].ui.at, nowMs)}` : ''
        out.push({
          text: mine > 0 && theirs.length === 0
            ? `, unless ${mine} ${mine === 1 ? 'decision goes' : 'decisions go'} through.`
            : mine === 0
              ? `, unless ${who.join(' and ')} ${who.length === 1 && theirs.length === 1 ? 'approves the decision' : 'approve'} (waiting${since}).`
              : `, unless ${mine} of your decisions and ${theirs.length} with ${who.join(' and ')} go through.`,
        })
      }
    } else if (s.finishes_at) {
      const spare = s.slack_hours
      out.push({ text: `${s.label} finishes ${when(s.finishes_at, nowMs)}${spare != null && spare > 0 ? `, ${hours(spare)} spare` : ''}.` })
    } else if ((s.short_of_time ?? 0) > 0) {
      out.push({ text: `${s.label} runs out of shift hours, `, late: true }, { text: `${fmtNum(s.short_of_time)} short.`, late: true })
    } else {
      out.push({ text: `Whether ${s.label} makes the trucks is unknown.` })
    }
  }
  return out
}

interface DataIssue { kind: 'not_read' | 'changed' | 'unused'; file: string; text: string }
interface DataHealth { checked_at: string | null; issues: DataIssue[] }

/** The data check: which customer files the picture leaves out (refreshed with the state). */
function useDataHealth(stamp: unknown): DataHealth | null {
  const [h, setH] = useState<DataHealth | null>(null)
  useEffect(() => {
    let live = true
    fetch('/api/data-health').then((r) => (r.ok ? r.json() : null)).then((j) => live && setH(j)).catch(() => {})
    return () => { live = false }
  }, [stamp])
  return h
}

export function Launch({ state, nowMs, phone, onOpen }: Props) {
  const health = useDataHealth(state)
  const gaps = (health?.issues ?? []).filter((i) => i.kind !== 'unused')
  const unused = (health?.issues ?? []).filter((i) => i.kind === 'unused')
  const bits = verdictBits(state, nowMs)
  const launch = launchAt(state)
  return (
    <div className="flex-1">
      <div className={cn('mx-auto w-full max-w-[960px]', phone ? 'px-6 pb-8 pt-6' : 'px-8 pb-12 pt-14')}>
        <p className="t-small mb-4 text-ink-3">{launch ? `Trucks leave ${when(launch, nowMs)}` : 'Launch time unknown'}</p>
        <h1 className="t-big max-w-[820px] text-ink">
          {bits.map((b, i) => <span key={i} className={cn(b.late && 'text-late')}>{b.text}</span>)}
        </h1>
        {gaps.length > 0 && (
          <div className="t-body mt-6 max-w-[760px] text-ink">
            <p>
              This picture leaves out {gaps.length === 1 ? 'one file' : `${gaps.length} files`} we could not read, so it
              may look better than it is:
            </p>
            <ul className="mt-2 space-y-1 text-ink-2">
              {gaps.map((g) => <li key={g.file}>{g.text}</li>)}
            </ul>
          </div>
        )}
      </div>

      <div className={cn('border-y border-hair', phone ? 'px-6 py-2' : 'px-8 py-4 xl:px-12')}>
        <LaunchTimeline state={state} nowMs={nowMs} selectedId={null} onSelect={onOpen} compact={phone} />
      </div>

      <div className={cn('mx-auto w-full max-w-[960px]', phone ? 'px-6 pb-24 pt-10' : 'px-8 pb-32 pt-14')}>
        <div className={cn('grid gap-y-14', !phone && 'grid-cols-2 gap-x-16')}>
          {state.strip.map((s) => <EditionBlock key={s.edition} s={s} state={state} nowMs={nowMs} />)}
        </div>

        <div className="mt-20 space-y-16">
          {state.strip.map((s) => (
            <PartList key={s.edition} s={s} state={state} nowMs={nowMs} phone={phone} onOpen={onOpen} />
          ))}
        </div>

        <p className="t-small mt-16 text-ink-3">
          Packed copies are projected at plan rate from the last count
          {state.strip.some((s) => s.packed_as_of)
            ? ` (${state.strip.filter((s) => s.packed_as_of).map((s) => `${s.label}: ${floorOf(state, s.edition)?.counted.reported ? `reported by ${state.launch?.floor?.site ?? 'the site'}` : 'pack-out log'} ${when(s.packed_as_of, nowMs)}`).join('; ')})`
            : ''}
          ; a count from the site replaces the projection. Parts and stock as the verifier counted them, unless the site
          counted them; the trucks leave when the last shift ends.
        </p>
        {health?.checked_at && (
          <p className="t-small mt-2 text-ink-3">
            {gaps.length === 0 ? 'Every customer spreadsheet was read' : `${gaps.length} customer ${gaps.length === 1 ? 'file' : 'files'} left out`}
            {unused.length > 0 ? `; new and not used yet: ${unused.map((u) => u.file.split('/').pop()).join(', ')}` : ''}
            . Checked {when(health.checked_at, nowMs)}.
          </p>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- one edition

function EditionBlock({ s, state, nowMs }: { s: StripItem; state: State; nowMs: number }) {
  const dayOne = state.launch?.editions.find((e) => e.edition === s.edition)?.day_one ?? null
  const fl = floorOf(state, s.edition)
  const site = state.launch?.floor?.site ?? 'the site'
  const left = s.remaining ?? null
  const packed = dayOne != null && left != null ? dayOne - left : null
  const rate = s.rate_per_hour ?? null
  const work = left != null && rate ? left / rate : null
  const hoursLeft = state.launch?.hours_left ?? null
  const spare = work != null && hoursLeft != null ? hoursLeft - work : s.slack_hours
  const stops = s.stops_at
  const past = stops ? t(stops) <= nowMs : false
  const head = stops ? (
    <span className="text-late">{past ? 'Stopped' : 'Stops'} {when(stops, nowMs)} · {fmtNum(s.copies_at_risk)} short</span>
  ) : s.finishes_at ? (
    <span className="text-ink">Finishes {when(s.finishes_at, nowMs)}</span>
  ) : (s.short_of_time ?? 0) > 0 ? (
    <span className="text-late">Out of shift hours · {fmtNum(s.short_of_time)} short</span>
  ) : <Unknown />
  const n = (v: number | null, f: (x: number) => ReactNode) => (v == null ? <Unknown /> : f(v))
  return (
    <section aria-label={`${s.label} Edition`}>
      <h2 className="t-small text-ink-3">{s.label} Edition</h2>
      <p className="t-body mt-1 font-medium">{head}</p>
      <dl className="t-small mt-5">
        {fl?.counted.number != null && (
          <Fact k="Counted">
            {fmtNum(fl.counted.number)}
            <span className="text-ink-3"> at {when(fl.counted.as_of, nowMs)}</span>
            {fl.counted.reported ? (
              <span className="text-ink-3">
                {' '}by {site}{fl.counted.by ? ` (${fl.counted.by})` : ''}, reported
                {fl.reported?.photo_url && <> · <a href={fl.reported.photo_url} target="_blank" rel="noreferrer" className="uline">count sheet</a></>}
              </span>
            ) : <span className="text-ink-3"> · pack-out log</span>}
          </Fact>
        )}
        <Fact k={fl?.counted.number != null ? 'Packed now' : 'Packed'}>
          {n(packed, (x) => <>{fmtNum(x)}{dayOne != null && <span className="text-ink-3"> of {fmtNum(dayOne)}</span>}</>)}
          {fl && fl.stale_hours > 0 && (
            <span className={cn('block', fl.stale ? 'text-late' : 'text-ink-3')}>
              projected{fl.stale ? `, ${fl.counted.reported ? 'count' : 'log'} ${Math.floor(fl.age_hours)} h old` : ' at plan rate'}
            </span>
          )}
        </Fact>
        <Fact k="Still to pack">{n(left, (x) => fmtNum(x))}</Fact>
        <Fact k="Pack rate">{n(rate, (x) => `${fmtNum(x)} an hour`)}</Fact>
        <Fact k="Work left">
          {n(work, (x) => <>{hours(x)}{hoursLeft != null && <span className="text-ink-3"> of {hours(hoursLeft)} shift time</span>}</>)}
        </Fact>
        <Fact k="Spare">
          {spare == null ? <Unknown /> : spare < 0
            ? <span className="text-late">{hours(-spare)} too few</span>
            : <>{hours(spare)}{stops && <span className="text-ink-3"> if every part were there</span>}</>}
        </Fact>
      </dl>
    </section>
  )
}

function Fact({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[120px_minmax(0,1fr)] items-baseline border-t border-hair py-2.5">
      <dt className="text-ink-3">{k}</dt>
      <dd className="text-ink">{children}</dd>
    </div>
  )
}

// ---------------------------------------------------------------- parts of one edition

function PartList({ s, state, nowMs, phone, onOpen }: { s: StripItem; state: State; nowMs: number; phone: boolean; onOpen: (id: string) => void }) {
  const parts = partsOf(state, s.edition).sort(byBreak)
  return (
    <section aria-label={`${s.label} parts`}>
      <h2 className="t-small text-ink-3">{s.label} · {parts.length ? `${parts.length} parts per box` : 'parts unknown'}</h2>
      {parts.length === 0 ? (
        <p className="t-small mt-3 text-ink-3">The verifier has not counted this edition's parts yet.</p>
      ) : (
        <ul className="mt-3">
          {parts.map((p) => <PartRow key={p.part} p={p} state={state} nowMs={nowMs} phone={phone} onOpen={onOpen} />)}
        </ul>
      )}
    </section>
  )
}

function PartRow({ p, state, nowMs, phone, onOpen }: { p: LaunchPart; state: State; nowMs: number; phone: boolean; onOpen: (id: string) => void }) {
  const short = runsShort(p)
  const launch = t(launchAt(state))
  const status: ReactNode = p.short == null ? <Unknown /> : !short ? (
    <span className="text-ink-3">covered</span>
  ) : p.runs_dry ? (
    <span className="text-late">{t(p.runs_dry) <= nowMs ? 'ran dry' : 'runs dry'} {when(p.runs_dry, nowMs)} · {fmtNum(p.short)} short</span>
  ) : (
    <span className="text-late">{fmtNum(p.short)} short{p.shared ? ', split between editions open' : ''}</span>
  )
  const cases = p.cards.map((id) => state.cards.find((c) => c.id === id)).filter((c): c is Card => !!c && OPEN.has(c.status))
  const lead = cases[0]
  const link = lead && (
    <button onClick={() => onOpen(lead.id)} className="uline cursor-pointer whitespace-nowrap">
      → {lead.status === 'escalated' ? `with ${firstName(lead.approver) || 'the approver'}` : lead.routed_to_other ? `with ${firstName(lead.approver) || 'someone else'}` : 'decide'}
      {cases.length > 1 ? ` · ${cases.length} cases` : ''}
    </button>
  )
  // Confirmed orders first: where an order stands matters more than a row that already counts.
  const inbound = [...p.inbound].sort((a, b) => Number(!!b.commitment) - Number(!!a.commitment)).slice(0, 2)
  return (
    <li className={cn('grid items-baseline gap-x-6 border-t border-hair py-3.5', phone ? 'grid-cols-[minmax(0,1fr)_auto]' : 'grid-cols-[220px_minmax(0,1fr)_auto]')}>
      <span className={cn('t-small', short ? 'text-ink' : 'text-ink-2')}>{p.short_name}</span>
      <span className={cn('t-small min-w-0', phone && 'col-start-1 row-start-2')}>
        {status}
        {p.counted && (
          <span className="block text-ink-3">
            {fmtNum(p.counted.count)} counted on the shelf {when(p.counted.at, nowMs)}, books said {fmtNum(p.counted.book)}
          </span>
        )}
        {inbound.map((i, k) => <span key={k} className="block text-ink-3">{inboundLine(i, launch, nowMs)}</span>)}
        {p.inbound.length > 2 && <span className="block text-ink-3">+{p.inbound.length - 2} more on the way</span>}
        {!inbound.length && short && <span className="block text-ink-3">Nothing on its way that counts</span>}
      </span>
      <span className={cn('t-small text-right', phone && 'col-start-2 row-span-2 row-start-1')}>{link}</span>
    </li>
  )
}

const MOVING: Record<string, string> = { sea: 'on the water', air: 'in the air', road: 'on the road', rail: 'on the rails' }

/** "350,000 at the port, not counted: needs collecting". */
function inboundLine(i: Inbound, launchMs: number, nowMs: number): string {
  if (i.commitment) return i.commitment.line
  const qty = i.quantity != null ? fmtNum(i.quantity) : 'Unknown quantity'
  if (i.reason === 'reported, not booked') return `${qty} arrived (${i.reference}), reported by the site, not booked yet`
  const sh = i.shipment
  const etaMs = t(i.eta ?? sh?.eta)
  const late = i.after_launch || (Number.isFinite(etaMs) && Number.isFinite(launchMs) && etaMs > launchMs)
  let where: string
  if (sh?.silent) where = `silent ${sh.silent_hours ?? '?'} h`
  else if (late) where = `arrives ${dayOf(i.eta ?? sh?.eta, nowMs)}, after launch`
  else if (!sh) where = 'not tracked'
  else if (sh.status === 'arrived_port') where = 'at the port'
  else if (sh.status === 'delayed') where = 'late'
  else if (sh.status === 'in_delivery') where = 'out for delivery'
  else where = sh.status === 'booked' ? 'booked' : MOVING[sh.mode] ?? sh.status.replace(/_/g, ' ')
  // Due on a day already over and not in yet: say so rather than repeat the old date.
  const due = i.eta ?? sh?.eta
  const passed = sh && !late && !sh.silent && sh.status !== 'arrived_port' && due && /^\d{4}-\d{2}-\d{2}$/.test(due) &&
    due < new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Berlin' }).format(new Date(nowMs))
  if (passed) where += `, due ${dayOf(due, nowMs)}, not in yet`
  else if (sh?.status === 'booked' && due && !late) where += `, due ${dayOf(due, nowMs)}`
  const reason = (i.reason ?? '').replace(/^at the port,\s*/, '').replace(/^\w+:\s*/, '')
  const counted = i.counted ? '' : !late && reason ? `, not counted: ${reason}` : ''
  return `${qty} ${where}${counted}`
}

const dayOf = (d: string | null | undefined, nowMs: number) => {
  if (!d) return 'unknown'
  const iso = /^\d{4}-\d{2}-\d{2}$/.test(d) ? `${d}T12:00:00+01:00` : d
  const v = new Date(iso)
  if (Number.isNaN(v.getTime())) return d
  const day = new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'Europe/Berlin' }).format(v)
  return /^\d{4}-\d{2}-\d{2}$/.test(d) ? day : when(d, nowMs)
}
