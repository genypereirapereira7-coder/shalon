"""Login, trava de PIN e rotação de refresh."""

import pytest

from app.config import get_config

cfg = get_config()


async def test_login_com_pin_correto(cliente, dados):
    resposta = await cliente.post(
        "/auth/login", json={"usuario_id": dados["joao"].id, "segredo": "1234"}
    )
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["papel"] == "FUNCIONARIO"
    assert corpo["nome"] == "João"
    assert corpo["acesso"] and corpo["refresh"]


async def test_pin_errado_nao_entra(cliente, dados):
    resposta = await cliente.post(
        "/auth/login", json={"usuario_id": dados["joao"].id, "segredo": "9999"}
    )
    assert resposta.status_code == 401


async def test_usuario_inexistente_responde_igual_a_pin_errado(cliente, dados):
    """Não entregamos de graça quais ids existem."""
    inexistente = await cliente.post("/auth/login", json={"usuario_id": 9999, "segredo": "1234"})
    errado = await cliente.post(
        "/auth/login", json={"usuario_id": dados["joao"].id, "segredo": "9999"}
    )
    assert inexistente.status_code == errado.status_code == 401
    assert inexistente.json()["detail"] == errado.json()["detail"]


async def test_trava_depois_de_varias_tentativas(cliente, dados):
    """PIN de 4 dígitos são 10 mil combinações — sem trava dá pra varrer."""
    for _ in range(cfg.login_max_tentativas):
        await cliente.post("/auth/login", json={"usuario_id": dados["joao"].id, "segredo": "0000"})

    bloqueado = await cliente.post(
        "/auth/login", json={"usuario_id": dados["joao"].id, "segredo": "1234"}
    )
    assert bloqueado.status_code == 429
    assert "Tente de novo" in bloqueado.json()["detail"]


async def test_renovar_devolve_par_novo(cliente, dados):
    login = await cliente.post(
        "/auth/login", json={"usuario_id": dados["joao"].id, "segredo": "1234"}
    )
    refresh = login.json()["refresh"]

    renovado = await cliente.post("/auth/renovar", json={"refresh": refresh})
    assert renovado.status_code == 200
    assert renovado.json()["refresh"] != refresh  # rotação


async def test_refresh_reutilizado_derruba_as_sessoes(cliente, dados):
    """Refresh usado duas vezes = token roubado. Encerra tudo."""
    login = await cliente.post(
        "/auth/login", json={"usuario_id": dados["joao"].id, "segredo": "1234"}
    )
    refresh = login.json()["refresh"]

    primeiro = await cliente.post("/auth/renovar", json={"refresh": refresh})
    assert primeiro.status_code == 200

    segundo = await cliente.post("/auth/renovar", json={"refresh": refresh})
    assert segundo.status_code == 401
    assert "reutilizado" in segundo.json()["detail"]

    # o par emitido no primeiro renovar também morreu
    novo = primeiro.json()["refresh"]
    assert (await cliente.post("/auth/renovar", json={"refresh": novo})).status_code == 401


async def test_sair_revoga_o_refresh(cliente, dados):
    login = await cliente.post(
        "/auth/login", json={"usuario_id": dados["joao"].id, "segredo": "1234"}
    )
    refresh = login.json()["refresh"]

    assert (await cliente.post("/auth/sair", json={"refresh": refresh})).status_code == 204
    assert (await cliente.post("/auth/renovar", json={"refresh": refresh})).status_code == 401


async def test_rota_protegida_exige_token(cliente, dados):
    assert (await cliente.get("/cardapio")).status_code == 401


async def test_eu_devolve_quem_esta_logado(cliente, dados, entrar):
    cabecalho = await entrar(dados["joao"].id, "1234")
    resposta = await cliente.get("/auth/eu", headers=cabecalho)
    assert resposta.status_code == 200
    assert resposta.json()["nome"] == "João"


async def test_lista_de_usuarios_nao_vaza_hash(cliente, dados):
    resposta = await cliente.get("/auth/usuarios")
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert {u["nome"] for u in corpo} == {"Dona Shalon", "João"}
    assert all("pin_hash" not in u for u in corpo)


@pytest.mark.parametrize("segredo", ["", "12", "abc"])
async def test_pin_curto_demais_e_recusado(cliente, dados, segredo):
    resposta = await cliente.post(
        "/auth/login", json={"usuario_id": dados["joao"].id, "segredo": segredo}
    )
    assert resposta.status_code == 422
