/**
 * Quem decide **quando** a comanda é impressa.
 *
 * O gatilho é um só: o servidor confirmou a venda. É nesse instante que existe
 * `numero_dia` — e comanda sem número é papel que ninguém casa com o pedido do
 * balcão. Antes disso não há o que imprimir; depois disso a impressão acontece
 * sozinha, sem ninguém apertar nada.
 *
 * ```
 *   POST /pedidos  ──201──►  comanda.js (texto)  ──►  rawbt.js (Intent)
 *                                                        │
 *                              POST /pedidos/{id}/impresso ◄┘
 * ```
 *
 * **O que "impresso" passa a significar.** O Intent do RawBT é de mão única:
 * despachar é tudo que o navegador consegue fazer, e não existe retorno
 * dizendo se saiu papel. Então o ACK pro servidor quer dizer *"a comanda foi
 * entregue à impressora deste celular"* — não *"o papel está na bandeja"*.
 * Marcar mesmo assim é o que tira a comanda de `/pedidos/nao-impressos` e
 * impede que o agente do PC (quando alguém o mantém ligado) imprima uma
 * segunda via da mesma venda.
 *
 * **A fila existe pelo caminho offline.** A venda pode subir horas depois, com
 * o app em segundo plano — e Intent nenhum sai de uma aba que não está na
 * frente. Então o que não foi despachado fica guardado e o `retomar()` tenta de
 * novo quando o app volta pra tela. A alternativa era o pedido subir e a
 * comanda nunca existir, que é o cliente esperando no balcão.
 *
 * **Nada aqui sabe formatar papel nem falar com o Android.** Isso é do
 * `comanda.js` e do `rawbt.js`, injetados na fábrica: trocar o RawBT por outro
 * aplicativo de impressão, ou o layout do cupom, não mexe numa linha deste
 * arquivo — e o teste consegue exercitar a fila inteira com dois dublês.
 */

import * as api from "../comum/api.js";
import * as comanda from "./comanda.js";
import * as rawbt from "./rawbt.js";

const CHAVE_FILA = "shalon.impressao.fila";

/**
 * Teto da fila. Guardar mais do que isso é guardar comanda de um expediente que
 * já acabou — e o que interessa imprimir atrasado é o pedido de agora.
 */
const MAX_NA_FILA = 20;

/**
 * Marca a comanda como REIMPRESSÃO depois deste tempo.
 *
 * Uma comanda despachada agora é a via original. Uma que ficou na fila e sai
 * meia hora depois pode ser a segunda via de um pedido que alguém já montou na
 * mão — e papel repetido sem aviso é pedido montado duas vezes.
 */
const IDADE_ATE_VIRAR_REIMPRESSAO_MS = 10 * 60 * 1000;

/**
 * Monta o serviço de impressão.
 *
 * @param {object} [dependencias]
 * @param {{imprimir: (texto: string) => Promise<void>, disponivel: () => boolean}} [dependencias.transporte]
 * @param {(pedido: object, opcoes: object) => string} [dependencias.formatar]
 * @param {(pedidoId: string) => Promise<void>} [dependencias.confirmar]
 * @param {Storage} [dependencias.deposito]
 * @param {(estado: {pendentes: number, ultimoErro: string|null}) => void} [dependencias.aoMudar]
 */
