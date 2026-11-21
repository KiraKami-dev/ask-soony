import { useEffect, useRef, useState } from "react";
import { enableAlerts, pushSupported, registerServiceWorker, sendTestAlert } from "./push";
import type { ActionKind, Card } from "./types";

// Voice conversation about one card with OpenAI gpt-realtime over WebRTC. The server mints a
// short-lived client secret; the decision goes through the normal card action endpoint.

type Line = { who: "assistant" | "you" | "system"; text: string; id?: string };

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
  // Lines tied to a conversation item: a placeholder appears when the turn starts and is filled
  // in place when its transcript arrives, so late transcriptions do not jump below the answer.
  const upsert = (l: Line) =>
    setLines((prev) => {
      const i = prev.findIndex((p) => p.id === l.id);
      if (i < 0) return [...prev, l].slice(-12);
      const next = [...prev];
      next[i] = l;
      return next;
    });
  const drop = (id: string) => setLines((prev) => prev.filter((p) => p.id !== id));
  // Hold to talk: the mic only sends while the button is held (for noisy rooms).
  const [holdToTalk, setHoldToTalk] = useState(true);
  const [talking, setTalking] = useState(false);
  const micTrack = useRef<MediaStreamTrack | null>(null);
  const setMic = (on: boolean) => {
    if (micTrack.current) micTrack.current.enabled = on;
    setTalking(on);
  };
  const [info, setInfo] = useState("");
  // Opened from a push notification: show the card as an incoming call.
  const params = new URLSearchParams(location.search);
  const [ringing, setRinging] = useState(params.get("talk") === "1" && params.get("card") === card.id);
  useEffect(() => {
    void registerServiceWorker();
  }, []);

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
      micTrack.current = mic.getAudioTracks()[0] ?? null;
      if (micTrack.current) micTrack.current.enabled = !holdToTalk;

      const dc = peer.createDataChannel("oai-events");
      let decided = false;
      dc.onopen = () => {
        setStatus("live");
        dc.send(JSON.stringify({ type: "response.create" })); // assistant speaks first
      };
      dc.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === "input_audio_buffer.committed") {
          upsert({ who: "you", text: "…", id: msg.item_id });
        } else if (msg.type === "conversation.item.input_audio_transcription.completed") {
          if (msg.transcript?.trim()) upsert({ who: "you", text: msg.transcript.trim(), id: msg.item_id });
          else drop(msg.item_id);
        } else if (msg.type === "conversation.item.input_audio_transcription.failed") {
          drop(msg.item_id);
        } else if (msg.type === "response.output_audio_transcript.delta" || msg.type === "response.audio_transcript.delta") {
          setLines((prev) => {
            const i = prev.findIndex((p) => p.id === msg.item_id);
            if (i < 0) return [...prev, { who: "assistant" as const, text: msg.delta, id: msg.item_id }].slice(-12);
            const next = [...prev];
            next[i] = { ...next[i], text: next[i].text + msg.delta };
            return next;
          });
        } else if (msg.type === "response.output_audio_transcript.done" || msg.type === "response.audio_transcript.done") {
          upsert({ who: "assistant", text: msg.transcript, id: msg.item_id });
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
        } else if (msg.type === "response.function_call_arguments.done") {
          // Other tools run on the server; relay the result back to the model.
          const args = JSON.parse(msg.arguments || "{}");
          add({ who: "system", text: `Running ${msg.name}…` });
          fetch(`/api/cards/${encodeURIComponent(card.id)}/tool`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: msg.name, arguments: args }),
          })
            .then((r) => r.json())
            .catch((e) => ({ ok: false, error: String(e) }))
            .then((out) => {
              if (msg.name === "send_supplier_email" && out.ok) add({ who: "system", text: `Email sent to ${out.sent_to}` });
              if (msg.name === "escalate" && out.ok) add({ who: "system", text: "Escalated" });
              if (out.ok && msg.name !== "get_card_details") decided = true;
              dc.send(JSON.stringify({
                type: "conversation.item.create",
                item: { type: "function_call_output", call_id: msg.call_id, output: JSON.stringify(out) },
              }));
              dc.send(JSON.stringify({ type: "response.create" }));
            });
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
  if (ringing && !live) {
    return (
      <div className="rounded-lg border-2 border-danger bg-danger/5 p-4 text-center">
        <div className="text-xs font-semibold uppercase tracking-wide text-danger">Incoming launch alert</div>
        <div className="mt-1 text-base font-semibold">{card.title}</div>
        <div className="mt-4 flex justify-center gap-3">
          <button
            className="rounded-full bg-emerald-600 px-6 py-3 text-base font-semibold text-white"
            onClick={() => {
              setRinging(false);
              history.replaceState(null, "", `/?card=${encodeURIComponent(card.id)}`);
              void start();
            }}
          >
            Answer
          </button>
          <button
            className="rounded-full border border-slate-300 px-6 py-3 text-base"
            onClick={() => {
              setRinging(false);
              history.replaceState(null, "", `/?card=${encodeURIComponent(card.id)}`);
            }}
          >
            Later
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className="rounded-md border border-slate-200 p-3">
      <div className="flex flex-wrap items-center gap-3">
        {!live ? (
          <button className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white" onClick={start}>
            Talk to the assistant
          </button>
        ) : (
          <>
            {holdToTalk && (
              <button
                className={`select-none rounded-full px-5 py-2 text-sm font-semibold text-white ${talking ? "bg-emerald-600" : "bg-slate-700"}`}
                style={{ WebkitUserSelect: "none", touchAction: "none" }}
                onPointerDown={() => setMic(true)}
                onPointerUp={() => setMic(false)}
                onPointerLeave={() => setMic(false)}
                onPointerCancel={() => setMic(false)}
                onContextMenu={(e) => e.preventDefault()}
              >
                {talking ? "Listening…" : "Hold to talk"}
              </button>
            )}
            <button className="rounded-md border border-danger px-3 py-1.5 text-sm font-medium text-danger" onClick={hangUp}>
              Hang up
            </button>
          </>
        )}
        <label className="flex items-center gap-1 text-xs text-slate-600">
          <input
            type="checkbox"
            checked={holdToTalk}
            onChange={(e) => {
              setHoldToTalk(e.target.checked);
              if (micTrack.current) micTrack.current.enabled = !e.target.checked;
              setTalking(false);
            }}
          />
          Hold to talk (noisy room)
        </label>
        {pushSupported() && !live && (
          <>
            <button className="rounded-md border border-slate-300 px-3 py-1.5 text-sm" onClick={async () => setInfo(await enableAlerts())}>
              Enable call alerts on this device
            </button>
            <button className="rounded-md border border-slate-300 px-3 py-1.5 text-sm" onClick={async () => setInfo(await sendTestAlert(card.id))}>
              Send test alert
            </button>
          </>
        )}
        <span className="text-xs text-slate-500">
          {info && <span className="mr-2">{info}</span>}
          {status === "connecting" && "Connecting…"}
          {status === "live" && (holdToTalk ? "Live: hold the button while you speak" : "Live: speak normally")}
          {status === "ended" && "Call ended"}
          {status === "error" && "Call failed"}
        </span>
      </div>
      {lines.length > 0 && (
        <ul className="mt-3 space-y-1 text-sm">
          {lines.map((l, i) => (
            <li
              key={l.id ?? i}
              className={`break-words ${l.who === "you" ? "text-right text-slate-500" : l.who === "system" ? "text-xs text-accent" : ""}`}
            >
              {l.who !== "system" && (
                <span className="font-medium">{l.who === "assistant" ? "Assistant: " : "You: "}</span>
              )}
              {l.text}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
