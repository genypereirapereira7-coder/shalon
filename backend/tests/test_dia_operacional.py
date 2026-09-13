"""O dia operacional é o que faz o fechamento bater com o caixa."""

from datetime import UTC, datetime, timedelta

from app.servicos.dia_operacional import (
    atraso_aceitavel,
    dia_a_fechar,
    dia_operacional,
    proximo_fechamento,
)


def _sp(ano, mes, dia, hora, minuto=0):
    """Um instante no fuso da loja, convertido pra UTC como o banco guarda."""
    from zoneinfo import ZoneInfo

    return datetime(ano, mes, dia, hora, minuto, tzinfo=ZoneInfo("America/Sao_Paulo")).astimezone(
        UTC
    )


def test_venda_da_tarde_cai_no_proprio_dia():
    assert dia_operacional(_sp(2026, 8, 11, 19, 42)).isoformat() == "2026-08-11"


def test_venda_depois_da_meia_noite_pertence_ao_dia_anterior():
    # 00h20 do dia 12 ainda é o movimento do dia 11.
    assert dia_operacional(_sp(2026, 8, 12, 0, 20)).isoformat() == "2026-08-11"


def test_virada_as_quatro_da_manha():
    assert dia_operacional(_sp(2026, 8, 12, 3, 59)).isoformat() == "2026-08-11"
    assert dia_operacional(_sp(2026, 8, 12, 4, 0)).isoformat() == "2026-08-12"


def test_fuso_nao_depende_do_relogio_do_servidor():
    # 02h UTC do dia 12 = 23h do dia 11 em São Paulo → movimento do dia 11.
    momento = datetime(2026, 8, 12, 2, 0, tzinfo=UTC)
    assert dia_operacional(momento).isoformat() == "2026-08-11"


def test_pedido_offline_atrasado_e_aceito():
    criado = datetime(2026, 8, 11, 22, 0, tzinfo=UTC)
    chegada = criado + timedelta(hours=3)
    assert atraso_aceitavel(criado, chegada)


def test_pedido_com_relogio_absurdo_e_recusado():
    criado = datetime(2026, 8, 11, 22, 0, tzinfo=UTC)
    assert not atraso_aceitavel(criado, criado + timedelta(hours=20))  # atrasado demais
    assert not atraso_aceitavel(criado, criado - timedelta(hours=2))  # celular no futuro


def test_pequeno_adiantamento_do_celular_e_tolerado():
    criado = datetime(2026, 8, 11, 22, 0, tzinfo=UTC)
    assert atraso_aceitavel(criado, criado - timedelta(minutes=2))


# ------------------------------------------------- hora do fechamento automático

def test_na_hora_marcada_fecha_o_movimento_da_vespera():
    """À 1h da manhã do dia 12, o que se fecha é o movimento do dia 11.

    É a razão de a hora do fechamento ter que ser menor que a virada: às 01h o
    dia operacional ainda é o de ontem, e é ele que acabou de terminar de
    verdade — a loja fechou as portas algumas horas antes.
    """
    assert dia_a_fechar(_sp(2026, 8, 12, 1, 0)).isoformat() == "2026-08-11"


def test_servidor_que_voltou_de_tarde_ainda_fecha_o_dia_que_ficou():
    """Caiu à 1h, voltou às 10h: o dia de ontem continua sendo o que fechar.

    Sem isto, um deploy no meio da madrugada faria o dia passar em branco — e
    ninguém repara num fechamento que não aconteceu.
    """
    assert dia_a_fechar(_sp(2026, 8, 12, 10, 30)).isoformat() == "2026-08-11"
    assert dia_a_fechar(_sp(2026, 8, 12, 23, 59)).isoformat() == "2026-08-11"


def test_antes_da_hora_marcada_o_alvo_ainda_e_o_dia_retrasado():
    """00h30: o fechamento da 1h ainda não chegou.

    O dia 11 só termina às 04h do dia 12, então à 00h30 ele ainda está em
    curso. O que já passou da hora é o dia 10.
    """
    assert dia_a_fechar(_sp(2026, 8, 12, 0, 30)).isoformat() == "2026-08-10"


def test_cada_dia_tem_o_seu_fechamento():
    """Dois dias seguidos fecham dias seguidos — não pula nem repete."""
    assert dia_a_fechar(_sp(2026, 8, 12, 1, 0)).isoformat() == "2026-08-11"
    assert dia_a_fechar(_sp(2026, 8, 13, 1, 0)).isoformat() == "2026-08-12"
    assert dia_a_fechar(_sp(2026, 8, 14, 1, 0)).isoformat() == "2026-08-13"


def test_proximo_disparo_e_sempre_no_futuro():
    """Inclusive no instante exato da hora marcada — senão o laço dispararia
    em rajada, fechando e redormindo zero segundo até virar o minuto."""
    assert proximo_fechamento(_sp(2026, 8, 12, 0, 30)).isoformat() == "2026-08-12T01:00:00-03:00"
    assert proximo_fechamento(_sp(2026, 8, 12, 1, 0)).isoformat() == "2026-08-13T01:00:00-03:00"
    assert proximo_fechamento(_sp(2026, 8, 12, 15, 0)).isoformat() == "2026-08-13T01:00:00-03:00"


def test_hora_do_fechamento_precisa_cair_antes_da_virada():
    """Configurar 04h fecharia o dia errado: às 04h em ponto o dia já virou, e
    o fechamento congelaria um dia recém-nascido e vazio, deixando o movimento
    de ontem aberto pra sempre. O config recusa em vez de deixar passar."""
    import pytest

    from app.config import Config

    for hora in (4, 5, 12, 23):
        with pytest.raises(ValueError, match="fechamento_automatico_hora"):
            Config(fechamento_automatico_hora=hora)

    for hora in (0, 1, 2, 3):
        assert Config(fechamento_automatico_hora=hora).fechamento_automatico_hora == hora
