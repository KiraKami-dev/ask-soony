import { type ReactNode, useEffect, useRef, useState } from 'react'
import { fmtNum } from '@/lib/api'
import { cn } from '@/lib/utils'

// The count page: one question for the people at the site, reached from a Teams message or a
// push (/count/<token>, no dashboard password, only a valid link). Phone first: what we know, one
// number to type, an optional photo of the sheet. A photo only suggests a number; the person
// confirms it. Their answer replaces the system's number on Launch.

interface Form {
  kind: 'packed' | 'received' | 'count'
  site: string
  title: string
  edition?: string
  part?: string
  supplier?: string
  last: { label: string; value: number | null; when?: string | null; unit?: string | null }
  field: string
  min: number
  max: number | null
  photo: string
  answered: boolean
}
interface Ocr { suggestion: number | null; numbers: number[]; note_number: string | null; photo: string }

const api = (token: string, tail = '') => `/api/count/${encodeURIComponent(token)}${tail}`
const digits = (s: string) => s.replace(/[^\d]/g, '')
const grouped = (s: string) => (digits(s) ? Number(digits(s)).toLocaleString('en-GB') : '')

/** Phone photos are large: send at most 1600 px as JPEG. */
async function shrink(file: File): Promise<string> {
  const url = URL.createObjectURL(file)
  try {
    const img = await new Promise<HTMLImageElement>((ok, bad) => {
      const i = new Image()
      i.onload = () => ok(i)
      i.onerror = bad
      i.src = url
    })
    const k = Math.min(1, 1600 / Math.max(img.width, img.height))
    const c = document.createElement('canvas')
    c.width = Math.round(img.width * k)
    c.height = Math.round(img.height * k)
    c.getContext('2d')!.drawImage(img, 0, 0, c.width, c.height)
    return c.toDataURL('image/jpeg', 0.85)
  } finally {
    URL.revokeObjectURL(url)
  }
}

