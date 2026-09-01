/**
 * PWA de Vendas — a tela que o funcionário usa no balcão, num celular.
 *
 * O fio condutor: **apertar ENVIAR nunca falha na frente do cliente**. O
 * pedido vai pro IndexedDB (`fila.js`), o carrinho limpa na hora e a subida
 * pro servidor acontece por trás. Sem internet, a venda continua; a fila sobe
 * sozinha quando a conexão volta.
 *
 * Por isso o cardápio também fica em cache: a tela precisa abrir e vender
 * mesmo que a primeira coisa que aconteça no dia seja a internet cair.
 *
 * **A comanda sai deste mesmo aparelho.** Assim que o servidor confirma a
 * venda, o texto do cupom é despachado pro RawBT por um Intent do Android e o
 * papel sai sem ninguém apertar nada (`impressao.js`). Este arquivo não sabe
 * formatar cupom nem falar com o Android — ele só diz *quando* a venda ficou
 * pronta.
 */

import * as api from "../comum/api.js";
import { reais, valor, hora, plural } from "../comum/formato.js";
import * as ws from "../comum/ws.js";
import * as fila from "./fila.js";
import { criarImpressora } from "./impressao.js";

const CHAVE_CARDAPIO = "shalon.cardapio";
// O sabor do dia mora à parte do cardápio: o cardápio muda uma vez por mês,
// o sabor troca toda manhã, e guardá-los juntos faria o balcão perder o
// cardápio inteiro do cache toda vez que a máquina trocasse de sabor.
const CHAVE_SABORES = "shalon.sabores";
const INTERVALO_SINCRONIA_MS = 15000;

const estado = {
  cardapio: null,
  categoriaAtiva: null,
  /**
   * chave → { produto_id, opcoes: number[], quantidade }
   *
   * A chave é produto + acompanhamentos (ver `chaveDe`), não só o produto: dois
   * açaís de 500ml, um com granola e outro com paçoca, são duas linhas. Somar
   * os dois num "2x" mandaria a cozinha montar os dois iguais.
   */
  carrinho: new Map(),
  /** opcao_id → opção, achatado do cardápio pra consulta rápida de preço/nome */
  opcoes: new Map(),
  /** Produto sendo montado na folha de acompanhamentos, ou null */
  escolha: null,
  /** Os dois sabores que a loja está servindo hoje, como o dono deixou. */
  sabores: { sabor1: null, sabor2: null },
  /** Registro na folha de exclusão, ou null */
  excluindo: null,
  /** Motivo marcado nos botões da folha de exclusão */
  motivoEscolhido: null,
  sincronizando: false,
  /** false depois de uma falha de rede; o pontinho do topo vive disto */
  online: navigator.onLine,
  saidaConfirmada: false,
  /** Conexão do WebSocket, quando existe. */
  socket: null,
};

const $ = (id) => document.getElementById(id);

/**
 * A impressora do balcão.
 *
 * Criada aqui, uma vez, com as dependências padrão (RawBT + layout do
 * `comanda.js`). Trocar o aplicativo de impressão é trocar o que entra nesta
 * chamada — nada mais neste arquivo muda.
 */
const impressora = criarImpressora({
  aoMudar: ({ pendentes, ultimoErro }) => atualizarFaixaImpressao(pendentes, ultimoErro),
});

// ==================================================================== início

async function iniciar() {
  registrarServiceWorker();
  ligarEventos();

  api.aoPerderSessao(() => {
    aviso("Sessão expirada — entre de novo", "erro");
    mostrarLogin();
  });

  if (api.estaLogado()) {
    await abrirVenda();
  } else {
    mostrarLogin();
  }

  setInterval(() => {
    sincronizar();
    impressora.retomar();
  }, INTERVALO_SINCRONIA_MS);
  fila.limparAntigos().catch(() => {});
}

function registrarServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.register("sw.js").catch((erro) => {
    // Sem service worker o app ainda funciona online; só não abre offline.
    console.warn("Service worker não registrou:", erro);
  });
}

// ===================================================================== login

function mostrarLogin() {
  fecharEscolhas();
  $("tela-venda").hidden = true;
  $("tela-cadastro").hidden = true;
  $("tela-login").hidden = false;
  $("login-erro").hidden = true;
  $("login-senha").value = "";
}

function mostrarCadastro() {
  $("tela-login").hidden = true;
  $("tela-cadastro").hidden = false;
  $("cadastro-erro").hidden = true;
  $("cadastro-senha").value = "";
  // Volta ao formulário: quem chega aqui pela segunda vez não pode encontrar
  // o "Conta criada" da vez anterior.
  $("cadastro-form").hidden = false;
  $("cadastro-pronto").hidden = true;
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
    await api.entrar(usuario, senha);
    // Só a senha some. O nome fica: é o mesmo aparelho e a mesma pessoa toda
    // manhã, e limpar os dois faria digitar duas coisas onde uma bastava.
    $("login-senha").value = "";
    await abrirVenda();
  } catch (erro) {
    erroEl.textContent =
      erro instanceof api.ErroRede
        ? "Sem conexão — não dá pra entrar agora."
        : erro.message;
    erroEl.hidden = false;
    $("login-senha").value = "";
    $("login-senha").focus();
  } finally {
    botao.disabled = false;
    botao.textContent = "ENTRAR";
  }
}

async function criarConta() {
  const nome = $("cadastro-nome").value.trim();
  const senha = $("cadastro-senha").value;
  const erroEl = $("cadastro-erro");
  const botao = $("cadastro-criar");

  if (!nome || !/^\d{6}$/.test(senha)) {
    erroEl.textContent = "Preencha o nome e uma senha de 6 números";
    erroEl.hidden = false;
    return;
  }

  botao.disabled = true;
  botao.textContent = "CRIANDO…";
  erroEl.hidden = true;

  try {
    await api.cadastrar(nome, senha);
    $("cadastro-senha").value = "";
    // Não entra: a conta nasce esperando o dono liberar. O formulário sai da
    // frente e dá lugar ao aviso — deixá-lo ali, com o botão "CRIAR CONTA"
    // ainda apertável, faria a pessoa tentar de novo e tomar "esse nome já
    // está em uso" como se tivesse feito algo errado.
    $("cadastro-form").hidden = true;
    $("cadastro-pronto").hidden = false;
  } catch (erro) {
    erroEl.textContent =
      erro instanceof api.ErroRede
        ? "Sem conexão — não dá pra criar a conta agora."
        : erro.message;
    erroEl.hidden = false;
    $("cadastro-senha").value = "";
    $("cadastro-senha").focus();
  } finally {
    botao.disabled = false;
    botao.textContent = "CRIAR CONTA";
  }
}

