// Service worker: shows launch alerts and opens the card ready to answer when tapped.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { title: "Launch alert", body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "Launch alert", {
      body: data.body || "",
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      tag: data.card_id || data.url || "alert",
      requireInteraction: true,
      data: { card_id: data.card_id || null, url: data.url || null },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const card = data.card_id;
  // A question for the site opens its count page; a decision opens the card ready to answer.
  const url = data.url || (card ? `/d/${encodeURIComponent(card)}?talk=1` : "/");
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
      for (const w of wins) {
        if ("navigate" in w) return w.navigate(url).then((c) => c && c.focus());
      }
      return self.clients.openWindow(url);
    }),
  );
});
