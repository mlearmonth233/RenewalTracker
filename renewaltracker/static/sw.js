/* RenewalTracker service worker: shows push notifications and focuses the app on click. */
"use strict";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (_) {
    data = { title: "RenewalTracker", body: event.data ? event.data.text() : "" };
  }
  const title = data.title || "RenewalTracker";
  const options = {
    body: data.body || "You have a renewal coming up.",
    icon: data.icon || "/static/icon.svg",
    badge: data.badge || "/static/icon.svg",
    tag: data.tag || "renewaltracker",
    renotify: !!data.tag,
    data: { url: data.url || "/" },
    requireInteraction: !!data.requireInteraction,
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL((event.notification.data && event.notification.data.url) || "/", self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ("focus" in client) {
          client.navigate && client.url !== target && client.navigate(target);
          return client.focus();
        }
      }
      return self.clients.openWindow(target);
    })
  );
});
