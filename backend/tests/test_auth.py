"""Login por nome e senha, trava de força bruta, rotação de refresh e sessões.

A tela de login não lista mais os usuários — não existe rota que os liste. Ela
mostra dois campos, e quem não souber o nome não tem o que tentar. Os testes
que cobriam `/auth/usuarios` saíram junto com a rota.
"""

import pytest

from app.config import get_config

cfg = get_config()


async def test_login_com_a_senha_certa(cliente, dados):
    resposta = await cliente.post(
        "/auth/login", json={"usuario": "João", "segredo": "1234"}
    )
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["papel"] == "FUNCIONARIO"
    assert corpo["nome"] == "João"
    assert corpo["acesso"] and corpo["refresh"]


async def test_senha_errada_nao_entra(cliente, dados):
    resposta = await cliente.post(
        "/auth/login", json={"usuario": "João", "segredo": "9999"}
    )
    assert resposta.status_code == 401


async def test_usuario_inexistente_responde_igual_a_senha_errada(cliente, dados):
    """Não entregamos de graça quais nomes existem."""
    inexistente = await cliente.post("/auth/login", json={"usuario": "ninguem", "segredo": "1234"})
    errado = await cliente.post(
        "/auth/login", json={"usuario": "João", "segredo": "9999"}
    )
    assert inexistente.status_code == errado.status_code == 401
    assert inexistente.json()["detail"] == errado.json()["detail"]


async def test_trava_depois_de_varias_tentativas(cliente, dados):
    """Sem trava, um nome conhecido vira alvo de varredura de senha."""
    for _ in range(cfg.login_max_tentativas):
        await cliente.post("/auth/login", json={"usuario": "João", "segredo": "0000"})

    bloqueado = await cliente.post(
        "/auth/login", json={"usuario": "João", "segredo": "1234"}
    )
    assert bloqueado.status_code == 429
    assert "Tente de novo" in bloqueado.json()["detail"]


async def test_renovar_devolve_par_novo(cliente, dados):
    login = await cliente.post(
        "/auth/login", json={"usuario": "João", "segredo": "1234"}
    )
    refresh = login.json()["refresh"]

    renovado = await cliente.post("/auth/renovar", json={"refresh": refresh})
    assert renovado.status_code == 200
    assert renovado.json()["refresh"] != refresh  # rotação


async def test_refresh_reutilizado_derruba_as_sessoes(cliente, dados):
    """Refresh usado duas vezes = token roubado. Encerra tudo."""
    login = await cliente.post(
        "/auth/login", json={"usuario": "João", "segredo": "1234"}
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
        "/auth/login", json={"usuario": "João", "segredo": "1234"}
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


@pytest.mark.parametrize("segredo", ["", "12", "abc"])
async def test_senha_curta_demais_e_recusada(cliente, dados, segredo):
    resposta = await cliente.post(
        "/auth/login", json={"usuario": "João", "segredo": segredo}
    )
    assert resposta.status_code == 422


async def test_nome_ignora_caixa_e_espaco(cliente, dados):
    """Quem digita está de pé, com pressa, e o teclado do celular capitaliza.

    "Joao " com um espaço a mais virando "usuário ou senha inválidos" seria a
    tela culpando o funcionário por um erro que é dela.
    """
    resposta = await cliente.post(
        "/auth/login", json={"usuario": "  joão  ", "segredo": "1234"}
    )
    assert resposta.status_code == 200
    assert resposta.json()["nome"] == "João"


# ------------------------------------------------------------------ sessões


async def test_dono_ve_quem_esta_logado(cliente, dados, entrar):
    await entrar(dados["joao"].id, "1234")
    dono = await entrar(dados["dono"].id, "senhaforte")

    sessoes = (await cliente.get("/auth/sessoes", headers=dono)).json()

    por_nome = {s["usuario_nome"]: s for s in sessoes}
    assert {"João", "Dona Shalon"} <= set(por_nome)
    assert por_nome["João"]["meu_usuario"] is False
    assert por_nome["Dona Shalon"]["meu_usuario"] is True
    # O que a tela mostra, e nada além: nenhum pedaço de token sai daqui.
    assert not any("refresh" in chave for chave in sessoes[0])


async def test_funcionario_nao_ve_as_sessoes(cliente, dados, entrar):
    """Quem está logado no sistema é informação de dono."""
    caixa = await entrar(dados["joao"].id, "1234")
    assert (await cliente.get("/auth/sessoes", headers=caixa)).status_code == 403


async def test_revogar_derruba_o_aparelho(cliente, dados, entrar):
    """O refresh morre, então o aparelho não renova mais e cai no login."""
    login = await cliente.post("/auth/login", json={"usuario": "João", "segredo": "1234"})
    refresh = login.json()["refresh"]

    dono = await entrar(dados["dono"].id, "senhaforte")
    sessoes = (await cliente.get("/auth/sessoes", headers=dono)).json()
    do_joao = next(s for s in sessoes if s["usuario_nome"] == "João")

    apagada = await cliente.delete(f"/auth/sessoes/{do_joao['id']}", headers=dono)
    assert apagada.status_code == 204

    assert (await cliente.post("/auth/renovar", json={"refresh": refresh})).status_code == 401
    restantes = (await cliente.get("/auth/sessoes", headers=dono)).json()
    assert all(s["usuario_nome"] != "João" for s in restantes)


async def test_revogar_duas_vezes_nao_e_erro(cliente, dados, entrar):
    """Dois toques no botão, ou duas abas abertas."""
    await cliente.post("/auth/login", json={"usuario": "João", "segredo": "1234"})
    dono = await entrar(dados["dono"].id, "senhaforte")

    sessoes = (await cliente.get("/auth/sessoes", headers=dono)).json()
    alvo = next(s for s in sessoes if s["usuario_nome"] == "João")["id"]

    assert (await cliente.delete(f"/auth/sessoes/{alvo}", headers=dono)).status_code == 204
    assert (await cliente.delete(f"/auth/sessoes/{alvo}", headers=dono)).status_code == 204


async def test_funcionario_nao_revoga_sessao(cliente, dados, entrar):
    dono = await entrar(dados["dono"].id, "senhaforte")
    caixa = await entrar(dados["joao"].id, "1234")

    sessoes = (await cliente.get("/auth/sessoes", headers=dono)).json()
    alvo = sessoes[0]["id"]

    assert (await cliente.delete(f"/auth/sessoes/{alvo}", headers=caixa)).status_code == 403


async def test_sessao_que_saiu_nao_aparece_mais(cliente, dados, entrar):
    login = await cliente.post("/auth/login", json={"usuario": "João", "segredo": "1234"})
    await cliente.post("/auth/sair", json={"refresh": login.json()["refresh"]})

    dono = await entrar(dados["dono"].id, "senhaforte")
    sessoes = (await cliente.get("/auth/sessoes", headers=dono)).json()

    assert all(s["usuario_nome"] != "João" for s in sessoes)


async def test_a_lista_de_usuarios_nao_existe_mais(cliente, dados):
    """A tela de login não mostra quem trabalha na loja nem quem é o dono."""
    assert (await cliente.get("/auth/usuarios")).status_code == 404
