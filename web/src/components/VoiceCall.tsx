import { Mic, Phone, PhoneOff } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import type { Card } from '@/lib/api'
import { cn } from '@/lib/utils'

// Voice conversation about one card with OpenAI gpt-realtime over WebRTC. The server mints a
// short-lived client secret; every tool call (look up, approve, escalate, reject) runs on the
// server through /api/cards/{id}/tool, the same path as the inbox buttons.

type Line = { who: 'assistant' | 'you' | 'system'; text: string; id?: string }
type ToolResult = { ok?: boolean; status?: string; sent_to?: string | null; error?: string }

interface Props {
  card: Card
  onChanged: () => void
  ringing?: boolean
  onRingingDone?: () => void
}

export function VoiceCall({ card, onChanged, ringing = false, onRingingDone }: Props) {
  const [status, setStatus] = useState<'idle' | 'connecting' | 'live' | 'ended' | 'error'>('idle')
  const [lines, setLines] = useState<Line[]>([])
  const pc = useRef<RTCPeerConnection | null>(null)
  const live = status === 'connecting' || status === 'live'

  useEffect(() => () => hangUp(), [])

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
      let decided = false
      let closing = false
      let fallback: ReturnType<typeof setTimeout> | undefined
      dc.onopen = () => {
        setStatus('live')
        dc.send(JSON.stringify({ type: 'response.create' })) // assistant speaks first
      }
      dc.onmessage = (ev) => {
        const msg = JSON.parse(ev.data)
        if (msg.type === 'input_audio_buffer.committed') {
          upsert({ who: 'you', text: '…', id: msg.item_id })
        } else if (msg.type === 'conversation.item.input_audio_transcription.completed') {
          if (msg.transcript?.trim()) upsert({ who: 'you', text: msg.transcript.trim(), id: msg.item_id })
          else drop(msg.item_id)
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
          const args = JSON.parse(msg.arguments || '{}')
          fetch(`/api/cards/${encodeURIComponent(card.id)}/tool`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: msg.name, arguments: args }),
          })
            .then((r) => r.json() as Promise<ToolResult>)
            .catch((e) => ({ ok: false, error: String(e) }) as ToolResult)
            .then((out) => {
              if (msg.name === 'decide' && out.ok) {
                decided = true
                add({ who: 'system', text: out.sent_to ? `Email sent to ${out.sent_to}` : `Recorded: ${out.status}` })
                onChanged()
              }
              dc.send(JSON.stringify({
                type: 'conversation.item.create',
                item: { type: 'function_call_output', call_id: msg.call_id, output: JSON.stringify(out) },
              }))
              dc.send(JSON.stringify({ type: 'response.create' }))
            })
        } else if (msg.type === 'response.done' && decided && !closing) {
          // Hang up once the goodbye has finished playing, not on a fixed timer.
          closing = true
          fallback = setTimeout(hangUp, 20000)
        } else if (msg.type === 'output_audio_buffer.stopped' && closing) {
          clearTimeout(fallback)
          setTimeout(hangUp, 800)
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

  if (ringing && !live) {
    return (
      <div className="mt-4 rounded-xl border-2 border-stop/50 bg-stop/5 p-4 text-center">
        <div className="text-xs font-bold uppercase tracking-wide text-stop">Incoming launch alert</div>
        <div className="mt-1 text-[15px] font-semibold">Talk it through with the assistant?</div>
        <div className="mt-3 flex justify-center gap-2">
          <Button onClick={start}><Phone size={18} /> Answer</Button>
          <Button variant="outline" onClick={onRingingDone}>Later</Button>
        </div>
      </div>
    )
  }

  return (
    <div className="mt-4">
      <div className="flex flex-wrap items-center gap-2">
        {!live ? (
          <Button variant="outline" size="sm" onClick={start}>
            <Mic size={16} /> Talk it through
          </Button>
        ) : (
          <Button variant="outline" size="sm" onClick={hangUp} className="text-stop">
            <PhoneOff size={16} /> Hang up
          </Button>
        )}
        <span className="text-sm text-ink-3">
          {status === 'connecting' && 'Connecting…'}
          {status === 'live' && 'Live: speak normally'}
          {status === 'ended' && 'Call ended'}
          {status === 'error' && 'Call failed'}
        </span>
      </div>
      {lines.length > 0 && (
        <ul className="mt-3 space-y-1.5 rounded-lg bg-panel p-3 text-[15px]">
          {lines.map((l, i) => (
            <li
              key={l.id ?? i}
              className={cn(
                'break-words',
                l.who === 'you' && 'text-right text-ink-2',
                l.who === 'system' && 'text-sm font-semibold text-ok',
              )}
            >
              {l.who !== 'system' && (
                <span className="font-semibold">{l.who === 'assistant' ? 'Assistant: ' : 'You: '}</span>
              )}
              {l.text}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
