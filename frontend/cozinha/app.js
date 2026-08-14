/**
 * Tela da cozinha — o monitor do PC onde as comandas aparecem.
 *
 * O que esta tela precisa acertar, em ordem de importância:
 *
 * 1. **Comanda nova não pode passar despercebida.** Ninguém fica olhando pro
 *    monitor: entra som e a comanda aparece no topo da fila.
 * 2. **Impressora travada tem que gritar.** Se o agente não confirmou a
 *    impressão em 15s (§6 da arquitetura), a comanda pulsa em vermelho com o
 *    botão REIMPRIMIR. O jeito errado de descobrir isso é o cliente reclamando.
 * 3. **Queda de conexão não apaga a tela.** As comandas que já estão aqui
 *    continuam, com um aviso de que a lista parou de atualizar (§7). Sumir com
 *    o pedido de alguém é pior do que mostrá-lo desatualizado.
 *
 * A comanda chega pelo WebSocket em menos de um segundo, mas **a tela não
 * depende dele**: o polling continua ligado por baixo e é ele que garante que
 * nada se perca quando o socket cair. Com o socket de pé o intervalo relaxa;
 * sem ele, aperta. É o mesmo desenho do agente de impressão (§7).
 */

import * as api from "../comum/api.js";
import { hora } from "../comum/formato.js";
import * as relogio from "../comum/relogio.js";
import * as ws from "../comum/ws.js";

/** Sem WebSocket, o polling é a única fonte: aqui o atraso é comanda parada. */
const INTERVALO_MS = 5000;

/** Com o socket de pé o polling vira só rede de segurança. */
const INTERVALO_COM_SOCKET_MS = 20000;

/** Sem ACK do agente depois disso, a impressora provavelmente travou (§6). */
const PRAZO_IMPRESSAO_MS = 15000;

/** Comanda esperando mais que isso acende o relógio. */
const ESPERA_LONGA_MIN = 10;

const EM_PRODUCAO = ["RECEBIDO", "EM_PREPARO", "PRONTO"];

const ROTULO = {
  RECEBIDO: "na fila",
  EM_PREPARO: "em preparo",
  PRONTO: "pronta",
  ENTREGUE: "entregue",
};

const estado = {
  /** Comandas em produção, na ordem da numeração. */
  pedidos: [],
  /** Números já vistos — é o que distingue comanda nova de comanda repetida. */
  conhecidos: new Set(),
  usuarioEscolhido: null,
  online: navigator.onLine,
  som: true,
  /** Mostrando as entregues em vez da fila de produção. */
  vendoEntregues: false,
  carregando: false,
  /** Ids com uma ação em voo, pra não mandar dois PATCH no mesmo toque. */
  ocupados: new Set(),
  /** Conexão do WebSocket, quando existe. */
  socket: null,
};

const $ = (id) => document.getElementById(id);
let timer = null;
let timerRelogio = null;

// ==================================================================== início

async function iniciar() {
  registrarServiceWorker();
  ligarEventos();

  // Antes de qualquer comanda aparecer: o alerta de impressora travada compara
  // "agora" com a hora do servidor, e o PC da cozinha pode estar meses sem
  // sincronizar o relógio.
  await relogio.sincronizar();

  api.aoPerderSessao(() => {
    aviso("Sessão expirada — entre de novo", "erro");
    mostrarLogin();
  });

  const papel = api.sessaoAtual()?.papel;
  if (api.estaLogado() && (papel === "COZINHA" || papel === "DONO")) {
    await abrirCozinha();
  } else {
    if (api.estaLogado()) await api.sair();
    mostrarLogin();
  }
}

function registrarServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.register("sw.js").catch((erro) => {
    console.warn("Service worker não registrou:", erro);
  });
}

// ===================================================================== login

async function mostrarLogin() {
  parar();
  $("tela-cozinha").hidden = true;
  $("tela-login").hidden = false;
  $("login-pin").hidden = true;
  $("login-lista").hidden = false;
  await carregarUsuarios();
}

