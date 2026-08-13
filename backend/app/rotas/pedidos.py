"""Pedidos: criação pelo celular, fila de impressão e ciclo de status."""

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select

from app.dependencias import IdentidadeDep, SessaoDep, SoAgente, SoCozinha, SoDono
from app.models.base import agora
from app.models.pedido import Pedido, StatusPedido
from app.schemas.pedido import (
    CancelamentoEntrada,
    ItemSaida,
    OpcaoEscolhida,
    PedidoEntrada,
    PedidoSaida,
    StatusEntrada,
)
from app.servicos import pedidos as servico
from app.servicos.dia_operacional import dia_atual

rotas = APIRouter(prefix="/pedidos", tags=["pedidos"])


# Pode pular etapa: numa sorveteria pequena o mesmo atendente monta e entrega,
# e obrigar a passar por EM_PREPARO só faria ele apertar dois botões à toa.
# Voltar atrás, não: relatório de status que anda pra trás não vale nada.
TRANSICOES: dict[StatusPedido, set[StatusPedido]] = {
    StatusPedido.RECEBIDO: {StatusPedido.EM_PREPARO, StatusPedido.PRONTO, StatusPedido.ENTREGUE},
    StatusPedido.EM_PREPARO: {StatusPedido.PRONTO, StatusPedido.ENTREGUE},
    StatusPedido.PRONTO: {StatusPedido.ENTREGUE},
    StatusPedido.ENTREGUE: set(),
    StatusPedido.CANCELADO: set(),
}


@rotas.post("", response_model=PedidoSaida, status_code=status.HTTP_201_CREATED)
async def criar_pedido(
    dados: PedidoEntrada, resposta: Response, sessao: SessaoDep, ident: IdentidadeDep
):
    """Cria o pedido. Idempotente pelo `id_cliente` que veio do celular.

    Reenvio do mesmo uuid devolve 200 com o pedido original — não 201, e não
    um segundo pedido. É o que impede duas comandas pelo mesmo cliente quando
    a internet oscila no meio do ENVIAR.
    """
    try:
        pedido, criado = await servico.criar(sessao, dados, ident.usuario_id)
    except servico.PedidoInvalido as erro:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(erro)) from erro

    if not criado:
        resposta.status_code = status.HTTP_200_OK

    # TODO(fase 4): publicar `pedido.novo` no WebSocket (cozinha, agente, dono).
    return _saida(
        pedido,
        duplicado=not criado,
        total_divergente=criado and servico.divergiu(pedido, dados.total_centavos),
    )


@rotas.get("/hoje", response_model=list[PedidoSaida])
async def pedidos_de_hoje(
    sessao: SessaoDep,
    _: IdentidadeDep,
    status_: Annotated[list[StatusPedido] | None, Query(alias="status")] = None,
):
    """Pedidos do dia operacional corrente, na ordem em que foram numerados.

    O filtro de status existe pela tela da cozinha: ela recarrega de poucos em
    poucos segundos e só precisa do que ainda está em produção. Num sábado à
    noite, baixar o dia inteiro — com itens e acompanhamentos — a cada 5s seria
    quase tudo comanda já entregue.
    """
    consulta = select(Pedido).where(Pedido.data_operacional == dia_atual())
    if status_:
        consulta = consulta.where(Pedido.status.in_(status_))

    return [_saida(p) for p in (await sessao.execute(consulta.order_by(Pedido.numero_dia))).scalars()]


@rotas.get("/nao-impressos", response_model=list[PedidoSaida])
async def nao_impressos(sessao: SessaoDep, _: SoAgente):
    """O agente chama isto ao reconectar, pra imprimir o que ficou pra trás.

    Cancelado fica de fora: não se imprime comanda de pedido que não existe
    mais. E a ordem é a da numeração — a cozinha produz na ordem da venda.
    """
    consulta = (
        select(Pedido)
        .where(
            Pedido.data_operacional == dia_atual(),
            Pedido.impresso_em.is_(None),
            Pedido.status != StatusPedido.CANCELADO,
        )
        .order_by(Pedido.numero_dia)
    )
    return [_saida(p) for p in (await sessao.execute(consulta)).scalars()]


@rotas.post("/{pedido_id}/impresso", response_model=PedidoSaida)
async def marcar_impresso(pedido_id: uuid.UUID, sessao: SessaoDep, _: SoAgente):
    """ACK do agente: saiu papel.

    Existe por REST porque a impressão (fase 3) precisa funcionar antes do
    WebSocket (fase 4). Depois o agente pode mandar `ack.impresso` pelo socket
    — o efeito no banco é este mesmo.
    """
    pedido = await _buscar(sessao, pedido_id)
    if pedido.impresso_em is None:
        pedido.impresso_em = agora()
        await sessao.flush()
    # TODO(fase 4): publicar `pedido.impresso` pra tela da cozinha.
    return _saida(pedido)


