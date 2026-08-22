/**
 * Fila de pedidos no IndexedDB.
 *
 * Regra que define este arquivo: **apertar ENVIAR sempre funciona**. O pedido
 * é gravado aqui primeiro, o carrinho limpa na hora, e a subida pro servidor é
 * problema de outro momento. Sem internet o funcionário continua vendendo; a
 * fila sobe sozinha quando a conexão volta.
 *
 * Por que IndexedDB e não localStorage: localStorage é síncrono (trava a tela
 * no meio da venda) e some quando o navegador aperta o cinto de armazenamento.
 * Isto aqui é a única cópia de uma venda que ainda não chegou no servidor.
 *
 * Estados de um registro:
 *   PENDENTE   esperando subir (ou tentando)
 *   ENVIADO    o servidor confirmou, tem numero_dia
 *   RECUSADO   o servidor recusou de vez (422). Não adianta reenviar.
 *   CANCELADO  a venda foi desfeita; o valor saiu do caixa
 */

const BANCO = "shalon-vendas";
const VERSAO = 1;
const LOJA = "pedidos";

/** Quanto tempo um pedido já enviado fica guardado só pra aparecer na lista. */
const GUARDAR_ENVIADOS_MS = 24 * 60 * 60 * 1000;

let conexao = null;

/**
 * A conexão, aberta uma vez e reaproveitada — mas **descartada se falhar**.
 *
 * Guardar a promessa rejeitada seria guardar o defeito: toda chamada seguinte
 * herdaria a mesma falha, pra sempre, mesmo depois de o motivo ter passado. E
 * o motivo passa — outra aba segurando o banco durante uma migração, o
 * navegador negando armazenamento por um instante em aba anônima. O sintoma
 * disso é cruel: a tela abre, fica em "Carregando cardápio…" e não explica
 * nada, porque quem morreu foi a leitura da fila e não o cardápio.
 */
function abrir() {
  if (conexao) return conexao;

  conexao = new Promise((resolve, rejeitar) => {
    const pedido = indexedDB.open(BANCO, VERSAO);

    pedido.onupgradeneeded = () => {
      const db = pedido.result;
      if (!db.objectStoreNames.contains(LOJA)) {
        // A chave é o uuid gerado no celular — o mesmo que garante
        // idempotência no servidor. Reenviar o mesmo pedido não duplica
        // nem aqui nem lá.
        const loja = db.createObjectStore(LOJA, { keyPath: "id_cliente" });
        loja.createIndex("status", "status");
        loja.createIndex("criado_em_cliente", "criado_em_cliente");
      }
    };

    pedido.onsuccess = () => resolve(pedido.result);
    pedido.onerror = () => rejeitar(pedido.error);
    pedido.onblocked = () => rejeitar(new Error("IndexedDB bloqueado por outra aba"));
  });

  // Falhou: esquece esta tentativa pra que a próxima chamada abra de novo.
  conexao.catch(() => {
    conexao = null;
  });

  return conexao;
}

async function transacao(modo, executar) {
  const db = await abrir();
  return new Promise((resolve, rejeitar) => {
    const tx = db.transaction(LOJA, modo);
    let resultado;
    tx.oncomplete = () => resolve(resultado);
    tx.onerror = () => rejeitar(tx.error);
    tx.onabort = () => rejeitar(tx.error ?? new Error("Transação abortada"));
    resultado = executar(tx.objectStore(LOJA));
  });
}

function promessa(requisicao) {
  return new Promise((resolve, rejeitar) => {
    requisicao.onsuccess = () => resolve(requisicao.result);
    requisicao.onerror = () => rejeitar(requisicao.error);
  });
}

// --------------------------------------------------------------- escrita

/**
 * Põe um pedido novo na fila.
 *
 * @param {object} pedido  itens, observacao, total_centavos, usuario_id, usuario_nome
 * @returns {Promise<object>} o registro gravado
 */
export async function enfileirar(pedido) {
  const registro = {
    id_cliente: novoUuid(),
    // Carimbado aqui, não no servidor: este é o horário em que a venda
    // aconteceu de verdade, mesmo que suba três horas depois.
    criado_em_cliente: new Date().toISOString(),
    status: "PENDENTE",
    tentativas: 0,
    erro: null,
    numero_dia: null,
    ...pedido,
  };

  await transacao("readwrite", (loja) => loja.add(registro));
  return registro;
}

export async function marcarEnviado(idCliente, resposta) {
  return _atualizar(idCliente, (registro) => ({
    ...registro,
    status: "ENVIADO",
    numero_dia: resposta.numero_dia,
    id_servidor: resposta.id,
    total_servidor: resposta.total_centavos,
    // O `PedidoSaida` inteiro, do jeito que o servidor devolveu. É dele que sai
    // a segunda via da comanda: reconstruir o cupom a partir do carrinho local
    // imprimiria os preços que o celular calculou, e quem manda no preço é o
    // servidor. Sai daqui junto com o registro no `limparAntigos`.
    pedido: resposta,
    enviado_em: new Date().toISOString(),
    erro: null,
  }));
}

