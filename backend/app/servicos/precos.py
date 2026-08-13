"""Preço vigente num instante — reconstruído a partir da preco_historico.

Por que isto existe: o servidor nunca confia no total que vem do celular
(qualquer um edita o JavaScript). Mas também não pode usar cegamente o preço
de agora: um pedido que ficou 40 min na fila offline foi vendido pelo preço
que o cliente viu na tela. Se o dono mudou o preço nesse meio-tempo, o valor
gravado tem que ser o antigo.

Online os dois caminhos dão o mesmo resultado — não há histórico entre a venda
e a chegada. A diferença só aparece no offline, que é justamente onde importa.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cardapio import PrecoHistorico, Produto


async def preco_em(sessao: AsyncSession, produto: Produto, momento: datetime) -> int:
    """Preço do produto no instante `momento`, em centavos.

    A primeira alteração registrada DEPOIS de `momento` guarda, no seu
    `preco_antigo`, exatamente o preço que valia naquele instante. Se não houve
    alteração nenhuma desde então, o preço atual é o que valia.
    """
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=UTC)

    consulta = (
        select(PrecoHistorico.preco_antigo)
        .where(
            PrecoHistorico.produto_id == produto.id,
            PrecoHistorico.criado_em > momento,
        )
        .order_by(PrecoHistorico.criado_em.asc())
        .limit(1)
    )
    anterior = (await sessao.execute(consulta)).scalar_one_or_none()
    return anterior if anterior is not None else produto.preco_centavos


async def registrar_mudanca(
    sessao: AsyncSession,
    produto: Produto,
    preco_novo: int,
    usuario_id: int,
) -> None:
    """Grava a auditoria e aplica o preço novo. Não faz commit."""
    if preco_novo == produto.preco_centavos:
        return
    sessao.add(
        PrecoHistorico(
            produto_id=produto.id,
            preco_antigo=produto.preco_centavos,
            preco_novo=preco_novo,
            usuario_id=usuario_id,
        )
    )
    produto.preco_centavos = preco_novo