async function carregarUsuarios() {
  const lista = $("usuarios");
  const carregando = $("login-carregando");
  const recarregar = $("login-recarregar");

  lista.innerHTML = "";
  carregando.hidden = false;
  carregando.textContent = "Carregando…";
  recarregar.hidden = true;

  try {
    // A cozinha não aparece na lista padrão (que é a do balcão), por isso o
    // filtro explícito. O dono entra aqui também: é o celular dele que salva o
    // expediente quando esta tela trava.
    const usuarios = await api.pedir(
      "GET", "/auth/usuarios?papel=COZINHA&papel=DONO", null, { autenticado: false },
    );
    carregando.hidden = true;

    for (const usuario of usuarios) {
      const botao = document.createElement("button");
      botao.innerHTML =
        `<span>${escapar(usuario.nome)}</span>` +
        `<span class="papel">${escapar(usuario.papel.toLowerCase())}</span>`;
      botao.onclick = () => escolherUsuario(usuario);
      lista.append(botao);
    }

    if (!usuarios.length) {
      carregando.hidden = false;
      carregando.textContent = "Nenhum usuário de cozinha. Rode o seed no servidor.";
    }
  } catch (erro) {
    carregando.hidden = false;
    carregando.textContent =
      erro instanceof api.ErroRede
        ? "Sem conexão com o servidor."
        : `Não deu pra carregar: ${erro.message}`;
    recarregar.hidden = false;
  }
}

function escolherUsuario(usuario) {
  estado.usuarioEscolhido = usuario;
  $("pin-nome").textContent = usuario.nome;
  $("pin-campo").value = "";
  $("login-erro").hidden = true;
  $("login-lista").hidden = true;
  $("login-pin").hidden = false;
  $("pin-campo").focus();
}

async function entrar() {
  const campo = $("pin-campo");
  const segredo = campo.value.trim();
  const erroEl = $("login-erro");
  const botao = $("pin-entrar");

  if (!segredo) {
    erroEl.textContent = "Digite o PIN";
    erroEl.hidden = false;
    return;
  }

  botao.disabled = true;
  botao.textContent = "ENTRANDO…";
  erroEl.hidden = true;

  try {
    const sessao = await api.entrar(estado.usuarioEscolhido.id, segredo);
    campo.value = "";

    if (sessao.papel !== "COZINHA" && sessao.papel !== "DONO") {
      await api.sair();
      throw new Error("Esta tela é da cozinha.");
    }

    await abrirCozinha();
  } catch (erro) {
    erroEl.textContent =
      erro instanceof api.ErroRede ? "Sem conexão — não dá pra entrar agora." : erro.message;
    erroEl.hidden = false;
    campo.value = "";
  } finally {
    botao.disabled = false;
    botao.textContent = "ENTRAR";
  }
}

// =================================================================== comandas

async function abrirCozinha() {
  $("tela-login").hidden = true;
  $("tela-cozinha").hidden = false;
  $("nome-usuario").textContent = api.sessaoAtual()?.nome ?? "";

  // Tudo que já existe entra como "conhecido": abrir a tela no meio do
  // expediente não pode disparar o som doze vezes seguidas.
  await carregar({ silencioso: true });

  agendar();
  ligarSocket();
  tiquetaque();
}

function agendar(intervalo = INTERVALO_MS) {
  clearInterval(timer);
  clearInterval(timerRelogio);
  timer = setInterval(carregar, intervalo);

  // O relógio do topo e os minutos de espera de cada comanda andam sozinhos,
  // sem esperar a próxima ida ao servidor.
  timerRelogio = setInterval(tiquetaque, 1000);
}

function parar() {
  clearInterval(timer);
  clearInterval(timerRelogio);
  timer = timerRelogio = null;
  estado.socket?.fechar();
  estado.socket = null;
}

// ================================================================ tempo real

function ligarSocket() {
  estado.socket?.fechar();
  estado.socket = ws.conectar({
    aoEvento: aplicarEvento,
    aoMudarConexao: (ligado) => {
      // O polling não é desligado nunca — só relaxa. É ele que traz de volta o
      // que o socket deixou passar num tombo curto de rede.
      agendar(ligado ? INTERVALO_COM_SOCKET_MS : INTERVALO_MS);
      if (ligado) carregar({ silencioso: true });
    },
  });
}

