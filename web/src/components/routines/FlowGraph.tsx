import { Filter, Lock, Radar } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { StepIcon } from './RoutineFlow'
import { condWords, triggerText, type Run, type Spec, type Step } from './types'

// The routine as an automatic flow graph (n8n style), drawn from the recipe alone: trigger →
// one node per condition → one node per step, with the fixed guard as a side path off the step
// that sends. Counters come from the latest run (a test run shows a "Test run" tag).

const REFUSED = 'Needs you, it costs money or changes the plan'

interface Counts { matched: number; byStep: Map<string, string>; guard: number }

function count(spec: Spec, run?: Run): Counts | null {
  if (!run || !run.ok) return null
  const did = run.did ?? []
  const byStep = new Map<string, string>()
  const sent = did.filter((d) => d.do === 'carry_out').length
  const refused = did.filter((d) => d.do === 'notify' && (d.text ?? '').startsWith(REFUSED)).length
  const told = did.filter((d) => d.do === 'notify' && !(d.text ?? '').startsWith(REFUSED))
  const sendTexts = spec.then.filter((s) => s.do === 'carry_out').map((s) => s.text)
  const noted = (run.log ?? []).filter((l) => l && !sendTexts.some((t) => l.startsWith(t))).length
  for (const s of spec.then) {
    if (s.do === 'carry_out' && sent) byStep.set(s.id, `${sent} sent`)
    if (s.do === 'notify') {
      const n = told.filter((d) => d.channel === (s.channel ?? 'both')).length
      if (n) byStep.set(s.id, `${n} told`)
    }
    if (s.do === 'log' && noted) byStep.set(s.id, `${noted} noted`)
    const did_ = (k: Step['do']) => did.filter((d) => d.do === k).length
    if (s.do === 'chase' && did_('chase')) byStep.set(s.id, `${did_('chase')} chased`)
    if (s.do === 'ask_status' && did_('ask_status')) byStep.set(s.id, `${did_('ask_status')} asked`)
    if (s.do === 'call' && did_('call')) byStep.set(s.id, `${did_('call')} called`)
  }
  const handled = typeof run.result === 'number' ? run.result : new Set(did.map((d) => d.card)).size
  return { matched: handled, byStep, guard: refused }
}

function stepTitle(s: Step): string {
  if (s.do === 'carry_out') return 'Send it'
  if (s.do === 'log') return 'Note it'
  if (s.do === 'chase') return 'Chase once'
  if (s.do === 'ask_status') return 'Ask for a status'
  if (s.do === 'call') return 'Call you'
  return s.channel === 'phone' ? 'Tell you on your phone' : s.channel === 'teams' ? 'Tell you on Teams' : 'Tell you'
}

function Node({
  icon, tone, title, text, badge, active, dashed, wide, person,
}: {
  icon: ReactNode; tone: string; title: string; text: string; badge?: string; active?: boolean; dashed?: boolean; wide?: boolean
  /** The step reaches a person (a call): its own emphasis. */
  person?: boolean
}) {
  return (
    <div
      className={cn(
        'relative w-full min-w-0 rounded-xl border p-2.5 shadow-[0_1px_2px_rgba(15,23,42,0.04)]',
        wide && 'lg:w-[210px] lg:shrink-0',
        dashed ? 'border-dashed border-ink-3/40 bg-panel' : person ? 'border-routine/40 bg-routine/5' : 'border-line bg-white',
        active && 'ring-2 ring-emerald-500/35',
      )}
      title={text}
    >

      <div className="flex items-start gap-2.5">
        <div className={cn('grid h-8 w-8 shrink-0 place-items-center rounded-lg', tone)}>{icon}</div>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-1.5">
            <div className="line-clamp-2 min-w-0 text-[13px] font-semibold leading-snug text-ink">{title}</div>
            {badge && (
              <span
                className={cn(
                  'shrink-0 whitespace-nowrap rounded-full px-1.5 py-px text-[11px] font-semibold ring-1',
                  active ? 'bg-emerald-50 text-emerald-700 ring-emerald-600/20' : 'bg-panel text-ink-3 ring-line',
                )}
              >
                {badge}
              </span>
            )}
          </div>
          <div className={cn('mt-0.5 line-clamp-2 text-[12.5px] leading-snug text-ink-2', wide && 'lg:line-clamp-3')}>{text}</div>
          {person && <div className="mt-1 text-[11px] font-bold uppercase tracking-wide text-routine">Rings a person</div>}
        </div>
      </div>
    </div>
  )
}