// ===================================================================== venda

async function abrirVenda() {
  $("tela-login").hidden = true;
  $("tela-cadastro").hidden = true;
  $("tela-venda").hidden = false;
  $("nome-usuario").textContent = api.sessaoAtual()?.nome ?? "";

  // Cache primeiro: a tela precisa estar vendável antes de qualquer rede.
  estado.sabores = lerSaboresLocais() ?? { sabor1: null, sabor2: null };
  const emCache = lerCardapioLocal();
  if (emCache) {
    aplicarCardapio(emCache);
  } else {
    $("produtos").innerHTML = '<p class="vazio">Carregando cardápio…</p>';
  }

  atualizarCarrinho();

  // O cardápio primeiro, e sem esperar por mais nada: é ele que deixa a tela
  // vendável, e tudo que vem depois é aviso. Quando isto vinha por último, uma
  // falha ao ler a fila do IndexedDB matava a função no meio e a tela ficava
  // em "Carregando cardápio…" pra sempre — sem erro visível, e culpando a
  // parte errada do app.
  //
  // Silencioso: entrar no app não é hora de aviso. Os produtos aparecendo na
  // tela já dizem que deu certo; o toque em "Atualizar cardápio" é que fala.
  baixarCardapio({ silencioso: true });
  carregarSabores();
  ligarSocket();

  // Comanda que ficou de ontem, ou de antes do app ser fechado: a fila mora no
  // localStorage e sobrevive ao recarregamento, então a faixa precisa aparecer
  // já na abertura, e não só na próxima venda.
  atualizarFaixaImpressao(impressora.pendentes(), null);
  impressora.retomar();

  try {
    await atualizarFaixaFila();
    sincronizar();
  } catch (erro) {
    // Sem a fila local dá pra vender online, e não dá pra vender offline. Quem
    // está no balcão precisa saber disso agora, não na primeira queda de sinal.
    console.error("fila local indisponível:", erro);
    mostrarErro("Fila offline indisponível neste aparelho — venda só com internet");
  }
}

// ================================================================ tempo real

/**
 * O socket serve a uma coisa só aqui: preço editado pelo dono chegar no balcão
 * antes da próxima venda (§2.3 promete menos de 1 segundo).
 *
 * A venda em si **não** passa por ele. Quem garante que o pedido sobe é a fila
 * do IndexedDB, que funciona com a internet fora — e um caminho que só funciona
 * com o socket de pé seria um caminho a menos de confiança, não a mais.
 */
function ligarSocket() {
  estado.socket?.fechar();
  estado.socket = ws.conectar({
    aoEvento: (evento, dados) => {
      if (evento === "usuario.desativado") {
        if (dados?.usuario_id === api.sessaoAtual()?.usuario_id) {
          forcarSaida("Sua conta foi desativada pelo dono");
        }
        return;
      }

      // O dono trocou o sabor da máquina agora. Chega na hora porque a
      // alternativa é o balcão oferecer chocolate num dia de creme até alguém
      // recarregar a página.
      if (evento === "sabor.alterado") {
        estado.sabores = { sabor1: dados?.sabor1 ?? null, sabor2: dados?.sabor2 ?? null };
        gravarSaboresLocais(estado.sabores);
        // Com a folha aberta, redesenhar trocaria os botões debaixo do dedo de
        // quem está escolhendo. O que já está no carrinho também não muda: o
        // que foi montado com o sabor de antes vai como foi montado, e o
        // servidor congela o texto na hora da venda.
        if (!estado.escolha) desenharProdutos();
        return;
      }

      if (evento !== "preco.alterado") return;

      // Não no meio de uma montagem: o `aplicarCardapio` remonta o mapa de
      // opções e redesenha a grade, e a folha de acompanhamentos aberta ficaria
      // apontando pra um produto que não existe mais. O preço novo entra assim
      // que o funcionário fechar a folha, pela sincronia de sempre.
      if (estado.escolha) return;

      baixarCardapio({ silencioso: true });
    },
  });
}

async function baixarCardapio({ silencioso = false } = {}) {
  try {
    const cardapio = await api.pedir("GET", "/cardapio");
    localStorage.setItem(CHAVE_CARDAPIO, JSON.stringify(cardapio));
    aplicarCardapio(cardapio);
    marcarOnline(true);
    if (!silencioso) aviso("Cardápio atualizado", "ok");
  } catch (erro) {
    const rede = erro instanceof api.ErroRede;
    if (rede) marcarOnline(false);

    // Primeiro acesso do aparelho sem internet: não há cache pra cair de volta,
    // e a tela ficaria em "Carregando…" pra sempre sem explicar nada.
    if (!estado.cardapio) {
      $("produtos").innerHTML =
        `<p class="vazio">${rede
          ? "Sem conexão e sem cardápio salvo neste aparelho.<br>Conecte à internet uma vez pra começar a vender."
          : `Não deu pra carregar o cardápio: ${escapar(erro.message)}`}</p>`;
      return;
    }

    if (!silencioso) {
      aviso(rede ? "Sem conexão — usando o cardápio salvo" : `Cardápio: ${erro.message}`, "erro");
    }
  }
}

function gravarSaboresLocais(sabores) {
  try {
    localStorage.setItem(CHAVE_SABORES, JSON.stringify(sabores));
  } catch {
    // Armazenamento cheio ou bloqueado. O sabor da sessão continua em memória.
  }
}

function lerSaboresLocais() {
  try {
    const bruto = localStorage.getItem(CHAVE_SABORES);
    return bruto ? JSON.parse(bruto) : null;
  } catch {
    return null;
  }
}

function lerCardapioLocal() {
  try {
    const bruto = localStorage.getItem(CHAVE_CARDAPIO);
    return bruto ? JSON.parse(bruto) : null;
  } catch {
    return null;
  }
}

