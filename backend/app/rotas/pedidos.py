"""Pedidos: criação pelo celular, fila de impressão e ciclo de status."""

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select

from app.dependencias import (
    IdentidadeDep,
    SessaoDep,
    SoAgente,
    SoCaixa,
    SoCozinha,
    SoImpressor,
)
from app.models.base import agora
from app.models.pedido import Pedido, StatusPedido
from app.models.usuario import Papel
from app.schemas.pedido import (
    CancelamentoEntrada,
    ItemSaida,
    OpcaoEscolhida,
    PedidoEntrada,
    PedidoSaida,
    StatusEntrada,
)
from app.servicos import pedidos as servico
from app.servicos import relatorios as relatorio
from app.servicos.dia_operacional import dia_atual
from app.servicos.eventos import Evento, hub, publicar_apos_commit

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

    saida = _saida(
        pedido,
        duplicado=not criado,
        total_divergente=criado and servico.divergiu(pedido, dados.total_centavos),
    )

    # Só o pedido novo é anunciado. O reenvio do mesmo `id_cliente` devolve o
    # pedido que já existe — anunciar de novo faria o agente imprimir uma
    # segunda comanda, que é exatamente o que a idempotência existe pra evitar.
    if criado:
        await _anunciar(sessao, Evento.PEDIDO_NOVO, saida, mexeu_no_caixa=True)

    return saida

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
async def marcar_impresso(pedido_id: uuid.UUID, sessao: SessaoDep, _: SoImpressor):
    """ACK de quem imprimiu: a comanda foi entregue à impressora.

    Existe por REST porque a impressão (fase 3) precisa funcionar antes do
    WebSocket (fase 4). Depois o agente pode mandar `ack.impresso` pelo socket
    — o efeito no banco é este mesmo.

    **Quem chama hoje é o celular do balcão**, logo depois de despachar o cupom
    pro RawBT. O agente do PC continua podendo chamar, e é por isso que os dois
    não devem rodar juntos: o primeiro que confirmar tira a comanda da fila do
    outro, mas na janela entre o papel e o ACK cada um imprimiria a sua via.

    Uma ressalva que o nome do campo esconde: o Intent do Android não devolve
    nada, então, vindo do celular, `impresso_em` quer dizer "foi mandado pra
    impressora" e não "o papel está na bandeja". A diferença aparece com a
    térmica desligada — a venda sai da fila sem ter saído no papel. Quem
    percebe é o balcão, que tem a comanda na mão, e reimprime pela lista de
    últimos pedidos.
    """
    pedido = await _buscar(sessao, pedido_id)
    if pedido.impresso_em is None:
        pedido.impresso_em = agora()
        await sessao.flush()
        saida = _saida(pedido)
        await _anunciar(sessao, Evento.PEDIDO_IMPRESSO, saida)
        return saida

    # Já estava impresso: o agente reenviou o ACK. Nada mudou, nada a anunciar.
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

    # `pedido.novo` e não um evento próprio: pro agente, "imprima este pedido"
    # é a mesma ordem das duas vezes, e a §5 só lhe manda este evento. A tela
    # da cozinha não apita de novo porque já conhece o número — quem distingue
    # comanda nova de repetida é ela, não o servidor.
    saida = _saida(pedido)
    await _anunciar(sessao, Evento.PEDIDO_NOVO, saida)
    return saida


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

    # Sem `metricas.tick`: andar de RECEBIDO pra PRONTO não mexe em centavo
    # nenhum, e o dono não precisa ver o total piscar por isso.
    saida = _saida(pedido)
    await _anunciar(sessao, Evento.PEDIDO_STATUS, saida)
    return saida