const stroke = (dashed?: boolean) => ({ stroke: 'currentColor', strokeWidth: 1.25, fill: 'none', strokeDasharray: dashed ? '3 3' : undefined })
const Down = ({ dashed }: { dashed?: boolean }) => (
  <svg width="10" height="22" viewBox="0 0 10 22" aria-hidden className="block"><line x1="5" y1="0" x2="5" y2="20" {...stroke(dashed)} /><path d="M1.5 16 L5 21 L8.5 16" {...stroke()} /></svg>
)
const Right = ({ dashed }: { dashed?: boolean }) => (
  <svg width="28" height="10" viewBox="0 0 28 10" aria-hidden className="block"><line x1="0" y1="5" x2="26" y2="5" {...stroke(dashed)} /><path d="M22 1.5 L27 5 L22 8.5" {...stroke()} /></svg>
)

/**
 * A thin line with a small arrow. The main path runs down (narrow) or right (wide view on a large
 * screen); a side branch runs the other way.
 */
function Arrow({ wide, dashed, side }: { wide?: boolean; dashed?: boolean; side?: boolean }) {
  // On a phone the side branch drops below the step instead of beside it.
  const narrow = side ? (
    <>
      <div className="ml-[26px] sm:hidden"><Down dashed={dashed} /></div>
      <div className="mt-[21px] hidden sm:block"><Right dashed={dashed} /></div>
    </>
  ) : <div className="ml-[26px]"><Down dashed={dashed} /></div>
  if (!wide) return <div className="shrink-0 text-ink-3/70">{narrow}</div>
  const large = side ? <div className="ml-[26px]"><Down dashed={dashed} /></div> : <div className="mt-[21px]"><Right dashed={dashed} /></div>
  return (
    <div className="shrink-0 text-ink-3/70">
      <div className="lg:hidden">{narrow}</div>
      <div className="hidden lg:block">{large}</div>
    </div>
  )
}

function nodesFor(spec: Spec, run: Run | undefined, wide: boolean) {
  const c = count(spec, run)
  const guard = (
    <Node
      wide={wide}
      dashed
      icon={<Lock size={15} className="text-ink-2" />}
      tone="bg-white ring-1 ring-line"
      title="You decide"
      text="Costs money or changes the plan? It comes to you."
      badge={c?.guard ? `${c.guard} to you` : undefined}
      active={!!c?.guard}
    />
  )
  const trigger = (
    <Node
      wide={wide}
      icon={<Radar size={15} className="text-ok" />}
      tone="bg-ok/10"
      title={triggerText(spec.on).title}
      text={triggerText(spec.on).text}
      badge={run?.dry ? 'Test run' : undefined}
    />
  )
  const conditions = spec.when.map((cond, i) => ({
    key: cond.id,
    node: (
      <Node
        wide={wide}
        icon={<Filter size={15} className="text-amber-700" />}
        tone="bg-amber-50"
        title={i === 0 ? 'If' : 'And'}
        text={condWords(cond)}
        badge={i === spec.when.length - 1 && c ? `${c.matched} matched` : undefined}
        active={i === spec.when.length - 1 && !!c?.matched}
      />
    ),
  }))
  const steps = spec.then.map((s) => {
    const badge = c?.byStep.get(s.id)
    return {
      key: s.id,
      sends: s.do === 'carry_out',
      node: (
        <Node
          wide={wide}
          icon={<StepIcon step={s} size={15} className={s.do === 'call' ? 'text-white' : undefined} />}
          tone={s.do === 'log' ? 'bg-panel ring-1 ring-line' : s.do === 'call' ? 'bg-routine' : 'bg-ok/10'}
          person={s.do === 'call'}
          title={stepTitle(s)}
          text={s.text}
          badge={badge}
          active={!!badge}
        />
      ),
    }
  })
  return { guard, trigger, conditions, steps }
}