export function criarImpressora({
  transporte = rawbt,
  formatar = comanda.montar,
  confirmar = confirmarNoServidor,
  deposito = localStorage,
  aoMudar = () => {},
} = {}) {
  let fila = lerFila(deposito);
  let rodando = false;
  let ultimoErro = null;

  function anunciar() {
    aoMudar({ pendentes: fila.length, ultimoErro });
  }

  function gravar() {
    // Só as `MAX_NA_FILA` mais novas: ver o comentário da constante.
    fila = fila.slice(-MAX_NA_FILA);
    try {
      deposito.setItem(CHAVE_FILA, JSON.stringify(fila));
    } catch {
      // Armazenamento cheio ou negado (aba anônima). A fila em memória continua
      // valendo enquanto o app estiver aberto, que é o caso que importa.
    }
    anunciar();
  }

  /**
   * Uma comanda por pedido. O mesmo `id` chegando de novo — reenvio da fila,
   * duas abas abertas — atualiza a tarefa em vez de criar uma segunda via.
   */
  function enfileirar(pedido, reimpressao) {
    const existente = fila.find((t) => t.pedido.id === pedido.id);
    if (existente) {
      existente.pedido = pedido;
      return existente;
    }

    const tarefa = {
      pedido,
      reimpressao,
      criada_em: new Date().toISOString(),
      despachada: false,
      tentativas: 0,
      erro: null,
    };
    fila.push(tarefa);
    return tarefa;
  }

  /** Executa as duas etapas que faltam à tarefa: despachar e confirmar. */
  async function executar(tarefa) {
    if (!tarefa.despachada) {
      await transporte.imprimir(formatar(tarefa.pedido, { reimpressao: reimpressaoDe(tarefa) }));
      tarefa.despachada = true;
    }

    // Só depois do papel — nunca antes. Confirmar de véspera transformaria uma
    // impressora desligada em comanda que ninguém vai buscar.
    await confirmar(tarefa.pedido.id);
  }

  /**
   * Anda com a fila inteira, da mais velha pra mais nova.
   *
   * Nunca levanta: chamar isto é sempre um efeito de segundo plano, e uma
   * exceção aqui derrubaria o envio da venda que a chamou.
   */
  async function trabalhar() {
    if (rodando) return;
    rodando = true;

    try {
      // Sempre a primeira da fila, relida a cada volta — e não um retrato dela
      // tirado na entrada. Uma venda feita enquanto este laço espera a rede
      // entra na fila depois do retrato, e com o retrato a comanda dela só
      // sairia na próxima varredura, quinze segundos mais tarde.
      while (fila.length) {
        const tarefa = fila[0];
        try {
          await executar(tarefa);
          fila = fila.filter((t) => t !== tarefa);
          ultimoErro = null;
        } catch (erro) {
          tarefa.tentativas += 1;
          tarefa.erro = erro.message;
          ultimoErro = erro.message;
          // Para na primeira que falhar: o motivo (sem RawBT, app em segundo
          // plano, servidor fora) vale pra todas, e insistir nas seguintes só
          // gastaria tempo e embaralharia a ordem das comandas.
          break;
        }
      }
    } finally {
      rodando = false;
      gravar();
    }
  }

  return {
    /**
     * Imprime a comanda de um pedido já confirmado pelo servidor.
     *
     * Não levanta e não espera papel: devolve assim que a tentativa termina, pra
     * que o próximo cliente já possa ser atendido.
     *
     * @param {object} pedido  o `PedidoSaida` da resposta do servidor
     * @param {object} [opcoes]
     * @param {boolean} [opcoes.reimpressao=false]
     */
    async imprimir(pedido, { reimpressao = false } = {}) {
      enfileirar(pedido, reimpressao);
      gravar();
      await trabalhar();
    },

    /** Tenta de novo o que ficou pra trás. Chamado quando o app volta pra tela. */
    async retomar() {
      if (fila.length) await trabalhar();
    },

    /** Quantas comandas ainda não foram despachadas. */
    pendentes() {
      return fila.length;
    },

    /** Este aparelho consegue falar com a impressora? */
    disponivel() {
      return transporte.disponivel();
    },

    /**
     * Esquece a fila.
     *
     * Existe pro dia em que a impressora não vai voltar (aparelho errado,
     * RawBT desinstalado): sem isto o aviso de comandas pendentes ficaria na
     * tela pra sempre, e um aviso permanente é um aviso que ninguém mais lê.
     */
    descartar() {
      fila = [];
      ultimoErro = null;
      gravar();
    },
  };
}

// ------------------------------------------------------------------ internos

function reimpressaoDe(tarefa) {
  if (tarefa.reimpressao) return true;
  return Date.now() - new Date(tarefa.criada_em).getTime() > IDADE_ATE_VIRAR_REIMPRESSAO_MS;
}

/**
 * O ACK: `POST /pedidos/{id}/impresso`.
 *
 * 404 não volta pra fila — o pedido não existe mais (banco recriado em
 * desenvolvimento, dia virado), e insistir seguraria a fila pra sempre num
 * papel que já saiu.
 */
async function confirmarNoServidor(pedidoId) {
  try {
    await api.pedir("POST", `/pedidos/${pedidoId}/impresso`);
  } catch (erro) {
    if (erro instanceof api.ErroApi && erro.status === 404) return;
    throw erro;
  }
}

function lerFila(deposito) {
  try {
    const bruto = deposito.getItem(CHAVE_FILA);
    const lida = bruto ? JSON.parse(bruto) : [];
    return Array.isArray(lida) ? lida.filter((t) => t?.pedido?.id) : [];
  } catch {
    return [];
  }
}
