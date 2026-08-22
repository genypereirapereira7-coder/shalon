/**
 * Service worker do PWA do Dono.
 *
 * Mesma ideia do de vendas: garante que o app **abra** sem internet, e nada de
 * API entra em cache. Aqui a regra é ainda mais dura — um `/relatorios/hoje`
 * servido do cache mostraria o faturamento de ontem como se fosse o de agora,
 * e o dono tomaria decisão em cima de número velho sem saber.
 *
 * Os últimos números conhecidos ficam no localStorage, controlados pelo app,
 * que os exibe sempre com a hora em que chegaram.
 *
 * Ao mexer nos arquivos do app, suba o VERSAO.
 */

const VERSAO = "v4";
const CACHE = `shalon-dono-${VERSAO}`;

const CASCA = [
  "./",
  "./index.html",
  "./app.js",
  "./dono.css",
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
      // Um a um, não `addAll`: um arquivo que falhe derrubaria a instalação
      // inteira e o app ficaria sem casca.
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
        if (nome.startsWith("shalon-dono-") && nome !== CACHE) {
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

  // Demais arquivos: cache na hora, revalida por trás.
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