@rotas.post("/{pedido_id}/reimprimir", response_model=PedidoSaida)
async def reimprimir(pedido_id: uuid.UUID, sessao: SessaoDep, _: SoCozinha):
    """Devolve o pedido pra fila de impressão.

    Zerar `impresso_em` é o suficiente: é assim que o agente reencontra o
    pedido em `/pedidos/nao-impressos`, com ou sem WebSocket no ar.
    """
    pedido = await _buscar(sessao, pedido_id)
    if pedido.status is StatusPedido.CANCELADO:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Pedido cancelado não vai pra impressora"
        )

    pedido.impresso_em = None
    await sessao.flush()
    # TODO(fase 4): publicar `pedido.novo` pro agente imprimir na hora.
    return _saida(pedido)


@rotas.patch("/{pedido_id}/status", response_model=PedidoSaida)
async def mudar_status(
    pedido_id: uuid.UUID, dados: StatusEntrada, sessao: SessaoDep, _: SoCozinha
):
    pedido = await _buscar(sessao, pedido_id)

    if dados.status is pedido.status:
        return _saida(pedido)  # a cozinha apertou duas vezes; não é erro

    if dados.status is StatusPedido.CANCELADO:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Cancelamento tem rota própria (POST /pedidos/{id}/cancelar) e exige motivo",
        )

    if dados.status not in TRANSICOES[pedido.status]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Não dá pra ir de {pedido.status.value} para {dados.status.value}",
        )

    pedido.status = dados.status
    await sessao.flush()
    # TODO(fase 4): publicar `pedido.status` (vendas, cozinha, dono).
    return _saida(pedido)


@rotas.post("/{pedido_id}/cancelar", response_model=PedidoSaida)
async def cancelar(
    pedido_id: uuid.UUID, dados: CancelamentoEntrada, sessao: SessaoDep, dono: SoDono
):
    """Cancelamento é só do dono e exige motivo — é dinheiro saindo do caixa."""
    pedido = await _buscar(sessao, pedido_id)

    if pedido.status is StatusPedido.CANCELADO:
        raise HTTPException(status.HTTP_409_CONFLICT, "Pedido já está cancelado")

    pedido.status = StatusPedido.CANCELADO
    pedido.cancelado_em = agora()
    pedido.cancelado_por = dono.usuario_id
    pedido.motivo_cancelamento = dados.motivo
    await sessao.flush()
    # TODO(fase 4): publicar `pedido.status` pra tela da cozinha parar de produzir.
    return _saida(pedido)


# ------------------------------------------------------------------ internos

async def _buscar(sessao, pedido_id: uuid.UUID) -> Pedido:
    pedido = await sessao.get(Pedido, pedido_id)
    if pedido is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pedido não existe")
    return pedido


def _saida(pedido: Pedido, *, duplicado: bool = False, total_divergente: bool = False):
    return PedidoSaida(
        id=pedido.id,
        id_cliente=pedido.id_cliente,
        numero_dia=pedido.numero_dia,
        data_operacional=pedido.data_operacional,
        usuario_id=pedido.usuario_id,
        usuario_nome=pedido.usuario.nome,
        status=pedido.status,
        total_centavos=pedido.total_centavos,
        observacao=pedido.observacao,
        criado_em=pedido.criado_em,
        criado_em_cliente=pedido.criado_em_cliente,
        impresso_em=pedido.impresso_em,
        cancelado_em=pedido.cancelado_em,
        motivo_cancelamento=pedido.motivo_cancelamento,
        pos_fechamento=pedido.pos_fechamento,
        itens=[
            ItemSaida(
                produto_id=i.produto_id,
                nome=i.nome_snapshot,
                preco_unit_centavos=i.preco_unit_centavos_snapshot,
                quantidade=i.quantidade,
                subtotal_centavos=i.subtotal_centavos,
                opcoes=[
                    OpcaoEscolhida(
                        opcao_id=o.opcao_id,
                        nome=o.nome_snapshot,
                        preco_extra_centavos=o.preco_extra_centavos_snapshot,
                    )
                    for o in i.opcoes
                ],
            )
            for i in pedido.itens
        ],
        duplicado=duplicado,
        total_divergente=total_divergente,
    )
