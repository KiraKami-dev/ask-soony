import { useRef, useState } from "react";
import type { ActionKind, Card } from "./types";

// Voice conversation about one card with OpenAI gpt-realtime over WebRTC. The server mints a
// short-lived client secret; the decision goes through the normal card action endpoint.

type Line = { who: "assistant" | "you" | "system"; text: string };

const DECISION_TO_ACTION: Record<string, ActionKind> = {
  approve: "allow_once",
  reject: "dismiss",
  escalate: "edit",
};

export default function VoiceCall({
  card,
  onAction,
  autoStart = false,
}: {
  card: Card;
  onAction: (id: string, action: ActionKind, note?: string) => void;
  autoStart?: boolean;
}) {
  const [status, setStatus] = useState<"idle" | "connecting" | "live" | "ended" | "error">("idle");
  const [lines, setLines] = useState<Line[]>([]);
  const pc = useRef<RTCPeerConnection | null>(null);
  const started = useRef(false);
  const add = (l: Line) => setLines((prev) => [...prev, l].slice(-12));

  function hangUp() {
    pc.current?.getSenders().forEach((s) => s.track?.stop());
    pc.current?.close();
    pc.current = null;
    setStatus("ended");
  }

  async function start() {
    if (started.current && status !== "ended" && status !== "error") return;
    started.current = true;
    setStatus("connecting");
    setLines([]);
    try {
      const res = await fetch(`/api/cards/${encodeURIComponent(card.id)}/voice`, { method: "POST" });
      if (!res.ok) throw new Error(await res.text());
      const { client_secret } = (await res.json()) as { client_secret: string };

      const peer = new RTCPeerConnection();
      pc.current = peer;
      const audio = new Audio();
      audio.autoplay = true;
      peer.ontrack = (e) => (audio.srcObject = e.streams[0]);
      const mic = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      mic.getTracks().forEach((t) => peer.addTrack(t, mic));

      const dc = peer.createDataChannel("oai-events");
      let decided = false;
      dc.onopen = () => {
        setStatus("live");
        dc.send(JSON.stringify({ type: "response.create" })); // assistant speaks first
      };
      dc.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === "response.output_audio_transcript.done" || msg.type === "response.audio_transcript.done") {
          add({ who: "assistant", text: msg.transcript });
        } else if (msg.type === "conversation.item.input_audio_transcription.completed" && msg.transcript?.trim()) {
          add({ who: "you", text: msg.transcript });
        } else if (msg.type === "response.function_call_arguments.done" && msg.name === "record_decision") {
          const args = JSON.parse(msg.arguments || "{}");
          const action = DECISION_TO_ACTION[args.decision] ?? "edit";
          const note = [`voice: ${args.decision}`, args.note].filter(Boolean).join(" · ");
          onAction(card.id, action, note);
          add({ who: "system", text: `Decision recorded: ${args.decision}` });
          dc.send(JSON.stringify({
            type: "conversation.item.create",
            item: { type: "function_call_output", call_id: msg.call_id, output: JSON.stringify({ ok: true }) },
          }));
          dc.send(JSON.stringify({ type: "response.create" }));
          decided = true;
        } else if (msg.type === "response.done" && decided) {
          setTimeout(hangUp, 4000); // let the goodbye play out
        } else if (msg.type === "error") {
          add({ who: "system", text: `Error: ${msg.error?.message ?? "unknown"}` });
        }
      };

      const offer = await peer.createOffer();
      await peer.setLocalDescription(offer);
      const sdp = await fetch("https://api.openai.com/v1/realtime/calls", {
        method: "POST",
        body: offer.sdp,
        headers: { Authorization: `Bearer ${client_secret}`, "Content-Type": "application/sdp" },
      });
      if (!sdp.ok) throw new Error(await sdp.text());
      await peer.setRemoteDescription({ type: "answer", sdp: await sdp.text() });
    } catch (e) {
      add({ who: "system", text: `Could not start: ${(e as Error).message.slice(0, 200)}` });
      setStatus("error");
      pc.current?.close();
    }
  }

  if (autoStart && !started.current) void start();

  const live = status === "connecting" || status === "live";
  return (
    <div className="rounded-md border border-slate-200 p-3">
      <div className="flex flex-wrap items-center gap-3">
        {!live ? (
          <button className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white" onClick={start}>
            Talk to the assistant
          </button>
        ) : (
          <button className="rounded-md border border-danger px-3 py-1.5 text-sm font-medium text-danger" onClick={hangUp}>
            Hang up
          </button>
        )}
        <span className="text-xs text-slate-500">
          {status === "connecting" && "Connecting…"}
          {status === "live" && "Live: speak normally, decide with approve, escalate or reject"}
          {status === "ended" && "Call ended"}
          {status === "error" && "Call failed"}
        </span>
      </div>
      {lines.length > 0 && (
        <ul className="mt-3 space-y-1 text-sm">
          {lines.map((l, i) => (
            <li key={i} className={l.who === "you" ? "text-slate-500" : l.who === "system" ? "text-xs text-accent" : ""}>
              <span className="font-medium">{l.who === "assistant" ? "Assistant: " : l.who === "you" ? "You: " : ""}</span>
              {l.text}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