function aplicarEvento(evento, dados) {
  if (evento === "impressora.status") return avisarImpressora(dados);

  if (!["pedido.novo", "pedido.status", "pedido.impresso"].includes(evento)) return;

  // Vendo os entregues, a lista é outra consulta: deixa o polling cuidar em
  // vez de injetar comanda em produção numa tela que não é a da produção.
  if (estado.vendoEntregues) return;

  aplicarPedido(dados, { apitar: evento === "pedido.novo" });
}

/**
 * Encaixa um pedido que chegou pelo socket no que já está na tela.
 *
 * Trabalha sobre a lista local em vez de recarregar do servidor: o `carregar`
 * redesenha tudo e, numa cozinha com doze comandas, apagar e remontar a tela a
 * cada mudança de status faz o dedo errar o botão.
 */
function aplicarPedido(pedido, { apitar: podeApitar = false } = {}) {
  if (!pedido?.id) return;

  const conhecido = estado.conhecidos.has(pedido.id);
  estado.conhecidos.add(pedido.id);

  const indice = estado.pedidos.findIndex((p) => p.id === pedido.id);
  const emProducao = EM_PRODUCAO.includes(pedido.status);

  if (!emProducao) {
    // Entregue ou cancelado: sai da tela de produção.
    if (indice === -1) return;
    estado.pedidos.splice(indice, 1);
  } else if (indice === -1) {
    estado.pedidos.push(pedido);
    // A cozinha produz na ordem da venda, não na de chegada do evento.
    estado.pedidos.sort((a, b) => a.numero_dia - b.numero_dia);
  } else {
    estado.pedidos[indice] = pedido;
  }

  desenhar();

  if (podeApitar && !conhecido && emProducao) {
    apitar();
    aviso(`Comanda #${pedido.numero_dia}`, "ok");
  }
}

/** A impressora avisou que travou (ou que voltou). Quem manda isso é o agente. */
function avisarImpressora(dados) {
  const faixa = $("faixa-impressora");
  if (dados?.ok) {
    faixa.hidden = true;
    return;
  }
  faixa.textContent =
    "⚠ A impressora não está respondendo" +
    (dados?.detalhe ? ` (${dados.detalhe})` : "") +
    " — as comandas continuam aparecendo aqui na tela.";
  faixa.hidden = false;
}

async function carregar({ silencioso = false } = {}) {
  if (estado.carregando || !api.estaLogado()) return;
  estado.carregando = true;

  try {
    const caminho = estado.vendoEntregues
      ? "/pedidos/hoje?status=ENTREGUE"
      : `/pedidos/hoje?${EM_PRODUCAO.map((s) => `status=${s}`).join("&")}`;

    const pedidos = await api.pedir("GET", caminho);

    const novas = pedidos.filter((p) => !estado.conhecidos.has(p.id));
    for (const pedido of pedidos) estado.conhecidos.add(pedido.id);

    estado.pedidos = pedidos;
    marcarOnline(true);
    $("faixa-conexao").hidden = true;
    desenhar();

    if (!silencioso && novas.length && !estado.vendoEntregues) {
      apitar();
      aviso(
        novas.length === 1
          ? `Comanda #${novas[0].numero_dia}`
          : `${novas.length} comandas novas`,
        "ok",
      );
    }
  } catch (erro) {
    if (erro instanceof api.ErroRede) {
      marcarOnline(false);
      // A lista NÃO é limpa: as comandas que já estão na tela continuam sendo
      // produzidas. Só avisamos que parou de atualizar.
      const faixa = $("faixa-conexao");
      faixa.textContent =
        "⚠ Sem conexão com o servidor — as comandas na tela continuam valendo, " +
        "mas nenhuma nova vai aparecer até a internet voltar.";
      faixa.hidden = false;
    } else if (!(erro instanceof api.ErroApi && erro.status === 401)) {
      aviso(erro.message, "erro");
    }
  } finally {
    estado.carregando = false;
  }
}

