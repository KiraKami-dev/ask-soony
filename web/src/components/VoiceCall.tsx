import { Mic, Phone, PhoneOff } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { refreshNow } from '@/components/routines/live'
import type { Card } from '@/lib/api'
import { cn } from '@/lib/utils'

// Voice conversation about one card with OpenAI gpt-realtime over WebRTC. The server mints a
// short-lived client secret; every tool call (look up, approve, escalate, reject) runs on the
// server through /api/cards/{id}/tool, the same path as the inbox buttons, except
// `show_on_screen`, which the browser answers itself by opening the source, the options or the
// email. A decision takes two steps (prepare_decision, then decide with its one-time token); the
// screen shows what was prepared, and `decide` is only relayed after the person spoke again.

export type ScreenWhat = 'document' | 'system' | 'options' | 'email'
export interface Prepared { decision: 'approve' | 'escalate' | 'reject'; option_index: number; summary: string }

type Line = { who: 'assistant' | 'you' | 'system'; text: string; id?: string }
type ToolResult = { ok?: boolean; status?: string; sent_to?: string | null; error?: string; name?: string; summary?: string }

interface Props {
  card: Card
  onChanged: () => void
  ringing?: boolean
  onRingingDone?: () => void
  /** Today screen: a quiet text button, the transcript rendered into `linesEl`. */
  quiet?: boolean
  linesEl?: HTMLElement | null
  /** The call is connecting or live (Today holds its Done screen until it ends). */
  onLive?: (live: boolean) => void
  /** prepare_decision returned ok (the summary to confirm), or null once it is decided or over. */
  onPrepared?: (p: Prepared | null) => void
  /** show_on_screen: open what the assistant talks about; false when there is nothing to show. */
  onScreen?: (what: ScreenWhat) => boolean
  /** Something the person did on screen during the call, told to the assistant (changes with k). */
  screenEvent?: { k: number; text: string } | null
}