function aplicarCardapio(cardapio) {
  estado.cardapio = cardapio;

  const existe = cardapio.categorias.some((c) => c.id === estado.categoriaAtiva);
  if (!existe) estado.categoriaAtiva = cardapio.categorias[0]?.id ?? null;

  estado.opcoes = new Map();
  for (const produto of produtosTodos()) {
    for (const grupo of produto.grupos ?? []) {
      for (const opcao of grupo.opcoes) estado.opcoes.set(opcao.id, opcao);
    }
  }

  // Produto (ou acompanhamento) que saiu do cardápio não pode continuar no
  // carrinho: o servidor recusaria a opção inexistente e a venda morreria no
  // ENVIAR, com o cliente na frente.
  const validos = new Set(produtosTodos().map((p) => p.id));
  for (const [chave, linha] of estado.carrinho) {
    const some =
      !validos.has(linha.produto_id) || linha.opcoes.some((id) => !estado.opcoes.has(id));
    if (some) estado.carrinho.delete(chave);
  }

  desenharCategorias();
  desenharProdutos();
  atualizarCarrinho();

  $("painel-versao").textContent = cardapio.versao
    ? `Cardápio de ${new Date(cardapio.versao).toLocaleString("pt-BR")}`
    : "Cardápio sem data";
}

function produtosTodos() {
  return (estado.cardapio?.categorias ?? []).flatMap((c) => c.produtos);
}

function produtoPorId(id) {
  return produtosTodos().find((p) => p.id === id) ?? null;
}

function desenharCategorias() {
  const nav = $("categorias");
  nav.innerHTML = "";

  for (const categoria of estado.cardapio.categorias) {
    const botao = document.createElement("button");
    botao.textContent = categoria.nome;
    botao.setAttribute("aria-current", categoria.id === estado.categoriaAtiva);
    botao.onclick = () => {
      estado.categoriaAtiva = categoria.id;
      desenharCategorias();
      desenharProdutos();
      $("produtos").scrollTop = 0;
    };
    nav.append(botao);
  }
}

function desenharProdutos() {
  const area = $("produtos");
  area.innerHTML = "";

  const categoria = estado.cardapio.categorias.find((c) => c.id === estado.categoriaAtiva);
  const produtos = categoria?.produtos ?? [];

  if (!produtos.length) {
    area.innerHTML = '<p class="vazio">Nenhum produto nesta categoria.</p>';
    return;
  }

  for (const produto of produtos) {
    const botao = document.createElement("button");
    botao.className = "produto";
    if (produto.cor_botao) botao.style.borderLeftColor = produto.cor_botao;

    const quantidade = quantidadeDoProduto(produto.id);
    const escolhas = (produto.grupos ?? []).length;
    const marca = escolhas
      ? "+ escolhas"
      : pedeSabor(produto)
        ? "escolher sabor"
        : produto.sabor_fixo
          ? `🍦 ${produto.sabor_fixo}`
          : "";
    botao.innerHTML =
      `<span class="produto__nome">${escapar(produto.nome)}</span>` +
      `<span class="produto__preco">` +
      // O valor num elemento próprio, e não solto no flex: como nó de texto
      // anônimo ele quebrava entre o "R$" e o número num aparelho estreito, e
      // preço partido em duas linhas é o número que o funcionário confere na
      // frente do cliente.
      `<span class="produto__valor">R$ ${valor(produto.preco_centavos)}</span>` +
      (marca ? `<span class="produto__marca">${marca}</span>` : "") +
      `</span>` +
      (quantidade ? `<span class="produto__qtd">${quantidade}</span>` : "");

    // Produto que pede sabor abre a folha mesmo sem grupo de opção nenhum: a
    // casquinha não tem acompanhamento, mas precisa saber qual bola vai nela.
    botao.onclick = () =>
      escolhas || pedeSabor(produto)
        ? abrirEscolhas(produto)
        : adicionarLinha(produto.id, []);
    area.append(botao);
  }
}

// ================================================================== carrinho

/**
 * Identidade de uma linha do carrinho: produto + acompanhamentos + sabor.
 *
 * O sabor entra na chave porque duas casquinhas de sabores diferentes são
 * duas linhas. Somadas em "2x Casquinha", a cozinha serviria as duas iguais e
 * o cliente levaria o sabor errado.
 */
function chaveDe(produtoId, opcoes, sabor = null) {
  return `${produtoId}|${[...opcoes].sort((a, b) => a - b).join(",")}|${sabor ?? ""}`;
}

function adicionarLinha(produtoId, opcoes, quantidade = 1, sabor = null) {
  const chave = chaveDe(produtoId, opcoes, sabor);
  const linha = estado.carrinho.get(chave);

  if (linha) {
    linha.quantidade = Math.min(99, linha.quantidade + quantidade);
  } else {
    estado.carrinho.set(chave, {
      produto_id: produtoId,
      opcoes: [...opcoes].sort((a, b) => a - b),
      quantidade: Math.min(99, quantidade),
      sabor,
    });
  }

  vibrar(12);
  desenharProdutos();
  atualizarCarrinho();
}

function mudarQuantidade(chave, delta) {
  const linha = estado.carrinho.get(chave);
  if (!linha) return;

  linha.quantidade = Math.min(99, linha.quantidade + delta);
  if (linha.quantidade <= 0) estado.carrinho.delete(chave);

  vibrar(12);
  desenharProdutos();
  atualizarCarrinho();
}

/** Quantas unidades deste produto estão no carrinho, somando as variações. */
function quantidadeDoProduto(produtoId) {
  let total = 0;
  for (const linha of estado.carrinho.values()) {
    if (linha.produto_id === produtoId) total += linha.quantidade;
  }
  return total;
}

function opcoesDe(ids) {
  return ids.map((id) => estado.opcoes.get(id)).filter(Boolean);
}

/** Preço de uma unidade: o produto mais os adicionais pagos. */
function precoUnitario(produtoId, opcoes) {
  const base = produtoPorId(produtoId)?.preco_centavos ?? 0;
  return base + opcoesDe(opcoes).reduce((soma, o) => soma + o.preco_extra_centavos, 0);
}

function totalCarrinho() {
  let total = 0;
  for (const linha of estado.carrinho.values()) {
    total += precoUnitario(linha.produto_id, linha.opcoes) * linha.quantidade;
  }
  return total;
}

