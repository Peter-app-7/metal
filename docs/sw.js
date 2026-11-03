// sw.js: "service worker". Guarda a app e os últimos dados no aparelho, para abrir sem internet.
// Tenta sempre primeiro a versão mais recente; sem internet, usa a guardada.

const NOME_CACHE = 'metal-v3';
const FICHEIROS = ['./', 'index.html', 'style.css', 'app.js', 'manifest.json', 'icon.png'];

self.addEventListener('install', (evento) => {
  evento.waitUntil(caches.open(NOME_CACHE).then((cache) => cache.addAll(FICHEIROS)));
  self.skipWaiting();
});

self.addEventListener('activate', (evento) => {
  // Apaga versões antigas dos ficheiros guardados.
  evento.waitUntil((async () => {
    const nomes = await caches.keys();
    await Promise.all(nomes.filter((nome) => nome !== NOME_CACHE).map((nome) => caches.delete(nome)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (evento) => {
  const endereco = new URL(evento.request.url);
  if (evento.request.method !== 'GET' || endereco.origin !== self.location.origin) return;

  evento.respondWith((async () => {
    const cache = await caches.open(NOME_CACHE);
    try {
      const resposta = await fetch(evento.request);
      if (resposta.ok) await cache.put(evento.request, resposta.clone());
      return resposta;
    } catch (erro) {
      const guardada = await cache.match(evento.request);
      if (guardada) return guardada;
      if (evento.request.mode === 'navigate') return cache.match('index.html');
      throw erro;
    }
  })());
});
