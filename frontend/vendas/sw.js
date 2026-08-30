/**
 * Service worker do PWA de Vendas.
 *
 * Só faz uma coisa: garantir que o app **abra** sem internet. Os dados
 * (cardápio e fila de pedidos) não passam por aqui — o cardápio mora no
 * localStorage e os pedidos no IndexedDB, os dois controlados pelo app.
 *
 * Nada de API entra em cache. Um `/pedidos` servido de cache seria uma venda
 * fantasma; um `/cardapio` velho seria preço errado no balcão.
 *
 * Ao mexer nos arquivos do app, suba o VERSAO: é ele que descarta o cache
 * antigo e evita o PWA congelado numa versão de duas semanas atrás.
 */

const VERSAO = "v10";
const CACHE = `shalon-vendas-${VERSAO}`;

const CASCA = [
  "./",
  "./index.html",
  "./app.js",
  "./fila.js",
  "./comanda.js",
  "./rawbt.js",
  "./impressao.js",
  "./vendas.css",
  "./manifest.json",
  "./icone-192.png",
  "./icone-512.png",
  "../comum/api.js",
  "../comum/formato.js",
  "../comum/ws.js",
  "../comum/estilo.css",
];

// Tudo que é servidor, não app. Nunca sai do cache.
const CAMINHOS_API = [
  "/auth", "/cardapio", "/produtos", "/pedidos",
  "/relatorios", "/fechamento", "/health", "/ws",
];

self.addEventListener("install", (evento) => {
  evento.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE);
      // addAll é tudo-ou-nada: um arquivo que falhe derruba a instalação
      // inteira e o app fica sem casca. Um a um, o que der certo fica.
      await Promise.all(
        CASCA.map((url) =>
          cache.add(new Request(url, { cache: "reload" })).catch((erro) => {
            console.warn("[sw] não cacheou", url, erro);
          }),
        ),
      );
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener("activate", (evento) => {
  evento.waitUntil(
    (async () => {
      for (const nome of await caches.keys()) {
        if (nome.startsWith("shalon-vendas-") && nome !== CACHE) {
          await caches.delete(nome);
        }
      }
      await self.clients.claim();
    })(),
  );
});

self.addEventListener("fetch", (evento) => {
  const { request } = evento;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (CAMINHOS_API.some((p) => url.pathname === p || url.pathname.startsWith(`${p}/`))) {
    return; // passa direto pra rede
  }

  // Navegação: rede primeiro (pra pegar versão nova assim que existir), cache
  // como rede de segurança quando a internet está fora.
  if (request.mode === "navigate") {
    evento.respondWith(
      (async () => {
        try {
          const resposta = await fetch(request);
          const cache = await caches.open(CACHE);
          cache.put("./index.html", resposta.clone());
          return resposta;
        } catch {
          return (await caches.match("./index.html")) ?? Response.error();
        }
      })(),
    );
    return;
  }

  // Demais arquivos: responde do cache na hora e revalida por trás. A tela
  // abre instantânea; a versão nova entra no próximo carregamento.
  evento.respondWith(
    (async () => {
      const cache = await caches.open(CACHE);
      const guardado = await cache.match(request);

      const daRede = fetch(request)
        .then((resposta) => {
          if (resposta.ok) cache.put(request, resposta.clone());
          return resposta;
        })
        .catch(() => null);

      return guardado ?? (await daRede) ?? Response.error();
    })(),
  );
});
