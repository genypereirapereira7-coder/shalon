"""Relatórios do dono e fechamento de caixa.

O que estes testes protegem: o número que o dono vê na tela é o mesmo que está
na gaveta. Venda cancelada não infla o faturamento, adicional pago entra no
valor do item que o levou, e fechamento gravado não muda depois — nem quando
uma venda atrasada sobe da fila offline.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.fechamento import FechamentoDia
from app.servicos.dia_operacional import dia_atual


def corpo(*itens, id_cliente=None, criado_em=None, **extra):
    """JSON do celular. `itens` são pares (produto, quantidade)."""
    return {
        "id_cliente": str(id_cliente or uuid.uuid4()),
        "criado_em_cliente": (criado_em or datetime.now(UTC)).isoformat(),
        "itens": [{"produto_id": p.id, "quantidade": q} for p, q in itens],
        **extra,
    }


@pytest.fixture
async def caixa(entrar, dados):
    return await entrar(dados["joao"].id, "1234")


@pytest.fixture
async def dono(entrar, dados):
    return await entrar(dados["dono"].id, "senhaforte")


# ------------------------------------------------------------------ permissão

async def test_funcionario_nao_ve_relatorio(cliente, caixa):
    """O faturamento do dia é do dono. Quem está no balcão não precisa ver."""
    assert (await cliente.get("/relatorios/hoje", headers=caixa)).status_code == 403
    assert (await cliente.get("/fechamento", headers=caixa)).status_code == 403
    assert (await cliente.post("/fechamento", json={}, headers=caixa)).status_code == 403


async def test_sem_token_nao_ve_relatorio(cliente):
    assert (await cliente.get("/relatorios/hoje")).status_code == 401


# --------------------------------------------------------------------- resumo

async def test_dia_sem_venda_vem_zerado(cliente, dono):
    resumo = (await cliente.get("/relatorios/hoje", headers=dono)).json()

    assert resumo["qtd_pedidos"] == 0
    assert resumo["total_centavos"] == 0
    assert resumo["ticket_medio_centavos"] == 0
    assert resumo["itens"] == []
    assert resumo["fechamento"] is None


async def test_soma_pedidos_e_calcula_ticket(cliente, dados, caixa, dono):
    # 2 casquinhas (1600) + 1 açaí (1800) = 3400 num pedido; 800 no outro.
    await cliente.post(
        "/pedidos", json=corpo((dados["casquinha"], 2), (dados["acai"], 1)), headers=caixa
    )
    await cliente.post("/pedidos", json=corpo((dados["casquinha"], 1)), headers=caixa)

    resumo = (await cliente.get("/relatorios/hoje", headers=dono)).json()

    assert resumo["qtd_pedidos"] == 2
    assert resumo["total_centavos"] == 4200
    assert resumo["ticket_medio_centavos"] == 2100


async def test_ticket_medio_arredonda_em_vez_de_truncar(cliente, dados, caixa, dono):
    """800 e 1800 em dois pedidos dão 1300 exatos; 3 pedidos testam o meio.

    800 + 800 + 1800 = 3400 / 3 = 1133,33… → 1133. Truncar e arredondar dão o
    mesmo aqui; o que o teste trava é que a conta não vira float e volte
    1133.3333333 pro celular formatar errado.
    """
    for _ in range(2):
        await cliente.post("/pedidos", json=corpo((dados["casquinha"], 1)), headers=caixa)
    await cliente.post("/pedidos", json=corpo((dados["acai"], 1)), headers=caixa)

    resumo = (await cliente.get("/relatorios/hoje", headers=dono)).json()

    assert resumo["total_centavos"] == 3400
    assert resumo["ticket_medio_centavos"] == 1133
    assert isinstance(resumo["ticket_medio_centavos"], int)


async def test_cancelado_sai_do_faturamento_e_e_contado_a_parte(cliente, dados, caixa, dono):
    """Cancelar é dinheiro que não entrou — mas o dono precisa ver que houve."""
    criado = (
        await cliente.post("/pedidos", json=corpo((dados["acai"], 1)), headers=caixa)
    ).json()
    await cliente.post("/pedidos", json=corpo((dados["casquinha"], 1)), headers=caixa)

    await cliente.post(
        f"/pedidos/{criado['id']}/cancelar",
        json={"motivo": "cliente desistiu"},
        headers=dono,
    )

    resumo = (await cliente.get("/relatorios/hoje", headers=dono)).json()

    assert resumo["qtd_pedidos"] == 1
    assert resumo["total_centavos"] == 800
    assert resumo["cancelados_qtd"] == 1
    assert resumo["cancelados_centavos"] == 1800
    # E o açaí cancelado não pode aparecer no ranking de itens vendidos.
    assert [i["nome"] for i in resumo["itens"]] == ["Casquinha 1 bola"]


# -------------------------------------------------------------------- ranking

async def test_ranking_ordena_pelo_que_mais_rendeu(cliente, dados, caixa, dono):
    # 3 casquinhas = 2400; 2 açaís = 3600. O açaí rende mais mesmo saindo menos.
    await cliente.post("/pedidos", json=corpo((dados["casquinha"], 3)), headers=caixa)
    await cliente.post("/pedidos", json=corpo((dados["acai"], 2)), headers=caixa)

    itens = (await cliente.get("/relatorios/hoje", headers=dono)).json()["itens"]

    assert [(i["nome"], i["quantidade"], i["total_centavos"]) for i in itens] == [
        ("Açaí 500ml", 2, 3600),
        ("Casquinha 1 bola", 3, 2400),
    ]


async def test_ranking_soma_o_mesmo_produto_de_pedidos_diferentes(
    cliente, dados, caixa, dono
):
    for _ in range(3):
        await cliente.post("/pedidos", json=corpo((dados["casquinha"], 1)), headers=caixa)

    itens = (await cliente.get("/relatorios/hoje", headers=dono)).json()["itens"]

    assert len(itens) == 1
    assert itens[0]["quantidade"] == 3
    assert itens[0]["total_centavos"] == 2400


async def test_adicional_pago_entra_no_valor_do_item(cliente, dados, opcoes, caixa, dono):
    """Açaí 1800 + geléia 300 = 2100 na linha do açaí, não numa linha à parte.

    É como o dono pensa o faturamento por produto: "quanto o açaí me deu",
    incluindo o que foi vendido junto dele.
    """
    await cliente.post(
        "/pedidos",
        json={
            "id_cliente": str(uuid.uuid4()),
            "criado_em_cliente": datetime.now(UTC).isoformat(),
            "itens": [
                {
                    "produto_id": dados["acai"].id,
                    "quantidade": 1,
                    "opcoes": [opcoes["geleia"].id],
                }
            ],
        },
        headers=caixa,
    )

    resumo = (await cliente.get("/relatorios/hoje", headers=dono)).json()

    assert resumo["total_centavos"] == 2100
    assert len(resumo["itens"]) == 1
    assert resumo["itens"][0]["nome"] == "Açaí 500ml"
    assert resumo["itens"][0]["total_centavos"] == 2100


# ----------------------------------------------------------------- atendentes

async def test_quebra_por_atendente(cliente, dados, entrar, caixa, dono):
    """O relatório diz quem vendeu — é o token que atribui, não o celular."""
    await cliente.post("/pedidos", json=corpo((dados["acai"], 1)), headers=caixa)

    do_dono = await entrar(dados["dono"].id, "senhaforte")
    await cliente.post("/pedidos", json=corpo((dados["casquinha"], 1)), headers=do_dono)

    por_atendente = (await cliente.get("/relatorios/hoje", headers=dono)).json()[
        "por_atendente"
    ]

    assert [(a["nome"], a["qtd_pedidos"], a["total_centavos"]) for a in por_atendente] == [
        ("João", 1, 1800),
        ("Dona Shalon", 1, 800),
    ]


# ----------------------------------------------------------------- fechamento

async def test_fecha_o_caixa_e_congela_o_total(cliente, dados, caixa, dono):
    await cliente.post("/pedidos", json=corpo((dados["acai"], 1)), headers=caixa)

    resposta = await cliente.post("/fechamento", json={}, headers=dono)

    assert resposta.status_code == 201
    fechamento = resposta.json()
    assert fechamento["total_centavos"] == 1800
    assert fechamento["qtd_pedidos"] == 1
    assert fechamento["fechado_por_nome"] == "Dona Shalon"
    assert fechamento["data_operacional"] == dia_atual().isoformat()

    # E o resumo passa a trazer o fechamento junto.
    resumo = (await cliente.get("/relatorios/hoje", headers=dono)).json()
    assert resumo["fechamento"]["total_centavos"] == 1800


async def test_fechado_em_leva_o_fuso(cliente, dados, caixa, dono):
    """Sem o "Z" no JSON, o celular lê a hora UTC como local.

    O dono veria o caixa fechado três horas no futuro. Como o Postgres devolve
    datetime com fuso e o SQLite sem, o bug apareceria só em dev — ou só em
    produção, dependendo de onde o descuido estivesse.
    """
    await cliente.post("/pedidos", json=corpo((dados["acai"], 1)), headers=caixa)
    await cliente.post("/fechamento", json={}, headers=dono)

    resumo = (await cliente.get("/relatorios/hoje", headers=dono)).json()

    for campo in (resumo["apurado_em"], resumo["fechamento"]["fechado_em"]):
        assert campo.endswith("Z") or "+00:00" in campo, campo

    # E as duas horas têm que ser próximas: foram geradas no mesmo teste.
    apurado = datetime.fromisoformat(resumo["apurado_em"])
    fechado = datetime.fromisoformat(resumo["fechamento"]["fechado_em"])
    assert abs((apurado - fechado).total_seconds()) < 60


async def test_nao_fecha_duas_vezes(cliente, dono):
    assert (await cliente.post("/fechamento", json={}, headers=dono)).status_code == 201

    repetido = await cliente.post("/fechamento", json={}, headers=dono)
    assert repetido.status_code == 409
    assert "já foi fechado" in repetido.json()["detail"]


async def test_nao_fecha_dia_futuro(cliente, dono):
    amanha = (dia_atual() + timedelta(days=1)).isoformat()

    resposta = await cliente.post(
        "/fechamento", json={"data_operacional": amanha}, headers=dono
    )

    assert resposta.status_code == 409
    assert "ainda não aconteceu" in resposta.json()["detail"]


async def test_total_conferido_diferente_recusa_o_fechamento(cliente, dados, caixa, dono):
    """O dono conferiu R$18 na tela e uma venda entrou antes do toque no botão.

    Fechar assim congelaria um número que ele nunca aprovou. Melhor recusar e
    deixar ele conferir de novo.
    """
    await cliente.post("/pedidos", json=corpo((dados["acai"], 1)), headers=caixa)
    await cliente.post("/pedidos", json=corpo((dados["casquinha"], 1)), headers=caixa)

    resposta = await cliente.post(
        "/fechamento", json={"total_conferido_centavos": 1800}, headers=dono
    )

    assert resposta.status_code == 409
    assert "mudou desde a conferência" in resposta.json()["detail"]

    # Nada foi gravado: o dia continua aberto.
    resumo = (await cliente.get("/relatorios/hoje", headers=dono)).json()
    assert resumo["fechamento"] is None


async def test_total_conferido_igual_fecha(cliente, dados, caixa, dono):
    await cliente.post("/pedidos", json=corpo((dados["acai"], 1)), headers=caixa)

    resposta = await cliente.post(
        "/fechamento", json={"total_conferido_centavos": 1800}, headers=dono
    )

    assert resposta.status_code == 201


async def test_venda_atrasada_apos_fechamento_aparece_separada(
    cliente, dados, caixa, dono
):
    """A fila offline subiu depois de o caixa fechar.

    A venda existiu e entra no total do dia; o fechamento gravado não muda. Sem
    o destaque, o dono compararia relatório e gaveta e acharia que faltou.
    """
    await cliente.post("/pedidos", json=corpo((dados["acai"], 1)), headers=caixa)
    await cliente.post("/fechamento", json={}, headers=dono)

    atrasado = await cliente.post(
        "/pedidos",
        json=corpo((dados["casquinha"], 1), criado_em=datetime.now(UTC) - timedelta(hours=2)),
        headers=caixa,
    )
    assert atrasado.status_code == 201
    assert atrasado.json()["pos_fechamento"] is True

    resumo = (await cliente.get("/relatorios/hoje", headers=dono)).json()

    assert resumo["total_centavos"] == 2600           # o dia todo, ao vivo
    assert resumo["fechamento"]["total_centavos"] == 1800  # congelado, não mexeu
    assert resumo["pos_fechamento_qtd"] == 1
    assert resumo["pos_fechamento_centavos"] == 800


# -------------------------------------------------------------------- por data

async def test_relatorio_de_um_dia_passado(cliente, dados, sessao, caixa, dono):
    ontem = dia_atual() - timedelta(days=1)
    sessao.add(
        FechamentoDia(
            data_operacional=ontem,
            total_centavos=12345,
            qtd_pedidos=7,
            fechado_por=dados["dono"].id,
        )
    )
    await sessao.commit()

    resumo = (await cliente.get(f"/relatorios/dia/{ontem}", headers=dono)).json()

    # Sem pedidos gravados naquele dia, o movimento é zero — mas o fechamento
    # que existe é devolvido do mesmo jeito.
    assert resumo["data_operacional"] == ontem.isoformat()
    assert resumo["qtd_pedidos"] == 0
    assert resumo["fechamento"]["total_centavos"] == 12345
    assert resumo["fechamento"]["qtd_pedidos"] == 7


async def test_historico_traz_o_mais_recente_primeiro(cliente, dados, sessao, dono):
    for dias, total in ((3, 100), (1, 300), (2, 200)):
        sessao.add(
            FechamentoDia(
                data_operacional=dia_atual() - timedelta(days=dias),
                total_centavos=total,
                qtd_pedidos=1,
                fechado_por=dados["dono"].id,
            )
        )
    await sessao.commit()

    historico = (await cliente.get("/fechamento", headers=dono)).json()

    assert [f["total_centavos"] for f in historico] == [300, 200, 100]
    assert all(f["fechado_por_nome"] == "Dona Shalon" for f in historico)
