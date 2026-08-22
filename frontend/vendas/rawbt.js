/**
 * Transporte: entrega o texto da comanda ao RawBT, no Android.
 *
 * O RawBT é o aplicativo que fala com a térmica (Bluetooth, USB ou rede) e
 * expõe um *Intent* pro resto do sistema. Um PWA não alcança a impressora
 * direto — Web Bluetooth não vê impressora clássica SPP, e Web USB não existe
 * no Chrome do Android pra esse perfil. O Intent é o único caminho, e é o que
 * este arquivo faz e só isso.
 *
 * A URI é a documentada pelo RawBT:
 *
 *     intent:<texto-url-encoded>#Intent;scheme=rawbt;package=ru.a402d.rawbtprinter;end;
 *
 * **Por que um iframe escondido e não `location.href`.** Trocar o `location`
 * tira o PWA da frente: o Android leva o RawBT pro topo, o funcionário vê a
 * tela piscar e volta pro app pela seta do sistema, com o balcão parado no
 * meio. Navegar um iframe dispara o mesmo Intent sem mexer na página de cima —
 * o app continua na venda seguinte e a comanda sai por trás. `package=` fixo é
 * o que evita o seletor "abrir com": com o pacote no Intent, o Android entrega
 * direto ao RawBT, sem perguntar nada.
 *
 * **O que este módulo não sabe.** Se saiu papel. O Intent é de mão única: não
 * há retorno, callback nem erro. Fora do Android, ou com o RawBT desinstalado,
 * a navegação simplesmente não vai a lugar nenhum. Por isso `disponivel()`
 * responde só o que dá pra saber de verdade — que o aparelho é Android — e
 * quem chama trata "despachei" como "entreguei ao RawBT", nunca como
 * "imprimiu". Ver `impressao.js`.
 */

/** O pacote do RawBT na Play Store. */
export const PACOTE = "ru.a402d.rawbtprinter";

/**
 * Tempo até tirar o iframe do documento.
 *
 * Remover na hora cancelaria a navegação antes de o Android resolver o Intent.
 * Deixar pra sempre acumularia um iframe por comanda numa aba que fica aberta o
 * dia inteiro.
 */
const VIDA_DO_IFRAME_MS = 4000;

/**
 * Teto de tamanho do texto. Uma comanda cabe folgada em 2 KB; passar disso é
 * sinal de defeito (laço no formatador, observação colada de outro app), e uma
 * URI gigante trava a resolução do Intent em vez de imprimir.
 */
const LIMITE_BYTES = 16 * 1024;

export class ErroImpressao extends Error {
  constructor(mensagem, causa) {
    super(mensagem);
    this.name = "ErroImpressao";
    this.causa = causa;
  }
}

/**
 * Este aparelho tem como despachar um Intent?
 *
 * Só o sistema operacional é verificável daqui — se o RawBT está instalado, o
 * navegador não conta. Fora do Android a resposta é `false` e quem chama avisa
 * o funcionário em vez de fingir que imprimiu.
 */
export function disponivel() {
  return /android/i.test(navigator.userAgent);
}

/** A URI do Intent pra um texto. Exportada porque é o que vale a pena conferir. */
export function montarUri(texto) {
  return `intent:${encodeURIComponent(texto)}#Intent;scheme=rawbt;package=${PACOTE};end;`;
}

/**
 * Despacha o texto pro RawBT.
 *
 * Resolve quando o Intent foi entregue ao sistema — **não** quando o papel
 * saiu. Levanta `ErroImpressao` quando nem o despacho foi possível.
 *
 * @param {string} texto
 * @returns {Promise<void>}
 */
export async function imprimir(texto) {
  if (!texto) throw new ErroImpressao("Comanda vazia");
  if (texto.length > LIMITE_BYTES) {
    throw new ErroImpressao(`Comanda grande demais (${texto.length} caracteres)`);
  }
  if (!disponivel()) {
    throw new ErroImpressao("Impressão pelo RawBT só funciona no Android");
  }

  try {
    abrirCanal().src = montarUri(texto);
  } catch (erro) {
    // O navegador recusou o esquema `intent:`. Acontece em WebView embutida e
    // em navegador que não é o Chrome.
    throw new ErroImpressao("O navegador não deixou abrir o RawBT", erro);
  }
}

// ------------------------------------------------------------------ internos

/**
 * Um iframe escondido, descartável, pra navegar pro `intent:`.
 *
 * `sandbox` de propósito não entra: `allow-top-navigation` seria necessário e
 * abriria mais do que fecha. O iframe nasce em `about:blank`, mesma origem, e
 * some em seguida — não há conteúdo de terceiro nele em momento nenhum.
 */
function abrirCanal() {
  const quadro = document.createElement("iframe");
  quadro.setAttribute("aria-hidden", "true");
  quadro.title = "Impressão RawBT";
  quadro.style.cssText = "position:absolute;width:0;height:0;border:0;visibility:hidden";

  document.body.append(quadro);
  setTimeout(() => quadro.remove(), VIDA_DO_IFRAME_MS);

  return quadro;
}