function atualizarCarrinho() {
  const lista = $("carrinho-itens");
  const total = totalCarrinho();
  const pecas = [...estado.carrinho.values()].reduce((a, l) => a + l.quantidade, 0);

  lista.innerHTML = "";
  for (const [chave, linha] of estado.carrinho) {
    const produto = produtoPorId(linha.produto_id);
    if (!produto) continue;

    const unitario = precoUnitario(linha.produto_id, linha.opcoes);
    const escolhidas = opcoesDe(linha.opcoes);

    const li = document.createElement("li");
    li.className = "item";
    // O sabor numa linha própria e acima dos acompanhamentos: é o que o
    // funcionário confere em voz alta com o cliente antes de finalizar.
    const sabor = saborDaLinha(linha, produto);

    li.innerHTML =
      `<span class="item__nome">${escapar(produto.nome)}` +
      (sabor ? `<small class="item__sabor">🍦 ${escapar(sabor)}</small>` : "") +
      (escolhidas.length
        ? `<small class="item__opcoes">${escapar(
            escolhidas.map((o) => o.nome).join(" · "),
          )}</small>`
        : "") +
      `<small>R$ ${valor(unitario)} cada</small></span>` +
      `<span class="item__contador">` +
      `<button data-menos aria-label="Tirar um">−</button>` +
      `<span class="item__qtd">${linha.quantidade}</span>` +
      `<button data-mais aria-label="Pôr mais um">+</button>` +
      `</span>` +
      `<span class="item__subtotal">${reais(unitario * linha.quantidade)}</span>`;

    li.querySelector("[data-menos]").onclick = () => mudarQuantidade(chave, -1);
    li.querySelector("[data-mais]").onclick = () => mudarQuantidade(chave, +1);
    lista.append(li);
  }

  $("total").textContent = reais(total);
  $("carrinho-resumo").textContent = pecas
    ? `${plural(pecas, "item", "itens")} · ${reais(total)}`
    : "Carrinho vazio";
  $("btn-enviar").disabled = pecas === 0;
  $("btn-limpar").disabled = pecas === 0;
}

function limparCarrinho() {
  estado.carrinho.clear();
  $("obs").value = "";
  $("obs").hidden = true;
  $("btn-obs").hidden = false;
  desenharProdutos();
  atualizarCarrinho();
}

// =========================================================== acompanhamentos

/**
 * A folha que abre ao tocar num açaí, sundae ou cestinha.
 *
 * A cota vem do cardápio (`max_escolhas`) e é respeitada aqui só pra não deixar
 * o funcionário errar — quem valida de verdade é o servidor. Adicional pago
 * (grupo sem teto) aparece com o preço no próprio botão: o cliente pergunta
 * "quanto fica com a geléia?" e a resposta está na tela.
 */
function abrirEscolhas(produto) {
  estado.escolha = { produto, selecionadas: new Set(), sabor: null };

  $("escolhas-nome").textContent = produto.nome;
  $("escolhas-base").textContent = `R$ ${valor(produto.preco_centavos)}`;
  $("escolhas").hidden = false;

  desenharEscolhas();
}

function fecharEscolhas() {
  $("escolhas").hidden = true;
  estado.escolha = null;
}

function desenharEscolhas() {
  const { produto, selecionadas } = estado.escolha;
  const area = $("escolhas-grupos");
  area.innerHTML = "";

  // O sabor vem primeiro na folha: é ele que define o que o cliente pediu, e
  // os acompanhamentos são o acabamento por cima. A mesma ordem que a borda do
  // trufado já segue no cardápio.
  if (pedeSabor(produto)) area.append(blocoDeSabor());

  /**
   * O primeiro grupo obrigatório que ainda não foi atendido, ou null.
   *
   * É o nome dele, e não um booleano, porque o botão travado precisa dizer o
   * que falta: a folha do trufado tem dois grupos e rola, então "ADICIONAR
   * apagado" sozinho manda o funcionário procurar o que está errado com o
   * cliente esperando.
   */
  let pendente = pedeSabor(produto) && estado.escolha.sabor === null ? "SABOR" : null;

  for (const grupo of produto.grupos) {
    const marcadas = grupo.opcoes.filter((o) => selecionadas.has(o.id)).length;
    const cheio = grupo.max_escolhas !== null && marcadas >= grupo.max_escolhas;
    if (pendente === null && marcadas < grupo.min_escolhas) pendente = grupo.nome;

    const bloco = document.createElement("section");
    bloco.className = "grupo";
    bloco.innerHTML =
      `<p class="grupo__titulo">${escapar(grupo.nome)}` +
      `<span class="grupo__cota" data-obrigatorio="${marcadas < grupo.min_escolhas ? 1 : 0}">` +
      `${escapar(textoCota(grupo, marcadas))}</span></p>` +
      `<div class="grupo__opcoes"></div>`;

    const opcoes = bloco.querySelector(".grupo__opcoes");
    for (const opcao of grupo.opcoes) {
      const marcada = selecionadas.has(opcao.id);
      const botao = document.createElement("button");
      botao.className = "opcao";
      botao.dataset.marcada = marcada ? "1" : "0";
      // Cota estourada: as não marcadas apagam, mas continuam clicáveis pra
      // explicar o porquê em vez de simplesmente não responder ao toque.
      botao.dataset.bloqueada = !marcada && cheio ? "1" : "0";
      botao.innerHTML =
        `<span>${escapar(opcao.nome)}</span>` +
        (opcao.preco_extra_centavos
          ? `<span class="opcao__extra">+${valor(opcao.preco_extra_centavos)}</span>`
          : "");

      botao.onclick = () => alternarOpcao(grupo, opcao);
      opcoes.append(botao);
    }

    area.append(bloco);
  }

  const total = produto.preco_centavos +
    [...selecionadas].reduce((soma, id) => soma + (estado.opcoes.get(id)?.preco_extra_centavos ?? 0), 0);

  $("escolhas-total").textContent = reais(total);
  $("escolhas-add").disabled = pendente !== null;
  $("escolhas-rotulo").textContent = pendente ? `FALTA: ${pendente}` : "ADICIONAR";
}

function textoCota(grupo, marcadas) {
  if (grupo.max_escolhas === null) {
    return marcadas ? `${marcadas} adicional${marcadas > 1 ? "is" : ""}` : "opcional, cobrado à parte";
  }
  // Grupo obrigatório ainda em aberto: o `0/1` seco não diz que é obrigatório,
  // e é justamente ele que está segurando o botão lá embaixo.
  if (marcadas < grupo.min_escolhas) {
    return `escolha ${grupo.min_escolhas}`;
  }
  return `${marcadas}/${grupo.max_escolhas}`;
}