export function CountPage({ token }: { token: string }) {
  const [form, setForm] = useState<Form | null>(null)
  const [gone, setGone] = useState(false)
  const [count, setCount] = useState('')
  const [arrived, setArrived] = useState<boolean | null>(null)
  const [name, setName] = useState('')
  const [note, setNote] = useState('')
  const [ocr, setOcr] = useState<Ocr | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [reading, setReading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState<string | null>(null)
  const camera = useRef<HTMLInputElement>(null)

  useEffect(() => {
    document.title = 'Count · Ask Soony'
    fetch(api(token))
      .then(async (r) => (r.ok ? setForm(await r.json()) : setGone(true)))
      .catch(() => setGone(true))
  }, [token])

  async function onPhoto(file: File | undefined) {
    if (!file) return
    setError(''); setReading(true); setOcr(null)
    try {
      const image = await shrink(file)
      setPreview(image)
      const r = await fetch(api(token, '/photo'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ image }) })
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'The photo did not go through.')
      const o: Ocr = await r.json()
      setOcr(o)
      if (o.note_number && !note) setNote(o.note_number)
    } catch (e) {
      setError((e as Error).message)
    } finally { setReading(false) }
  }

  async function submit() {
    if (!form) return
    setError(''); setBusy(true)
    try {
      const body = {
        count: digits(count) ? Number(digits(count)) : null,
        by: name.trim() || null,
        photo: ocr?.photo ?? null,
        ...(form.kind === 'received' ? { arrived: arrived !== false, note_number: note.trim() || null } : {}),
      }
      const r = await fetch(api(token), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || 'That did not go through. Try again.')
      setDone(j.text)
    } catch (e) {
      setError((e as Error).message)
    } finally { setBusy(false) }
  }

  const shell = (children: ReactNode) => (
    <main className="min-h-full bg-ground px-6 pb-[max(32px,env(safe-area-inset-bottom))] pt-12">
      <div className="mx-auto max-w-[440px]">{children}</div>
    </main>
  )
  if (gone) return shell(
    <>
      <h1 className="t-big text-ink">This link has expired.</h1>
      <p className="t-body mt-4 text-ink-2">The question was answered or the launch is over. Nothing to do here.</p>
    </>,
  )
  if (!form) return shell(<p className="t-body text-ink-3">Loading…</p>)
  if (done) return shell(
    <div className="rise" role="status">
      <svg width="36" height="36" viewBox="0 0 24 24" aria-hidden="true" className="text-accent">
        <path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <h1 className="t-big mt-6 text-ink">{done}</h1>
      <p className="t-body mt-4 text-ink-2">You can close this page. If the number changes, open the link again.</p>
    </div>,
  )

  const askNumber = form.kind !== 'received' || arrived === true
  const ready = form.kind === 'received' ? arrived === false || (arrived === true && !!digits(count)) : !!digits(count)
  const suggestion = ocr?.suggestion ?? null
  const button = form.kind === 'received' && arrived === false ? 'Send: not here yet' : 'Send the count'
  return shell(
    <>
      <p className="t-small text-ink-3">{form.site}{form.edition ? ` · ${form.edition} Edition` : form.part ? ` · ${form.part}` : ''}</p>
      <h1 className="t-big mt-3 text-ink">{form.title}</h1>
      <p className="t-body mt-4 text-ink-2">
        {form.last.label}: <span className="text-ink">{fmtNum(form.last.value)}{form.last.unit ? ` ${form.last.unit}` : ''}</span>
        {form.last.when ? <span className="text-ink-3">{form.kind === 'packed' ? ' at ' : ' · '}{form.last.when}</span> : null}
        {form.supplier ? <span className="text-ink-3"> · from {form.supplier}</span> : null}
      </p>

      {form.kind === 'received' && (
        <fieldset className="mt-9">
          <legend className="t-small mb-3 text-ink-3">Is it here?</legend>
          <div className="grid grid-cols-2 gap-3">
            {[true, false].map((v) => (
              <button
                key={String(v)}
                type="button"
                onClick={() => setArrived(v)}
                aria-pressed={arrived === v}
                className={cn('t-body h-[52px] cursor-pointer rounded-[14px] focus-visible:outline-2 focus-visible:outline-accent',
                  arrived === v ? 'bg-ink text-white' : 'bg-soft text-ink hover:bg-hair')}
              >
                {v ? 'Yes, it arrived' : 'Not yet'}
              </button>
            ))}
          </div>
        </fieldset>
      )}

      {askNumber && (
        <div className="mt-9">
          <label htmlFor="count" className="t-small text-ink-3">{form.field}</label>
          <input
            id="count"
            inputMode="numeric"
            autoComplete="off"
            autoFocus={form.kind !== 'received'}
            value={grouped(count)}
            onChange={(e) => { setCount(digits(e.target.value)); setError('') }}
            placeholder={form.last.value != null && form.kind === 'packed' ? `more than ${fmtNum(form.last.value)}` : '0'}
            className="mt-2 h-[64px] w-full rounded-[14px] bg-soft px-4 text-[28px] font-medium tracking-tight text-ink placeholder:text-ink-3 placeholder:text-[17px] placeholder:font-normal focus:outline-2 focus:outline-accent"
          />
          {suggestion != null && digits(count) !== String(suggestion) && (
            <button type="button" onClick={() => setCount(String(suggestion))} className="t-body mt-3 cursor-pointer text-left text-ink-2 hover:text-ink">
              The photo says <span className="font-medium text-ink">{fmtNum(suggestion)}</span>. <span className="uline">Use it</span>
            </button>
          )}
          {ocr && suggestion == null && (
            <p className="t-small mt-3 text-ink-3">No number on the photo looks right. Type it in, please.</p>
          )}
        </div>
      )}

      {form.kind === 'received' && arrived === true && (
        <div className="mt-6">
          <label htmlFor="note" className="t-small text-ink-3">Delivery note number (optional)</label>
          <input id="note" value={note} onChange={(e) => setNote(e.target.value)} className="t-body mt-2 h-12 w-full rounded-[14px] bg-soft px-4 text-ink focus:outline-2 focus:outline-accent" />
        </div>
      )}

      {askNumber && (
        <div className="mt-6">
          <input ref={camera} type="file" accept="image/*" capture="environment" className="sr-only" onChange={(e) => void onPhoto(e.target.files?.[0])} aria-label={form.photo} />
          <button type="button" onClick={() => camera.current?.click()} disabled={reading} className="t-body flex cursor-pointer items-center gap-3 text-ink-2 hover:text-ink disabled:opacity-60">
            <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 8h3l1.5-2h7L17 8h3v11H4z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /><circle cx="12" cy="13" r="3.5" fill="none" stroke="currentColor" strokeWidth="1.6" /></svg>
            {reading ? 'Reading the photo…' : preview ? 'Take another photo' : `${form.photo} (optional)`}
          </button>
          {preview && <img src={preview} alt="" className="mt-4 max-h-40 rounded-xl object-cover" />}
        </div>
      )}

      <div className="mt-6">
        <label htmlFor="name" className="t-small text-ink-3">Your name (optional)</label>
        <input id="name" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} className="t-body mt-2 h-12 w-full rounded-[14px] bg-soft px-4 text-ink focus:outline-2 focus:outline-accent" />
      </div>

      {error && <p className="t-body mt-6 text-late" role="alert">{error}</p>}
      <button
        onClick={() => void submit()}
        disabled={!ready || busy}
        className="t-body mt-9 h-[54px] w-full cursor-pointer rounded-[14px] bg-accent font-medium text-white shadow-[0_1px_2px_rgba(35,72,208,.25)] hover:bg-[#1d3db5] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-default disabled:opacity-40"
      >
        {busy ? 'Sending…' : button}
      </button>
      <p className="t-small mt-4 text-center text-ink-3">Your number goes straight to the launch plan.</p>
    </>,
  )
}
