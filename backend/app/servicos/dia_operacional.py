"""Dia operacional: uma venda às 00h20 pertence ao movimento do dia anterior."""

from datetime import UTC, date, datetime, timedelta

from app.config import get_config

cfg = get_config()


def dia_operacional(momento: datetime) -> date:
    """Converte um instante (UTC) no dia de movimento da loja.

    A virada é às 04h no fuso da loja, não à meia-noite — e o fuso vem da
    config, nunca do relógio do servidor (a VPS roda em UTC).
    """
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=UTC)
    local = momento.astimezone(cfg.tz)
    if local.hour < cfg.hora_virada_dia:
        local -= timedelta(days=1)
    return local.date()


def dia_atual() -> date:
    return dia_operacional(datetime.now(UTC))


def atraso_aceitavel(criado_em_cliente: datetime, chegada: datetime | None = None) -> bool:
    """Um pedido da fila offline pode chegar horas depois. Mas se vier com
    horário absurdo — celular com relógio errado, ou data no futuro — o dia
    operacional sairia errado e sujaria o fechamento. Aí recusamos.
    """
    chegada = chegada or datetime.now(UTC)
    if criado_em_cliente.tzinfo is None:
        criado_em_cliente = criado_em_cliente.replace(tzinfo=UTC)
    atraso = chegada - criado_em_cliente
    # tolerância de 5 min pra frente cobre relógio de celular levemente adiantado
    return -timedelta(minutes=5) <= atraso <= timedelta(hours=cfg.atraso_max_horas)