function alternarOpcao(grupo, opcao) {
  const { selecionadas } = estado.escolha;

  if (selecionadas.has(opcao.id)) {
    selecionadas.delete(opcao.id);
  } else {
    const marcadas = grupo.opcoes.filter((o) => selecionadas.has(o.id)).length;
    if (grupo.max_escolhas !== null && marcadas >= grupo.max_escolhas) {
      aviso(`${grupo.nome}: só ${grupo.max_escolhas} — tire uma pra trocar`, "erro");
      vibrar([30, 40, 30]);
      return;
    }
    selecionadas.add(opcao.id);
  }

  vibrar(10);
  desenharEscolhas();
}

function confirmarEscolhas() {
  const { produto, selecionadas, sabor } = estado.escolha;
  adicionarLinha(produto.id, [...selecionadas], 1, sabor);
  fecharEscolhas();
}

// ================================================================ sabor do dia

/** Há sabor definido pela loja hoje? Sem isso, ninguém pergunta nada. */
function temSabor() {
  return Boolean(estado.sabores.sabor1 || estado.sabores.sabor2);
}

/**
 * Este produto pergunta o sabor agora?
 *
 * As duas condições juntas, e não só a marca do produto: numa manhã em que o
 * dono ainda não preencheu os sabores, perguntar abriria uma folha com três
 * botões sem nome. A venda simplesmente segue como seguia antes desta
 * funcionalidade existir.
 */
function pedeSabor(produto) {
  // Sabor fixo não se pergunta: é receita da casa, e a resposta nunca muda.
  if (produto?.sabor_fixo) return false;
  return Boolean(produto?.pede_sabor) && temSabor();
}

/** O sabor desta linha do carrinho: o fixo do produto, ou o que foi escolhido. */
function saborDaLinha(linha, produto) {
  return produto?.sabor_fixo || textoDoSabor(linha.sabor);
}

/** O texto de uma escolha, pro carrinho e pra comanda. */
function textoDoSabor(sabor) {
  const { sabor1, sabor2 } = estado.sabores;
  if (sabor === "SABOR_1") return sabor1;
  if (sabor === "SABOR_2") return sabor2;
  if (sabor === "MISTO") {
    return sabor1 && sabor2 ? `${sabor1} + ${sabor2}` : sabor1 || sabor2;
  }
  return null;
}

function blocoDeSabor() {
  const { sabor1, sabor2 } = estado.sabores;
  const escolhido = estado.escolha.sabor;

  // Só entram os que existem: com um sabor só na máquina, "Misto" não é
  // opção — e um botão que não faz o que promete é pior que botão nenhum.
  const opcoes = [];
  if (sabor1) opcoes.push(["SABOR_1", sabor1]);
  if (sabor2) opcoes.push(["SABOR_2", sabor2]);
  if (sabor1 && sabor2) opcoes.push(["MISTO", "Misto (os dois)"]);

  const bloco = document.createElement("section");
  bloco.className = "grupo";
  bloco.innerHTML =
    `<p class="grupo__titulo">Sabor` +
    `<span class="grupo__cota" data-obrigatorio="${escolhido === null ? 1 : 0}">` +
    `${escolhido === null ? "escolha 1" : "1/1"}</span></p>` +
    `<div class="grupo__opcoes"></div>`;

  const area = bloco.querySelector(".grupo__opcoes");
  for (const [valorSabor, rotulo] of opcoes) {
    const botao = document.createElement("button");
    botao.className = "opcao";
    botao.dataset.marcada = escolhido === valorSabor ? "1" : "0";
    botao.dataset.bloqueada = "0";
    botao.innerHTML = `<span>${escapar(rotulo)}</span>`;
    botao.onclick = () => {
      // Tocar de novo no que já está marcado desmarca. Sem isso, quem erra o
      // sabor tem que fechar a folha e recomeçar com o cliente esperando.
      estado.escolha.sabor = escolhido === valorSabor ? null : valorSabor;
      vibrar(10);
      desenharEscolhas();
    };
    area.append(botao);
  }
  return bloco;
}

async function carregarSabores() {
  try {
    estado.sabores = await api.pedir("GET", "/sabores");
    gravarSaboresLocais(estado.sabores);
    desenharProdutos();
  } catch {
    // Offline: vale o que ficou no aparelho da última vez. O balcão continua
    // vendendo, que é a regra desta tela inteira.
  }
}

// ==================================================================== envio

async function enviar() {
  if (!estado.carrinho.size) return;

  const sessao = api.sessaoAtual();
  if (!sessao) {
    mostrarLogin();
    return;
  }

  const itens = [...estado.carrinho.values()].map((linha) => {
    const produto = produtoPorId(linha.produto_id);
    return {
      produto_id: linha.produto_id,
      quantidade: linha.quantidade,
      opcoes: linha.opcoes,
      // Só o tipo vai pro servidor. O nome do sabor é resolvido lá, pelo que
      // valia no instante da venda — mandar o texto daqui deixaria um aparelho
      // com cache velho gravar "Chocolate" num dia de creme.
      sabor: linha.sabor,
      // Guardados só pra mostrar na lista de recentes quando o pedido ainda
      // não subiu. Quem manda no preço é sempre o servidor.
      nome: produto?.nome ?? `#${linha.produto_id}`,
      // O texto do sabor entra aqui pelo mesmo motivo que o nome do produto:
      // a comanda é impressa no celular, antes de o pedido subir.
      sabor_texto: saborDaLinha(linha, produto),
      opcoes_nomes: opcoesDe(linha.opcoes).map((o) => o.nome),
      preco_unit_centavos: precoUnitario(linha.produto_id, linha.opcoes),
    };
  });

  const registro = await fila.enfileirar({
    itens,
    observacao: $("obs").value.trim() || null,
    total_centavos: totalCarrinho(),
    usuario_id: sessao.usuario_id,
    usuario_nome: sessao.nome,
  });

  // O carrinho limpa antes de qualquer ida ao servidor: a próxima venda já
  // pode começar enquanto esta sobe.
  limparCarrinho();
  fecharCarrinho();
  vibrar([18, 40, 18]);
  aviso("Pedido na fila…");

  await enviarUm(registro, { avisar: true });
  await atualizarFaixaFila();
}

