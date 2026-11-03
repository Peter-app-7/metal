// sw.js: "service worker". Guarda a app e os últimos dados no aparelho, para abrir sem internet.
// Vai sempre primeiro à rede; a cópia guardada só serve quando não há ligação.
//
// O GitHub Pages deixa guardar os ficheiros 10 minutos (max-age=600). Por isso cada pedido leva
// cache: 'no-cache', que obriga a perguntar ao servidor se o ficheiro mudou, em vez de usar
// a cache do browser. Não é preciso mudar nada aqui ao publicar uma versão nova.

const NOME_CACHE = 'metal-v7';
// O data.json também fica, para a app abrir sem ligação logo depois de uma atualização.
const FICHEIROS = ['./', 'index.html', 'style.css', 'app.js', 'manifest.json', 'icon.png', 'data.json'];

self.addEventListener('install', (evento) => {
  evento.waitUntil((async () => {
    const cache = await caches.open(NOME_CACHE);
    await cache.addAll(FICHEIROS.map((ficheiro) => new Request(ficheiro, { cache: 'reload' })));
    await self.skipWaiting();
  })());
});

async function recarregarJanelas() {
  const janelas = await self.clients.matchAll({ type: 'window' });
  await Promise.all(janelas.map((janela) => janela.navigate(janela.url).catch(() => {})));
}

self.addEventListener('activate', (evento) => {
  evento.waitUntil((async () => {
    const nomes = await caches.keys();
    const antigas = nomes.filter((nome) => nome !== NOME_CACHE);
    await Promise.all(antigas.map((nome) => caches.delete(nome)));
    await self.clients.claim();
    // Uma app aberta com o código anterior não recarrega sozinha: recarrega-a aqui.
    // Sem await: o recarregamento passa por este service worker, que só atende pedidos
    // depois de o activate terminar. Esperar por ele aqui bloquearia os dois.
    if (antigas.length) recarregarJanelas();
  })());
});

self.addEventListener('fetch', (evento) => {
  const endereco = new URL(evento.request.url);
  if (evento.request.method !== 'GET' || endereco.origin !== self.location.origin) return;

  evento.respondWith((async () => {
    const cache = await caches.open(NOME_CACHE);
    let resposta = null;
    try {
      resposta = await fetch(evento.request.url, { cache: 'no-cache', credentials: 'same-origin' });
      if (resposta.ok) {
        await cache.put(evento.request, resposta.clone());
        return resposta;
      }
    } catch (erro) {
      // Sem ligação: segue para a cópia guardada.
    }
    const guardada = await cache.match(evento.request);
    if (guardada) return guardada;
    if (evento.request.mode === 'navigate') {
      const pagina = await cache.match('index.html');
      if (pagina) return pagina;
    }
    if (resposta) return resposta;
    return Response.error();
  })());
});
