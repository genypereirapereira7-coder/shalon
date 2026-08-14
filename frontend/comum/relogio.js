/**
 * A hora do servidor, não a do aparelho.
 *
 * Existe por causa de uma conta que a tela da cozinha faz o tempo todo: "esta
 * comanda foi criada há mais de 15 segundos e ainda não imprimiu, então a
 * impressora travou". Os dois lados dessa conta vinham de relógios diferentes
 * — `criado_em` do servidor contra `Date.now()` do aparelho.
 *
 * Num celular isso passa despercebido, porque a operadora acerta a hora. Num PC
 * de cozinha, não: é uma máquina que pode ficar meses sem sincronizar, e uns
 * poucos minutos de desvio bastam pra acender o alerta vermelho em todas as
 * comandas ou em nenhuma. Um alerta que mente é pior do que não ter alerta.
 *
 * A correção é um número só, e o padrão é zero: sem informação do servidor, o
 * comportamento é o mesmo de antes.
 */

let desvio = 0;

/** Guarda a diferença entre o relógio do servidor e o daqui. */
export function ajustar(isoDoServidor) {
  const servidor = new Date(isoDoServidor).getTime();
  if (Number.isNaN(servidor)) return;
  desvio = servidor - Date.now();
}

/** `Date.now()` corrigido pelo desvio. */
export function agora() {
  return Date.now() + desvio;
}

/** Quantos milissegundos se passaram desde uma data ISO do servidor. */
export function desde(iso) {
  return agora() - new Date(iso).getTime();
}

/**
 * Acerta o relógio pelo `/health`, que responde sem token.
 *
 * O `pronto` do WebSocket também acerta, mas só depois de logar — e a tela da
 * cozinha já mostra comandas antes disso quando volta de uma sessão salva.
 */
export async function sincronizar() {
  try {
    const resposta = await fetch("/health");
    const dados = await resposta.json();
    if (dados?.agora_utc) ajustar(dados.agora_utc);
  } catch {
    // Sem servidor não há o que acertar; segue com o relógio local.
  }
}