/**
 * Sobe um pedido da fila. Devolve `true` se subiu (ou se foi recusado de vez —
 * nos dois casos ele saiu da fila).
 */
async function enviarUm(registro, { avisar = false } = {}) {
  try {
    const resposta = await api.pedir("POST", "/pedidos", {
      id_cliente: registro.id_cliente,
      criado_em_cliente: registro.criado_em_cliente,
      itens: registro.itens.map(({ produto_id, quantidade, opcoes, sabor }) => ({
        produto_id,
        quantidade,
        // Pedido antigo, enfileirado antes de o app ter acompanhamentos: sem
        // isto o `.map` mandaria `undefined` e o servidor recusaria uma venda
        // que já estava paga.
        opcoes: opcoes ?? [],
        // Este `sabor` já esqueceu de ser copiado uma vez. Como a lista é
        // remontada campo a campo, um campo novo que não seja acrescentado
        // aqui é silenciosamente descartado: a venda sobe, a comanda sai, e só
        // falta o sabor no papel. Campo novo no item entra aqui também.
        sabor: sabor ?? null,
      })),
      observacao: registro.observacao,
      total_centavos: registro.total_centavos,
    });

    await fila.marcarEnviado(registro.id_cliente, resposta);
    marcarOnline(true);

    if (avisar) aviso(`Pedido #${resposta.numero_dia} enviado`, "ok");

    // O papel sai agora. Este é o primeiro instante em que existe `numero_dia`,
    // e é ele que casa a comanda com o pedido do balcão.
    //
    // `duplicado` fica de fora: é o reenvio do mesmo `id_cliente` devolvendo o
    // pedido que já existe, e imprimir de novo desfaria exatamente o que a
    // idempotência do servidor está lá pra evitar.
    if (!resposta.duplicado) await impressora.imprimir(resposta);

    // O celular calculou um total diferente do servidor: o cache do cardápio
    // está velho. O pedido vale — mas o preço na tela precisa ser corrigido
    // antes da próxima venda.
    if (resposta.total_divergente) {
      aviso("Preço mudou — cardápio atualizado", "erro");
      baixarCardapio({ silencioso: true });
    }
    return true;
  } catch (erro) {
    if (erro instanceof api.ErroRede) {
      marcarOnline(false);
      await fila.registrarFalha(registro.id_cliente, "sem conexão");
      if (avisar) aviso("Sem conexão — pedido guardado, sobe sozinho");
      return false;
    }

    // 422 é regra de negócio: produto que não existe mais, relógio do celular
    // fora da janela. Reenviar dá o mesmo erro — sai da fila e o funcionário
    // precisa saber agora, não no fechamento.
    if (erro instanceof api.ErroApi && erro.status === 422) {
      await fila.marcarRecusado(registro.id_cliente, erro.message);
      mostrarErro(`Pedido recusado: ${erro.message}`);
      return true;
    }

    if (erro instanceof api.ErroApi && erro.status === 401) {
      return false; // a renovação já falhou; aoPerderSessao cuida da tela
    }

    // 5xx e o resto: o servidor pode estar só engasgado. Continua na fila.
    await fila.registrarFalha(registro.id_cliente, erro.message);
    if (avisar) aviso(`Guardado na fila: ${erro.message}`, "erro");
    return false;
  }
}

/** Sobe tudo que está pendente, na ordem em que foi vendido. */
async function sincronizar() {
  if (estado.sincronizando || !api.estaLogado()) return;

  estado.sincronizando = true;
  try {
    const sessao = api.sessaoAtual();
    const todos = await fila.pendentes();

    // Só sobem os pedidos de quem está logado agora. O pedido carrega o nome
    // de quem vendeu, e o servidor atribui pelo token: mandar o pedido do João
    // com o token da Maria faria o relatório mentir sobre quem vendeu.
    const meus = todos.filter((r) => r.usuario_id === sessao.usuario_id);

    for (const registro of meus) {
      const seguiu = await enviarUm(registro);
      if (!seguiu) break; // rede fora: parar aqui preserva a ordem da fila
    }
  } finally {
    estado.sincronizando = false;
    await atualizarFaixaFila();
    if (!$("painel-recentes").hidden) desenharRecentes();
  }
}

async function atualizarFaixaFila() {
  const faixa = $("faixa-fila");
  const pendentes = await fila.pendentes();

  if (!pendentes.length) {
    faixa.hidden = true;
    return;
  }

  const sessao = api.sessaoAtual();
  const meus = pendentes.filter((r) => r.usuario_id === sessao?.usuario_id);
  const alheios = pendentes.filter((r) => r.usuario_id !== sessao?.usuario_id);

  const partes = [];
  if (meus.length) {
    partes.push(`${plural(meus.length, "pedido aguardando", "pedidos aguardando")} envio`);
  }
  if (alheios.length) {
    const nomes = [...new Set(alheios.map((r) => r.usuario_nome))].join(", ");
    partes.push(`${alheios.length} de ${nomes} — entre com esse usuário pra enviar`);
  }

  faixa.textContent = `⏳ ${partes.join(" · ")}`;
  faixa.hidden = false;
}

/**
 * A faixa de "comanda sem papel".
 *
 * Separada da fila de envio de propósito: ali a venda ainda não chegou no
 * caixa; aqui ela já chegou e o que faltou foi o papel. São dois problemas
 * diferentes, com duas saídas diferentes — e uma faixa só, somando os dois,
 * faria o funcionário conferir o lugar errado.
 */
function atualizarFaixaImpressao(pendentes, ultimoErro) {
  const faixa = $("faixa-impressao");

  faixa.hidden = pendentes === 0;
  if (pendentes) {
    faixa.textContent =
      `🖨 ${plural(pendentes, "comanda não saiu", "comandas não saíram")}` +
      `${ultimoErro ? ` (${ultimoErro})` : ""} — toque pra tentar de novo`;
  }

  $("btn-imprimir-fila").hidden = pendentes === 0;
  $("btn-descartar-impressao").hidden = pendentes === 0;
  if (!$("painel-usuario").hidden) descreverImpressora();
}

