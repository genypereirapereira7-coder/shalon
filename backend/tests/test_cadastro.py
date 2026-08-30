"""Cadastro de funcionário e a liberação pelo dono.

O que estes testes protegem é uma linha que já foi o contrário: `/auth/cadastro`
devolvia a sessão pronta, e criar a conta *era* entrar. Dentro da loja isso
bastava, porque alcançar a tela já exigia estar atrás do balcão. Num endereço
público, a mesma porta atende qualquer um com o link.
"""

from app.models.usuario import Papel, Usuario
from app.seguranca import trava_login


async def test_cadastro_nao_devolve_token(cliente, dados):
    """A regressão que mais importa: nada de sessão pronta na resposta."""
    resposta = await cliente.post(
        "/auth/cadastro", json={"nome": "Vanusa", "senha": "691040"}
    )
    assert resposta.status_code == 201, resposta.text
    corpo = resposta.json()
    assert corpo["nome"] == "Vanusa"
    assert corpo["aguardando_liberacao"] is True
    assert "acesso" not in corpo
    assert "refresh" not in corpo


async def test_conta_nova_nasce_inativa_e_nao_aprovada(cliente, dados, sessao):
    await cliente.post("/auth/cadastro", json={"nome": "Vanusa", "senha": "691040"})

    from sqlalchemy import select

    nova = (
        await sessao.execute(select(Usuario).where(Usuario.nome == "Vanusa"))
    ).scalar_one()
    assert nova.papel == Papel.FUNCIONARIO
    assert nova.ativo is False
    assert nova.aprovado_em is None


async def test_quem_acabou_de_se_cadastrar_nao_entra(cliente, dados):
    await cliente.post("/auth/cadastro", json={"nome": "Vanusa", "senha": "691040"})

    resposta = await cliente.post(
        "/auth/login", json={"usuario": "Vanusa", "segredo": "691040"}
    )
    assert resposta.status_code == 401


async def test_depois_que_o_dono_libera_entra(cliente, dados, entrar, sessao):
    await cliente.post("/auth/cadastro", json={"nome": "Vanusa", "senha": "691040"})

    from sqlalchemy import select

    nova = (
        await sessao.execute(select(Usuario).where(Usuario.nome == "Vanusa"))
    ).scalar_one()

    cabecalho = await entrar(dados["dono"].id, "senhaforte")
    liberou = await cliente.patch(
        f"/usuarios/{nova.id}", json={"ativo": True}, headers=cabecalho
    )
    assert liberou.status_code == 200, liberou.text
    assert liberou.json()["aprovado_em"] is not None

    entrou = await cliente.post(
        "/auth/login", json={"usuario": "Vanusa", "segredo": "691040"}
    )
    assert entrou.status_code == 200
    assert entrou.json()["papel"] == "FUNCIONARIO"


async def test_pausar_depois_de_liberar_nao_volta_pra_fila(cliente, dados, entrar, sessao):
    """Pausar não pode apagar o carimbo — senão a tela do dono mostra quem ele
    barrou de propósito junto de quem está esperando pra começar."""
    await cliente.post("/auth/cadastro", json={"nome": "Vanusa", "senha": "691040"})

    from sqlalchemy import select

    nova = (
        await sessao.execute(select(Usuario).where(Usuario.nome == "Vanusa"))
    ).scalar_one()
    cabecalho = await entrar(dados["dono"].id, "senhaforte")

    await cliente.patch(f"/usuarios/{nova.id}", json={"ativo": True}, headers=cabecalho)
    pausou = await cliente.patch(
        f"/usuarios/{nova.id}", json={"ativo": False}, headers=cabecalho
    )
    assert pausou.status_code == 200
    assert pausou.json()["ativo"] is False
    assert pausou.json()["aprovado_em"] is not None  # continua fora da fila


async def test_o_dono_ve_quem_esta_esperando(cliente, dados, entrar):
    await cliente.post("/auth/cadastro", json={"nome": "Vanusa", "senha": "691040"})

    cabecalho = await entrar(dados["dono"].id, "senhaforte")
    lista = await cliente.get("/usuarios", headers=cabecalho)
    assert lista.status_code == 200

    vanusa = next(p for p in lista.json() if p["nome"] == "Vanusa")
    assert vanusa["ativo"] is False
    assert vanusa["aprovado_em"] is None


async def test_nome_repetido_e_recusado(cliente, dados):
    resposta = await cliente.post(
        "/auth/cadastro", json={"nome": "joão", "senha": "691040"}
    )
    assert resposta.status_code == 409


async def test_senha_precisa_ser_seis_digitos(cliente, dados):
    for ruim in ("12345", "1234567", "abcdef", "12 456"):
        resposta = await cliente.post(
            "/auth/cadastro", json={"nome": f"Fulano{ruim}", "senha": ruim}
        )
        assert resposta.status_code == 422, ruim


async def test_criar_conta_em_serie_esbarra_na_trava(cliente, dados):
    """Sem trava, um laço cria mil contas e a tela do dono vira uma lista
    impossível de auditar — cada uma esperando um toque distraído."""
    trava_login._por_chave.clear()

    criadas = 0
    for i in range(cfg_max := 12):
        resposta = await cliente.post(
            "/auth/cadastro", json={"nome": f"Pessoa {i}", "senha": "691040"}
        )
        if resposta.status_code == 429:
            break
        criadas += 1

    assert criadas < cfg_max, "a trava não segurou o cadastro em série"
    trava_login._por_chave.clear()
