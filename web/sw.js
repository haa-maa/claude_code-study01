/* 오늘의 기록 — 껍데기를 담아 두어 인터넷 없이도 열리게 한다 */
var CACHE = "girok-shell-1";
var SHELL = [
  "./",
  "./index.html",
  "./manifest.webmanifest",
  "./icon-192.png",
  "./icon-512.png",
  "./apple-touch-icon.png",
  "./favicon-32.png"
];

self.addEventListener("install", function(ev){
  ev.waitUntil(
    caches.open(CACHE)
      .then(function(box){ return box.addAll(SHELL); })
      .then(function(){ return self.skipWaiting(); })
      .catch(function(){})
  );
});

self.addEventListener("activate", function(ev){
  ev.waitUntil(
    caches.keys().then(function(names){
      return Promise.all(names.map(function(n){
        return n === CACHE ? null : caches.delete(n);
      }));
    }).then(function(){ return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function(ev){
  var req = ev.request;
  if(req.method !== "GET") return;

  var url;
  try { url = new URL(req.url); } catch(e){ return; }

  var mine = (url.origin === self.location.origin);
  var font = /(^|\.)fonts\.(googleapis|gstatic)\.com$/.test(url.hostname);
  if(!mine && !font) return;

  // 화면 이동은 담아 둔 페이지를 먼저 (인터넷이 없어도 열린다)
  if(req.mode === "navigate"){
    ev.respondWith(
      caches.match("./index.html").then(function(hit){
        return hit || fetch(req).catch(function(){ return caches.match("./"); });
      })
    );
    return;
  }

  ev.respondWith(
    caches.match(req).then(function(hit){
      var live = fetch(req).then(function(res){
        if(res && (res.ok || res.type === "opaque")){
          var copy = res.clone();
          caches.open(CACHE).then(function(box){ box.put(req, copy); }).catch(function(){});
        }
        return res;
      }).catch(function(){ return hit; });
      return hit || live;
    })
  );
});