function desenhar() {
  const area = $("comandas");
  area.innerHTML = "";

  for (const pedido of estado.pedidos) {
    area.append(cartao(pedido));
  }

  $("vazio").hidden = estado.pedidos.length > 0;
  $("vazio").firstElementChild.nextSibling.textContent = estado.vendoEntregues
    ? " Nenhuma comanda entregue hoje."
    : " Nenhuma comanda em produção.";

  const contar = (status) => estado.pedidos.filter((p) => p.status === status).length;
  $("c-recebido").textContent = contar("RECEBIDO");
  $("c-preparo").textContent = contar("EM_PREPARO");
  $("c-pronto").textContent = contar("PRONTO");
}

function cartao(pedido) {
  const carta = document.createElement("article");
  carta.className = "comanda";
  carta.dataset.status = pedido.status;
  carta.dataset.id = pedido.id;
  carta.dataset.impressao = situacaoImpressao(pedido);

  const topo = document.createElement("div");
  topo.className = "comanda__topo";
  topo.innerHTML =
    `<span class="comanda__numero">#${pedido.numero_dia}</span>` +
    `<span class="comanda__quando">${hora(pedido.criado_em)}` +
    ` · ${escapar(pedido.usuario_nome)}` +
    `<br><span class="comanda__espera" data-desde="${pedido.criado_em}"></span></span>` +
    `<span class="comanda__selo">${ROTULO[pedido.status]}</span>`;
  carta.append(topo);

  const itens = document.createElement("ul");
  itens.className = "itens";
  for (const item of pedido.itens) {
    const li = document.createElement("li");
    li.innerHTML =
      `<span class="item__qtd">${item.quantidade}×</span>` +
      `<span class="item__nome">${escapar(item.nome)}` +
      (item.opcoes?.length
        ? `<small class="item__opcoes">${escapar(
            item.opcoes.map((o) => o.nome).join(" · "),
          )}</small>`
        : "") +
      `</span>`;
    itens.append(li);
  }
  carta.append(itens);

  if (pedido.observacao) {
    const obs = document.createElement("p");
    obs.className = "comanda__obs";
    obs.textContent = `Obs: ${pedido.observacao}`;
    carta.append(obs);
  }

  if (carta.dataset.impressao === "atrasada") {
    const alerta = document.createElement("div");
    alerta.className = "comanda__impressao";
    alerta.innerHTML = "<span>⚠ Não saiu papel</span>";

    const botao = document.createElement("button");
    botao.className = "comanda__reimprimir";
    botao.textContent = "REIMPRIMIR";
    botao.onclick = () => reimprimir(pedido);
    alerta.append(botao);
    carta.append(alerta);
  }

  carta.append(rodape(pedido));
  return carta;
}

/**
 * "ok" | "esperando" | "atrasada".
 *
 * O prazo conta do `criado_em` do servidor, não do relógio do celular que
 * vendeu: uma comanda que ficou na fila offline chega com `criado_em_cliente`
 * de meia hora atrás e apareceria atrasada sem nunca ter ido pra impressora.
 *
 * E o "agora" também é o do servidor (`relogio`), não o deste PC: os dois lados
 * da conta precisam sair do mesmo relógio. Um PC de cozinha meses sem
 * sincronizar acenderia o alerta em todas as comandas ou em nenhuma.
 */
function situacaoImpressao(pedido) {
  if (pedido.impresso_em) return "ok";
  return relogio.desde(pedido.criado_em) > PRAZO_IMPRESSAO_MS ? "atrasada" : "esperando";
}

