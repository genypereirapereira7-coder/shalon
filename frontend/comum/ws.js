/**
 * Cliente do WebSocket, compartilhado pelos dois PWAs.
 *
 * **O socket acelera; quem garante é o polling.** Nenhuma das duas telas
 * depende deste arquivo pra estar correta: as duas continuam recarregando sozinhas
 * num intervalo próprio, e o que o WebSocket faz é encurtar a espera de 5-15
 * segundos pra menos de um. Se ele nunca conectar — proxy que bloqueia, rede da
 * loja capenga, servidor reiniciando — as telas continuam funcionando mais
 * devagar, e ninguém na sorveteria fica sem saber de um pedido.
 *
 * Por isso aqui não há fila de mensagens perdidas nem reenvio: evento que não
 * chegou é evento que o próximo polling resolve.
 *
 * **A autenticação é a primeira mensagem, não a URL.** O navegador não deixa pôr
 * cabeçalho `Authorization` num WebSocket, e `?token=…` gravaria o token de
 * acesso no log do servidor e no histórico do navegador. Ver `app/rotas/ws.py`.
 */

import * as api from "./api.js";

/** O servidor derruba quem fica 60s sem falar. */
const PING_MS = 25000;

const RECONEXAO_MIN_MS = 1000;
const RECONEXAO_MAX_MS = 30000;

/** "Policy violation" — não autenticou ou o token venceu. */
const FECHOU_POR_POLITICA = 1008;

/**
 * Abre e mantém a conexão. Devolve `{ fechar() }`.
 *
 * @param {object} opcoes
 * @param {(evento: string, dados: any) => void} opcoes.aoEvento
 * @param {(ligado: boolean) => void} [opcoes.aoMudarConexao]
 */
export function conectar({ aoEvento, aoMudarConexao }) {
  let socket = null;
  let timerPing = null;
  let timerReconexao = null;
  let espera = RECONEXAO_MIN_MS;
  let ativo = true;
  let anunciado = false;

  function avisar(ligado) {
    // Só na virada: a tela não precisa redesenhar o pontinho verde a cada ping.
    if (ligado === anunciado) return;
    anunciado = ligado;
    aoMudarConexao?.(ligado);
  }

  function abrir() {
    if (!ativo || !api.estaLogado()) return;

    const url = new URL("/ws", location.href);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";

    try {
      socket = new WebSocket(url);
    } catch {
      return reagendar();
    }

    socket.onopen = () => {
      // A sessão pode ter caído entre o `new WebSocket` e este instante — um
      // logout, ou um refresh recusado. Sem token não há o que apresentar.
      const sessao = api.sessaoAtual();
      if (!sessao) return socket.close();

      socket.send(JSON.stringify({ tipo: "auth", token: sessao.acesso }));
      timerPing = setInterval(() => {
        if (socket?.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ tipo: "ping" }));
        }
      }, PING_MS);
    };

    socket.onmessage = (mensagem) => {
      let pacote;
      try {
        pacote = JSON.parse(mensagem.data);
      } catch {
        return;
      }

      if (pacote.evento === "pronto") {
        // Só agora a conexão está de fato valendo: o `onopen` dispara antes do
        // servidor conferir o token, e anunciar "online" ali acenderia o
        // pontinho verde numa conexão que vai fechar no instante seguinte.
        espera = RECONEXAO_MIN_MS;
        avisar(true);
        return;
      }

      if (pacote.evento === "pong") return;

      aoEvento(pacote.evento, pacote.dados);
    };

    socket.onclose = async (fechamento) => {
      limpar();
      avisar(false);

      // O servidor derruba a conexão quando o token de acesso vence — não tem
      // como devolver 401 num socket já aberto. Renovar antes de tentar de novo
      // evita ficar batendo na porta com a credencial velha.
      if (fechamento.code === FECHOU_POR_POLITICA && api.estaLogado()) {
        await api.renovarSessao();
      }

      // Este `await` é uma janela: o `limpar()` acima já zerou o `socket`, e
      // durante a renovação o `aoVoltar` (visibilitychange/online) vê o campo
      // nulo, entende "não há conexão" e abre uma. Reagendar cegamente aqui
      // abriria a segunda — dois sockets vivos, cada evento chegando duas
      // vezes, cada comanda apitando em dobro na cozinha.
      if (socket) return;

      reagendar();
    };

    socket.onerror = () => {
      // O `onclose` vem logo atrás e é lá que a reconexão é marcada. Tratar os
      // dois agendaria duas reconexões pro mesmo tombo.
    };
  }

  function reagendar() {
    if (!ativo || timerReconexao) return;
    timerReconexao = setTimeout(() => {
      timerReconexao = null;
      abrir();
    }, espera);
    espera = Math.min(espera * 2, RECONEXAO_MAX_MS);
  }

  function limpar() {
    clearInterval(timerPing);
    timerPing = null;
    socket = null;
  }

  // O celular fica horas com o app em segundo plano e o Android congela o timer
  // da reconexão. Voltar pro app tem que reconectar na hora, não no fim do
  // backoff.
  const aoVoltar = () => {
    if (document.visibilityState === "visible" && !socket && ativo) {
      espera = RECONEXAO_MIN_MS;
      clearTimeout(timerReconexao);
      timerReconexao = null;
      abrir();
    }
  };
  document.addEventListener("visibilitychange", aoVoltar);
  window.addEventListener("online", aoVoltar);

  abrir();

  return {
    fechar() {
      ativo = false;
      document.removeEventListener("visibilitychange", aoVoltar);
      window.removeEventListener("online", aoVoltar);
      clearTimeout(timerReconexao);
      try {
        socket?.close();
      } catch { /* já estava fechado */ }
      limpar();
    },
  };
}
