"""Dia operacional: uma venda às 00h20 pertence ao movimento do dia anterior."""

from datetime import UTC, date, datetime, time, timedelta

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


def _hora_marcada(dia_calendario: date) -> datetime:
    """A hora do fechamento automático, no fuso da loja, num dia do calendário."""
    return datetime.combine(
        dia_calendario, time(hour=cfg.fechamento_automatico_hora), tzinfo=cfg.tz
    )


def proximo_fechamento(momento: datetime | None = None) -> datetime:
    """Quando o caixa fecha sozinho da próxima vez."""
    local = (momento or datetime.now(UTC)).astimezone(cfg.tz)
    alvo = _hora_marcada(local.date())
    if alvo <= local:
        alvo = _hora_marcada(local.date() + timedelta(days=1))
    return alvo


def dia_a_fechar(momento: datetime | None = None) -> date:
    """O dia operacional cujo fechamento automático já deveria ter acontecido.

    Não é `dia_atual()`. Às 10h da manhã o dia de hoje mal começou e não tem o
    que congelar — o que passou da hora foi o fechamento da 1h, e o dia que ele
    fecha é o movimento de ontem. Devolver isso, e não o dia corrente, é o que
    faz o servidor que ficou fora do ar na hora marcada conseguir se acertar
    quando volta.

    Funciona porque a hora do fechamento é menor que a virada (o config recusa
    o contrário): às 01h, `dia_operacional` ainda devolve a data de ontem, que
    é exatamente o dia que se quer fechar.
    """
    local = (momento or datetime.now(UTC)).astimezone(cfg.tz)
    ultima = _hora_marcada(local.date())
    if ultima > local:
        ultima = _hora_marcada(local.date() - timedelta(days=1))
    return dia_operacional(ultima)