export function VoiceCall({
  card, onChanged, ringing = false, onRingingDone, quiet, linesEl, onLive, onPrepared, onScreen, screenEvent,
}: Props) {
  const [status, setStatus] = useState<'idle' | 'connecting' | 'live' | 'ended' | 'error'>('idle')
  const [lines, setLines] = useState<Line[]>([])
  const pc = useRef<RTCPeerConnection | null>(null)
  const dcRef = useRef<RTCDataChannel | null>(null)
  const decidedRef = useRef(false)
  const live = status === 'connecting' || status === 'live'
  // Latest callbacks for the data channel handler, which is set up once per call.
  const cb = useRef({ onPrepared, onScreen })
  cb.current = { onPrepared, onScreen }

  useEffect(() => () => hangUp(), [])
  useEffect(() => { onLive?.(live) }, [live]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => { onLive?.(false) }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Tell the assistant what happened on screen (e.g. the email was sent with a tap), once.
  useEffect(() => {
    const dc = dcRef.current
    if (!screenEvent || !dc || dc.readyState !== 'open') return
    decidedRef.current = true
    dc.send(JSON.stringify({
      type: 'conversation.item.create',
      item: { type: 'message', role: 'system', content: [{ type: 'input_text', text: screenEvent.text }] },
    }))
    dc.send(JSON.stringify({ type: 'response.create' }))
  }, [screenEvent?.k]) // eslint-disable-line react-hooks/exhaustive-deps

  const add = (l: Line) => setLines((prev) => [...prev, l].slice(-12))
  // Lines tied to a conversation item: a placeholder appears when the turn starts and is filled
  // in place when its transcript arrives, so late transcriptions do not jump below the answer.
  const upsert = (l: Line) =>
    setLines((prev) => {
      const i = prev.findIndex((p) => p.id === l.id)
      if (i < 0) return [...prev, l].slice(-12)
      const next = [...prev]
      next[i] = l
      return next
    })
  const drop = (id: string) => setLines((prev) => prev.filter((p) => p.id !== id))

  function hangUp() {
    pc.current?.getSenders().forEach((s) => s.track?.stop())
    pc.current?.close()
    pc.current = null
    dcRef.current = null
    cb.current.onPrepared?.(null)
    setStatus((s) => (s === 'idle' ? s : 'ended'))
  }

  async function start() {
    if (live) return
    onRingingDone?.()
    setStatus('connecting')
    setLines([])
    try {
      const res = await fetch(`/api/cards/${encodeURIComponent(card.id)}/voice`, { method: 'POST' })
      if (!res.ok) throw new Error(await res.text())
      const { client_secret } = (await res.json()) as { client_secret: string }

      const peer = new RTCPeerConnection()
      pc.current = peer
      const audio = new Audio()
      audio.autoplay = true
      peer.ontrack = (e) => (audio.srcObject = e.streams[0])
      const mic = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      })
      mic.getTracks().forEach((t) => peer.addTrack(t, mic))

      const dc = peer.createDataChannel('oai-events')
      dcRef.current = dc
      decidedRef.current = false
      let closing = false
      // Speech turns since the last prepare_decision result: decide needs at least one.
      let heard = new Set<string>()
      const reply = (callId: string, out: unknown) => {
        dc.send(JSON.stringify({
          type: 'conversation.item.create',
          item: { type: 'function_call_output', call_id: callId, output: JSON.stringify(out) },
        }))
        dc.send(JSON.stringify({ type: 'response.create' }))
      }
      let fallback: ReturnType<typeof setTimeout> | undefined
      let hangTimer: ReturnType<typeof setTimeout> | undefined
      // Hold the hang-up while the person speaks again or a tool runs (e.g. "next time just do
      // that" right after a decision drafts a routine).
      const holdOn = () => {
        closing = false
        clearTimeout(fallback)
        clearTimeout(hangTimer)
      }
      dc.onopen = () => {
        setStatus('live')
        dc.send(JSON.stringify({ type: 'response.create' })) // assistant speaks first
      }
      dc.onmessage = (ev) => {
        const msg = JSON.parse(ev.data)
        if (msg.type === 'input_audio_buffer.speech_started') {
          holdOn()
        } else if (msg.type === 'input_audio_buffer.committed') {
          heard.add(msg.item_id)
          upsert({ who: 'you', text: '…', id: msg.item_id })
        } else if (msg.type === 'conversation.item.input_audio_transcription.completed') {
          if (msg.transcript?.trim()) upsert({ who: 'you', text: msg.transcript.trim(), id: msg.item_id })
          else { heard.delete(msg.item_id); drop(msg.item_id) }
        } else if (msg.type === 'conversation.item.input_audio_transcription.failed') {
          drop(msg.item_id)
        } else if (msg.type === 'response.output_audio_transcript.delta') {
          setLines((prev) => {
            const i = prev.findIndex((p) => p.id === msg.item_id)
            if (i < 0) return [...prev, { who: 'assistant' as const, text: msg.delta, id: msg.item_id }].slice(-12)
            const next = [...prev]
            next[i] = { ...next[i], text: next[i].text + msg.delta }
            return next
          })
        } else if (msg.type === 'response.output_audio_transcript.done') {
          upsert({ who: 'assistant', text: msg.transcript, id: msg.item_id })
        } else if (msg.type === 'response.function_call_arguments.done') {
          holdOn()
          const args = JSON.parse(msg.arguments || '{}')
          // Answered here, never sent to the server: open what the assistant talks about.
          if (msg.name === 'show_on_screen') {
            const what = String(args.what ?? '') as ScreenWhat
            const shown = cb.current.onScreen?.(what) ?? false
            reply(msg.call_id, shown ? { ok: true, shown: what } : { ok: false, error: `Nothing to show for ${what} here.` })
            return
          }
          // A yes needs a person: only relay decide after they spoke since the summary.
          if (msg.name === 'decide' && heard.size === 0) {
            reply(msg.call_id, { ok: false, error: 'Wait for the person to answer first.' })
            return
          }
          fetch(`/api/cards/${encodeURIComponent(card.id)}/tool`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: msg.name, arguments: args }),
          })
            .then((r) => r.json() as Promise<ToolResult>)
            .catch((e) => ({ ok: false, error: String(e) }) as ToolResult)
            .then((out) => {
              if (msg.name === 'prepare_decision' && out.ok) {
                heard = new Set()
                cb.current.onPrepared?.({
                  decision: (args.decision ?? 'approve') as Prepared['decision'],
                  option_index: Number(args.option_index ?? 0),
                  summary: String(out.summary ?? ''),
                })
              } else if (msg.name === 'decide' && out.ok) {
                decidedRef.current = true
                cb.current.onPrepared?.(null)
                add({ who: 'system', text: out.sent_to ? `Email sent to ${out.sent_to}` : `Recorded: ${out.status}` })
                onChanged()
              } else if (msg.name === 'decide' && /already/i.test(out.error ?? '')) {
                // Decided on screen meanwhile (a tap on Send): nothing went out twice.
                decidedRef.current = true
                cb.current.onPrepared?.(null)
              } else if (msg.name === 'teach_routine' && out.ok) {
                add({ who: 'system', text: `Drafted a routine: ${out.name}. It waits on screen.` })
                void refreshNow()
                onChanged()
              }
              reply(msg.call_id, out)
            })
        } else if (msg.type === 'response.done' && decidedRef.current && !closing) {
          // Hang up once the goodbye has finished playing, not on a fixed timer.
          closing = true
          fallback = setTimeout(hangUp, 20000)
        } else if (msg.type === 'output_audio_buffer.stopped' && closing) {
          clearTimeout(fallback)
          hangTimer = setTimeout(hangUp, 4000)
        } else if (msg.type === 'error') {
          add({ who: 'system', text: `Error: ${msg.error?.message ?? 'unknown'}` })
        }
      }

      const offer = await peer.createOffer()
      await peer.setLocalDescription(offer)
      const sdp = await fetch('https://api.openai.com/v1/realtime/calls', {
        method: 'POST',
        body: offer.sdp,
        headers: { Authorization: `Bearer ${client_secret}`, 'Content-Type': 'application/sdp' },
      })
      if (!sdp.ok) throw new Error(await sdp.text())
      await peer.setRemoteDescription({ type: 'answer', sdp: await sdp.text() })
    } catch (e) {
      add({ who: 'system', text: `Could not start: ${(e as Error).message.slice(0, 200)}` })
      setStatus('error')
      pc.current?.close()
    }
  }

  if (quiet) {
    const transcript: ReactNode = lines.length > 0 && (
      <ul className="mt-6 space-y-2 text-[15px] leading-relaxed" aria-live="polite">
        {lines.map((l, i) => (
          <li key={l.id ?? i} className={cn('break-words', l.who === 'you' ? 'text-ink-2' : l.who === 'system' ? 'text-[13.5px] text-ink-3' : 'text-ink')}>
            {l.who === 'you' && <span className="text-ink-3">You: </span>}
            {l.text}
          </li>
        ))}
      </ul>
    )
    if (ringing && !live) {
      return (
        <div className="w-full text-center lg:text-left" role="alert">
          <p className="text-[length:var(--body)] text-ink">Incoming launch alert. Talk it through?</p>
          <div className="mt-3 flex items-center justify-center gap-6 lg:justify-start">
            <button onClick={start} className="inline-flex h-11 cursor-pointer items-center gap-2 rounded-xl bg-accent px-6 font-medium text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
              <Phone size={17} /> Answer
            </button>
            <button onClick={onRingingDone} className="cursor-pointer text-ink-2 hover:text-ink">Later</button>
          </div>
        </div>
      )
    }
    return (
      <>
        <button
          onClick={live ? hangUp : start}
          className="inline-flex cursor-pointer items-center gap-1.5 text-ink-2 hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent"
        >
          {live ? <PhoneOff size={15} /> : <Mic size={15} />}
          {status === 'connecting' ? 'Connecting…' : status === 'live' ? 'Hang up' : 'Talk it through'}
        </button>
        {linesEl ? createPortal(transcript, linesEl) : transcript}
      </>
    )
  }

  if (ringing && !live) {
    return (
      <div className="mt-5" role="alert">
        <p className="t-body text-ink">Incoming launch alert. Talk it through?</p>
        <div className="t-body mt-3 flex items-center gap-6">
          <button onClick={start} className="inline-flex h-11 cursor-pointer items-center gap-2 rounded-xl bg-accent px-6 font-medium text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
            <Phone size={17} /> Answer
          </button>
          <button onClick={onRingingDone} className="cursor-pointer text-ink-2 hover:text-ink">Later</button>
        </div>
      </div>
    )
  }

  return (
    <div className="mt-5">
      <div className="t-small flex flex-wrap items-center gap-x-4 gap-y-1">
        <button
          onClick={live ? hangUp : start}
          className="inline-flex cursor-pointer items-center gap-1.5 text-ink-2 hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent"
        >
          {live ? <PhoneOff size={15} /> : <Mic size={15} />} {live ? 'Hang up' : 'Talk it through'}
        </button>
        <span className="text-ink-3">
          {status === 'connecting' && 'Connecting…'}
          {status === 'live' && 'Live: speak normally'}
          {status === 'ended' && 'Call ended'}
          {status === 'error' && 'Call failed'}
        </span>
      </div>
      {lines.length > 0 && (
        <ul className="t-small mt-4 space-y-2 leading-relaxed" aria-live="polite">
          {lines.map((l, i) => (
            <li key={l.id ?? i} className={cn('break-words', l.who === 'you' ? 'text-ink-2' : l.who === 'system' ? 'text-ink-3' : 'text-ink')}>
              {l.who === 'you' && <span className="text-ink-3">You: </span>}
              {l.text}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
