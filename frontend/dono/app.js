/**
 * PWA do Dono — os números do negócio no celular.
 *
 * A diferença de intenção em relação ao PWA de vendas: lá o pior erro é uma
 * venda que não sai; aqui é **um número errado na tela**. Por isso nada é
 * calculado no celular. Total, ticket médio e ranking vêm prontos do servidor,
 * em centavos, numa resposta só (`/relatorios/hoje`) — três chamadas separadas
 * dariam três chances de mostrar o total novo com o ranking velho.
 *
 * Quando a conexão cai, a tela não zera nem inventa: mostra os últimos números
 * que chegaram, com a hora em que chegaram, em amarelo (§7 da arquitetura).
 *
 * O `metricas.tick` do WebSocket traz o resumo **inteiro**, não só os três
 * números do topo — é o que permite trocar total, ranking e alertas de uma vez
 * só, sem nunca abrir uma janela em que a soma e o detalhe se contradizem. O
 * polling continua por baixo como rede de segurança, só mais espaçado.
 */

import * as api from "../comum/api.js";
import { reais, valor, hora } from "../comum/formato.js";
import * as ws from "../comum/ws.js";

const INTERVALO_MS = 15000;

/** Com o socket de pé, o polling é só conferência. */
const INTERVALO_COM_SOCKET_MS = 60000;
const CHAVE_ULTIMO = "shalon.dono.ultimo-resumo";

const estado = {
  aba: "hoje",
  /** Último resumo de HOJE que chegou do servidor (ou do cache local). */
  resumo: null,
  /** Data que a aba Fechamento está mostrando; null = hoje. */
  dataFechamento: null,
  /** Resumo da data acima, quando não é hoje. */
  resumoFechamento: null,
  cardapio: null,
  historico: [],
  usuarioEscolhido: null,
  online: navigator.onLine,
  /** id do produto em edição de preço, ou null */
  editando: null,
  carregando: false,
  /** Conexão do WebSocket, quando existe. */
  socket: null,
};

const $ = (id) => document.getElementById(id);
let timer = null;

// ==================================================================== início

