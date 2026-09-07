"""Apuração do movimento do dia e fechamento de caixa.

Duas regras valem em todas as contas daqui:

**Cancelado não é venda.** Fica fora de total, ticket médio e ranking, e é
contado à parte — o dono precisa ver que houve cancelamento, mas somar isso ao
faturamento faria o relatório mentir.

**Pedido pós-fechamento conta na venda e é sinalizado.** A venda existiu (foi
feita antes da virada, só subiu da fila offline depois que o caixa fechou),
então entra no total do dia. Mas o `fechamento_dia` é imutável e não a inclui —
por isso ela também aparece separada. Sem isso o dono compararia o relatório
com o caixa e acharia que faltou dinheiro.
"""

from collections import defaultdict
from datetime import date
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.base import agora
from app.models.fechamento import FechamentoDia
from app.models.pedido import Pedido, PedidoItem, StatusPedido
from app.models.usuario import Usuario
from app.schemas.relatorio import (
    FechamentoAgrupadoSaida,
    FechamentoSaida,
    ItemVendido,
    ResumoDia,
    VendaPorAtendente,
)
from app.servicos.dia_operacional import dia_atual


class FechamentoInvalido(Exception):
    """Erro de negócio ao fechar o caixa. A rota traduz pra HTTP."""


async def resumo(sessao: AsyncSession, data: date) -> ResumoDia:
    """O movimento de um dia operacional inteiro.

    São cinco consultas agregadas em vez de carregar os pedidos e somar em
    Python: num sábado de movimento são centenas de pedidos, e a tela do dono
    recarrega isto a cada 15 segundos.
    """
    qtd, total = await _totais(sessao, *_vendas_do_dia(data))
    cancelados_qtd, cancelados_total = await _totais(
        sessao,
        Pedido.data_operacional == data,
        Pedido.status == StatusPedido.CANCELADO,
    )
    pos_qtd, pos_total = await _totais(
        sessao, *_vendas_do_dia(data), Pedido.pos_fechamento.is_(True)
    )

    return ResumoDia(
        data_operacional=data,
        total_centavos=total,
        qtd_pedidos=qtd,
        ticket_medio_centavos=_ticket_medio(total, qtd),
        itens=await _ranking(sessao, data),
        por_atendente=await _por_atendente(sessao, data),
        cancelados_qtd=cancelados_qtd,
        cancelados_centavos=cancelados_total,
        pos_fechamento_qtd=pos_qtd,
        pos_fechamento_centavos=pos_total,
        fechamento=await buscar_fechamento(sessao, data),
        apurado_em=agora(),
    )


async def buscar_fechamento(sessao: AsyncSession, data: date) -> FechamentoSaida | None:
    consulta = (
        select(FechamentoDia, Usuario.nome)
        .join(Usuario, FechamentoDia.fechado_por == Usuario.id)
        .where(FechamentoDia.data_operacional == data)
    )
    linha = (await sessao.execute(consulta)).first()
    return _saida_fechamento(*linha) if linha else None


async def historico(sessao: AsyncSession, limite: int = 60) -> list[FechamentoSaida]:
    """Fechamentos mais recentes primeiro — é a lista de datas da tela."""
    consulta = (
        select(FechamentoDia, Usuario.nome)
        .join(Usuario, FechamentoDia.fechado_por == Usuario.id)
        .order_by(FechamentoDia.data_operacional.desc())
        .limit(limite)
    )
    return [_saida_fechamento(f, nome) for f, nome in (await sessao.execute(consulta)).all()]


async def historico_semanal(sessao: AsyncSession, limite: int = 26) -> list[FechamentoAgrupadoSaida]:
    """Fechamentos diários somados por semana ISO (segunda a domingo)."""
    return await _historico_agrupado(sessao, limite, lambda d: d.isocalendar()[:2])


async def historico_mensal(sessao: AsyncSession, limite: int = 12) -> list[FechamentoAgrupadoSaida]:
    """Fechamentos diários somados por mês corrido."""
    return await _historico_agrupado(sessao, limite, lambda d: (d.year, d.month))


async def historico_anual(sessao: AsyncSession, limite: int = 5) -> list[FechamentoAgrupadoSaida]:
    """Fechamentos diários somados por ano corrido."""
    return await _historico_agrupado(sessao, limite, lambda d: (d.year, 0))


async def fechar(
    sessao: AsyncSession,
    data: date,
    usuario_id: int,
    total_conferido: int | None = None,
) -> FechamentoSaida:
    """Congela o total do dia. Não faz commit.

    O fechamento é imutável: refazer o dia depois de uma venda atrasada
    apagaria o número que o dono já conferiu contra a gaveta. A venda que subir
    depois entra marcada como `pos_fechamento` e aparece à parte no resumo.
    """
    if data > dia_atual():
        raise FechamentoInvalido("Não dá pra fechar um dia que ainda não aconteceu")

    if await buscar_fechamento(sessao, data) is not None:
        raise FechamentoInvalido(f"O caixa de {data:%d/%m/%Y} já foi fechado")

    qtd, total = await _totais(sessao, *_vendas_do_dia(data))

    # O dono conferiu o total na tela e tocou em fechar. Se uma venda entrou
    # nesse meio-tempo, o número que ele viu não é mais o do dia — melhor
    # recusar e deixá-lo conferir de novo do que congelar um valor que ele
    # nunca aprovou.
    if total_conferido is not None and total_conferido != total:
        raise FechamentoInvalido(
            f"O total mudou desde a conferência (era {total_conferido}, agora é {total}). "
            "Confira de novo antes de fechar."
        )

    fechamento = FechamentoDia(
        data_operacional=data,
        total_centavos=total,
        qtd_pedidos=qtd,
        fechado_por=usuario_id,
    )
    sessao.add(fechamento)
    await sessao.flush()

    usuario = await sessao.get(Usuario, usuario_id)
    return _saida_fechamento(fechamento, usuario.nome if usuario else "?")


