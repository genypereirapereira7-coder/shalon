"""O sabor do dia: o dono escreve, o balcão escolhe, a comanda congela.

O que estes testes cercam é a diferença entre o sabor *de hoje* e o sabor *que
foi vendido*. São coisas separadas de propósito: a máquina troca de sabor toda
manhã, e a comanda de ontem não pode mudar junto.
"""

import uuid
from datetime import UTC, datetime

from app.models.sabor import EscolhaSabor


async def _pedir(cliente, cabecalho, itens):
    return await cliente.post(
        "/pedidos",
        json={
            "id_cliente": str(uuid.uuid4()),
            "criado_em_cliente": datetime.now(UTC).isoformat(),
            "itens": itens,
        },
        headers=cabecalho,
    )


async def _preparar(cliente, dados, entrar, sabor1="Chocolate", sabor2="Morango"):
    """Sabores definidos e a casquinha marcada como quem pede sabor."""
    dono = await entrar(dados["dono"].id, "senhaforte")
    await cliente.put("/sabores", json={"sabor1": sabor1, "sabor2": sabor2}, headers=dono)
    await cliente.patch(
        f"/produtos/{dados['casquinha'].id}",
        json={"pede_sabor": True},
        headers=dono,
    )
    return dono


# --------------------------------------------------------------- a tela do dono

async def test_comeca_vazio(cliente, dados, entrar):
    cabecalho = await entrar(dados["dono"].id, "senhaforte")
    resposta = await cliente.get("/sabores", headers=cabecalho)
    assert resposta.status_code == 200
    assert resposta.json()["sabor1"] is None
    assert resposta.json()["sabor2"] is None


async def test_dono_troca_os_sabores(cliente, dados, entrar):
    cabecalho = await entrar(dados["dono"].id, "senhaforte")
    resposta = await cliente.put(
        "/sabores", json={"sabor1": "Chocolate", "sabor2": "Morango"}, headers=cabecalho
    )
    assert resposta.status_code == 200
    assert resposta.json()["sabor1"] == "Chocolate"
    assert resposta.json()["sabor2"] == "Morango"
    assert resposta.json()["atualizado_em"] is not None


async def test_funcionario_le_mas_nao_troca(cliente, dados, entrar):
    dono = await entrar(dados["dono"].id, "senhaforte")
    await cliente.put("/sabores", json={"sabor1": "Creme", "sabor2": "Flocos"}, headers=dono)

    balcao = await entrar(dados["joao"].id, "1234")
    lido = await cliente.get("/sabores", headers=balcao)
    assert lido.json()["sabor1"] == "Creme"

    recusa = await cliente.put(
        "/sabores", json={"sabor1": "Invadido", "sabor2": "x"}, headers=balcao
    )
    assert recusa.status_code == 403


async def test_espaco_sobrando_some(cliente, dados, entrar):
    """Chocolate com espaço na ponta viraria outro sabor na comanda impressa."""
    cabecalho = await entrar(dados["dono"].id, "senhaforte")
    resposta = await cliente.put(
        "/sabores", json={"sabor1": "  Chocolate  ", "sabor2": "   "}, headers=cabecalho
    )
    assert resposta.json()["sabor1"] == "Chocolate"
    assert resposta.json()["sabor2"] is None


# ------------------------------------------------------------ a tela de vendas

async def test_sabor_vai_pro_item(cliente, dados, entrar):
    await _preparar(cliente, dados, entrar)
    balcao = await entrar(dados["joao"].id, "1234")

    resposta = await _pedir(
        cliente,
        balcao,
        [{"produto_id": dados["casquinha"].id, "quantidade": 1, "sabor": "SABOR_1"}],
    )
    assert resposta.status_code == 201, resposta.text
    item = resposta.json()["itens"][0]
    assert item["sabor_tipo"] == "SABOR_1"
    assert item["sabor"] == "Chocolate"


async def test_misto_junta_os_dois(cliente, dados, entrar):
    await _preparar(cliente, dados, entrar)
    balcao = await entrar(dados["joao"].id, "1234")

    resposta = await _pedir(
        cliente,
        balcao,
        [{"produto_id": dados["casquinha"].id, "quantidade": 1, "sabor": "MISTO"}],
    )
    assert resposta.json()["itens"][0]["sabor"] == "Chocolate + Morango"


