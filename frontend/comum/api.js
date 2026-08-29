/**
 * Cliente da API do Shalon.
 *
 * Três coisas moram aqui porque os três PWAs precisam das três:
 *
 * 1. A sessão (tokens + quem está logado), guardada no localStorage — o
 *    celular da loja não pode deslogar sozinho no meio do expediente. O
 *    refresh dura um ano e desliza a cada renovação: a senha é digitada uma
 *    vez no aparelho e não é pedida de novo. Quem tira o acesso de alguém é o
 *    dono, pela tela de sessões, e não o relógio.
 * 2. A renovação do token de acesso, que dura 30 minutos. Ela acontece sem o
 *    funcionário perceber, e uma renovação só mesmo que dez chamadas tomem
 *    401 ao mesmo tempo (`renovacaoEmVoo`).
 * 3. A diferença entre "a internet caiu" (`ErroRede`) e "o servidor recusou"
 *    (`ErroApi`). O PWA de vendas trata as duas de formas opostas: a primeira
 *    manda o pedido pra fila, a segunda avisa o funcionário na cara.
 */

// A chave leva o nome do PWA (`/vendas/…` → "vendas") porque os três rodam na
// mesma origem e dividiriam o mesmo localStorage. Sem isto, o dono que instala
// os dois apps no celular dele desloga de um toda vez que entra no outro — e,
// pior, o app de vendas abriria já logado com o token do dono.
const CHAVE_SESSAO = `shalon.sessao.${_app()}`;

function _app() {
  return location.pathname.split("/").filter(Boolean)[0] || "raiz";
}

// Sem isto, o Android trata o localStorage como descartável: com pouco espaço
// livre, o Chrome apaga o site "menos usado" pra abrir lugar — e a sessão de
// um ano vira nada, sem aviso, sem erro, só o login pedindo tudo de novo. Este
// pedido diz ao navegador "não apague isto sozinho". Best-effort: navegador
// que não suporta, ou que recusa, deixa o app funcionando do jeito de sempre.
navigator.storage?.persist?.().catch(() => {});

/** O servidor recusou: 4xx/5xx com resposta. Reenviar não resolve. */
export class ErroApi extends Error {
  constructor(status, mensagem, corpo) {
    super(mensagem);
    this.name = "ErroApi";
    this.status = status;
    this.corpo = corpo;
  }
}

/** Não deu pra falar com o servidor. Reenviar depois costuma resolver. */
export class ErroRede extends Error {
  constructor(causa) {
    super("Sem conexão com o servidor");
    this.name = "ErroRede";
    this.causa = causa;
  }
}

let sessao = _ler();
let renovacaoEmVoo = null;
const ouvintesSessao = new Set();

// ------------------------------------------------------------------ sessão

export function sessaoAtual() {
  return sessao;
}

export function estaLogado() {
  return sessao !== null;
}

/** Avisa quando a sessão cai (refresh expirado ou revogado). */
export function aoPerderSessao(callback) {
  ouvintesSessao.add(callback);
  return () => ouvintesSessao.delete(callback);
}

/**
 * Entra com **nome de usuário** e senha.
 *
 * Não há mais lista de usuários pra escolher: a tela de login mostra dois
 * campos, e a rota que listava quem existe deixou de existir junto com ela.
 */
export async function entrar(usuario, segredo) {
  const tokens = await pedir("POST", "/auth/login", {
    usuario,
    segredo,
    dispositivo: _dispositivo(),
  }, { autenticado: false });

  _gravar(tokens);
  return sessao;
}

/**
 * Cria a conta do funcionário e já entra com ela — mesma ideia do `entrar`,
 * numa tacada só. Só nasce FUNCIONARIO; quem é dono já existe no banco antes
 * do sistema subir.
 */
export async function cadastrar(nome, senha) {
  const tokens = await pedir("POST", "/auth/cadastro", {
    nome,
    senha,
    dispositivo: _dispositivo(),
  }, { autenticado: false });

  _gravar(tokens);
  return sessao;
}

export async function sair() {
  const refresh = sessao?.refresh;
  _gravar(null);
  if (refresh) {
    // Melhor esforço: se o servidor estiver fora, o token local já sumiu — que
    // é o que importa pra quem está entregando o celular pro colega.
    try {
      await pedir("POST", "/auth/sair", { refresh }, { autenticado: false });
    } catch { /* ignora */ }
  }
}

/**
 * Renova o token de acesso agora. Devolve `true` se conseguiu.
 *
 * As chamadas REST renovam sozinhas ao tomar 401, mas o WebSocket não tem 401:
 * o servidor derruba a conexão quando o token vence (ver `app/rotas/ws.py`) e
 * quem reconecta precisa de um token novo na mão antes de tentar. Continua
 * passando pelo mesmo `_renovar`, então segue valendo a trava que impede duas
 * renovações simultâneas.
 */
