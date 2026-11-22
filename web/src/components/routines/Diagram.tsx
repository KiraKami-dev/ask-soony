import { cn } from '@/lib/utils'
import { ChannelIcon, StepIcon } from './RoutineFlow'
import { MODES, didLines, type Mode, type Run, type Spec } from './types'

// Small parts of the routine view: the test run as plain lines, and the trust choice as quiet
// text ("Ask me · Do it and tell me · Do it quietly").

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

/** "Ask me · Do it and tell me · Do it quietly" as quiet text, the chosen one in ink. */
export function TrustControl({
  value, onChange, disabled, dim,
}: { value: Mode; onChange: (m: Mode) => void; disabled?: boolean; dim?: boolean }) {
  return (
    <div role="radiogroup" aria-label="How it acts" className={cn('t-small flex flex-wrap items-baseline gap-x-1', dim && 'opacity-60')}>
      {MODES.map((m, i) => (
        <span key={m.key} className="inline-flex items-baseline gap-x-1">
          {i > 0 && <span aria-hidden className="text-ink-3">·</span>}
          <button
            type="button"
            role="radio"
            aria-checked={value === m.key}
            disabled={disabled}
            title={m.hint}
            onClick={() => value !== m.key && onChange(m.key)}
            className={cn(
              'cursor-pointer whitespace-nowrap rounded px-0.5 focus-visible:outline-2 focus-visible:outline-accent disabled:cursor-default',
              value === m.key ? 'font-medium text-ink underline decoration-ink-3 decoration-1 underline-offset-[5px]' : 'text-ink-3 hover:text-ink-2',
            )}
          >
            {m.label}
          </button>
        </span>
      ))}
    </div>
  )
}
