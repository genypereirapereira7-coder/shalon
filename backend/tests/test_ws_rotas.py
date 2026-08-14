"""As rotas anunciam mesmo o que dizem anunciar.

O `test_ws.py` cuida do hub e do aperto de mão. Aqui a pergunta é outra: uma
venda que entra pelo `POST /pedidos` faz papel sair na cozinha? Um reenvio faz
sair duas vezes? A resposta mora na conversa entre a rota e o hub, e é ela que
estes testes assinam — grampeando o hub de verdade que a aplicação usa.
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.models.usuario import Papel
from app.servicos.eventos import hub


class Espiao:
    def __init__(self) -> None:
        self.recebidos: list[dict] = []

    async def send_json(self, dados) -> None:
        self.recebidos.append(dados)

    def eventos(self) -> list[str]:
        return [m["evento"] for m in self.recebidos]

    def dados_de(self, evento: str) -> list[dict]:
        return [m["dados"] for m in self.recebidos if m["evento"] == evento]


@pytest.fixture
def assinar():
    """Grampeia o hub da aplicação e limpa tudo no fim do teste."""
    ligados: list[tuple[Espiao, Papel]] = []

    def _assinar(papel: Papel) -> Espiao:
        espiao = Espiao()
        hub.entrar(espiao, papel)
        ligados.append((espiao, papel))
        return espiao

    yield _assinar

    for espiao, papel in ligados:
        hub.sair(espiao, papel)


def corpo(dados, *itens, id_cliente=None):
    return {
        "id_cliente": str(id_cliente or uuid.uuid4()),
        "criado_em_cliente": datetime.now(UTC).isoformat(),
        "itens": [{"produto_id": p.id, "quantidade": q} for p, q in itens],
    }


@pytest.fixture
async def caixa(entrar, dados):
    return await entrar(dados["joao"].id, "1234")


# ------------------------------------------------------------------- pedidos

async def test_venda_chega_na_cozinha_e_no_agente(cliente, dados, caixa, assinar):
    cozinha, agente = assinar(Papel.COZINHA), assinar(Papel.AGENTE)

    criado = (
        await cliente.post("/pedidos", json=corpo(dados, (dados["casquinha"], 2)), headers=caixa)
    ).json()

    assert cozinha.eventos() == ["pedido.novo"]
    assert agente.eventos() == ["pedido.novo"]

    # O evento leva o pedido inteiro: o agente precisa dos itens pra imprimir a
    # comanda sem uma segunda ida ao servidor.
    anunciado = agente.dados_de("pedido.novo")[0]
    assert anunciado["id"] == criado["id"]
    assert anunciado["numero_dia"] == criado["numero_dia"]
    assert anunciado["itens"][0]["nome"] == "Casquinha 1 bola"
    assert anunciado["usuario_nome"] == "João"


async def test_reenvio_do_mesmo_pedido_nao_anuncia_de_novo(cliente, dados, caixa, assinar):
    """A internet oscilou no meio do ENVIAR e o celular mandou duas vezes.

    Anunciar o segundo faria o agente imprimir uma comanda a mais — que é
    exatamente o que a idempotência por `id_cliente` existe pra evitar.
    """
    agente = assinar(Papel.AGENTE)
    mesmo = uuid.uuid4()
    envio = corpo(dados, (dados["casquinha"], 1), id_cliente=mesmo)

    primeira = await cliente.post("/pedidos", json=envio, headers=caixa)
    segunda = await cliente.post("/pedidos", json=envio, headers=caixa)

    assert primeira.status_code == 201
    assert segunda.status_code == 200
    assert agente.eventos() == ["pedido.novo"]


async def test_pedido_recusado_nao_anuncia_nada(cliente, dados, caixa, assinar):
    """Comanda fantasma na cozinha é papel gasto e sorvete montado à toa."""
    agente = assinar(Papel.AGENTE)

    resposta = await cliente.post(
        "/pedidos",
        json={
            "id_cliente": str(uuid.uuid4()),
            "criado_em_cliente": datetime.now(UTC).isoformat(),
            "itens": [{"produto_id": 99999, "quantidade": 1}],
        },
        headers=caixa,
    )

    assert resposta.status_code == 422
    assert agente.eventos() == []


async def test_mudanca_de_status_avisa_balcao_e_cozinha(cliente, dados, entrar, caixa, assinar):
    vendas, cozinha = assinar(Papel.FUNCIONARIO), assinar(Papel.COZINHA)
    pedido = (
        await cliente.post("/pedidos", json=corpo(dados, (dados["acai"], 1)), headers=caixa)
    ).json()

    cozinheiro = await entrar(dados["cozinha"].id, "0000")
    await cliente.patch(
        f"/pedidos/{pedido['id']}/status", json={"status": "PRONTO"}, headers=cozinheiro
    )

    assert vendas.eventos() == ["pedido.status"]
    assert cozinha.eventos() == ["pedido.novo", "pedido.status"]
    assert cozinha.dados_de("pedido.status")[0]["status"] == "PRONTO"


async def test_status_repetido_nao_vira_evento(cliente, dados, entrar, caixa, assinar):
    """A cozinha apertou PRONTA duas vezes. Nada mudou, nada a anunciar."""
    cozinha = assinar(Papel.COZINHA)
    pedido = (
        await cliente.post("/pedidos", json=corpo(dados, (dados["acai"], 1)), headers=caixa)
    ).json()
    cozinheiro = await entrar(dados["cozinha"].id, "0000")

    for _ in range(2):
        await cliente.patch(
            f"/pedidos/{pedido['id']}/status", json={"status": "PRONTO"}, headers=cozinheiro
        )

    assert cozinha.eventos() == ["pedido.novo", "pedido.status"]


# ---------------------------------------------------------------- impressão

async def test_ack_do_agente_apaga_o_alerta_da_cozinha(cliente, dados, entrar, caixa, assinar):
    cozinha = assinar(Papel.COZINHA)
    pedido = (
        await cliente.post("/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa)
    ).json()

    agente = await entrar(dados["agente"].id, "0000")
    await cliente.post(f"/pedidos/{pedido['id']}/impresso", headers=agente)

    assert cozinha.eventos() == ["pedido.novo", "pedido.impresso"]
    assert cozinha.dados_de("pedido.impresso")[0]["impresso_em"] is not None


async def test_ack_repetido_nao_vira_evento(cliente, dados, entrar, caixa, assinar):
    """O agente reenvia o ACK quando o primeiro se perdeu na rede."""
    cozinha = assinar(Papel.COZINHA)
    pedido = (
        await cliente.post("/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa)
    ).json()
    agente = await entrar(dados["agente"].id, "0000")

    for _ in range(2):
        await cliente.post(f"/pedidos/{pedido['id']}/impresso", headers=agente)

    assert cozinha.eventos() == ["pedido.novo", "pedido.impresso"]


async def test_reimprimir_manda_o_pedido_de_volta_pro_agente(
    cliente, dados, entrar, caixa, assinar
):
    """Pro agente, "imprima este pedido" é a mesma ordem das duas vezes — por
    isso é `pedido.novo` de novo, e não um evento próprio.
    """
    agente_ouvindo = assinar(Papel.AGENTE)
    pedido = (
        await cliente.post("/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa)
    ).json()
    agente = await entrar(dados["agente"].id, "0000")
    await cliente.post(f"/pedidos/{pedido['id']}/impresso", headers=agente)

    cozinheiro = await entrar(dados["cozinha"].id, "0000")
    await cliente.post(f"/pedidos/{pedido['id']}/reimprimir", headers=cozinheiro)

    assert agente_ouvindo.eventos() == ["pedido.novo", "pedido.novo"]
    # E volta pra fila de não-impressos: é assim que o agente o reencontra
    # mesmo se o socket estiver fora do ar.
    assert agente_ouvindo.dados_de("pedido.novo")[1]["impresso_em"] is None


# ------------------------------------------------------------------ métricas

async def test_venda_atualiza_os_numeros_do_dono(cliente, dados, caixa, assinar):
    dono = assinar(Papel.DONO)

    await cliente.post("/pedidos", json=corpo(dados, (dados["casquinha"], 2)), headers=caixa)

    assert dono.eventos() == ["pedido.novo", "metricas.tick"]

    # O resumo vai inteiro, e não só os três números: a tela do dono troca
    # total, ranking e alertas de uma vez, sem nunca mostrar a soma nova com o
    # detalhe velho.
    tick = dono.dados_de("metricas.tick")[0]
    assert tick["total_centavos"] == 1600
    assert tick["qtd_pedidos"] == 1
    assert tick["ticket_medio_centavos"] == 1600
    assert tick["itens"][0]["nome"] == "Casquinha 1 bola"
    assert "por_atendente" in tick and "fechamento" in tick


async def test_cancelamento_tira_do_faturamento_na_hora(cliente, dados, entrar, caixa, assinar):
    dono_ouvindo = assinar(Papel.DONO)
    pedido = (
        await cliente.post("/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa)
    ).json()

    dono = await entrar(dados["dono"].id, "senhaforte")
    await cliente.post(
        f"/pedidos/{pedido['id']}/cancelar", json={"motivo": "cliente desistiu"}, headers=dono
    )

    assert dono_ouvindo.eventos() == [
        "pedido.novo", "metricas.tick", "pedido.status", "metricas.tick",
    ]
    assert dono_ouvindo.dados_de("metricas.tick")[-1]["total_centavos"] == 0
    assert dono_ouvindo.dados_de("metricas.tick")[-1]["cancelados_qtd"] == 1


async def test_mudanca_de_status_nao_mexe_no_caixa(cliente, dados, entrar, caixa, assinar):
    """Andar de RECEBIDO pra PRONTO não mexe em centavo nenhum: o dono não
    precisa ver o total piscar, e o servidor não precisa refazer o resumo.
    """
    dono = assinar(Papel.DONO)
    pedido = (
        await cliente.post("/pedidos", json=corpo(dados, (dados["acai"], 1)), headers=caixa)
    ).json()
    cozinheiro = await entrar(dados["cozinha"].id, "0000")

    await cliente.patch(
        f"/pedidos/{pedido['id']}/status", json={"status": "PRONTO"}, headers=cozinheiro
    )

    assert dono.eventos() == ["pedido.novo", "metricas.tick", "pedido.status"]


async def test_sem_dono_conectado_o_resumo_nao_e_calculado(cliente, dados, caixa, assinar):
    """O resumo são cinco consultas agregadas. Rodá-las a cada venda pra
    ninguém seria queimar banco num sábado de movimento à toa.
    """
    cozinha = assinar(Papel.COZINHA)

    await cliente.post("/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa)

    assert cozinha.eventos() == ["pedido.novo"]
    assert hub.conectados(Papel.DONO) == 0


# ------------------------------------------------------------------ cardápio

async def test_preco_novo_chega_no_balcao(cliente, dados, entrar, assinar):
    """A §2.3 promete o preço editado no celular da loja em menos de 1 segundo."""
    vendas, cozinha = assinar(Papel.FUNCIONARIO), assinar(Papel.COZINHA)
    dono = await entrar(dados["dono"].id, "senhaforte")

    await cliente.patch(
        f"/produtos/{dados['casquinha'].id}", json={"preco_centavos": 950}, headers=dono
    )

    assert vendas.eventos() == ["preco.alterado"]
    assert cozinha.eventos() == ["preco.alterado"]
    assert vendas.dados_de("preco.alterado")[0]["preco_centavos"] == 950


async def test_produto_desativado_tambem_avisa(cliente, dados, entrar, assinar):
    """É o mesmo PATCH que muda preço e tira do cardápio, e sumir do balcão na
    hora importa tanto quanto: item fora de linha não pode virar venda.
    """
    vendas = assinar(Papel.FUNCIONARIO)
    dono = await entrar(dados["dono"].id, "senhaforte")

    await cliente.patch(f"/produtos/{dados['acai'].id}", json={"ativo": False}, headers=dono)

    assert vendas.dados_de("preco.alterado")[0]["ativo"] is False


async def test_produto_inexistente_nao_anuncia(cliente, dados, entrar, assinar):
    vendas = assinar(Papel.FUNCIONARIO)
    dono = await entrar(dados["dono"].id, "senhaforte")

    resposta = await cliente.patch("/produtos/99999", json={"preco_centavos": 100}, headers=dono)

    assert resposta.status_code == 404
    assert vendas.eventos() == []


# ---------------------------------------------------- anunciar só o que gravou

async def test_o_pedido_ja_esta_gravado_quando_o_evento_sai(cliente, dados, caixa, assinar):
    """O anúncio vem depois do commit, não antes.

    Se saísse antes, um commit que falhasse deixaria a cozinha com uma comanda
    fantasma e o agente com papel gasto numa venda que não aconteceu. Aqui o
    espião vai ao banco no instante em que recebe o evento: o pedido precisa
    estar lá.
    """
    achados: list[int] = []

    class EspiaoQueConfere:
        async def send_json(self, mensagem) -> None:
            if mensagem["evento"] != "pedido.novo":
                return
            resposta = await cliente.get("/pedidos/hoje", headers=caixa)
            achados.append(len(resposta.json()))

    espiao = EspiaoQueConfere()
    hub.entrar(espiao, Papel.COZINHA)
    try:
        await cliente.post("/pedidos", json=corpo(dados, (dados["casquinha"], 1)), headers=caixa)
    finally:
        hub.sair(espiao, Papel.COZINHA)

    assert achados == [1]