/**
 * The wide view reads like a sentence in two lanes: WHEN (trigger, then every condition) left to
 * right, then a turn down into THEN (the steps) left to right. Lanes wrap instead of running off
 * the edge; the guard hangs below the step it protects, or sits in its own ALWAYS lane.
 */
function Lanes({ spec, run }: { spec: Spec; run?: Run }) {
  const { guard, trigger, conditions, steps } = nodesFor(spec, run, true)
  const hasSend = steps.some((s) => s.sends)
  const chain = (items: { key: string; node: ReactNode }[]) =>
    items.map((it, i) => (
      <div key={it.key} className="flex items-start">
        {i > 0 && <div className="mt-[21px] shrink-0 px-1 text-ink-3/70"><Right /></div>}
        {it.node}
      </div>
    ))
  const lane = (label: string, body: ReactNode, last = false) => (
    <div className="grid grid-cols-[64px_minmax(0,1fr)] gap-x-3">
      <div className="flex flex-col items-center pt-[18px]">
        <span className="text-[11px] font-bold uppercase tracking-wider text-ink-3">{label}</span>
        {!last && (
          <div className="mt-2 flex flex-1 flex-col items-center text-ink-3/70">
            <div className="w-px flex-1 bg-current" />
            <svg width="10" height="7" viewBox="0 0 10 7" aria-hidden className="block"><path d="M1.5 1 L5 6 L8.5 1" {...stroke()} /></svg>
          </div>
        )}
      </div>
      <div className="flex min-w-0 flex-wrap items-start gap-y-5 pb-6">{body}</div>
    </div>
  )
  return (
    <div className="flex flex-col">
      {lane('When', chain([{ key: 'trigger', node: trigger }, ...conditions]))}
      {lane(
        'Then',
        chain(
          steps.map((s) => ({
            key: s.key,
            node: s.sends ? (
              <div className="flex flex-col">
                {s.node}
                <div className="ml-[26px] text-ink-3/70"><Down dashed /></div>
                {guard}
              </div>
            ) : (
              s.node
            ),
          })),
        ),
        hasSend,
      )}
      {!hasSend && lane('Always', guard, true)}
    </div>
  )
}

export function FlowGraph({ spec, run, wide }: { spec: Spec; run?: Run; wide?: boolean }) {
  const { guard, trigger, conditions, steps } = nodesFor(spec, run, false)
  const sendAt = steps.findIndex((s) => s.sends)
  // Narrow: one vertical path; conditions stack (they all have to hold); the step that sends
  // carries the guard as a side branch.
  const columns: { key: string; node: ReactNode }[] = [
    { key: 'trigger', node: trigger },
    {
      key: 'when',
      node: (
        <div className="flex flex-col">
          {conditions.map((cond, i) => (
            <div key={cond.key}>
              {i > 0 && <Arrow />}
              {cond.node}
            </div>
          ))}
        </div>
      ),
    },
    ...steps.map((s, i) => {
      if (i !== sendAt) return { key: s.key, node: s.node }
      return {
        key: s.key,
        node: (
          <div className="flex flex-col sm:flex-row sm:items-start">
            <div className="min-w-0 sm:flex-[3]">{s.node}</div>
            <Arrow side dashed />
            <div className="min-w-0 pl-6 sm:flex-[2] sm:pl-0">{guard}</div>
          </div>
        ),
      }
    }),
  ]
  if (sendAt < 0) columns.push({ key: 'guard', node: guard })
  const vertical = (
    <div className="flex flex-col">
      {columns.map((col, i) => (
        <div key={col.key} className="flex min-w-0 flex-col">
          {i > 0 && <Arrow dashed={col.key === 'guard'} />}
          {col.node}
        </div>
      ))}
    </div>
  )
  if (!wide) return vertical
  return (
    <>
      <div className="hidden lg:block"><Lanes spec={spec} run={run} /></div>
      <div className="lg:hidden">{vertical}</div>
    </>
  )
}
