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

// Espelha `CATEGORIAS_SABOR_OBRIGATORIO` do backend (app/models/cardapio.py):
// nessas categorias todo produto leva bola, e o servidor recusa desligar o
// 🍦. Repetido aqui só pra travar o botão antes do toque virar um erro de
// rede — quem manda é sempre o servidor.
const CATEGORIAS_SABOR_OBRIGATORIO = new Set(["Sorvetes", "Trufados", "Sundae", "Kids"]);

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
  /** Sessões abertas, quando a folha de acessos está aberta. */
  acessos: [],
  /** Id da sessão à espera do segundo toque, quando ela é do próprio dono. */
  confirmarAcesso: null,
  /** Funcionários (nome, ativo), quando a folha de funcionários está aberta. */
  funcionarios: [],

  /** Os dois sabores que estão na máquina agora. */
  sabores: { sabor1: null, sabor2: null, atualizado_em: null },
  /** Id do funcionário à espera do segundo toque em "Excluir". */
  confirmarExclusao: null,
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

function mostrarLogin() {
  pararAtualizacao();
  $("tela-painel").hidden = true;
  $("tela-login").hidden = false;
  $("login-erro").hidden = true;
  $("login-senha").value = "";
}

async function entrar() {
  const usuario = $("login-usuario").value.trim();
  const senha = $("login-senha").value;
  const erroEl = $("login-erro");
  const botao = $("login-entrar");

  if (!usuario || !senha) {
    erroEl.textContent = "Preencha usuário e senha";
    erroEl.hidden = false;
    return;
  }

  botao.disabled = true;
  botao.textContent = "ENTRANDO…";
  erroEl.hidden = true;

  try {
    const sessao = await api.entrar(usuario, senha);
    $("login-senha").value = "";

    // O papel vem do servidor, no token. Sem esta checagem o funcionário
    // entraria aqui com a própria senha e veria o faturamento do dia — as
    // rotas de relatório respondem 403, mas a tela abriria vazia sem explicar.
    if (sessao.papel !== "DONO") {
      await api.sair();
      throw new Error("Este painel é só do dono.");
    }

    await abrirPainel();
  } catch (erro) {
    erroEl.textContent =
      erro instanceof api.ErroRede ? "Sem conexão — não dá pra entrar agora." : erro.message;
    erroEl.hidden = false;
    $("login-senha").value = "";
    $("login-senha").focus();
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
    // O evento já traz o produto inteiro — inclusive quando o toque saiu
    // deste mesmo celular, que recebe o próprio eco de volta pelo socket.
    // Recarregar tudo aqui apagava a lista pra mostrar "Carregando…" e só
    // voltava depois de uma ida e volta ao servidor: exatamente o atraso e o
    // pulo pro topo que sobravam mesmo numa edição instantânea. Só quando o
    // produto ainda não existe na lista (criado por outro caminho) vale ir
    // buscar de novo.
    if (!aplicarProdutoNoCardapio(dados)) carregarCardapio();
  }

  // Alguém pediu uma conta agora. Se a folha de funcionários está aberta, ela
  // se atualiza sozinha; se não está, o aviso é o que evita a pessoa ficar
  // esperando no balcão enquanto o dono mexe em outra aba sem saber de nada.
  // Outro aparelho do dono trocou o sabor. Sem isto, este celular continuaria
  // mostrando o de ontem até alguém recarregar a página.
  if (evento === "sabor.alterado") {
    estado.sabores = { ...estado.sabores, ...dados };
    desenharSabores();
  }

  if (evento === "usuario.pendente") {
    if (!$("funcionarios").hidden) {
      carregarFuncionarios();
    } else {
      aviso(`${dados?.nome ?? "Alguém"} pediu acesso — libere em Funcionários`, "ok");
    }
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
  $("aba-historico").hidden = qual !== "historico";

  // Carregamento sob demanda: quem abre o app pra ver o total do dia não
  // precisa baixar o cardápio inteiro nem o histórico de fechamentos.
  if (qual === "cardapio" && !estado.cardapio) carregarCardapio();
  // Sempre, e não só na primeira vez: o sabor é a informação mais perecível
  // desta tela, e abrir a aba é justamente o gesto de quem quer conferi-lo.
  if (qual === "cardapio") carregarSabores();
  if (qual === "fechamento") carregarFechamento();
  if (qual === "historico") carregarHistorico();
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

/**
 * Encaixa o produto que veio de um evento `preco.alterado` na lista já
 * carregada, em vez de buscar tudo de novo. `grupos` não vem no evento (o
 * PATCH de preço/ativo/sabor não mexe em acompanhamento nenhum) — preserva o
 * que já está na tela, senão o produto perderia a lista até o próximo
 * recarregamento inteiro.
 *
 * Devolve `false` quando o produto não está na lista ainda, pra quem chamou
 * decidir buscar de novo.
 */
function aplicarProdutoNoCardapio(atualizado) {
  for (const categoria of estado.cardapio.categorias) {
    const alvo = categoria.produtos.find((p) => p.id === atualizado.id);
    if (alvo) {
      Object.assign(alvo, atualizado, { grupos: alvo.grupos });
      desenharCardapio();
      return true;
    }
  }
  return false;
}

function desenharCardapio() {
  const area = $("lista-cardapio");
  // A lista inteira é reconstruída a cada edição — inclusive uma edição só de
  // preço, que troca um número. Sem isto, o dono rolando a lista pra editar o
  // décimo produto voltaria pro topo a cada toque no ✓.
  const painel = $("aba-cardapio");
  const rolagem = painel.scrollTop;

  area.innerHTML = "";

  for (const categoria of estado.cardapio.categorias) {
    if (!categoria.produtos.length) continue;

    const bloco = document.createElement("section");
    bloco.className = "categoria";
    bloco.innerHTML = `<h2 class="categoria__nome">${escapar(categoria.nome)}</h2>`;

    for (const produto of categoria.produtos) {
      bloco.append(linhaProduto(produto, categoria.nome));
    }
    area.append(bloco);
  }

  $("painel-versao").textContent = estado.cardapio.versao
    ? `Cardápio de ${new Date(estado.cardapio.versao).toLocaleString("pt-BR")}`
    : "Cardápio sem data";

  painel.scrollTop = rolagem;
}

function linhaProduto(produto, categoriaNome) {
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

  // O interruptor do sabor. Fica apagado quando o produto não leva bola: a
  // água mineral não pergunta sabor, e o dono é quem sabe onde está a
  // fronteira — por isso é um toque por produto na maioria dos casos.
  // Exceção: sorvete, trufado, sundae e kids são sempre bola, então o
  // servidor recusa desligar e o botão nasce travado (ver `travado` abaixo).
  //
  // Produto com sabor extra mostra qual é, ao lado do botão: o milk-shake
  // oferece chocolate além dos dois do dia, e um 🍦 sozinho não conta isso.
  // O sabor extra não se edita aqui — é estrutura, definida no `seed.py` —,
  // mas aparece, porque um produto que oferece algo que a tela não mostra é o
  // tipo de coisa que faz alguém achar que o sistema está errado.
  const travado =
    Boolean(produto.sabor_extra) || CATEGORIAS_SABOR_OBRIGATORIO.has(categoriaNome);

  const sabor = document.createElement("button");
  sabor.className = "linha__sabor";
  sabor.dataset.ligado = produto.pede_sabor ? "1" : "0";
  sabor.textContent = produto.sabor_extra ? `🍦+${produto.sabor_extra}` : "🍦";
  if (travado) {
    sabor.title = "Este tipo de produto sempre pergunta o sabor — não dá pra desligar";
  } else {
    sabor.title = produto.pede_sabor
      ? "Pergunta o sabor do dia — toque pra parar de perguntar"
      : "Não pergunta o sabor — toque pra passar a perguntar";
  }
  if (produto.sabor_extra) {
    sabor.title += `. Este oferece ${produto.sabor_extra} além dos dois do dia.`;
  }
  sabor.setAttribute("aria-label", `${produto.nome}: ${sabor.title}`);
  sabor.setAttribute("aria-pressed", produto.pede_sabor ? "true" : "false");
  if (travado) {
    sabor.disabled = true;
  } else {
    sabor.onclick = () => alternarSabor(produto, sabor);
  }

  const ativo = document.createElement("button");
  ativo.className = "linha__ativo";
  ativo.textContent = produto.ativo ? "👁" : "🚫";
  ativo.title = produto.ativo ? "Tirar do cardápio" : "Voltar pro cardápio";
  ativo.setAttribute("aria-label", ativo.title);
  ativo.onclick = () => alternarAtivo(produto, ativo);

  linha.append(preco, sabor, ativo);
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
    // `grupos` preservado: a resposta do PATCH traz `grupos: []` (a rota valida
    // o produto sem os vínculos), e um `Object.assign` cru apagaria a lista de
    // acompanhamentos que já estava na tela. O `aplicarProdutoNoCardapio` se
    // protege disso trinta linhas acima; estes três caminhos furavam a proteção.
    Object.assign(produto, atualizado, { grupos: produto.grupos });
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

/**
 * Enquanto o servidor não responde, o botão fica apagado e travado.
 *
 * Sem isto o toque não dizia nada: o 🍦 só mudava quando o PATCH voltava, e
 * numa rede ruim isso é um segundo de tela parada. O dono conclui que não
 * pegou e toca de novo — e o segundo toque manda o mesmo valor outra vez,
 * porque o `produto` local ainda não mudou. Não corrompia nada, mas eram duas
 * idas ao servidor e a sensação de aparelho travado.
 *
 * O `finally` devolve o botão mesmo quando a lista foi redesenhada por baixo:
 * aí o nó daqui já saiu da tela e mexer nele não faz mal nenhum.
 */
async function comBotaoOcupado(botao, trabalho) {
  if (botao) {
    botao.disabled = true;
    botao.dataset.ocupado = "1";
  }
  try {
    return await trabalho();
  } finally {
    if (botao) {
      botao.disabled = false;
      delete botao.dataset.ocupado;
    }
  }
}

async function alternarAtivo(produto, botao) {
  return comBotaoOcupado(botao, async () => {
    try {
      const atualizado = await api.pedir("PATCH", `/produtos/${produto.id}`, {
        ativo: !produto.ativo,
      });
      // `grupos` preservado: a resposta do PATCH traz `grupos: []` (a rota valida
      // o produto sem os vínculos), e um `Object.assign` cru apagaria a lista de
      // acompanhamentos que já estava na tela. O `aplicarProdutoNoCardapio` se
      // protege disso trinta linhas acima; estes três caminhos furavam a proteção.
      Object.assign(produto, atualizado, { grupos: produto.grupos });
      marcarOnline(true);
      desenharCardapio();
      aviso(
        produto.ativo ? `${produto.nome} está no cardápio` : `${produto.nome} saiu do cardápio`,
        "ok",
      );
    } catch (erro) {
      if (erro instanceof api.ErroRede) marcarOnline(false);
      aviso(erro instanceof api.ErroRede ? "Sem conexão" : erro.message, "erro");
    }
  });
}

async function alternarSabor(produto, botao) {
  return comBotaoOcupado(botao, async () => {
    try {
      const atualizado = await api.pedir("PATCH", `/produtos/${produto.id}`, {
        pede_sabor: !produto.pede_sabor,
      });
      // Mesma preservação de `grupos` do `alternarAtivo`, pelo mesmo motivo.
      Object.assign(produto, atualizado, { grupos: produto.grupos });
      marcarOnline(true);
      desenharCardapio();
      aviso(
        produto.pede_sabor
          ? `${produto.nome} agora pergunta o sabor`
          : `${produto.nome} não pergunta mais o sabor`,
        "ok",
      );
    } catch (erro) {
      if (erro instanceof api.ErroRede) marcarOnline(false);
      aviso(erro instanceof api.ErroRede ? "Sem conexão" : erro.message, "erro");
    }
  });
}

// =============================================================== sabor do dia

async function carregarSabores() {
  try {
    estado.sabores = await api.pedir("GET", "/sabores");
    desenharSabores();
  } catch (erro) {
    // Sem conexão os campos ficam como estão. Não é motivo pra assustar
    // ninguém: o resto da aba de cardápio continua utilizável.
    if (erro instanceof api.ErroRede) marcarOnline(false);
  }
}

function desenharSabores() {
  // Só preenche o que não está sendo digitado agora: sobrescrever o campo em
  // foco apagaria o que o dono está escrevendo quando o evento de outro
  // aparelho chegasse no meio.
  const foco = document.activeElement;
  for (const [campo, valorAtual] of [
    ["sabor1", estado.sabores.sabor1],
    ["sabor2", estado.sabores.sabor2],
  ]) {
    const el = $(campo);
    if (el !== foco) el.value = valorAtual ?? "";
  }

  const quando = estado.sabores.atualizado_em;
  $("sabores-quando").textContent = quando
    ? `Trocado em ${new Date(quando).toLocaleString("pt-BR")}`
    : "Nenhum sabor definido — o balcão não vai perguntar nada.";
}

async function salvarSabores() {
  const botao = $("sabores-salvar");
  botao.disabled = true;
  botao.textContent = "SALVANDO…";
  try {
    estado.sabores = await api.pedir("PUT", "/sabores", {
      sabor1: $("sabor1").value,
      sabor2: $("sabor2").value,
    });
    marcarOnline(true);
    desenharSabores();
    aviso(
      estado.sabores.sabor1 || estado.sabores.sabor2
        ? "Sabor de hoje atualizado"
        : "Sabores apagados — o balcão não vai perguntar",
      "ok",
    );
  } catch (erro) {
    if (erro instanceof api.ErroRede) marcarOnline(false);
    aviso(erro instanceof api.ErroRede ? "Sem conexão" : erro.message, "erro");
  } finally {
    botao.disabled = false;
    botao.textContent = "SALVAR SABORES";
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
}

/** As três listas da aba Histórico — dia, semana e mês —, sempre juntas
 *  porque é a mesma aba e o mesmo gesto de abrir ela. */
async function carregarHistorico() {
  await Promise.all([
    carregarHistoricoDiario(),
    carregarHistoricoAgrupado("semanal", "f-historico-semanal", "Nenhuma semana fechada ainda."),
    carregarHistoricoAgrupado("mensal", "f-historico-mensal", "Nenhum mês fechado ainda."),
    carregarHistoricoAgrupado("anual", "f-historico-anual", "Nenhum ano fechado ainda."),
  ]);
}

async function carregarHistoricoDiario() {
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

async function carregarHistoricoAgrupado(caminho, idLista, vazioTexto) {
  try {
    const grupos = await api.pedir("GET", `/fechamento/${caminho}`);
    marcarOnline(true);
    desenharHistoricoAgrupado($(idLista), grupos, vazioTexto);
  } catch (erro) {
    if (erro instanceof api.ErroRede) marcarOnline(false);
    $(idLista).innerHTML = `<li class="vazio">${escapar(
      erro instanceof api.ErroRede ? "Sem conexão." : erro.message,
    )}</li>`;
  }
}

/** Semana e mês só mostram o valor final (§ pedido do dono): sem botão, sem
 *  detalhe por item — pra isso existe o dia, que continua clicável. */
function desenharHistoricoAgrupado(lista, grupos, vazioTexto) {
  lista.innerHTML = "";

  if (!grupos.length) {
    lista.innerHTML = `<li class="vazio">${escapar(vazioTexto)}</li>`;
    return;
  }

  for (const grupo of grupos) {
    const li = document.createElement("li");
    li.className = "historico__linha";
    const periodo =
      grupo.inicio === grupo.fim
        ? dataBonita(grupo.inicio)
        : `${dataBonita(grupo.inicio)} – ${dataBonita(grupo.fim)}`;
    li.innerHTML =
      `<span class="data">${escapar(periodo)}</span>` +
      `<span class="quem">${contar(grupo.qtd_dias, "dia fechado", "dias fechados")}</span>` +
      `<span class="total">${reais(grupo.total_centavos)}</span>`;
    lista.append(li);
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
    // O dia é clicado na aba Histórico, mas o detalhe mora na Fechamento —
    // é lá que estão o total, a situação e o ranking por item.
    trocarAba("fechamento");
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

// ==================================================================== acessos

/**
 * Quem está logado — e o botão de tirar.
 *
 * Mora no painel do usuário e não numa aba porque é conta, não número: o dono
 * abre isto quando alguém saiu da loja ou perdeu o celular, não todo dia. As
 * três abas continuam sendo as três coisas que ele olha diariamente.
 */
async function abrirAcessos() {
  $("painel").hidden = true;
  $("acessos").hidden = false;
  await carregarAcessos();
}

async function carregarAcessos() {
  const lista = $("lista-acessos");
  lista.innerHTML = '<li class="fraco">Carregando…</li>';

  try {
    estado.acessos = await api.pedir("GET", "/auth/sessoes");
    desenharAcessos();
  } catch (erro) {
    lista.innerHTML = `<li class="fraco">${escapar(
      erro instanceof api.ErroRede ? "Sem conexão com o servidor." : erro.message,
    )}</li>`;
  }
}

function desenharAcessos() {
  const lista = $("lista-acessos");
  lista.innerHTML = "";

  if (!estado.acessos.length) {
    lista.innerHTML = '<li class="fraco">Ninguém logado no momento.</li>';
    return;
  }

  for (const acesso of estado.acessos) {
    const li = document.createElement("li");
    li.className = "acesso";
    li.innerHTML =
      `<span class="acesso__quem">` +
      `<strong>${escapar(acesso.usuario_nome)}</strong>` +
      `<small>${escapar(acesso.papel.toLowerCase())}` +
      (acesso.meu_usuario ? " · você" : "") +
      `</small>` +
      `<small>${escapar(acesso.dispositivo || "aparelho não identificado")}</small>` +
      `<small>entrou ${escapar(quando(acesso.criado_em))}</small>` +
      `</span>` +
      `<button class="acesso__x" data-remover>Remover</button>`;

    li.querySelector("[data-remover]").onclick = () => removerAcesso(acesso);
    lista.append(li);
  }
}

/** Data e hora do login, curtinho: "hoje 14:32" ou "12/08 14:32". */
function quando(iso) {
  const momento = new Date(iso);
  const hoje = new Date().toDateString() === momento.toDateString();
  return hoje
    ? `hoje ${hora(momento)}`
    : `${momento.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" })} ${hora(momento)}`;
}

/**
 * Tira o acesso de um aparelho.
 *
 * Pede confirmação quando o alvo é o próprio dono, porque o token de acesso
 * não diz *qual* aparelho é este — só de quem ele é. Sem o aviso, um toque
 * distraído derrubaria o próprio celular sem o dono entender o porquê.
 */
async function removerAcesso(acesso) {
  if (acesso.meu_usuario && estado.confirmarAcesso !== acesso.id) {
    estado.confirmarAcesso = acesso.id;
    aviso("É um login seu — toque de novo pra remover mesmo assim", "erro");
    return;
  }

  estado.confirmarAcesso = null;
  try {
    await api.pedir("DELETE", `/auth/sessoes/${acesso.id}`);
    aviso("Acesso removido", "ok");
    await carregarAcessos();
  } catch (erro) {
    aviso(
      erro instanceof api.ErroRede ? "Sem conexão — tente de novo" : erro.message,
      "erro",
    );
  }
}

// ============================================================== funcionários

/**
 * As contas que os funcionários criaram sozinhos na tela de vendas.
 *
 * Mesma ideia da folha de acessos: fica no painel do usuário porque é conta,
 * não número — o dono abre isto quando alguém sai da loja, não todo dia.
 */
async function abrirFuncionarios() {
  $("painel").hidden = true;
  $("funcionarios").hidden = false;
  await carregarFuncionarios();
}

async function carregarFuncionarios() {
  const lista = $("lista-funcionarios");
  lista.innerHTML = '<li class="fraco">Carregando…</li>';

  try {
    estado.funcionarios = await api.pedir("GET", "/usuarios");
    desenharFuncionarios();
  } catch (erro) {
    lista.innerHTML = `<li class="fraco">${escapar(
      erro instanceof api.ErroRede ? "Sem conexão com o servidor." : erro.message,
    )}</li>`;
  }
}

function desenharFuncionarios() {
  const lista = $("lista-funcionarios");
  lista.innerHTML = "";

  if (!estado.funcionarios.length) {
    lista.innerHTML = '<li class="fraco">Nenhum funcionário cadastrado ainda.</li>';
    return;
  }

  // Quem espera liberação vem primeiro. É a única linha desta tela que pede
  // uma decisão agora — tem gente parada no balcão esperando pra trabalhar —
  // e no fim da lista, embaixo da equipe inteira, ela passaria batida.
  const ordenados = [...estado.funcionarios].sort(
    (a, b) => Number(aguardando(b)) - Number(aguardando(a)),
  );

  for (const pessoa of ordenados) {
    const espera = aguardando(pessoa);
    const li = document.createElement("li");
    li.className = espera ? "acesso acesso--espera" : "acesso";

    // Três estados, não dois. "Pausado" e "esperando liberação" são os dois
    // `ativo: false`, mas significam o oposto um do outro: um é alguém que o
    // dono barrou de propósito, o outro é alguém que ele ainda nem viu. Com o
    // mesmo rótulo nos dois, "Reativar" devolve o acesso de quem foi barrado.
    const situacao = espera ? "esperando liberação" : pessoa.ativo ? "ativo" : "pausado";
    const acao = espera ? "Liberar" : pessoa.ativo ? "Pausar" : "Reativar";

    li.innerHTML =
      `<span class="acesso__quem">` +
      `<strong>${escapar(pessoa.nome)}</strong>` +
      `<small>${situacao}</small>` +
      `</span>` +
      `<span class="acesso__acoes">` +
      `<button class="${espera ? "acesso__liberar" : "acesso__pausar"}" data-pausar>${acao}</button>` +
      `<button class="acesso__x" data-excluir>Excluir</button>` +
      `</span>`;

    li.querySelector("[data-pausar]").onclick = () => pausarFuncionario(pessoa);
    li.querySelector("[data-excluir]").onclick = () => excluirFuncionario(pessoa);
    lista.append(li);
  }
}

/** Conta criada pela tela de vendas que o dono ainda não liberou nenhuma vez. */
function aguardando(pessoa) {
  return !pessoa.ativo && !pessoa.aprovado_em;
}

async function pausarFuncionario(pessoa) {
  const espera = aguardando(pessoa);
  try {
    await api.pedir("PATCH", `/usuarios/${pessoa.id}`, { ativo: !pessoa.ativo });
    aviso(
      espera
        ? `${pessoa.nome} já pode entrar`
        : pessoa.ativo
          ? "Funcionário pausado"
          : "Funcionário reativado",
      "ok",
    );
    await carregarFuncionarios();
  } catch (erro) {
    aviso(
      erro instanceof api.ErroRede ? "Sem conexão — tente de novo" : erro.message,
      "erro",
    );
  }
}

/** Pede confirmação com um segundo toque — excluir não tem volta. */
async function excluirFuncionario(pessoa) {
  if (estado.confirmarExclusao !== pessoa.id) {
    estado.confirmarExclusao = pessoa.id;
    aviso("Toque em Excluir de novo pra confirmar", "erro");
    return;
  }

  estado.confirmarExclusao = null;
  try {
    await api.pedir("DELETE", `/usuarios/${pessoa.id}`);
    aviso("Funcionário excluído", "ok");
    await carregarFuncionarios();
  } catch (erro) {
    aviso(
      erro instanceof api.ErroRede ? "Sem conexão — tente de novo" : erro.message,
      "erro",
    );
  }
}

// =================================================================== eventos

function ligarEventos() {
  // --- login
  // `submit` e não o clique do botão: é o que faz o "ir" do teclado do celular
  // entrar, sem ter que fechar o teclado pra achar o botão.
  $("login-form").addEventListener("submit", (e) => {
    e.preventDefault();
    entrar();
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
  $("btn-acessos").onclick = abrirAcessos;
  $("acessos-atualizar").onclick = carregarAcessos;
  for (const alvo of document.querySelectorAll("[data-fechar-acessos]")) {
    alvo.onclick = () => ($("acessos").hidden = true);
  }
  $("btn-funcionarios").onclick = abrirFuncionarios;
  $("sabores-salvar").onclick = salvarSabores;
  $("funcionarios-atualizar").onclick = carregarFuncionarios;
  for (const alvo of document.querySelectorAll("[data-fechar-funcionarios]")) {
    alvo.onclick = () => ($("funcionarios").hidden = true);
  }
  $("btn-sair").onclick = sair;
  $("btn-atualizar").onclick = async () => {
    $("painel").hidden = true;
    aviso("Atualizando…");
    estado.cardapio = null;
    await atualizar();
    if (estado.aba === "cardapio") await carregarCardapio();
    if (estado.aba === "fechamento") await carregarFechamento();
    if (estado.aba === "historico") await carregarHistorico();
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
