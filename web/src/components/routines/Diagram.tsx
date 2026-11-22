import { cn } from '@/lib/utils'
import { ChannelIcon, StepIcon } from './RoutineFlow'
import { MODES, didLines, type Mode, type Run, type Spec } from './types'

// Small parts of the routine view: the test run as plain lines, and the trust ladder as a
// segmented control ("Ask me · Tell me · Quietly").

export function TestResult({ run, spec, title = "Test on today's inbox" }: { run: Run; spec?: Spec | null; title?: string }) {
  const lines = didLines(run, spec)
  return (
    <div>
      <p className="t-small text-ink-3">{title}</p>
      {!run.ok ? (
        <div className="mt-2">
          <p className="t-body text-late">The test failed, so this version cannot be used.</p>
          <pre className="t-small mt-2 max-h-32 overflow-auto whitespace-pre-wrap rounded-xl bg-soft p-3 text-ink-2">{run.error}</pre>
        </div>
      ) : lines.length === 0 ? (
        <p className="t-body mt-2 text-ink-2">Nothing in today's inbox matches. It waits for the next case like this.</p>
      ) : (
        <ul className="mt-2 space-y-1.5">
          {lines.map((l, i) => (
            <li key={i} className={cn('t-body flex items-start gap-2.5', run.dry ? 'text-ink' : 'text-routine')}>
              <span className="mt-[5px] shrink-0">
                {l.kind === 'send' ? <StepIcon step={{ do: 'carry_out' }} size={15} />
                  : l.kind === 'note' ? <StepIcon step={{ do: 'log' }} size={15} />
                  : l.kind === 'tell' ? <ChannelIcon channel={l.channel} size={15} />
                  : <StepIcon step={{ do: l.kind }} size={15} />}
              </span>
              <span className="min-w-0 break-words">{l.text}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * The trust ladder as a segmented control: Ask me, Tell me, Quietly. Each segment says in one
 * line what it does (on a phone only the selected one does, below the control).
 */
export function TrustControl({
  value, onChange, disabled, dim, note,
}: { value: Mode; onChange: (m: Mode) => void; disabled?: boolean; dim?: boolean; note?: string }) {
  const cur = MODES.find((m) => m.key === value) ?? MODES[0]
  return (
    <div className={cn('transition-opacity', dim && 'opacity-55')}>
      <div role="radiogroup" aria-label="How much it does on its own" className="grid grid-cols-3 gap-1 rounded-[14px] bg-soft p-1">
        {MODES.map((m) => {
          const on = value === m.key
          return (
            <button
              key={m.key}
              type="button"
              role="radio"
              aria-checked={on}
              disabled={disabled}
              onClick={() => !on && onChange(m.key)}
              className={cn(
                'min-w-0 cursor-pointer rounded-[10px] px-3 py-2 text-left transition-[background-color,box-shadow,color] duration-200 focus-visible:outline-2 focus-visible:outline-accent disabled:cursor-default',
                'max-sm:py-2.5 max-sm:text-center',
                on ? 'bg-white shadow-[0_1px_2px_rgba(24,24,26,.06),0_0_0_1px_var(--color-hair)]' : 'hover:bg-white/60',
              )}
            >
              <span className={cn('t-small block whitespace-nowrap font-medium', on ? 'text-ink' : 'text-ink-2')}>{m.label}</span>
              <span className={cn('t-small hidden leading-snug sm:block', on ? 'text-ink-2' : 'text-ink-3')}>{m.hint}</span>
            </button>
          )
        })}
      </div>
      <p className="t-small mt-2 text-ink-3" aria-live="polite">
        <span className="sm:hidden">{cur.hint}. </span>{note ?? cur.changes}
      </p>
    </div>
  )
}