async function iniciar() {
  registrarServiceWorker();
  ligarEventos();

  api.aoPerderSessao(() => {
    aviso("Sessão expirada — entre de novo", "erro");
    mostrarLogin();
  });

  // Sessão de outro papel não abre este app. Não é só estética: as rotas de
  // relatório respondem 403 pra quem não é dono, e a tela ficaria vazia sem
  // explicar por quê.
  if (api.estaLogado() && api.sessaoAtual()?.papel === "DONO") {
    await abrirPainel();
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
  pararAtualizacao();
  $("tela-painel").hidden = true;
  $("tela-login").hidden = false;
  $("login-senha").hidden = true;
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
    // Só quem é dono. O funcionário aparece na lista do PWA de vendas; aqui
    // ele só tomaria 403 depois de digitar a senha.
    const donos = (await api.usuarios()).filter((u) => u.papel === "DONO");
    carregando.hidden = true;

    for (const usuario of donos) {
      const botao = document.createElement("button");
      botao.innerHTML =
        `<span>${escapar(usuario.nome)}</span>` +
        `<span class="papel">${escapar(usuario.papel.toLowerCase())}</span>`;
      botao.onclick = () => escolherUsuario(usuario);
      lista.append(botao);
    }

    if (!donos.length) {
      carregando.hidden = false;
      carregando.textContent = "Nenhum dono cadastrado. Rode o seed no servidor.";
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
  $("senha-nome").textContent = usuario.nome;
  $("senha-campo").value = "";
  $("login-erro").hidden = true;
  $("login-lista").hidden = true;
  $("login-senha").hidden = false;
  $("senha-campo").focus();
}

async function entrar() {
  const campo = $("senha-campo");
  const segredo = campo.value.trim();
  const erroEl = $("login-erro");
  const botao = $("senha-entrar");

  if (!segredo) {
    erroEl.textContent = "Digite a senha";
    erroEl.hidden = false;
    return;
  }

  botao.disabled = true;
  botao.textContent = "ENTRANDO…";
  erroEl.hidden = true;

  try {
    const sessao = await api.entrar(estado.usuarioEscolhido.id, segredo);
    campo.value = "";

    // O papel vem do servidor no token, não da lista que a tela filtrou.
    if (sessao.papel !== "DONO") {
      await api.sair();
      throw new Error("Este painel é só do dono.");
    }

    await abrirPainel();
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

// ==================================================================== painel

async function abrirPainel() {
  $("tela-login").hidden = true;
  $("tela-painel").hidden = false;
  $("nome-usuario").textContent = api.sessaoAtual()?.nome ?? "";

  // O último resumo conhecido entra na tela antes de qualquer rede: abrir o
  // app no elevador tem que mostrar o número de agora há pouco, não um traço.
  const guardado = lerUltimo();
  if (guardado) {
    estado.resumo = guardado;
    desenharHoje();
  }

  trocarAba("hoje");
  await atualizar();
  agendarAtualizacao();
  ligarSocket();
}

// ================================================================ tempo real

function ligarSocket() {
  estado.socket?.fechar();
  estado.socket = ws.conectar({
    aoEvento: aplicarEvento,
    aoMudarConexao: (ligado) => {
      agendarAtualizacao(ligado ? INTERVALO_COM_SOCKET_MS : INTERVALO_MS);
      if (ligado) atualizar();
    },
  });
}

function aplicarEvento(evento, dados) {
  if (evento === "metricas.tick") {
    // O resumo vem inteiro e coerente: dá pra pintar direto, sem ir ao
    // servidor de novo. É o mesmo formato do `/relatorios/hoje`.
    estado.resumo = dados;
    gravarUltimo(dados);
    marcarOnline(true);
    esconderErro();
    desenharHoje();
    if (estado.aba === "fechamento" && estado.dataFechamento === null) {
      desenharFechamento(dados);
    }
    return;
  }

  // O dono também recebe os eventos de pedido (é o celular dele que salva o
  // expediente quando a tela da cozinha trava), mas quem mexe nos números é o
  // `metricas.tick`. Reagir aos dois pediria duas atualizações pra cada venda.
  // Quem edita preço é o próprio dono, e o `salvarPreco` já acerta a tela dele.
  // Isto aqui é pro segundo aparelho: o celular que está com o cardápio aberto
  // enquanto a mudança sai no tablet. Não recarrega no meio de uma edição —
  // puxar o cardápio debaixo do editor aberto apagaria o que está sendo
  // digitado.
  if (evento === "preco.alterado" && estado.cardapio && estado.editando === null) {
    carregarCardapio();
  }
}

function trocarAba(qual) {
  estado.aba = qual;

  for (const botao of document.querySelectorAll(".abas__t")) {
    botao.setAttribute("aria-current", String(botao.dataset.aba === qual));
  }
  $("aba-hoje").hidden = qual !== "hoje";
  $("aba-cardapio").hidden = qual !== "cardapio";
  $("aba-fechamento").hidden = qual !== "fechamento";

  // Carregamento sob demanda: quem abre o app pra ver o total do dia não
  // precisa baixar o cardápio inteiro nem o histórico de fechamentos.
  if (qual === "cardapio" && !estado.cardapio) carregarCardapio();
  if (qual === "fechamento") carregarFechamento();
}

/**
 * Recarrega o que a aba visível precisa.
 *
 * `Hoje` sempre é recarregado, mesmo em outra aba: é o número que o dono quer
 * fresco no instante em que volta pra ele.
 */
async function atualizar() {
  if (estado.carregando || !api.estaLogado()) return;
  estado.carregando = true;

  try {
    const resumo = await api.pedir("GET", "/relatorios/hoje");
    estado.resumo = resumo;
    gravarUltimo(resumo);
    marcarOnline(true);
    esconderErro();
    desenharHoje();

    // A aba de fechamento mostrando hoje vive do mesmo resumo.
    if (estado.aba === "fechamento" && estado.dataFechamento === null) {
      desenharFechamento(resumo);
    }
  } catch (erro) {
    if (erro instanceof api.ErroRede) {
      marcarOnline(false);
      desenharHoje(); // repinta o carimbo em amarelo, sem mexer nos números
    } else if (!(erro instanceof api.ErroApi && erro.status === 401)) {
      mostrarErro(erro.message);
    }
  } finally {
    estado.carregando = false;
  }
}

function agendarAtualizacao(intervalo = INTERVALO_MS) {
  clearInterval(timer);
  timer = setInterval(() => {
    // Celular no bolso com a tela apagada não precisa de número novo — e cada
    // chamada dessas é bateria.
    if (!document.hidden) atualizar();
  }, intervalo);
}

function pararAtualizacao() {
  clearInterval(timer);
  timer = null;
  estado.socket?.fechar();
  estado.socket = null;
}

// ====================================================================== hoje

function desenharHoje() {
  const resumo = estado.resumo;
  if (!resumo) return;

  $("n-total").textContent = reais(resumo.total_centavos);
  $("n-pedidos").textContent = resumo.qtd_pedidos;
  $("n-ticket").textContent = reais(resumo.ticket_medio_centavos);

  desenharCarimbo(resumo);

  // Venda que subiu depois do caixa fechado: o fechamento gravado não a inclui,
  // então o relatório e a gaveta não vão bater se ninguém avisar.
  const pos = $("alerta-pos");
  pos.hidden = !resumo.pos_fechamento_qtd;
  if (resumo.pos_fechamento_qtd) {
    pos.textContent =
      `⚠ ${contar(resumo.pos_fechamento_qtd, "venda entrou", "vendas entraram")} ` +
      `depois do fechamento (${reais(resumo.pos_fechamento_centavos)}) — fora do total fechado`;
  }

  const cancelados = $("alerta-cancelados");
  cancelados.hidden = !resumo.cancelados_qtd;
  if (resumo.cancelados_qtd) {
    cancelados.textContent =
      `${contar(resumo.cancelados_qtd, "pedido cancelado", "pedidos cancelados")} hoje ` +
      `(${reais(resumo.cancelados_centavos)}) — fora do total`;
  }

  const fechado = $("alerta-fechado");
  fechado.hidden = !resumo.fechamento;
  if (resumo.fechamento) {
    fechado.textContent =
      `✓ Caixa fechado em ${reais(resumo.fechamento.total_centavos)} ` +
      `por ${resumo.fechamento.fechado_por_nome}`;
  }

  desenharRanking($("hoje-itens"), resumo.itens, "Nenhuma venda ainda hoje.");
  desenharAtendentes(resumo.por_atendente);
}

function desenharCarimbo(resumo) {
  const carimbo = $("hoje-carimbo");
  const quando = hora(resumo.apurado_em);

  if (estado.online) {
    carimbo.dataset.velho = "0";
    carimbo.textContent = `Atualizado às ${quando}`;
  } else {
    // Sem conexão os números na tela são de antes. Dizer só "atualizado às
    // 19:42" faria o dono achar que o movimento parou.
    carimbo.dataset.velho = "1";
    carimbo.textContent = `Sem conexão — números de ${quando}`;
  }
}

function desenharRanking(lista, itens, vazio) {
  lista.innerHTML = "";

  if (!itens?.length) {
    lista.innerHTML = `<li class="vazio">${escapar(vazio)}</li>`;
    return;
  }

  for (const item of itens) {
    const li = document.createElement("li");
    li.innerHTML =
      `<span class="qtd">${item.quantidade}×</span>` +
      `<span class="nome">${escapar(item.nome)}</span>` +
      `<span class="valor">${reais(item.total_centavos)}</span>`;
    lista.append(li);
  }
}

function desenharAtendentes(atendentes) {
  const lista = $("hoje-atendentes");
  lista.innerHTML = "";

  if (!atendentes?.length) {
    lista.innerHTML = '<li class="vazio">Ninguém vendeu ainda.</li>';
    return;
  }

  for (const pessoa of atendentes) {
    const li = document.createElement("li");
    li.innerHTML =
      `<span class="qtd">${pessoa.qtd_pedidos}</span>` +
      `<span class="nome">${escapar(pessoa.nome)}</span>` +
      `<span class="valor">${reais(pessoa.total_centavos)}</span>`;
    lista.append(li);
  }
}

// ================================================================== cardápio

async function carregarCardapio() {
  const area = $("lista-cardapio");
  area.innerHTML = '<p class="fraco">Carregando cardápio…</p>';

  try {
    // `incluir_inativos`: esta é a tela que desativa produto. Sem eles na
    // lista, desativar um item o faria sumir sem jeito de trazer de volta.
    estado.cardapio = await api.pedir("GET", "/cardapio?incluir_inativos=true");
    marcarOnline(true);
    desenharCardapio();
  } catch (erro) {
    if (erro instanceof api.ErroRede) marcarOnline(false);
    area.innerHTML = `<p class="fraco">${escapar(
      erro instanceof api.ErroRede
        ? "Sem conexão — não dá pra editar preço agora."
        : `Não deu pra carregar: ${erro.message}`,
    )}</p>`;
  }
}

function desenharCardapio() {
  const area = $("lista-cardapio");
  area.innerHTML = "";

  for (const categoria of estado.cardapio.categorias) {
    if (!categoria.produtos.length) continue;

    const bloco = document.createElement("section");
    bloco.className = "categoria";
    bloco.innerHTML = `<h2 class="categoria__nome">${escapar(categoria.nome)}</h2>`;

    for (const produto of categoria.produtos) {
      bloco.append(linhaProduto(produto));
    }
    area.append(bloco);
  }

  $("painel-versao").textContent = estado.cardapio.versao
    ? `Cardápio de ${new Date(estado.cardapio.versao).toLocaleString("pt-BR")}`
    : "Cardápio sem data";
}

function linhaProduto(produto) {
  const linha = document.createElement("div");
  linha.className = "linha";
  linha.dataset.inativo = produto.ativo ? "0" : "1";

  const nome = document.createElement("span");
  nome.className = "linha__nome";
  nome.textContent = produto.nome;
  linha.append(nome);

  if (estado.editando === produto.id) {
    linha.append(editorDePreco(produto));
    return linha;
  }

  const preco = document.createElement("button");
  preco.className = "linha__preco";
  preco.textContent = `R$ ${valor(produto.preco_centavos)}`;
  preco.setAttribute("aria-label", `Mudar o preço de ${produto.nome}`);
  preco.onclick = () => {
    estado.editando = produto.id;
    desenharCardapio();
    document.querySelector(".editor__campo")?.select();
  };

  const ativo = document.createElement("button");
  ativo.className = "linha__ativo";
  ativo.textContent = produto.ativo ? "👁" : "🚫";
  ativo.title = produto.ativo ? "Tirar do cardápio" : "Voltar pro cardápio";
  ativo.setAttribute("aria-label", ativo.title);
  ativo.onclick = () => alternarAtivo(produto);

  linha.append(preco, ativo);
  return linha;
}

function editorDePreco(produto) {
  const editor = document.createElement("span");
  editor.className = "editor";

  const campo = document.createElement("input");
  campo.className = "editor__campo";
  campo.type = "text";
  // `decimal` e não `numeric`: o teclado precisa ter a vírgula pra digitar
  // 18,50. Com `numeric` o dono só consegue digitar reais inteiros.
  campo.inputMode = "decimal";
  campo.value = valor(produto.preco_centavos);
  campo.setAttribute("aria-label", `Preço de ${produto.nome} em reais`);

  const ok = document.createElement("button");
  ok.className = "editor__ok";
  ok.textContent = "✓";
  ok.setAttribute("aria-label", "Salvar preço");

  const cancelar = document.createElement("button");
  cancelar.className = "editor__x";
  cancelar.textContent = "✕";
  cancelar.setAttribute("aria-label", "Cancelar");

  const salvar = () => salvarPreco(produto, campo.value);
  ok.onclick = salvar;
  campo.addEventListener("keydown", (e) => {
    if (e.key === "Enter") salvar();
    if (e.key === "Escape") fecharEditor();
  });
  cancelar.onclick = fecharEditor;

  editor.append(campo, ok, cancelar);
  return editor;
}

function fecharEditor() {
  estado.editando = null;
  desenharCardapio();
}

/** "18,50" → 1850. Devolve null se não der pra ler como dinheiro. */
function lerCentavos(texto) {
  const limpo = String(texto).trim().replace(/\s|R\$/g, "").replace(",", ".");
  if (!/^\d+(\.\d{1,2})?$/.test(limpo)) return null;
  // Math.round porque 18.5 * 100 dá 1850.0000000000002 em ponto flutuante.
  return Math.round(parseFloat(limpo) * 100);
}

async function salvarPreco(produto, texto) {
  const centavos = lerCentavos(texto);

  if (centavos === null) {
    aviso("Preço inválido — escreva como 18,50", "erro");
    return;
  }
  if (centavos === produto.preco_centavos) {
    fecharEditor();
    return;
  }

  try {
    const atualizado = await api.pedir("PATCH", `/produtos/${produto.id}`, {
      preco_centavos: centavos,
    });
    Object.assign(produto, atualizado);
    marcarOnline(true);
    fecharEditor();
    aviso(`${produto.nome}: ${reais(centavos)}`, "ok");
  } catch (erro) {
    if (erro instanceof api.ErroRede) marcarOnline(false);
    // O editor fica aberto de propósito: o valor digitado continua na tela pra
    // tentar de novo, em vez de o dono ter que redigitar.
    aviso(
      erro instanceof api.ErroRede ? "Sem conexão — preço não salvou" : erro.message,
      "erro",
    );
  }
}

async function alternarAtivo(produto) {
  try {
    const atualizado = await api.pedir("PATCH", `/produtos/${produto.id}`, {
      ativo: !produto.ativo,
    });
    Object.assign(produto, atualizado);
    marcarOnline(true);
    desenharCardapio();
    aviso(produto.ativo ? `${produto.nome} está no cardápio` : `${produto.nome} saiu do cardápio`, "ok");
  } catch (erro) {
    if (erro instanceof api.ErroRede) marcarOnline(false);
    aviso(erro instanceof api.ErroRede ? "Sem conexão" : erro.message, "erro");
  }
}

// ================================================================ fechamento

async function carregarFechamento() {
  // Hoje já está na mão; outra data precisa ir buscar.
  if (estado.dataFechamento === null) {
    if (estado.resumo) desenharFechamento(estado.resumo);
  } else if (estado.resumoFechamento) {
    desenharFechamento(estado.resumoFechamento);
  }

  await carregarHistorico();
}

async function carregarHistorico() {
  try {
    estado.historico = await api.pedir("GET", "/fechamento?limite=60");
    marcarOnline(true);
    desenharHistorico();
  } catch (erro) {
    if (erro instanceof api.ErroRede) marcarOnline(false);
    $("f-historico").innerHTML = `<li class="vazio">${escapar(
      erro instanceof api.ErroRede ? "Sem conexão." : erro.message,
    )}</li>`;
  }
}

function desenharFechamento(resumo) {
  const ehHoje = estado.dataFechamento === null;

  $("fech-titulo").textContent = ehHoje
    ? "Fechamento de hoje"
    : `Dia ${dataBonita(resumo.data_operacional)}`;
  $("fech-hoje").hidden = ehHoje;

  $("f-total").textContent = reais(resumo.total_centavos);
  $("f-pedidos").textContent = resumo.qtd_pedidos;

  const situacao = $("f-situacao");
  const botao = $("btn-fechar");

  if (resumo.fechamento) {
    const f = resumo.fechamento;
    situacao.textContent =
      `Fechado em ${reais(f.total_centavos)} por ${f.fechado_por_nome}, ` +
      `às ${hora(f.fechado_em)}.` +
      (resumo.pos_fechamento_qtd
        ? ` Entraram ${reais(resumo.pos_fechamento_centavos)} depois — fora deste total.`
        : "");
    botao.hidden = true;
  } else if (ehHoje) {
    situacao.textContent = resumo.qtd_pedidos
      ? "O caixa de hoje ainda está aberto."
      : "Nenhuma venda hoje — nada pra fechar ainda.";
    botao.hidden = false;
    botao.disabled = resumo.qtd_pedidos === 0;
  } else {
    situacao.textContent = "Este dia não foi fechado.";
    botao.hidden = true;
  }

  desenharRanking($("f-itens"), resumo.itens, "Nenhuma venda neste dia.");
}

function desenharHistorico() {
  const lista = $("f-historico");
  lista.innerHTML = "";

  if (!estado.historico.length) {
    lista.innerHTML = '<li class="vazio">Nenhum dia fechado ainda.</li>';
    return;
  }

  for (const fechamento of estado.historico) {
    const li = document.createElement("li");
    const botao = document.createElement("button");
    botao.innerHTML =
      `<span class="data">${escapar(dataBonita(fechamento.data_operacional))}</span>` +
      `<span class="quem">${fechamento.qtd_pedidos} pedidos · ` +
      `${escapar(fechamento.fechado_por_nome)}</span>` +
      `<span class="total">${reais(fechamento.total_centavos)}</span>`;
    botao.onclick = () => verDia(fechamento.data_operacional);
    li.append(botao);
    lista.append(li);
  }
}

async function verDia(data) {
  try {
    const resumo = await api.pedir("GET", `/relatorios/dia/${data}`);
    estado.dataFechamento = data;
    estado.resumoFechamento = resumo;
    marcarOnline(true);
    desenharFechamento(resumo);
    $("aba-fechamento").scrollTop = 0;
  } catch (erro) {
    if (erro instanceof api.ErroRede) marcarOnline(false);
    aviso(erro instanceof api.ErroRede ? "Sem conexão" : erro.message, "erro");
  }
}

function voltarPraHoje() {
  estado.dataFechamento = null;
  estado.resumoFechamento = null;
  if (estado.resumo) desenharFechamento(estado.resumo);
}

function pedirConfirmacao() {
  const resumo = estado.resumo;
  if (!resumo?.qtd_pedidos) return;

  $("confirmar-texto").textContent =
    `${reais(resumo.total_centavos)} em ${contar(resumo.qtd_pedidos, "pedido", "pedidos")}.`;
  $("confirmar").hidden = false;
}

async function confirmarFechamento() {
  const botao = $("confirmar-sim");
  botao.disabled = true;
  botao.textContent = "FECHANDO…";

  try {
    await api.pedir("POST", "/fechamento", {
      // O total que está na tela vai junto: se uma venda entrou entre o dono
      // conferir e apertar o botão, o servidor recusa e ele confere de novo,
      // em vez de congelar um número que ele nunca viu.
      total_conferido_centavos: estado.resumo.total_centavos,
    });

    $("confirmar").hidden = true;
    aviso("Caixa fechado", "ok");
    await atualizar();
    await carregarHistorico();
    if (estado.resumo) desenharFechamento(estado.resumo);
  } catch (erro) {
    $("confirmar").hidden = true;

    // 409 aqui é quase sempre o total tendo mudado. Recarregar mostra o número
    // novo — o dono confere e fecha de novo.
    if (erro instanceof api.ErroApi && erro.status === 409) {
      await atualizar();
      if (estado.resumo) desenharFechamento(estado.resumo);
    }
    aviso(erro instanceof api.ErroRede ? "Sem conexão" : erro.message, "erro");
  } finally {
    botao.disabled = false;
    botao.textContent = "CONFIRMAR FECHAMENTO";
  }
}

// =================================================================== eventos

function ligarEventos() {
  // --- login
  $("login-recarregar").onclick = carregarUsuarios;
  $("senha-voltar").onclick = () => {
    $("login-senha").hidden = true;
    $("login-lista").hidden = false;
  };
  $("senha-entrar").onclick = entrar;
  $("senha-campo").addEventListener("keydown", (e) => {
    if (e.key === "Enter") entrar();
  });

  // --- abas
  for (const botao of document.querySelectorAll(".abas__t")) {
    botao.onclick = () => trocarAba(botao.dataset.aba);
  }

  // --- painel do usuário
  $("btn-usuario").onclick = () => {
    $("painel").hidden = false;
    const sessao = api.sessaoAtual();
    $("painel-usuario-nome").textContent = `${sessao?.nome ?? ""} · ${sessao?.papel ?? ""}`;
  };
  $("btn-sair").onclick = sair;
  $("btn-atualizar").onclick = async () => {
    $("painel").hidden = true;
    aviso("Atualizando…");
    estado.cardapio = null;
    await atualizar();
    if (estado.aba === "cardapio") await carregarCardapio();
    if (estado.aba === "fechamento") await carregarFechamento();
  };
  for (const alvo of document.querySelectorAll("[data-fechar]")) {
    alvo.onclick = () => ($("painel").hidden = true);
  }

  // --- fechamento
  $("btn-fechar").onclick = pedirConfirmacao;
  $("fech-hoje").onclick = voltarPraHoje;
  $("confirmar-sim").onclick = confirmarFechamento;
  for (const alvo of document.querySelectorAll("[data-fechar-confirmar]")) {
    alvo.onclick = () => ($("confirmar").hidden = true);
  }

  // --- conexão
  window.addEventListener("online", () => {
    marcarOnline(true);
    atualizar();
  });
  window.addEventListener("offline", () => {
    marcarOnline(false);
    desenharHoje();
  });

  // Voltar pro app é o momento em que o número na tela está mais velho.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) atualizar();
  });
}

async function sair() {
  pararAtualizacao();
  await api.sair();
  localStorage.removeItem(CHAVE_ULTIMO);
  estado.resumo = null;
  estado.cardapio = null;
  estado.historico = [];
  $("painel").hidden = true;
  mostrarLogin();
}

// ================================================================ utilidades

function gravarUltimo(resumo) {
  try {
    localStorage.setItem(CHAVE_ULTIMO, JSON.stringify(resumo));
  } catch {
    /* cota cheia: perder o cache não impede o app de funcionar online */
  }
}

function lerUltimo() {
  try {
    const bruto = localStorage.getItem(CHAVE_ULTIMO);
    if (!bruto) return null;
    const resumo = JSON.parse(bruto);
    // O resumo guardado é de outro dia operacional: mostrar o total de ontem
    // como "vendido hoje" seria pior do que não mostrar nada.
    return resumo?.data_operacional === hojeLocal() ? resumo : null;
  } catch {
    return null;
  }
}

/**
 * O dia operacional de hoje na visão do celular.
 *
 * A virada é às 04h (o mesmo `hora_virada_dia` do servidor): antes disso, o
 * movimento ainda é o de ontem. Só serve pra decidir se o cache velho vale —
 * o dia que aparece na tela é sempre o que o servidor mandou.
 */
function hojeLocal() {
  const agora = new Date();
  if (agora.getHours() < 4) agora.setDate(agora.getDate() - 1);
  return `${agora.getFullYear()}-${String(agora.getMonth() + 1).padStart(2, "0")}-${String(
    agora.getDate(),
  ).padStart(2, "0")}`;
}

function dataBonita(iso) {
  const [ano, mes, dia] = String(iso).split("-");
  return `${dia}/${mes}/${ano}`;
}

function contar(n, singular, plural_) {
  return `${n} ${n === 1 ? singular : plural_}`;
}

function marcarOnline(ligado) {
  estado.online = ligado;
  $("ponto-conexao").dataset.estado = ligado ? "online" : "offline";
}

function mostrarErro(texto) {
  const faixa = $("faixa-erro");
  faixa.textContent = `⚠ ${texto}`;
  faixa.hidden = false;
}

function esconderErro() {
  $("faixa-erro").hidden = true;
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
