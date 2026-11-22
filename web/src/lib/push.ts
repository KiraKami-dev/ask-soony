// PWA helpers: register the service worker and subscribe this device to launch alerts.

export async function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (!("serviceWorker" in navigator)) return null;
  try {
    return await navigator.serviceWorker.register("/sw.js");
  } catch {
    return null;
  }
}

function urlBase64ToUint8Array(base64: string): Uint8Array {
  const padded = (base64 + "=".repeat((4 - (base64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
}

export function pushSupported(): boolean {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

export async function enableAlerts(): Promise<string> {
  if (!pushSupported()) {
    return "This browser cannot receive alerts. On iPhone: Share → Add to Home Screen, then open the app.";
  }
  const permission = await Notification.requestPermission();
  if (permission !== "granted") return "Notifications were not allowed.";
  const reg = (await registerServiceWorker()) ?? (await navigator.serviceWorker.ready);
  const { public_key } = await fetch("/api/push/key").then((r) => r.json());
  const sub =
    (await reg.pushManager.getSubscription()) ??
    (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(public_key) as BufferSource }));
  const res = await fetch("/api/push/subscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(sub.toJSON()),
  });
  return res.ok ? "Alerts are on for this device." : `Could not save the subscription (${res.status}).`;
}

export async function sendTestAlert(cardId: string): Promise<string> {
  const res = await fetch("/api/push/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ card_id: cardId }),
  });
  if (!res.ok) return `Test alert failed: ${await res.text()}`;
  const out = await res.json();
  return out.sent ? `Test alert sent to ${out.sent} device(s). Lock your phone to see it.` : `Not delivered: ${out.failed?.join(", ")}`;
}