export function renovarSessao() {
  return _renovar();
}

// ------------------------------------------------------------------ chamadas

/**
 * Faz a chamada e devolve o JSON já convertido.
 *
 * @param {object} [opcoes]
 * @param {boolean} [opcoes.autenticado=true] manda o Bearer
 * @param {boolean} [opcoes.jaRenovou=false]  uso interno, corta o laço de renovação
 */
export async function pedir(metodo, caminho, corpo = null, opcoes = {}) {
  const { autenticado = true, jaRenovou = false } = opcoes;

  if (autenticado && !sessao) {
    throw new ErroApi(401, "Sem sessão");
  }

  const cabecalhos = {};
  if (corpo !== null) cabecalhos["Content-Type"] = "application/json";
  if (autenticado) cabecalhos["Authorization"] = `Bearer ${sessao.acesso}`;

  let resposta;
  try {
    resposta = await fetch(caminho, {
      method: metodo,
      headers: cabecalhos,
      body: corpo === null ? undefined : JSON.stringify(corpo),
    });
  } catch (erro) {
    throw new ErroRede(erro);
  }

  // Token de acesso venceu: renova uma vez e repete a chamada. Se a renovação
  // também falhar, a sessão morreu de verdade e a tela de login volta.
  if (resposta.status === 401 && autenticado && !jaRenovou) {
    if (await _renovar()) {
      return pedir(metodo, caminho, corpo, { ...opcoes, jaRenovou: true });
    }
  }

  if (resposta.status === 204) return null;

  const texto = await resposta.text();
  const dados = texto ? _json(texto) : null;

  if (!resposta.ok) {
    throw new ErroApi(resposta.status, _mensagem(dados, resposta), dados);
  }

  return dados;
}

// ------------------------------------------------------------------ internos

async function _renovar() {
  if (!sessao?.refresh) return false;

  // Dez chamadas tomando 401 juntas fariam dez renovações — e como o refresh
  // é rotativo, nove delas viriam com um token já usado. O servidor leria isso
  // como token roubado e derrubaria todas as sessões do usuário.
  if (renovacaoEmVoo) return renovacaoEmVoo;

  renovacaoEmVoo = (async () => {
    try {
      const tokens = await pedir(
        "POST", "/auth/renovar", { refresh: sessao.refresh }, { autenticado: false },
      );
      _gravar(tokens);
      return true;
    } catch (erro) {
      // Rede fora não é sessão perdida — é só esperar a conexão voltar.
      if (erro instanceof ErroRede) return false;
      _gravar(null);
      for (const ouvinte of ouvintesSessao) ouvinte();
      return false;
    } finally {
      renovacaoEmVoo = null;
    }
  })();

  return renovacaoEmVoo;
}

function _gravar(tokens) {
  if (tokens === null) {
    sessao = null;
    localStorage.removeItem(CHAVE_SESSAO);
    return;
  }
  sessao = {
    acesso: tokens.acesso,
    refresh: tokens.refresh,
    usuario_id: tokens.usuario_id,
    nome: tokens.nome,
    papel: tokens.papel,
  };
  localStorage.setItem(CHAVE_SESSAO, JSON.stringify(sessao));
}

function _ler() {
  try {
    const bruto = localStorage.getItem(CHAVE_SESSAO);
    if (!bruto) return null;
    const dados = JSON.parse(bruto);
    return dados?.acesso && dados?.refresh ? dados : null;
  } catch {
    return null;
  }
}

function _json(texto) {
  try {
    return JSON.parse(texto);
  } catch {
    return { detail: texto };
  }
}

function _mensagem(dados, resposta) {
  const detalhe = dados?.detail;
  if (typeof detalhe === "string") return detalhe;
  // Erro de validação do Pydantic vem como lista de objetos.
  if (Array.isArray(detalhe) && detalhe.length) {
    return detalhe.map((e) => e.msg).join("; ");
  }
  return `Erro ${resposta.status}`;
}

/**
 * O rótulo do aparelho na tela de sessões do dono.
 *
 * Sai do user agent, que é o que o navegador entrega — não é identificação
 * confiável, é só o que permite ao dono olhar a lista e reconhecer "esse é o
 * celular do balcão". Leva junto qual PWA abriu a sessão, porque "Vanusa no
 * app do dono" e "Vanusa no balcão" são coisas diferentes de se ver ali.
 */
function _dispositivo() {
  const ua = navigator.userAgent;
  const modelo = /\(([^)]+)\)/.exec(ua)?.[1]?.split(";").pop()?.trim();
  return `${modelo || "navegador"} · ${_app()}`.slice(0, 120);
}