async def test_sabores_diferentes_nao_viram_uma_linha_so(cliente, dados, entrar):
    """Duas casquinhas de sabores diferentes somariam 2x e a cozinha serviria
    as duas iguais."""
    await _preparar(cliente, dados, entrar)
    balcao = await entrar(dados["joao"].id, "1234")

    resposta = await _pedir(
        cliente,
        balcao,
        [
            {"produto_id": dados["casquinha"].id, "quantidade": 1, "sabor": "SABOR_1"},
            {"produto_id": dados["casquinha"].id, "quantidade": 1, "sabor": "SABOR_2"},
        ],
    )
    itens = resposta.json()["itens"]
    assert len(itens) == 2, "sabores diferentes viraram uma linha só"
    assert {i["sabor"] for i in itens} == {"Chocolate", "Morango"}


async def test_mesmo_sabor_ainda_agrupa(cliente, dados, entrar):
    await _preparar(cliente, dados, entrar)
    balcao = await entrar(dados["joao"].id, "1234")

    resposta = await _pedir(
        cliente,
        balcao,
        [
            {"produto_id": dados["casquinha"].id, "quantidade": 1, "sabor": "SABOR_1"},
            {"produto_id": dados["casquinha"].id, "quantidade": 2, "sabor": "SABOR_1"},
        ],
    )
    itens = resposta.json()["itens"]
    assert len(itens) == 1
    assert itens[0]["quantidade"] == 3


async def test_produto_que_nao_pede_sabor_ignora_a_escolha(cliente, dados, entrar):
    """Celular com cardápio velho manda sabor num produto que o dono
    desmarcou. Isso não pode recusar a venda."""
    await _preparar(cliente, dados, entrar)
    balcao = await entrar(dados["joao"].id, "1234")

    resposta = await _pedir(
        cliente,
        balcao,
        [{"produto_id": dados["acai"].id, "quantidade": 1, "sabor": "MISTO"}],
    )
    assert resposta.status_code == 201
    assert resposta.json()["itens"][0]["sabor"] is None
    assert resposta.json()["itens"][0]["sabor_tipo"] is None


async def test_vende_mesmo_sem_sabor_definido(cliente, dados, entrar):
    """O dono não preencheu os sabores hoje. A loja não pode parar por isso."""
    dono = await entrar(dados["dono"].id, "senhaforte")
    await cliente.patch(
        f"/produtos/{dados['casquinha'].id}",
        json={"pede_sabor": True},
        headers=dono,
    )
    balcao = await entrar(dados["joao"].id, "1234")

    resposta = await _pedir(
        cliente,
        balcao,
        [{"produto_id": dados["casquinha"].id, "quantidade": 1, "sabor": "SABOR_1"}],
    )
    assert resposta.status_code == 201
    assert resposta.json()["itens"][0]["sabor"] is None


async def test_misto_com_um_sabor_so_nao_imprime_lixo(cliente, dados, entrar):
    """Misto com um campo vazio sairia como Chocolate + nada, e a cozinha
    ficaria adivinhando."""
    await _preparar(cliente, dados, entrar, sabor1="Chocolate", sabor2=None)
    balcao = await entrar(dados["joao"].id, "1234")

    resposta = await _pedir(
        cliente,
        balcao,
        [{"produto_id": dados["casquinha"].id, "quantidade": 1, "sabor": "MISTO"}],
    )
    assert resposta.json()["itens"][0]["sabor"] == "Chocolate"


# ------------------------------------------------------------------ congelamento

async def test_trocar_o_sabor_nao_mexe_na_venda_de_antes(cliente, dados, entrar):
    """O motivo de existir o snapshot: amanhã a máquina tem outro sabor."""
    dono = await _preparar(cliente, dados, entrar)
    balcao = await entrar(dados["joao"].id, "1234")

    venda = await _pedir(
        cliente,
        balcao,
        [{"produto_id": dados["casquinha"].id, "quantidade": 1, "sabor": "SABOR_1"}],
    )
    pedido_id = venda.json()["id"]

    # No dia seguinte a máquina troca.
    await cliente.put("/sabores", json={"sabor1": "Creme", "sabor2": "Flocos"}, headers=dono)

    hoje = await cliente.get("/pedidos/hoje", headers=dono)
    antigo = next(p for p in hoje.json() if p["id"] == pedido_id)
    assert antigo["itens"][0]["sabor"] == "Chocolate", "a venda de ontem mudou sozinha"


def test_enum_tem_os_tres():
    assert {e.value for e in EscolhaSabor} == {"SABOR_1", "SABOR_2", "MISTO"}
