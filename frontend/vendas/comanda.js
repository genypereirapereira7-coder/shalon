/**
 * O papel: o pedido confirmado pelo servidor vira o texto da comanda.
 *
 * Porte fiel do `agente/cupom.py` — mesmo layout, mesma largura, mesmas
 * decisões. As duas versões existem porque as duas impressoras existem: a
 * térmica do PC continua sendo alimentada pelo agente em Python, e o celular
 * do balcão alimenta o RawBT com este arquivo. **Comanda que sai diferente
 * dependendo de quem imprimiu é comanda que ninguém confere.**
 *
 * Este módulo é texto puro de propósito: sem DOM, sem `fetch`, sem
 * `localStorage`. Quem entrega o texto pra impressora é o `rawbt.js`; quem
 * decide *quando* imprimir é o `impressao.js`. Assim o layout do papel pode ser
 * conferido lendo uma função só, e mudar de impressora não mexe numa linha
 * daqui.
 *
 * Três coisas que o papel precisa ter e o mockup da §6 não mostra:
 *
 * **Os acompanhamentos.** Metade do cardápio da Shalon é montada — sem "com
 * granola, paçoca" embaixo do item, ninguém tem como saber o que vai dentro.
 *
 * **A marca de REIMPRESSÃO.** Um segundo papel do pedido #37 sem aviso é um
 * pedido #37 montado duas vezes.
 *
 * **A hora da loja.** O servidor manda tudo em UTC; quem lê o papel confere com
 * o relógio da parede.
 */

/**
 * 80mm em ESC/POS Font A dá 48 colunas. O desenho da §6 é mais estreito porque
 * é um rascunho — quem manda é a bobina.
 */
export const LARGURA = 48;

/**
 * Linhas em branco no fim, pra comanda passar da serrilha.
 *
 * Sem isto o funcionário rasga o papel no meio do TOTAL. O RawBT corta sozinho
 * quando a impressora tem guilhotina e está configurada pra isso; quando não
 * tem, é este avanço que salva o cupom.
 */
const AVANCO_FINAL = "\n\n\n";

/**
 * Monta o texto da comanda.
 *
 * @param {object} pedido      o `PedidoSaida` que o servidor devolveu
 * @param {object} [opcoes]
 * @param {boolean} [opcoes.reimpressao=false]
 * @param {number}  [opcoes.largura=LARGURA]
 * @returns {string} o papel pronto, em texto
 */
export function montar(pedido, { reimpressao = false, largura = LARGURA } = {}) {
  const linhas = [];
  const escrever = (linha) => linhas.push(linha);

  escrever(centro("SORVETERIA SHALON", largura));
  escrever("=".repeat(largura));

  if (reimpressao) {
    escrever(centro("*** REIMPRESSAO ***", largura));
    escrever("");
  }

  escrever(centro(`PEDIDO  #${pedido.numero_dia ?? "?"}`, largura));
  escrever("");

  const quando = new Date(pedido.criado_em_cliente ?? pedido.criado_em ?? Date.now());
  escrever(ladoALado(data(quando), horario(quando), largura));
  escrever(`Atendente: ${pedido.usuario_nome || "?"}`);
  escrever("-".repeat(largura));

  for (const item of pedido.itens ?? []) {
    escrever(linhaItem(item, largura));
    // Antes dos acompanhamentos: é o sabor que diz o que servir, e os
    // acompanhamentos são o que vai por cima. Quem monta lê de cima pra baixo.
    //
    // `sabor` é o que o servidor devolveu; `sabor_texto` é o que o celular
    // resolveu na hora de montar. Os dois existem porque a comanda é impressa
    // antes de o pedido subir — numa fila offline o primeiro é `undefined`, e
    // sem o segundo a comanda sairia sem o sabor justamente no dia em que a
    // internet caiu.
    const sabor = item.sabor ?? item.sabor_texto;
    if (sabor) escrever(linhaSabor(sabor, largura));
    for (const opcao of item.opcoes ?? []) escrever(linhaOpcao(opcao, largura));
  }

  if (pedido.observacao) {
    escrever("-".repeat(largura));
    // `OBS:` no fim e destacado: é o campo que faz a cozinha fazer diferente do
    // padrão, e perdido no meio dos itens ninguém lê.
    for (const linha of quebrar(`OBS: ${pedido.observacao}`, largura)) escrever(linha);
  }

  escrever("=".repeat(largura));
  escrever(ladoALado("TOTAL", moeda(pedido.total_centavos), largura));

  return linhas.join("\n") + AVANCO_FINAL;
}