@rotas.post("/{pedido_id}/cancelar", response_model=PedidoSaida)
async def cancelar(
    pedido_id: uuid.UUID, dados: CancelamentoEntrada, sessao: SessaoDep, quem: SoCaixa
):
    """Tira a venda do caixa. Exige motivo — é dinheiro saindo.

    **O balcão cancela o que o balcão vendeu, hoje.** É lá que o erro acontece
    (pedido digitado errado, cliente que desistiu antes de pagar) e é lá que o
    cliente está esperando; obrigar a chamar o dono deixaria a fila parada por
    um engano de dez segundos.

    Fora dessa janela — venda de outro atendente, venda de ontem — quem cancela
    é o dono. Não é desconfiança do funcionário: é que qualquer um dos dois
    casos significa que alguém está mexendo em movimento que já foi conferido,
    e isso precisa passar por quem responde pelo caixa.

    O que protege o dinheiro não é a dificuldade de cancelar, é o registro:
    fica gravado o motivo e quem cancelou, o valor sai do faturamento e entra
    na conta de cancelados, que aparece destacada no painel do dono.
    """
    pedido = await _buscar(sessao, pedido_id)

    if pedido.status is StatusPedido.CANCELADO:
        raise HTTPException(status.HTTP_409_CONFLICT, "Pedido já está cancelado")

    if quem.papel is not Papel.DONO:
        if pedido.usuario_id != quem.usuario_id:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Este pedido é de outro atendente — só o dono cancela",
            )
        if pedido.data_operacional != dia_atual():
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Pedido de outro dia — só o dono cancela",
            )

    pedido.status = StatusPedido.CANCELADO
    pedido.cancelado_em = agora()
    pedido.cancelado_por = quem.usuario_id
    pedido.motivo_cancelamento = dados.motivo
    await sessao.flush()

    # Este mexe no caixa: cancelado sai do faturamento, então o total do dono
    # muda junto.
    saida = _saida(pedido)
    await _anunciar(sessao, Evento.PEDIDO_STATUS, saida, mexeu_no_caixa=True)
    return saida


# ------------------------------------------------------------------ internos

async def _anunciar(
    sessao, evento: Evento, saida: PedidoSaida, *, mexeu_no_caixa: bool = False
) -> None:
    """Commita e avisa quem assina o evento.

    Todos os `pedido.*` levam o `PedidoSaida` inteiro, e não o `{id, status}`
    da §5. É mais bytes numa rede que é uma loja só, em troca de um formato só:
    a tela da cozinha redesenha a comanda do mesmo jeito tenha ela nascido,
    mudado de status ou saído na impressora, e o PWA de vendas — que não tem o
    pedido em mãos — não precisa ir buscar o resto.
    """
    await publicar_apos_commit(sessao, evento, saida.model_dump(mode="json"))

    if mexeu_no_caixa:
        await _tick_do_dono(sessao)


async def _tick_do_dono(sessao) -> None:
    """`metricas.tick` com o resumo do dia **inteiro**, não só os três números.

    A §5 previa um evento magro — total, nº de pedidos e ticket. Mandar só isso
    obrigaria a tela do dono a pintar o total novo por cima do ranking velho,
    que é exatamente o que o PWA dele foi escrito pra nunca fazer: os números
    saem todos da mesma resposta justamente pra não existir um instante em que
    a soma e o detalhe se contradizem. Mandando o resumo completo, a tela troca
    tudo de uma vez.

    A conta só é feita se houver dono conectado. O resumo são cinco consultas
    agregadas, e rodá-las a cada venda pra ninguém seria pura queima de banco
    num sábado de movimento.
    """
    if hub.conectados(Papel.DONO) == 0:
        return

    resumo = await relatorio.resumo(sessao, dia_atual())
    await hub.publicar(Evento.METRICAS_TICK, resumo.model_dump(mode="json"))


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
                        grupo=o.grupo_snapshot,
                        preco_extra_centavos=o.preco_extra_centavos_snapshot,
                    )
                    for o in i.opcoes
                ],
                sabor_tipo=i.sabor_tipo,
                sabor=i.sabor_snapshot,
            )
            for i in pedido.itens
        ],
        duplicado=duplicado,
        total_divergente=total_divergente,
    )