function descreverImpressora() {
  const pendentes = impressora.pendentes();
  $("painel-impressora").textContent = impressora.disponivel()
    ? `Impressão: RawBT neste aparelho${pendentes ? ` · ${pendentes} na fila` : ""}`
    : "Impressão: indisponível — o RawBT só funciona no Android";
}

/** Tenta despachar o que ficou pra trás. Nunca levanta: é botão de balcão. */
async function tentarImprimirPendentes() {
  aviso("Mandando pra impressora…");
  await impressora.retomar();
  if (impressora.pendentes() === 0) aviso("Comandas impressas", "ok");
}

function mostrarErro(texto) {
  const faixa = $("faixa-erro");
  faixa.textContent = `⚠ ${texto}`;
  faixa.hidden = false;
  setTimeout(() => (faixa.hidden = true), 15000);
}

// =================================================================== painéis

function abrirPainel(qual) {
  $("painel").hidden = false;
  $("painel-usuario").hidden = qual !== "usuario";
  $("painel-recentes").hidden = qual !== "recentes";

  if (qual === "usuario") {
    const sessao = api.sessaoAtual();
    $("painel-usuario-nome").textContent = `${sessao?.nome ?? ""} · ${sessao?.papel ?? ""}`;
    descreverImpressora();
  } else {
    desenharRecentes();
  }
}

function fecharPainel() {
  $("painel").hidden = true;
  estado.saidaConfirmada = false;
}

async function desenharRecentes() {
  const lista = $("lista-recentes");
  const registros = await fila.recentes(25);

  lista.innerHTML = "";
  if (!registros.length) {
    lista.innerHTML = '<li class="fraco">Nenhum pedido ainda hoje.</li>';
    return;
  }

  for (const registro of registros) {
    const li = document.createElement("li");
    const numero = registro.numero_dia ? `#${registro.numero_dia}` : "—";
    li.innerHTML =
      `<span class="num">${numero}</span>` +
      `<span class="quando">${hora(registro.criado_em_cliente)} · ` +
      `${reais(registro.total_servidor ?? registro.total_centavos)}</span>` +
      `<span class="selo" data-status="${registro.status}">${registro.status}</span>` +
      // Só o que já subiu tem `pedido`: comanda precisa de `numero_dia`, e
      // quem numera é o servidor.
      // Texto e não ícone: o glifo de impressora (U+2399) não existe em toda
      // fonte de Android, e um quadradinho vazio no lugar do botão é um botão
      // que ninguém aperta.
      (registro.pedido && registro.status !== "CANCELADO"
        ? '<button class="recentes__via" data-via>2ª via</button>'
        : "") +
      // Venda já cancelada não tem o que excluir de novo.
      (registro.status === "CANCELADO"
        ? ""
        : '<button class="recentes__x" data-excluir aria-label="Excluir esta venda">✕</button>');

    if (registro.status === "RECUSADO" && registro.erro) {
      li.title = registro.erro;
    }
    if (registro.status === "CANCELADO" && registro.motivo_cancelamento) {
      li.title = registro.motivo_cancelamento;
    }
    li.querySelector("[data-via]")?.addEventListener("click", () => reimprimir(registro));
    li.querySelector("[data-excluir]")?.addEventListener("click", () => abrirExcluir(registro));
    lista.append(li);
  }
}

/**
 * Outra via de um pedido que já subiu.
 *
 * Sai marcada como REIMPRESSÃO: papel repetido sem aviso é pedido montado duas
 * vezes. E é reconstruída do `PedidoSaida` guardado na fila — o mesmo objeto
 * que gerou a primeira via, com os preços da hora da venda.
 */
async function reimprimir(registro) {
  vibrar(12);
  aviso(`Reimprimindo #${registro.numero_dia}…`);
  await impressora.imprimir(registro.pedido, { reimpressao: true });
}

// ================================================================== excluir

/**
 * Desfazer uma venda.
 *
 * São dois caminhos, e a diferença importa: o pedido que **já subiu** vira um
 * cancelamento no servidor — com motivo, autor e o valor saindo do faturamento
 * —, enquanto o que ainda não subiu é só apagado daqui, porque nunca chegou a
 * existir pra ninguém além deste aparelho.
 *
 * A folha existe em vez de um "toque duas vezes" porque aqui o toque errado
 * não é reversível: cancelamento não se desfaz, e o dono vai ler o motivo no
 * fim do dia pra entender por que o total não bate com a gaveta.
 */
function abrirExcluir(registro) {
  estado.excluindo = registro;
  estado.motivoEscolhido = null;

  const numero = registro.numero_dia ? `o pedido #${registro.numero_dia}` : "este pedido";
  $("excluir-numero").textContent = numero;
  $("excluir-resumo").textContent =
    `${hora(registro.criado_em_cliente)} · ` +
    `${reais(registro.total_servidor ?? registro.total_centavos)}` +
    (registro.status === "ENVIADO" ? "" : " · ainda não subiu pro servidor");

  $("excluir-outro").value = "";
  for (const botao of document.querySelectorAll(".excluir__motivo")) {
    botao.dataset.marcado = "0";
  }
  $("excluir-confirmar").disabled = true;
  $("excluir").hidden = false;
}

function fecharExcluir() {
  $("excluir").hidden = true;
  estado.excluindo = null;
  estado.motivoEscolhido = null;
}

function escolherMotivo(botao) {
  estado.motivoEscolhido = botao.dataset.motivo;
  for (const outro of document.querySelectorAll(".excluir__motivo")) {
    outro.dataset.marcado = outro === botao ? "1" : "0";
  }
  $("excluir-confirmar").disabled = false;
  vibrar(10);
}

/** O motivo que vai pro servidor: o botão marcado, mais o texto livre. */
function motivoFinal() {
  const extra = $("excluir-outro").value.trim();
  if (!estado.motivoEscolhido) return extra;
  return extra ? `${estado.motivoEscolhido} — ${extra}` : estado.motivoEscolhido;
}

