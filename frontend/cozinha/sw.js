/**
 * Service worker da tela da cozinha.
 *
 * Aqui ele importa mais do que nos outros dois: este PC fica ligado o dia
 * inteiro e alguém vai apertar F5 exatamente quando a internet estiver fora.
 * Sem a casca em cache, a tela viraria "sem conexão" do navegador e a cozinha
 * perderia até as comandas que já estavam ali.
 *
 * Nada de API entra em cache. Uma lista de comandas velha servida do cache
 * mandaria a cozinha produzir pedido já entregue e esconderia o que acabou de
 * entrar.
 *
 * Ao mexer nos arquivos do app, suba o VERSAO.
 */

const VERSAO = "v2";
const CACHE = `shalon-cozinha-${VERSAO}`;

const CASCA = [
  "./",
  "./index.html",
  "./app.js",
  "./cozinha.css",
  "./manifest.json",
  "./icone-192.png",
  "./icone-512.png",
  "../comum/api.js",
  "../comum/formato.js",
  "../comum/ws.js",
  "../comum/relogio.js",
  "../comum/estilo.css",
];

const CAMINHOS_API = [
  "/auth", "/cardapio", "/produtos", "/pedidos",
  "/relatorios", "/fechamento", "/health", "/ws",
];

self.addEventListener("install", (evento) => {
  evento.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE);
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
        if (nome.startsWith("shalon-cozinha-") && nome !== CACHE) {
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