async def _historico_agrupado(
    sessao: AsyncSession,
    limite: int,
    chave: Callable[[date], tuple[int, int]],
) -> list[FechamentoAgrupadoSaida]:
    """Agrupa em Python, não em SQL: são no máximo alguns milhares de linhas
    (um fechamento por dia), e `strftime`/`date_trunc` divergem entre o
    SQLite de dev e o Postgres de produção — a mesma armadilha que a
    `0001_inicial` já bateu com `postgresql.UUID` (ver README, "Rodar
    localmente"). Somar em Python funciona igual nos dois bancos.
    """
    consulta = select(FechamentoDia).order_by(FechamentoDia.data_operacional.desc())
    grupos: dict[tuple[int, int], list[FechamentoDia]] = defaultdict(list)
    for fechamento in (await sessao.execute(consulta)).scalars():
        grupos[chave(fechamento.data_operacional)].append(fechamento)

    saida = []
    for grupo in sorted(grupos.values(), key=lambda g: g[0].data_operacional, reverse=True)[:limite]:
        saida.append(
            FechamentoAgrupadoSaida(
                inicio=min(f.data_operacional for f in grupo),
                fim=max(f.data_operacional for f in grupo),
                total_centavos=sum(f.total_centavos for f in grupo),
                qtd_pedidos=sum(f.qtd_pedidos for f in grupo),
                qtd_dias=len(grupo),
            )
        )
    return saida


# ------------------------------------------------------------------ internos


def _vendas_do_dia(data: date) -> tuple[ColumnElement[bool], ...]:
    """Filtro base de toda conta de faturamento: o dia, sem os cancelados."""
    return (
        Pedido.data_operacional == data,
        Pedido.status != StatusPedido.CANCELADO,
    )


async def _totais(sessao: AsyncSession, *condicoes: ColumnElement[bool]) -> tuple[int, int]:
    """(quantidade, soma em centavos) dos pedidos que casam com as condições."""
    consulta = select(
        func.count(Pedido.id),
        func.coalesce(func.sum(Pedido.total_centavos), 0),
    ).where(*condicoes)
    qtd, total = (await sessao.execute(consulta)).one()
    return int(qtd), int(total)


def _ticket_medio(total: int, qtd: int) -> int:
    """Arredondamento em inteiro: `total / qtd` em float daria 1799.9999… num
    dia em que a conta fecha redonda, e a tela mostraria R$ 17,99 em vez de 18.
    """
    if qtd == 0:
        return 0
    return (total + qtd // 2) // qtd


async def _ranking(sessao: AsyncSession, data: date) -> list[ItemVendido]:
    """Itens mais vendidos, do que mais rendeu pro que menos rendeu.

    O `subtotal_centavos` do item já inclui os adicionais pagos, então a geléia
    de R$3 aparece no valor do açaí que a levou — que é como o dono pensa o
    faturamento por produto.

    Agrupa por `produto_id`, não pelo nome: renomear um produto no meio do dia
    não pode partir a linha dele em duas. O nome exibido é o do snapshot (como
    estava na hora da venda); num dia em que o produto foi renomeado, sai um
    dos dois nomes.
    """
    consulta = (
        select(
            PedidoItem.produto_id,
            func.max(PedidoItem.nome_snapshot).label("nome"),
            func.sum(PedidoItem.quantidade).label("quantidade"),
            func.sum(PedidoItem.subtotal_centavos).label("total"),
        )
        .join(Pedido, PedidoItem.pedido_id == Pedido.id)
        .where(
            Pedido.data_operacional == data,
            Pedido.status != StatusPedido.CANCELADO,
        )
        .group_by(PedidoItem.produto_id)
        .order_by(func.sum(PedidoItem.subtotal_centavos).desc())
    )
    return [
        ItemVendido(
            produto_id=produto_id,
            nome=nome,
            quantidade=int(quantidade),
            total_centavos=int(total),
        )
        for produto_id, nome, quantidade, total in (await sessao.execute(consulta)).all()
    ]


async def _por_atendente(sessao: AsyncSession, data: date) -> list[VendaPorAtendente]:
    consulta = (
        select(
            Pedido.usuario_id,
            Usuario.nome,
            func.count(Pedido.id).label("qtd"),
            func.coalesce(func.sum(Pedido.total_centavos), 0).label("total"),
        )
        .join(Usuario, Pedido.usuario_id == Usuario.id)
        .where(
            Pedido.data_operacional == data,
            Pedido.status != StatusPedido.CANCELADO,
        )
        .group_by(Pedido.usuario_id, Usuario.nome)
        .order_by(func.sum(Pedido.total_centavos).desc())
    )
    return [
        VendaPorAtendente(
            usuario_id=usuario_id,
            nome=nome,
            qtd_pedidos=int(qtd),
            total_centavos=int(total),
        )
        for usuario_id, nome, qtd, total in (await sessao.execute(consulta)).all()
    ]


def _saida_fechamento(fechamento: FechamentoDia, nome: str) -> FechamentoSaida:
    return FechamentoSaida(
        data_operacional=fechamento.data_operacional,
        total_centavos=fechamento.total_centavos,
        qtd_pedidos=fechamento.qtd_pedidos,
        # O fuso é garantido pelo tipo `Utc` do schema, não aqui: ver
        # `app/schemas/tipos.py` pra por que essa normalização é do schema.
        fechado_em=fechamento.fechado_em,
        fechado_por=fechamento.fechado_por,
        fechado_por_nome=nome,
    )