/**
 * A venda foi desfeita no servidor.
 *
 * O registro fica na lista em vez de sumir: quem cancelou precisa ver que deu
 * certo, e um pedido que evapora da tela deixa a dúvida de se o valor saiu
 * mesmo do caixa. Some sozinho no `limparAntigos`, junto com os enviados.
 */
export async function marcarCancelado(idCliente, motivo) {
  return _atualizar(idCliente, (registro) => ({
    ...registro,
    status: "CANCELADO",
    motivo_cancelamento: motivo,
    cancelado_em: new Date().toISOString(),
  }));
}

export async function marcarRecusado(idCliente, motivo) {
  return _atualizar(idCliente, (registro) => ({
    ...registro,
    status: "RECUSADO",
    erro: motivo,
    tentativas: registro.tentativas + 1,
  }));
}

/** Tentou e não deu (rede fora). Continua PENDENTE — só conta a tentativa. */
export async function registrarFalha(idCliente, motivo) {
  return _atualizar(idCliente, (registro) => ({
    ...registro,
    erro: motivo,
    tentativas: registro.tentativas + 1,
    ultima_tentativa: new Date().toISOString(),
  }));
}

export async function descartar(idCliente) {
  await transacao("readwrite", (loja) => loja.delete(idCliente));
}

/**
 * Limpa o que já foi resolvido e envelheceu. Pedido PENDENTE nunca é apagado
 * por idade: é venda que ainda não chegou no caixa.
 */
export async function limparAntigos(agora = Date.now()) {
  const resolvidos = [...(await porStatus("ENVIADO")), ...(await porStatus("CANCELADO"))];
  const velhos = resolvidos.filter(
    (r) => agora - new Date(r.enviado_em ?? r.criado_em_cliente).getTime() > GUARDAR_ENVIADOS_MS,
  );
  await transacao("readwrite", (loja) => {
    for (const registro of velhos) loja.delete(registro.id_cliente);
  });
  return velhos.length;
}

// --------------------------------------------------------------- leitura

/** Todos de um status, do mais velho pro mais novo — a ordem de envio. */
export async function porStatus(status) {
  const db = await abrir();
  const tx = db.transaction(LOJA, "readonly");
  const linhas = await promessa(tx.objectStore(LOJA).index("status").getAll(status));
  return linhas.sort((a, b) => a.criado_em_cliente.localeCompare(b.criado_em_cliente));
}

export function pendentes() {
  return porStatus("PENDENTE");
}

export async function contarPendentes() {
  const db = await abrir();
  const tx = db.transaction(LOJA, "readonly");
  return promessa(tx.objectStore(LOJA).index("status").count("PENDENTE"));
}

/** Os últimos pedidos, do mais novo pro mais velho — a lista da tela. */
export async function recentes(limite = 20) {
  const db = await abrir();
  const tx = db.transaction(LOJA, "readonly");
  const todos = await promessa(tx.objectStore(LOJA).getAll());
  return todos
    .sort((a, b) => b.criado_em_cliente.localeCompare(a.criado_em_cliente))
    .slice(0, limite);
}

// --------------------------------------------------------------- internos

/**
 * UUID v4 do pedido — a chave da idempotência.
 *
 * `crypto.randomUUID` só existe em contexto seguro. Em produção sobra HTTPS,
 * mas testar o app pelo IP da rede local (`http://192.168.0.x:8000`) cai fora
 * disso — e sem esta volta o botão ENVIAR quebraria justo no primeiro teste
 * com o celular da loja. `getRandomValues` funciona nos dois casos.
 */
function novoUuid() {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();

  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // versão 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // variante RFC 4122

  const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0"));
  const junta = (i, f) => hex.slice(i, f).join("");
  return `${junta(0, 4)}-${junta(4, 6)}-${junta(6, 8)}-${junta(8, 10)}-${junta(10, 16)}`;
}

async function _atualizar(idCliente, transformar) {
  const db = await abrir();
  return new Promise((resolve, rejeitar) => {
    const tx = db.transaction(LOJA, "readwrite");
    const loja = tx.objectStore(LOJA);
    let atualizado = null;

    const leitura = loja.get(idCliente);
    leitura.onsuccess = () => {
      const registro = leitura.result;
      if (!registro) return; // sumiu no meio do caminho: nada a fazer
      atualizado = transformar(registro);
      loja.put(atualizado);
    };

    tx.oncomplete = () => resolve(atualizado);
    tx.onerror = () => rejeitar(tx.error);
    tx.onabort = () => rejeitar(tx.error ?? new Error("Transação abortada"));
  });
}