function rodape(pedido) {
  const pe = document.createElement("div");
  pe.className = "comanda__pe";

  if (estado.vendoEntregues) {
    const nota = document.createElement("span");
    nota.className = "fraco";
    nota.textContent = "Entregue";
    pe.append(nota);
    return pe;
  }

  const ocupado = estado.ocupados.has(pedido.id);

  // Só o próximo passo e o atalho pro fim. Botão de "voltar pra fila" não
  // existe de propósito: relatório de status que anda pra trás não vale nada,
  // e o servidor recusaria a transição.
  if (pedido.status === "RECEBIDO") {
    pe.append(botaoAcao("EM PREPARO", "preparo", pedido, "EM_PREPARO", ocupado));
  }
  if (pedido.status === "RECEBIDO" || pedido.status === "EM_PREPARO") {
    pe.append(botaoAcao("PRONTA", "pronto", pedido, "PRONTO", ocupado));
  }
  if (pedido.status === "PRONTO") {
    pe.append(botaoAcao("ENTREGUE", "entregue", pedido, "ENTREGUE", ocupado));
  }

  return pe;
}

function botaoAcao(texto, variante, pedido, destino, ocupado) {
  const botao = document.createElement("button");
  botao.className = `acao acao--${variante}`;
  botao.textContent = texto;
  botao.disabled = ocupado;
  botao.onclick = () => mudarStatus(pedido, destino);
  return botao;
}

// ==================================================================== ações

async function mudarStatus(pedido, destino) {
  if (estado.ocupados.has(pedido.id)) return;
  estado.ocupados.add(pedido.id);

  // A tela responde na hora e o servidor confirma depois: numa cozinha, o
  // botão que demora meio segundo é apertado duas vezes.
  const anterior = pedido.status;
  pedido.status = destino;
  if (destino === "ENTREGUE") {
    estado.pedidos = estado.pedidos.filter((p) => p.id !== pedido.id);
  }
  desenhar();

  try {
    const atualizado = await api.pedir("PATCH", `/pedidos/${pedido.id}/status`, {
      status: destino,
    });
    marcarOnline(true);
    Object.assign(pedido, atualizado);
    if (destino === "ENTREGUE") aviso(`#${pedido.numero_dia} entregue`, "ok");
  } catch (erro) {
    // Não deu: desfaz. Mostrar "pronta" uma comanda que o servidor não aceitou
    // faria a cozinha parar de produzir um pedido que continua na fila.
    pedido.status = anterior;
    if (destino === "ENTREGUE" && !estado.pedidos.some((p) => p.id === pedido.id)) {
      estado.pedidos.push(pedido);
      estado.pedidos.sort((a, b) => a.numero_dia - b.numero_dia);
    }
    desenhar();

    if (erro instanceof api.ErroRede) {
      marcarOnline(false);
      aviso(`Sem conexão — #${pedido.numero_dia} continua ${ROTULO[anterior]}`, "erro");
    } else {
      aviso(erro.message, "erro");
    }
  } finally {
    estado.ocupados.delete(pedido.id);
    desenhar();
  }
}

async function reimprimir(pedido) {
  try {
    await api.pedir("POST", `/pedidos/${pedido.id}/reimprimir`);
    marcarOnline(true);
    aviso(`#${pedido.numero_dia} voltou pra fila de impressão`, "ok");
    await carregar({ silencioso: true });
  } catch (erro) {
    if (erro instanceof api.ErroRede) marcarOnline(false);
    aviso(erro instanceof api.ErroRede ? "Sem conexão" : erro.message, "erro");
  }
}

// =================================================================== relógio

/** Anda o relógio do topo e os minutos de espera, sem ir ao servidor. */
function tiquetaque() {
  $("relogio").textContent = new Date().toLocaleTimeString("pt-BR", {
    hour: "2-digit",
    minute: "2-digit",
  });

  for (const el of document.querySelectorAll(".comanda__espera")) {
    const minutos = Math.floor(relogio.desde(el.dataset.desde) / 60000);
    el.textContent = minutos < 1 ? "agora" : `há ${minutos} min`;
    el.dataset.atrasada = minutos >= ESPERA_LONGA_MIN ? "1" : "0";
  }

  // Uma comanda pode cruzar o prazo de impressão entre dois carregamentos: sem
  // isto, o alerta vermelho só apareceria no próximo polling.
  for (const carta of document.querySelectorAll(".comanda")) {
    const pedido = estado.pedidos.find((p) => p.id === carta.dataset.id);
    if (!pedido) continue;
    const situacao = situacaoImpressao(pedido);
    if (situacao !== carta.dataset.impressao) {
      desenhar();
      return;
    }
  }
}

