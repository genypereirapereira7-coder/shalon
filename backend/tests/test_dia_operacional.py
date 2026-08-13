"""O dia operacional é o que faz o fechamento bater com o caixa."""

from datetime import UTC, datetime, timedelta

from app.servicos.dia_operacional import atraso_aceitavel, dia_operacional


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
