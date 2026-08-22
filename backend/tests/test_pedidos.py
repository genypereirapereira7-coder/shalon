"""Criação de pedido, idempotência, numeração e ciclo de status.

O que estes testes protegem, em uma frase cada: o cliente não paga duas vezes,
a cozinha não recebe duas comandas, e o total que entra no caixa é o do
servidor — não o que veio do celular.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.cardapio import PrecoHistorico
from app.models.fechamento import FechamentoDia
from app.models.pedido import Pedido, StatusPedido
from app.servicos.dia_operacional import dia_atual


def corpo(dados, *itens, id_cliente=None, criado_em=None, **extra):
    """Monta o JSON que o celular manda. `itens` são pares (produto, qtd)."""
    return {
        "id_cliente": str(id_cliente or uuid.uuid4()),
        "criado_em_cliente": (criado_em or datetime.now(UTC)).isoformat(),
        "itens": [{"produto_id": p.id, "quantidade": q} for p, q in itens],
        **extra,
    }


@pytest.fixture
async def caixa(entrar, dados):
    """Header do funcionário que está vendendo."""
    return await entrar(dados["joao"].id, "1234")


# ------------------------------------------------------------------ criação

async def test_cria_pedido_e_devolve_numero(cliente, dados, caixa):
    resposta = await cliente.post(
        "/pedidos", json=corpo(dados, (dados["casquinha"], 2)), headers=caixa
    )

    assert resposta.status_code == 201
    pedido = resposta.json()
    assert pedido["numero_dia"] == 1
    assert pedido["status"] == "RECEBIDO"
    assert pedido["total_centavos"] == 1600
    assert pedido["usuario_nome"] == "João"
    assert pedido["itens"][0]["nome"] == "Casquinha 1 bola"
    assert pedido["itens"][0]["preco_unit_centavos"] == 800


async def test_total_do_celular_e_ignorado(cliente, dados, caixa):
    """Quem manda no valor é o banco. O JavaScript do celular qualquer um edita."""
    resposta = await cliente.post(
        "/pedidos",
        json=corpo(dados, (dados["casquinha"], 1), total_centavos=1),
        headers=caixa,
    )

    assert resposta.status_code == 201
    assert resposta.json()["total_centavos"] == 800
    assert resposta.json()["total_divergente"] is True


async def test_total_conferido_nao_marca_divergencia(cliente, dados, caixa):
    resposta = await cliente.post(
        "/pedidos",
        json=corpo(dados, (dados["casquinha"], 1), total_centavos=800),
        headers=caixa,
    )
    assert resposta.json()["total_divergente"] is False


async def test_itens_repetidos_viram_uma_linha(cliente, dados, caixa):
    """Apertar o botão três vezes tem que sair '3x' na comanda, não três linhas."""
    resposta = await cliente.post(
        "/pedidos",
        json=corpo(dados, (dados["casquinha"], 1), (dados["casquinha"], 2)),
        headers=caixa,
    )

    itens = resposta.json()["itens"]
    assert len(itens) == 1
    assert itens[0]["quantidade"] == 3
    assert itens[0]["subtotal_centavos"] == 2400


async def test_varios_produtos_somam_certo(cliente, dados, caixa):
    resposta = await cliente.post(
        "/pedidos",
        json=corpo(dados, (dados["casquinha"], 2), (dados["acai"], 1)),
        headers=caixa,
    )
    assert resposta.json()["total_centavos"] == 1600 + 1800


async def test_produto_inexistente_e_recusado(cliente, dados, caixa):
    pedido = corpo(dados, (dados["casquinha"], 1))
    pedido["itens"].append({"produto_id": 9999, "quantidade": 1})

    resposta = await cliente.post("/pedidos", json=pedido, headers=caixa)
    assert resposta.status_code == 422
    assert "inexistente" in resposta.json()["detail"]


async def test_pedido_sem_item_e_recusado(cliente, dados, caixa):
    assert (await cliente.post("/pedidos", json=corpo(dados), headers=caixa)).status_code == 422


async def test_pedido_exige_autenticacao(cliente, dados):
    assert (await cliente.post("/pedidos", json=corpo(dados, (dados["casquinha"], 1)))).status_code == 401


# ------------------------------------------------------- acompanhamentos

async def test_acompanhamentos_saem_na_comanda(cliente, dados, caixa, opcoes):
    """Grátis não mexe no preço, mas tem que chegar na cozinha."""
    pedido = corpo(dados)
    pedido["itens"] = [
        {
            "produto_id": dados["acai"].id,
            "quantidade": 1,
            "opcoes": [opcoes["pacoca"].id, opcoes["granola"].id],
        }
    ]

    resposta = await cliente.post("/pedidos", json=pedido, headers=caixa)

    assert resposta.status_code == 201
    item = resposta.json()["itens"][0]
    assert resposta.json()["total_centavos"] == 1800
    # Ordem do cardápio, não a ordem em que o funcionário tocou na tela.
    assert [o["nome"] for o in item["opcoes"]] == ["Granola", "Paçoca"]


async def test_adicional_pago_entra_no_total(cliente, dados, caixa, opcoes):
    pedido = corpo(dados)
    pedido["itens"] = [
        {
            "produto_id": dados["acai"].id,
            "quantidade": 2,
            "opcoes": [opcoes["granola"].id, opcoes["geleia"].id],
        }
    ]

    resposta = await cliente.post("/pedidos", json=pedido, headers=caixa)

    assert resposta.status_code == 201
    # (1800 do açaí + 300 da geléia) × 2 — o extra é por unidade.
    assert resposta.json()["total_centavos"] == 4200
    assert resposta.json()["itens"][0]["subtotal_centavos"] == 4200


async def test_cota_estourada_e_recusada(cliente, dados, caixa, opcoes):
    """A tela já limita, mas o JavaScript é do cliente. A trava é aqui."""
    pedido = corpo(dados)
    pedido["itens"] = [
        {
            "produto_id": dados["acai"].id,
            "quantidade": 1,
            "opcoes": [opcoes["granola"].id, opcoes["pacoca"].id, opcoes["inativa"].id],
        }
    ]

    resposta = await cliente.post("/pedidos", json=pedido, headers=caixa)

    assert resposta.status_code == 422
    assert "no máximo 2" in resposta.json()["detail"]


async def test_acompanhamento_de_outro_produto_e_recusado(cliente, dados, caixa, opcoes):
    """Granola é do açaí. Aceitar na casquinha seria adicional pago de graça."""
    pedido = corpo(dados)
    pedido["itens"] = [
        {
            "produto_id": dados["casquinha"].id,
            "quantidade": 1,
            "opcoes": [opcoes["granola"].id],
        }
    ]

    resposta = await cliente.post("/pedidos", json=pedido, headers=caixa)

    assert resposta.status_code == 422
    assert "não aceita" in resposta.json()["detail"]


async def test_escolha_obrigatoria_faltando_e_recusada(cliente, dados, caixa, opcoes):
    pedido = corpo(dados)
    pedido["itens"] = [
        {"produto_id": dados["casquinha"].id, "quantidade": 1, "opcoes": []}
    ]

    resposta = await cliente.post("/pedidos", json=pedido, headers=caixa)

    assert resposta.status_code == 422
    assert "pelo menos 1" in resposta.json()["detail"]


async def test_mesmo_produto_com_acompanhamentos_diferentes_sao_duas_linhas(
    cliente, dados, caixa, opcoes
):
    """Somar num '2x' faria a cozinha montar os dois açaís iguais."""
    pedido = corpo(dados)
    pedido["itens"] = [
        {"produto_id": dados["acai"].id, "quantidade": 1, "opcoes": [opcoes["granola"].id]},
        {"produto_id": dados["acai"].id, "quantidade": 1, "opcoes": [opcoes["pacoca"].id]},
    ]

    resposta = await cliente.post("/pedidos", json=pedido, headers=caixa)

    itens = resposta.json()["itens"]
    assert len(itens) == 2
    assert {i["opcoes"][0]["nome"] for i in itens} == {"Granola", "Paçoca"}


async def test_mesma_escolha_agrupa_em_uma_linha(cliente, dados, caixa, opcoes):
    pedido = corpo(dados)
    escolha = [opcoes["granola"].id, opcoes["pacoca"].id]
    pedido["itens"] = [
        {"produto_id": dados["acai"].id, "quantidade": 1, "opcoes": escolha},
        # Mesma escolha, ordem invertida: continua sendo o mesmo açaí.
        {"produto_id": dados["acai"].id, "quantidade": 2, "opcoes": escolha[::-1]},
    ]

    resposta = await cliente.post("/pedidos", json=pedido, headers=caixa)

    itens = resposta.json()["itens"]
    assert len(itens) == 1
    assert itens[0]["quantidade"] == 3


async def test_acompanhamento_inexistente_e_recusado(cliente, dados, caixa, opcoes):
    pedido = corpo(dados)
    pedido["itens"] = [
        {"produto_id": dados["acai"].id, "quantidade": 1, "opcoes": [99999]}
    ]

    resposta = await cliente.post("/pedidos", json=pedido, headers=caixa)

    assert resposta.status_code == 422
    assert "Opção inexistente" in resposta.json()["detail"]


# ------------------------------------------------------------- idempotência

async def test_reenvio_do_mesmo_id_nao_duplica(cliente, dados, caixa, sessao):
    """A internet oscilou no meio do ENVIAR e o funcionário apertou de novo.
    Uma venda, uma comanda, um número."""
    envio = corpo(dados, (dados["casquinha"], 1))

    primeira = await cliente.post("/pedidos", json=envio, headers=caixa)
    segunda = await cliente.post("/pedidos", json=envio, headers=caixa)

    assert primeira.status_code == 201
    assert segunda.status_code == 200  # já existia
    assert segunda.json()["duplicado"] is True
    assert primeira.json()["id"] == segunda.json()["id"]
    assert primeira.json()["numero_dia"] == segunda.json()["numero_dia"]

    total = list((await sessao.execute(select(Pedido))).scalars())
    assert len(total) == 1


async def test_numeracao_sobe_de_um_em_um(cliente, dados, caixa):
    numeros = []
    for _ in range(3):
        resposta = await cliente.post(
            "/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa
        )
        numeros.append(resposta.json()["numero_dia"])

    assert numeros == [1, 2, 3]


# ----------------------------------------------------- venda da fila offline

async def test_pedido_atrasado_vale_o_preco_da_hora_da_venda(cliente, dados, caixa, sessao):
    """Vendeu a R$8, dono subiu pra R$9, e só então o pedido subiu da fila.
    O cliente pagou R$8 — é R$8 que entra no caixa."""
    venda = datetime.now(UTC) - timedelta(hours=2)
    produto = dados["casquinha"]

    sessao.add(
        PrecoHistorico(
            produto_id=produto.id,
            preco_antigo=800,
            preco_novo=900,
            usuario_id=dados["dono"].id,
            criado_em=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    produto.preco_centavos = 900
    await sessao.commit()

    resposta = await cliente.post(
        "/pedidos", json=corpo(dados, (produto, 1), criado_em=venda), headers=caixa
    )
    assert resposta.json()["total_centavos"] == 800


async def test_relogio_absurdo_e_recusado(cliente, dados, caixa):
    """Celular com data errada geraria dia operacional furado e sujaria o fechamento."""
    antigo = datetime.now(UTC) - timedelta(days=3)
    resposta = await cliente.post(
        "/pedidos", json=corpo(dados, (dados["casquinha"], 1), criado_em=antigo), headers=caixa
    )
    assert resposta.status_code == 422
    assert "relógio" in resposta.json()["detail"]


async def test_pedido_no_futuro_e_recusado(cliente, dados, caixa):
    futuro = datetime.now(UTC) + timedelta(hours=2)
    resposta = await cliente.post(
        "/pedidos", json=corpo(dados, (dados["casquinha"], 1), criado_em=futuro), headers=caixa
    )
    assert resposta.status_code == 422


async def test_venda_depois_do_caixa_fechado_fica_marcada(cliente, dados, caixa, sessao):
    """O fechamento é imutável. A venda entra, mas acesa em vermelho pro dono."""
    sessao.add(
        FechamentoDia(
            data_operacional=dia_atual(),
            total_centavos=0,
            qtd_pedidos=0,
            fechado_por=dados["dono"].id,
        )
    )
    await sessao.commit()

    resposta = await cliente.post(
        "/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa
    )
    assert resposta.status_code == 201
    assert resposta.json()["pos_fechamento"] is True


# ------------------------------------------------------------------ listagem

async def test_hoje_lista_na_ordem_da_numeracao(cliente, dados, caixa):
    for _ in range(2):
        await cliente.post("/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa)

    resposta = await cliente.get("/pedidos/hoje", headers=caixa)
    assert resposta.status_code == 200
    assert [p["numero_dia"] for p in resposta.json()] == [1, 2]


# ------------------------------------------------------------------ impressão

async def test_fila_de_impressao_e_o_ack_do_agente(cliente, dados, caixa, entrar):
    agente = await entrar(dados["agente"].id, "0000")
    criado = await cliente.post(
        "/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa
    )
    pedido_id = criado.json()["id"]

    fila = await cliente.get("/pedidos/nao-impressos", headers=agente)
    assert [p["id"] for p in fila.json()] == [pedido_id]

    ack = await cliente.post(f"/pedidos/{pedido_id}/impresso", headers=agente)
    assert ack.status_code == 200
    assert ack.json()["impresso_em"] is not None

    vazia = await cliente.get("/pedidos/nao-impressos", headers=agente)
    assert vazia.json() == []


async def test_funcionario_nao_le_a_fila_do_agente(cliente, dados, caixa):
    assert (await cliente.get("/pedidos/nao-impressos", headers=caixa)).status_code == 403


async def test_o_balcao_confirma_a_propria_impressao(cliente, dados, caixa, entrar):
    """Quem imprime a comanda hoje é o celular que vendeu, pelo RawBT.

    Sem esta permissão a venda ficaria pra sempre em `/pedidos/nao-impressos`, e
    o agente do PC — quando alguém o mantém ligado — imprimiria uma segunda via
    de tudo que o balcão já tinha impresso.
    """
    criado = await cliente.post(
        "/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa
    )
    pedido_id = criado.json()["id"]

    ack = await cliente.post(f"/pedidos/{pedido_id}/impresso", headers=caixa)
    assert ack.status_code == 200
    assert ack.json()["impresso_em"] is not None

    agente = await entrar(dados["agente"].id, "0000")
    assert (await cliente.get("/pedidos/nao-impressos", headers=agente)).json() == []


async def test_reimprimir_devolve_o_pedido_pra_fila(cliente, dados, caixa, entrar):
    agente = await entrar(dados["agente"].id, "0000")
    cozinha = await entrar(dados["cozinha"].id, "0000")

    criado = await cliente.post(
        "/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa
    )
    pedido_id = criado.json()["id"]
    await cliente.post(f"/pedidos/{pedido_id}/impresso", headers=agente)

    resposta = await cliente.post(f"/pedidos/{pedido_id}/reimprimir", headers=cozinha)
    assert resposta.status_code == 200
    assert resposta.json()["impresso_em"] is None

    fila = await cliente.get("/pedidos/nao-impressos", headers=agente)
    assert [p["id"] for p in fila.json()] == [pedido_id]


async def test_cancelado_nao_entra_na_fila_de_impressao(cliente, dados, caixa, entrar):
    dono = await entrar(dados["dono"].id, "senhaforte")
    agente = await entrar(dados["agente"].id, "0000")

    criado = await cliente.post(
        "/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa
    )
    pedido_id = criado.json()["id"]
    await cliente.post(
        f"/pedidos/{pedido_id}/cancelar", json={"motivo": "cliente desistiu"}, headers=dono
    )

    assert (await cliente.get("/pedidos/nao-impressos", headers=agente)).json() == []


# --------------------------------------------------------------------- status

async def test_o_balcao_cancela_a_propria_venda(cliente, dados, caixa, entrar):
    """O erro acontece no balcão e o cliente está lá. Chamar o dono pra desfazer
    um pedido digitado errado deixaria a fila parada por um engano de dez
    segundos.
    """
    criado = await cliente.post(
        "/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa
    )
    pedido_id = criado.json()["id"]

    resposta = await cliente.post(
        f"/pedidos/{pedido_id}/cancelar",
        json={"motivo": "Cliente desistiu"},
        headers=caixa,
    )
    assert resposta.status_code == 200
    assert resposta.json()["status"] == "CANCELADO"
    assert resposta.json()["motivo_cancelamento"] == "Cliente desistiu"

    # E o valor sai do faturamento — é isso que "excluir" quer dizer.
    dono = await entrar(dados["dono"].id, "senhaforte")
    resumo = (await cliente.get("/relatorios/hoje", headers=dono)).json()
    assert resumo["total_centavos"] == 0
    assert resumo["cancelados_qtd"] == 1


async def test_funcionario_nao_cancela_venda_de_outro(cliente, dados, caixa, entrar, sessao):
    """Mexer no movimento de outro atendente passa por quem responde pelo caixa."""
    from app.models.usuario import Papel, Usuario
    from app.seguranca import gerar_hash

    outra = Usuario(nome="Maria", pin_hash=gerar_hash("4321"), papel=Papel.FUNCIONARIO)
    sessao.add(outra)
    await sessao.commit()

    criado = await cliente.post(
        "/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa
    )
    pedido_id = criado.json()["id"]

    de_maria = await entrar(outra.id, "4321")
    recusado = await cliente.post(
        f"/pedidos/{pedido_id}/cancelar", json={"motivo": "quis desfazer"}, headers=de_maria
    )
    assert recusado.status_code == 403
    assert "outro atendente" in recusado.json()["detail"]

    # O dono passa por cima da restrição — é ele quem responde pelo caixa.
    dono = await entrar(dados["dono"].id, "senhaforte")
    assert (
        await cliente.post(
            f"/pedidos/{pedido_id}/cancelar", json={"motivo": "conferido"}, headers=dono
        )
    ).status_code == 200


async def test_cancelar_exige_motivo(cliente, dados, caixa):
    """Sumir com dinheiro sem explicação é o que o motivo existe pra impedir."""
    criado = await cliente.post(
        "/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa
    )
    resposta = await cliente.post(
        f"/pedidos/{criado.json()['id']}/cancelar", json={"motivo": ""}, headers=caixa
    )
    assert resposta.status_code == 422


async def test_cozinha_avanca_o_status(cliente, dados, caixa, entrar):
    cozinha = await entrar(dados["cozinha"].id, "0000")
    criado = await cliente.post(
        "/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa
    )
    pedido_id = criado.json()["id"]

    for destino in ("EM_PREPARO", "PRONTO", "ENTREGUE"):
        resposta = await cliente.patch(
            f"/pedidos/{pedido_id}/status", json={"status": destino}, headers=cozinha
        )
        assert resposta.status_code == 200
        assert resposta.json()["status"] == destino


async def test_status_nao_anda_pra_tras(cliente, dados, caixa, entrar):
    cozinha = await entrar(dados["cozinha"].id, "0000")
    criado = await cliente.post(
        "/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa
    )
    pedido_id = criado.json()["id"]

    await cliente.patch(f"/pedidos/{pedido_id}/status", json={"status": "PRONTO"}, headers=cozinha)
    voltando = await cliente.patch(
        f"/pedidos/{pedido_id}/status", json={"status": "EM_PREPARO"}, headers=cozinha
    )
    assert voltando.status_code == 409


async def test_apertar_o_mesmo_status_duas_vezes_nao_e_erro(cliente, dados, caixa, entrar):
    cozinha = await entrar(dados["cozinha"].id, "0000")
    criado = await cliente.post(
        "/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa
    )
    pedido_id = criado.json()["id"]

    await cliente.patch(f"/pedidos/{pedido_id}/status", json={"status": "PRONTO"}, headers=cozinha)
    de_novo = await cliente.patch(
        f"/pedidos/{pedido_id}/status", json={"status": "PRONTO"}, headers=cozinha
    )
    assert de_novo.status_code == 200


async def test_cancelar_pelo_status_e_barrado(cliente, dados, caixa, entrar):
    """Cancelamento passa pela rota que exige motivo — não por um PATCH solto."""
    cozinha = await entrar(dados["cozinha"].id, "0000")
    criado = await cliente.post(
        "/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa
    )

    resposta = await cliente.patch(
        f"/pedidos/{criado.json()['id']}/status", json={"status": "CANCELADO"}, headers=cozinha
    )
    assert resposta.status_code == 400


# ---------------------------------------------------------------- cancelamento

async def test_dono_cancela_com_motivo(cliente, dados, caixa, entrar):
    dono = await entrar(dados["dono"].id, "senhaforte")
    criado = await cliente.post(
        "/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa
    )

    resposta = await cliente.post(
        f"/pedidos/{criado.json()['id']}/cancelar",
        json={"motivo": "cliente desistiu"},
        headers=dono,
    )
    assert resposta.status_code == 200
    assert resposta.json()["status"] == StatusPedido.CANCELADO.value
    assert resposta.json()["motivo_cancelamento"] == "cliente desistiu"
    assert resposta.json()["cancelado_em"] is not None


async def test_funcionario_nao_cancela_pedido_de_outro_dia(cliente, dados, caixa, sessao):
    """Mexer em movimento que já foi conferido passa por quem responde pelo caixa.

    O balcão desfaz o engano de agora — o de ontem já entrou em relatório, e
    pode ter sido usado pra fechar a gaveta.
    """
    criado = await cliente.post(
        "/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa
    )
    pedido_id = criado.json()["id"]

    de_ontem = await sessao.get(Pedido, uuid.UUID(pedido_id))
    de_ontem.data_operacional = dia_atual() - timedelta(days=1)
    await sessao.commit()

    resposta = await cliente.post(
        f"/pedidos/{pedido_id}/cancelar", json={"motivo": "quis desfazer"}, headers=caixa
    )
    assert resposta.status_code == 403
    assert "outro dia" in resposta.json()["detail"]


async def test_cancelar_sem_motivo_e_recusado(cliente, dados, caixa, entrar):
    dono = await entrar(dados["dono"].id, "senhaforte")
    criado = await cliente.post(
        "/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa
    )
    resposta = await cliente.post(
        f"/pedidos/{criado.json()['id']}/cancelar", json={"motivo": ""}, headers=dono
    )
    assert resposta.status_code == 422


async def test_cancelar_duas_vezes(cliente, dados, caixa, entrar):
    dono = await entrar(dados["dono"].id, "senhaforte")
    criado = await cliente.post(
        "/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa
    )
    url = f"/pedidos/{criado.json()['id']}/cancelar"

    assert (await cliente.post(url, json={"motivo": "desistiu"}, headers=dono)).status_code == 200
    assert (await cliente.post(url, json={"motivo": "desistiu"}, headers=dono)).status_code == 409


async def test_pedido_inexistente_da_404(cliente, dados, entrar):
    dono = await entrar(dados["dono"].id, "senhaforte")
    resposta = await cliente.post(
        f"/pedidos/{uuid.uuid4()}/cancelar", json={"motivo": "nada"}, headers=dono
    )
    assert resposta.status_code == 404


# ------------------------------------------------ filtro de status da cozinha

async def test_hoje_filtra_por_status(cliente, dados, entrar, caixa):
    """A tela da cozinha recarrega a cada poucos segundos.

    Sem o filtro ela baixaria o dia inteiro — num sábado, quase tudo comanda
    já entregue — só pra mostrar as três que ainda estão em produção.
    """
    a = (await cliente.post("/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa)).json()
    b = (await cliente.post("/pedidos", json=corpo(dados, (dados["acai"], 1)), headers=caixa)).json()
    await cliente.post("/pedidos", json=corpo(dados, (dados["casquinha"], 2)), headers=caixa)

    # Quem muda status é a cozinha, não o balcão.
    cozinha = await entrar(dados["cozinha"].id, "0000")
    await cliente.patch(f"/pedidos/{a['id']}/status", json={"status": "ENTREGUE"}, headers=cozinha)
    await cliente.patch(f"/pedidos/{b['id']}/status", json={"status": "EM_PREPARO"}, headers=cozinha)

    todos = (await cliente.get("/pedidos/hoje", headers=caixa)).json()
    assert len(todos) == 3

    em_producao = (
        await cliente.get(
            "/pedidos/hoje",
            params=[("status", "RECEBIDO"), ("status", "EM_PREPARO"), ("status", "PRONTO")],
            headers=caixa,
        )
    ).json()

    assert {p["status"] for p in em_producao} == {"RECEBIDO", "EM_PREPARO"}
    assert len(em_producao) == 2
    # E continua na ordem da numeração: a cozinha produz na ordem da venda.
    assert [p["numero_dia"] for p in em_producao] == sorted(p["numero_dia"] for p in em_producao)


async def test_status_invalido_no_filtro_e_recusado(cliente, caixa):
    resposta = await cliente.get("/pedidos/hoje", params={"status": "VOANDO"}, headers=caixa)
    assert resposta.status_code == 422


async def test_datas_do_pedido_levam_o_fuso(cliente, dados, entrar, caixa):
    """Sem o "Z" no JSON, o navegador lê a hora UTC como local.

    A tela da cozinha calcula "há quantos minutos" e o prazo de impressão em
    cima destes campos: deslocados, a comanda parece criada no futuro e o
    alerta de impressora travada nunca acende. O Postgres devolve datetime com
    fuso e o SQLite sem — então o descuido some em produção e vive em dev.
    """
    criado = (
        await cliente.post("/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa)
    ).json()

    agente = await entrar(dados["agente"].id, "0000")
    await cliente.post(f"/pedidos/{criado['id']}/impresso", headers=agente)

    pedido = (await cliente.get("/pedidos/hoje", headers=caixa)).json()[0]

    for campo in ("criado_em", "criado_em_cliente", "impresso_em"):
        valor = pedido[campo]
        assert valor.endswith("Z") or "+00:00" in valor, f"{campo}: {valor}"

    # E a data lida de volta tem que ser do passado, não do futuro.
    criado_em = datetime.fromisoformat(pedido["criado_em"])
    assert criado_em <= datetime.now(UTC)
