"""Formatação do papel: o JSON do pedido vira a comanda de produção.

Não é documento fiscal (§3 da arquitetura) — é o papel que alguém lê de pé, com
pressa, pra montar um açaí. O que manda no layout é isso e nada mais.

Três coisas que o desenho da §6 não mostra e o papel precisa ter:

**Os acompanhamentos.** O mockup lista só "1x Açaí 500ml". Metade do cardápio da
Shalon é montada — sem "com granola, paçoca" impresso embaixo do item, a cozinha
não tem como saber o que vai dentro, e a tela vira a única fonte. Papel que não
serve pra produzir não serve pra nada.

**A marca de REIMPRESSÃO.** Um segundo papel do pedido #37 sem aviso é um pedido
#37 montado duas vezes.

**A hora da loja.** O servidor manda tudo em UTC; quem lê o papel confere com o
relógio da parede.
"""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

# 80mm em ESC/POS Font A dá 48 colunas. O desenho da §6 é mais estreito porque é
# um rascunho — quem manda é a bobina.
LARGURA = 48


@dataclass(frozen=True)
class Cupom:
    """O papel já pronto, em texto.

    Texto e não bytes de ESC/POS: quem traduz pro protocolo da impressora é cada
    implementação de `Impressora`, e a `ImpressoraFake` só precisa gravar num
    arquivo. Manter o cupom em texto é o que deixa testar o layout sem hardware.
    """

    numero: int
    texto: str
    reimpressao: bool


def montar(pedido: dict, *, fuso: str, reimpressao: bool = False, largura: int = LARGURA) -> Cupom:
    linhas: list[str] = []
    escrever = linhas.append

    escrever(_centro("SORVETERIA SHALON", largura))
    escrever("=" * largura)

    if reimpressao:
        escrever(_centro("*** REIMPRESSAO ***", largura))
        escrever("")

    escrever(_centro(f"PEDIDO  #{pedido['numero_dia']}", largura))
    escrever("")

    quando = _local(pedido["criado_em_cliente"], fuso)
    escrever(_lado_a_lado(f"{quando:%d/%m/%Y}", f"{quando:%H:%M}", largura))
    escrever(f"Atendente: {pedido.get('usuario_nome') or '?'}")
    escrever("-" * largura)

    for item in pedido["itens"]:
        escrever(_linha_item(item, largura))

        # Antes dos acompanhamentos: é o sabor que diz o que servir, e os
        # acompanhamentos são o que vai por cima. Quem monta lê de cima pra
        # baixo.
        #
        # Este bloco faltava aqui e existia no `comanda.js`, que é o porte deste
        # arquivo. O papel saía sem o sabor quando quem imprimia era o PC — e
        # comanda que sai diferente dependendo de quem imprimiu é comanda que
        # ninguém confere.
        if sabor := item.get("sabor"):
            escrever(_titulo_de_bloco("SABOR", largura))
            escrever(_linha_sabor(sabor, largura))

        # Agrupado pelo nome do grupo, com um título por bloco. Sem os títulos,
        # num item que tem sabor e cobertura o "Chocolate" aparecia sem dizer se
        # era a bola ou o que ia por cima.
        grupo_atual = None
        for opcao in item.get("opcoes") or []:
            grupo = opcao.get("grupo")
            if grupo and grupo != grupo_atual:
                escrever(_titulo_de_bloco(grupo, largura))
            grupo_atual = grupo
            escrever(_linha_opcao(opcao, largura))

    if pedido.get("observacao"):
        escrever("-" * largura)
        # `Obs:` no fim e destacado: é o campo que faz a cozinha fazer diferente
        # do padrão, e perdido no meio dos itens ninguém lê.
        for linha in _quebrar(f"OBS: {pedido['observacao']}", largura):
            escrever(linha)

    escrever("=" * largura)
    escrever(_lado_a_lado("TOTAL", _reais(pedido["total_centavos"]), largura))

    return Cupom(numero=pedido["numero_dia"], texto="\n".join(linhas), reimpressao=reimpressao)


# ------------------------------------------------------------------ internos

def _linha_item(item: dict, largura: int) -> str:
    """`2x  Casquinha 1 bola` à esquerda, valor à direita."""
    prefixo = f"{item['quantidade']}x "
    valor = _reais(item["subtotal_centavos"])
    espaco = max(largura - len(prefixo) - len(valor) - 1, 1)
    return f"{prefixo}{_cortar(item['nome'], espaco).ljust(espaco)} {valor}"


def _titulo_de_bloco(nome: str, largura: int) -> str:
    """Título de um bloco sob o item: `SABOR`, `COBERTURA`, `ACOMPANHAMENTOS`.

    Gêmeo do `tituloDeBloco` do `frontend/vendas/comanda.js`. A queixa que veio
    do balcão foi exatamente esta: no item que tem sabor *e* cobertura, os dois
    saíam como nomes soltos e ninguém sabia qual era qual.
    """
    return _cortar(f"  {nome.upper()}", largura)


def _linha_sabor(texto: str, largura: int) -> str:
    """O sabor, indentado sob o título e em maiúsculas.

    Maiúsculas porque numa térmica, com papel gasto e a cozinha lendo de
    relance, é a linha que não pode ser confundida com um acompanhamento.
    """
    return _cortar(f"   * {texto.upper()}", largura)


def _linha_opcao(opcao: dict, largura: int) -> str:
    """Indentado sob o item, com o "+" quando é adicional pago.

    O preço do adicional aparece porque o cupom é conferido contra o valor
    total: sem ele, a soma dos itens não bate com o TOTAL e alguém acha que a
    máquina errou.
    """
    extra = opcao.get("preco_extra_centavos") or 0
    marca = "+" if extra else "-"
    texto = f"   {marca} {opcao['nome']}"
    if not extra:
        return _cortar(texto, largura)

    valor = _reais(extra)
    return f"{_cortar(texto, largura - len(valor) - 1).ljust(largura - len(valor) - 1)} {valor}"


def _local(iso: str, fuso: str) -> datetime:
    """UTC do servidor → hora da loja.

    O `fromisoformat` do 3.12 aceita o "Z"; é por isso que o backend garante o
    fuso na saída (`app/schemas/tipos.py`). Se um dia vier sem, a data é tratada
    como UTC — o mesmo palpite que o servidor faz.
    """
    momento = datetime.fromisoformat(iso)
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=ZoneInfo("UTC"))
    return momento.astimezone(ZoneInfo(fuso))


def _reais(centavos: int) -> str:
    return f"R$ {centavos // 100},{centavos % 100:02d}"


def _centro(texto: str, largura: int) -> str:
    return _cortar(texto, largura).center(largura).rstrip()


def _lado_a_lado(esquerda: str, direita: str, largura: int) -> str:
    folga = max(largura - len(esquerda) - len(direita), 1)
    return f"{esquerda}{' ' * folga}{direita}"


def _cortar(texto: str, largura: int) -> str:
    return texto if len(texto) <= largura else texto[: largura - 1] + "…"


def _quebrar(texto: str, largura: int) -> list[str]:
    """Quebra por palavra. A observação é escrita à mão pelo funcionário e vem
    em tamanho imprevisível — cortada no meio, ela perde justamente o detalhe.
    """
    linhas: list[str] = []
    atual = ""
    for palavra in texto.split():
        candidata = f"{atual} {palavra}".strip()
        if len(candidata) <= largura:
            atual = candidata
        else:
            if atual:
                linhas.append(atual)
            atual = _cortar(palavra, largura)
    if atual:
        linhas.append(atual)
    return linhas
