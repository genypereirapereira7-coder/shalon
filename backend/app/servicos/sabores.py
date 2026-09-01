"""Leitura e escrita do sabor do dia, e a resolução do texto que vai na comanda.

A regra que vale a pena isolar aqui é a do **misto**: ele não é um terceiro
sabor cadastrado, é os dois juntos. Se estivesse escrito na tela de vendas, o
dia em que o dono deixasse só um sabor preenchido produziria uma comanda
dizendo "Misto: Chocolate + " — e a cozinha teria que adivinhar.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sabor import MAX_SABORES, EscolhaSabor, SaborDoDia

# `id` fixo: é uma linha só, e procurar "a mais recente" abriria a porta pra
# duas linhas coexistirem e a loja servir sabores diferentes em dois celulares.
LINHA_UNICA = 1


async def ler(sessao: AsyncSession) -> SaborDoDia:
    """Devolve a linha, criando-a vazia na primeira vez.

    Nunca devolve `None`: a tela do dono precisa de dois campos pra desenhar,
    e obrigar cada chamador a tratar a ausência espalharia o mesmo `if` por
    toda parte.
    """
    atual = (
        await sessao.execute(select(SaborDoDia).where(SaborDoDia.id == LINHA_UNICA))
    ).scalar_one_or_none()

    if atual is None:
        atual = SaborDoDia(id=LINHA_UNICA, sabor1=None, sabor2=None)
        sessao.add(atual)
        await sessao.flush()
    return atual


async def gravar(
    sessao: AsyncSession, sabor1: str | None, sabor2: str | None, usuario_id: int
) -> SaborDoDia:
    atual = await ler(sessao)
    atual.sabor1 = sabor1
    atual.sabor2 = sabor2
    atual.usuario_id = usuario_id
    await sessao.flush()
    return atual


def resolver(
    escolhas: list[EscolhaSabor], sabores: SaborDoDia, extra: str | None
) -> tuple[str | None, str | None]:
    """Traduz as escolhas em `(codigos, texto)` pra gravar no item.

    `codigos` é o que foi escolhido ("SABOR_1,EXTRA"); `texto` é o que sai no
    papel ("Morango + Chocolate"). Os dois são congelados no item porque amanhã
    a máquina tem outro sabor e a comanda de ontem não pode mudar junto.

    Escolha sem nome por trás é descartada em silêncio: o dono pode não ter
    preenchido o sabor 2 hoje, e o celular pode estar com a lista de ontem em
    cache. Recusar a venda por isso pararia a fila por uma divergência que não
    muda preço nem o que o cliente leva — sai o que dá pra nomear, e o que
    sobra é uma linha a menos no papel, não uma venda perdida.
    """
    nomes: list[str] = []
    codigos: list[str] = []

    # `dict.fromkeys` e não `set`: a ordem importa. "Morango + Chocolate" e
    # "Chocolate + Morango" são a mesma casquinha, mas duas comandas
    # diferentes aos olhos de quem confere o papel com o balcão.
    for escolha in dict.fromkeys(escolhas):
        nome = _nome(escolha, sabores, extra)
        if not nome:
            continue
        nomes.append(nome)
        codigos.append(escolha.value)
        if len(nomes) == MAX_SABORES:
            break

    if not nomes:
        return None, None
    return ",".join(codigos), " + ".join(nomes)


def _nome(escolha: EscolhaSabor, sabores: SaborDoDia, extra: str | None) -> str | None:
    if escolha is EscolhaSabor.SABOR_1:
        return sabores.sabor1
    if escolha is EscolhaSabor.SABOR_2:
        return sabores.sabor2
    return extra
