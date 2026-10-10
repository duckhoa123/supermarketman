// Service worker: installable app shell + web push notifications.
const SHELL = "pass-shell-v1";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(["/", "/manifest.webmanifest", "/icon.svg"])));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});

// Network first for navigations so owners always get fresh numbers; fall back to the cached shell offline.
self.addEventListener("fetch", (event) => {
  if (event.request.mode === "navigate") {
    event.respondWith(fetch(event.request).catch(() => caches.match("/")));
  }
});

self.addEventListener("push", (event) => {
  const data = event.data ? event.data.json() : {};
  event.waitUntil(
    self.registration.showNotification(data.title || "Pass", {
      body: data.body || "",
      icon: "/icon.svg",
      data: data.data || {},
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(self.clients.openWindow("/alerts"));
});
