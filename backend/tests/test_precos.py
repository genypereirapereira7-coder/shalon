"""Preço vigente no momento da venda.

É a decisão que separa "o cliente pagou o que viu" de "o relatório inventou
um número". Só aparece no offline, que é justamente quando ninguém está olhando.
"""

from datetime import UTC, datetime, timedelta

from app.models.cardapio import PrecoHistorico
from app.servicos.precos import preco_em, registrar_mudanca


async def test_sem_mudanca_vale_o_preco_atual(sessao, dados):
    agora = datetime.now(UTC)
    assert await preco_em(sessao, dados["casquinha"], agora) == 800


async def test_pedido_offline_vale_o_preco_da_hora_da_venda(sessao, dados):
    """Vendeu 14h a R$8. Dono subiu pra R$9 às 15h. O pedido de 14h sobe às 16h.
    Tem que valer R$8 — foi o que o cliente pagou."""
    venda = datetime.now(UTC) - timedelta(hours=2)
    mudanca = datetime.now(UTC) - timedelta(hours=1)

    produto = dados["casquinha"]
    sessao.add(
        PrecoHistorico(
            produto_id=produto.id,
            preco_antigo=800,
            preco_novo=900,
            usuario_id=dados["dono"].id,
            criado_em=mudanca,
        )
    )
    produto.preco_centavos = 900
    await sessao.commit()

    assert await preco_em(sessao, produto, venda) == 800
    assert await preco_em(sessao, produto, datetime.now(UTC)) == 900


async def test_varias_mudancas_pega_a_faixa_certa(sessao, dados):
    produto = dados["casquinha"]
    base = datetime.now(UTC) - timedelta(hours=5)

    # 800 → 900 (h-4) → 1000 (h-2)
    sessao.add_all(
        [
            PrecoHistorico(
                produto_id=produto.id,
                preco_antigo=800,
                preco_novo=900,
                usuario_id=dados["dono"].id,
                criado_em=base + timedelta(hours=1),
            ),
            PrecoHistorico(
                produto_id=produto.id,
                preco_antigo=900,
                preco_novo=1000,
                usuario_id=dados["dono"].id,
                criado_em=base + timedelta(hours=3),
            ),
        ]
    )
    produto.preco_centavos = 1000
    await sessao.commit()

    assert await preco_em(sessao, produto, base) == 800
    assert await preco_em(sessao, produto, base + timedelta(hours=2)) == 900
    assert await preco_em(sessao, produto, base + timedelta(hours=4)) == 1000


async def test_historico_de_outro_produto_nao_interfere(sessao, dados):
    outro = dados["inativo"]
    sessao.add(
        PrecoHistorico(
            produto_id=outro.id,
            preco_antigo=500,
            preco_novo=600,
            usuario_id=dados["dono"].id,
            criado_em=datetime.now(UTC),
        )
    )
    await sessao.commit()

    passado = datetime.now(UTC) - timedelta(hours=1)
    assert await preco_em(sessao, dados["casquinha"], passado) == 800


async def test_registrar_mudanca_aplica_e_audita(sessao, dados):
    produto = dados["casquinha"]
    await registrar_mudanca(sessao, produto, 950, dados["dono"].id)
    await sessao.commit()

    assert produto.preco_centavos == 950
    antes = datetime.now(UTC) - timedelta(minutes=1)
    assert await preco_em(sessao, produto, antes) == 800
