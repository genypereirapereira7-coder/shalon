"""Leitura e escrita do sabor do dia, e a resolução do texto que vai na comanda.

A regra que vale a pena isolar aqui é a do **misto**: ele não é um terceiro
sabor cadastrado, é os dois juntos. Se estivesse escrito na tela de vendas, o
dia em que o dono deixasse só um sabor preenchido produziria uma comanda
dizendo "Misto: Chocolate + " — e a cozinha teria que adivinhar.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sabor import EscolhaSabor, SaborDoDia

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


def texto(escolha: EscolhaSabor | None, sabores: SaborDoDia) -> str | None:
    """O que sai impresso na comanda pra esta escolha.

    Devolve `None` quando não há o que dizer — e é isso que faz a loja
    continuar vendendo num dia em que ninguém preencheu os sabores: o item sai
    sem a linha do sabor, exatamente como saía antes desta funcionalidade
    existir. Barrar a venda seria transformar um esquecimento de dois campos
    numa fila parada.
    """
    if escolha is None:
        return None

    if escolha is EscolhaSabor.SABOR_1:
        return sabores.sabor1
    if escolha is EscolhaSabor.SABOR_2:
        return sabores.sabor2

    # MISTO: só é misto se houver os dois. Com um só preenchido, o que o
    # cliente vai levar é aquele — e é ele que a cozinha precisa ler.
    if sabores.sabor1 and sabores.sabor2:
        return f"{sabores.sabor1} + {sabores.sabor2}"
    return sabores.sabor1 or sabores.sabor2