// ------------------------------------------------------------------ internos

/** `2x  Casquinha 1 bola` à esquerda, valor à direita. */
function linhaItem(item, largura) {
  const prefixo = `${item.quantidade}x `;
  const valor = moeda(item.subtotal_centavos);
  const espaco = Math.max(largura - prefixo.length - valor.length - 1, 1);
  return `${prefixo}${cortar(item.nome, espaco).padEnd(espaco)} ${valor}`;
}

/**
 * O sabor, indentado sob o item e em maiúsculas.
 *
 * Maiúsculas porque numa térmica de 32 colunas, com papel gasto e a cozinha
 * lendo de relance, é a linha que não pode ser confundida com um
 * acompanhamento. Ela responde a única pergunta que impede de montar o pedido.
 */
function linhaSabor(texto, largura) {
  return cortar(`   * ${texto.toUpperCase()}`, largura);
}

/**
 * Indentado sob o item, com o "+" quando é adicional pago.
 *
 * O preço do adicional aparece porque o cupom é conferido contra o valor total:
 * sem ele, a soma dos itens não bate com o TOTAL e alguém acha que a máquina
 * errou.
 */
function linhaOpcao(opcao, largura) {
  const extra = opcao.preco_extra_centavos || 0;
  const texto = `   ${extra ? "+" : "-"} ${opcao.nome}`;
  if (!extra) return cortar(texto, largura);

  const valor = moeda(extra);
  const espaco = largura - valor.length - 1;
  return `${cortar(texto, espaco).padEnd(espaco)} ${valor}`;
}

/**
 * 800 → "R$ 8,00".
 *
 * À mão, e não com `Intl.NumberFormat`: o `Intl` usa espaço não-quebrável entre
 * "R$" e o número, e a térmica imprime esse byte como um caractere qualquer no
 * meio do total. Dinheiro é inteiro em centavos em todo o sistema.
 */
function moeda(centavos) {
  const total = centavos ?? 0;
  return `R$ ${Math.trunc(total / 100)},${String(total % 100).padStart(2, "0")}`;
}

/**
 * Data e hora **da loja**, não do celular.
 *
 * `toLocaleString` com `timeZone` fixo: o celular do balcão pode estar com o
 * fuso errado, e um horário que não bate com o relógio da parede faz alguém
 * pegar a comanda errada na pilha.
 */
const FUSO = "America/Sao_Paulo";

function data(momento) {
  return momento.toLocaleDateString("pt-BR", {
    timeZone: FUSO, day: "2-digit", month: "2-digit", year: "numeric",
  });
}

function horario(momento) {
  return momento.toLocaleTimeString("pt-BR", {
    timeZone: FUSO, hour: "2-digit", minute: "2-digit",
  });
}

function centro(texto, largura) {
  const curto = cortar(texto, largura);
  const folga = Math.max(Math.trunc((largura - curto.length) / 2), 0);
  return " ".repeat(folga) + curto;
}

function ladoALado(esquerda, direita, largura) {
  const folga = Math.max(largura - esquerda.length - direita.length, 1);
  return esquerda + " ".repeat(folga) + direita;
}

function cortar(texto, largura) {
  const valor = String(texto ?? "");
  return valor.length <= largura ? valor : `${valor.slice(0, largura - 1)}…`;
}

/**
 * Quebra por palavra. A observação é escrita à mão pelo funcionário e vem em
 * tamanho imprevisível — cortada no meio, ela perde justamente o detalhe.
 */
function quebrar(texto, largura) {
  const linhas = [];
  let atual = "";

  for (const palavra of texto.split(/\s+/).filter(Boolean)) {
    const candidata = atual ? `${atual} ${palavra}` : palavra;
    if (candidata.length <= largura) {
      atual = candidata;
    } else {
      if (atual) linhas.push(atual);
      atual = cortar(palavra, largura);
    }
  }

  if (atual) linhas.push(atual);
  return linhas;
}