// ======================================================================= som

let audio = null;

/**
 * Dois bipes curtos quando entra comanda.
 *
 * Oscilador em vez de arquivo de áudio: um .mp3 a mais é uma coisa a mais pra
 * falhar no cache do quiosque, e o navegador só precisa fazer barulho.
 */
function apitar() {
  if (!estado.som) return;

  try {
    audio ??= new AudioContext();
    if (audio.state === "suspended") audio.resume();

    for (const [quando, freq] of [[0, 880], [0.18, 1174]]) {
      const osc = audio.createOscillator();
      const ganho = audio.createGain();
      osc.frequency.value = freq;
      osc.type = "sine";
      // Rampa em vez de corte seco: sem ela o navegador estala no fim da nota.
      ganho.gain.setValueAtTime(0.0001, audio.currentTime + quando);
      ganho.gain.exponentialRampToValueAtTime(0.25, audio.currentTime + quando + 0.02);
      ganho.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + quando + 0.15);
      osc.connect(ganho).connect(audio.destination);
      osc.start(audio.currentTime + quando);
      osc.stop(audio.currentTime + quando + 0.16);
    }
  } catch {
    /* navegador sem áudio: a comanda aparece na tela do mesmo jeito */
  }
}

// =================================================================== eventos

function ligarEventos() {
  $("login-recarregar").onclick = carregarUsuarios;
  $("pin-voltar").onclick = () => {
    $("login-pin").hidden = true;
    $("login-lista").hidden = false;
  };
  $("pin-entrar").onclick = entrar;
  $("pin-campo").addEventListener("keydown", (e) => {
    if (e.key === "Enter") entrar();
  });

  $("btn-som").onclick = () => {
    estado.som = !estado.som;
    $("btn-som").setAttribute("aria-pressed", String(estado.som));
    $("btn-som").textContent = estado.som ? "🔔" : "🔕";
    // Tocar aqui também destrava o áudio: o navegador só deixa fazer barulho
    // depois de um clique, e sem isto o primeiro apito do dia seria engolido.
    if (estado.som) apitar();
    aviso(estado.som ? "Aviso sonoro ligado" : "Aviso sonoro desligado");
  };

  $("btn-usuario").onclick = () => {
    $("painel").hidden = false;
    const sessao = api.sessaoAtual();
    $("painel-usuario-nome").textContent = `${sessao?.nome ?? ""} · ${sessao?.papel ?? ""}`;
    $("painel-nota").textContent = estado.vendoEntregues
      ? "Mostrando as comandas entregues de hoje."
      : "Mostrando as comandas em produção.";
  };
  $("btn-entregues").onclick = async () => {
    estado.vendoEntregues = !estado.vendoEntregues;
    $("btn-entregues").textContent = estado.vendoEntregues
      ? "Voltar pra produção"
      : "Ver entregues de hoje";
    $("painel").hidden = true;
    await carregar({ silencioso: true });
  };
  $("btn-sair").onclick = sair;
  for (const alvo of document.querySelectorAll("[data-fechar]")) {
    alvo.onclick = () => ($("painel").hidden = true);
  }

  window.addEventListener("online", () => {
    marcarOnline(true);
    carregar({ silencioso: true });
  });
  window.addEventListener("offline", () => marcarOnline(false));
}

async function sair() {
  parar();
  await api.sair();
  estado.pedidos = [];
  estado.conhecidos.clear();
  $("painel").hidden = true;
  mostrarLogin();
}

// ================================================================ utilidades

function marcarOnline(ligado) {
  estado.online = ligado;
  $("ponto-conexao").dataset.estado = ligado ? "online" : "offline";
}

let timerAviso = null;

function aviso(texto, tipo = "") {
  const el = $("aviso");
  el.textContent = texto;
  el.dataset.tipo = tipo;
  el.dataset.visivel = "1";

  clearTimeout(timerAviso);
  timerAviso = setTimeout(() => (el.dataset.visivel = "0"), 2600);
}

function escapar(texto) {
  return String(texto).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

iniciar();