async function confirmarExclusao() {
  const registro = estado.excluindo;
  if (!registro) return;

  const motivo = motivoFinal();
  const botao = $("excluir-confirmar");
  botao.disabled = true;
  botao.textContent = "EXCLUINDO…";

  try {
    if (registro.status === "ENVIADO" && registro.id_servidor) {
      await api.pedir("POST", `/pedidos/${registro.id_servidor}/cancelar`, { motivo });
      await fila.marcarCancelado(registro.id_cliente, motivo);
      aviso(`Pedido #${registro.numero_dia} excluído — valor fora do caixa`, "ok");
    } else {
      // Nunca chegou no servidor: não há caixa de onde tirar, e guardar um
      // registro cancelado de uma venda que ninguém viu só ocuparia a lista.
      await fila.descartar(registro.id_cliente);
      aviso("Pedido apagado", "ok");
    }

    fecharExcluir();
    await desenharRecentes();
    await atualizarFaixaFila();
  } catch (erro) {
    aviso(
      erro instanceof api.ErroRede
        ? "Sem conexão — o pedido continua valendo. Tente de novo."
        : erro.message,
      "erro",
    );
  } finally {
    botao.disabled = false;
    botao.textContent = "EXCLUIR A VENDA";
  }
}

async function sair() {
  const pendentes = await fila.contarPendentes();

  // Sair com venda na fila é perder a venda: o próximo usuário não consegue
  // subir o pedido de quem saiu. Avisa uma vez; se insistir, deixa sair.
  if (pendentes > 0 && !estado.saidaConfirmada) {
    estado.saidaConfirmada = true;
    aviso(`${plural(pendentes, "pedido ainda não subiu", "pedidos ainda não subiram")} — aperte de novo pra sair mesmo assim`, "erro");
    return;
  }

  // Antes do `api.sair()`: o socket ainda vai tentar reconectar com o token
  // que está prestes a ser apagado, e ficaria batendo na porta à toa.
  estado.socket?.fechar();
  estado.socket = null;

  await api.sair();
  estado.carrinho.clear();
  estado.saidaConfirmada = false;
  fecharPainel();
  mostrarLogin();
}

/**
 * O dono pausou ou excluiu esta conta enquanto o aparelho estava logado.
 *
 * Diferente do `sair()` normal, não pergunta nada: quem perdeu o acesso não
 * decide mais se sai com venda pendente na fila — ela fica salva no aparelho
 * e sobe sozinha quando alguém logar de novo aqui.
 */
async function forcarSaida(motivo) {
  estado.socket?.fechar();
  estado.socket = null;

  await api.sair();
  estado.carrinho.clear();
  estado.saidaConfirmada = false;
  fecharPainel();
  mostrarLogin();
  aviso(motivo, "erro");
}

// ==================================================================== eventos

function ligarEventos() {
  // --- login
  // `submit` e não o clique do botão: é o que faz o "ir" do teclado do celular
  // entrar, em vez de o funcionário ter que fechar o teclado pra achar o botão.
  $("login-form").addEventListener("submit", (e) => {
    e.preventDefault();
    entrar();
  });
  $("cadastro-form").addEventListener("submit", (e) => {
    e.preventDefault();
    criarConta();
  });
  $("link-cadastro").onclick = mostrarCadastro;
  $("cadastro-voltar").onclick = mostrarLogin;
  $("link-login").onclick = mostrarLogin;

  // --- venda
  $("carrinho-alca").onclick = () => {
    const carrinho = $("carrinho");
    carrinho.dataset.aberto = carrinho.dataset.aberto === "1" ? "0" : "1";
  };
  $("btn-enviar").onclick = enviar;
  $("btn-limpar").onclick = limparCarrinho;
  $("btn-obs").onclick = () => {
    $("btn-obs").hidden = true;
    $("obs").hidden = false;
    $("obs").focus();
  };

  // --- acompanhamentos
  $("escolhas-add").onclick = confirmarEscolhas;
  $("escolhas-cancelar").onclick = fecharEscolhas;
  for (const alvo of document.querySelectorAll("[data-fechar-escolhas]")) {
    alvo.onclick = fecharEscolhas;
  }

  $("btn-usuario").onclick = () => abrirPainel("usuario");
  $("btn-recentes").onclick = () => abrirPainel("recentes");
  $("btn-sair").onclick = sair;
  $("btn-enviar-fila").onclick = async () => {
    aviso("Enviando fila…");
    await sincronizar();
  };

  // --- impressão
  $("faixa-impressao").onclick = tentarImprimirPendentes;
  $("btn-imprimir-fila").onclick = tentarImprimirPendentes;
  $("btn-descartar-impressao").onclick = () => {
    impressora.descartar();
    aviso("Fila de impressão limpa");
  };

  // --- excluir venda
  for (const botao of document.querySelectorAll(".excluir__motivo")) {
    botao.onclick = () => escolherMotivo(botao);
  }
  // O texto livre sozinho também serve de motivo — o servidor exige 3 letras.
  $("excluir-outro").addEventListener("input", () => {
    $("excluir-confirmar").disabled = motivoFinal().trim().length < 3;
  });
  $("excluir-confirmar").onclick = confirmarExclusao;
  for (const alvo of document.querySelectorAll("[data-fechar-excluir]")) {
    alvo.onclick = fecharExcluir;
  }

  for (const alvo of document.querySelectorAll("[data-fechar]")) {
    alvo.onclick = fecharPainel;
  }

  // --- conexão
  window.addEventListener("online", () => {
    marcarOnline(true);
    sincronizar();
  });
  window.addEventListener("offline", () => marcarOnline(false));

  // Voltar pro app depois de trocar de janela é o momento mais provável de a
  // conexão ter voltado — e é também a única hora em que um Intent volta a
  // funcionar: Android nenhum abre o RawBT a partir de uma aba em segundo
  // plano, então é aqui que a comanda atrasada consegue sair.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    sincronizar();
    impressora.retomar();
  });
}

function fecharCarrinho() {
  $("carrinho").dataset.aberto = "0";
}

function marcarOnline(ligado) {
  estado.online = ligado;
  $("ponto-conexao").dataset.estado = ligado ? "online" : "offline";
}

// ==================================================================== utilidades

let timerAviso = null;

function aviso(texto, tipo = "") {
  const el = $("aviso");
  el.textContent = texto;
  el.dataset.tipo = tipo;
  el.dataset.visivel = "1";

  clearTimeout(timerAviso);
  timerAviso = setTimeout(() => (el.dataset.visivel = "0"), 2600);
}

function vibrar(padrao) {
  navigator.vibrate?.(padrao);
}

function escapar(texto) {
  return String(texto).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

iniciar();
