"""Cardápio, permissão do dono e auditoria de preço."""


async def test_cardapio_lista_so_produtos_ativos(cliente, dados, entrar):
    cabecalho = await entrar(dados["joao"].id, "1234")
    resposta = await cliente.get("/cardapio", headers=cabecalho)

    assert resposta.status_code == 200
    corpo = resposta.json()
    nomes = [p["nome"] for c in corpo["categorias"] for p in c["produtos"]]
    assert nomes == ["Casquinha 1 bola", "Açaí 500ml"]  # "Fora de linha" não aparece
    assert corpo["versao"] is not None  # carimbo pro PWA saber se o cache venceu


async def test_cardapio_traz_os_grupos_com_a_cota_do_produto(cliente, dados, entrar, opcoes):
    """Tudo numa resposta só: o PWA monta a tela de escolhas sem internet."""
    cabecalho = await entrar(dados["joao"].id, "1234")
    resposta = await cliente.get("/cardapio", headers=cabecalho)

    produtos = {p["nome"]: p for c in resposta.json()["categorias"] for p in c["produtos"]}
    grupos = {g["nome"]: g for g in produtos["Açaí 500ml"]["grupos"]}

    assert grupos["Acompanhamentos"]["max_escolhas"] == 2
    # A cota é do vínculo produto↔grupo: adicional pago não tem teto.
    assert grupos["Adicionais"]["max_escolhas"] is None
    assert grupos["Adicionais"]["opcoes"][0]["preco_extra_centavos"] == 300
    # Opção desativada some da tela do balcão.
    assert [o["nome"] for o in grupos["Acompanhamentos"]["opcoes"]] == ["Granola", "Paçoca"]

    # A casquinha só oferece cobertura, e ela é obrigatória.
    cobertura = produtos["Casquinha 1 bola"]["grupos"][0]
    assert (cobertura["nome"], cobertura["min_escolhas"]) == ("Cobertura", 1)


async def test_funcionario_nao_edita_preco(cliente, dados, entrar):
    cabecalho = await entrar(dados["joao"].id, "1234")
    resposta = await cliente.patch(
        f"/produtos/{dados['casquinha'].id}", json={"preco_centavos": 900}, headers=cabecalho
    )
    assert resposta.status_code == 403


async def test_dono_edita_preco(cliente, dados, entrar):
    cabecalho = await entrar(dados["dono"].id, "senhaforte")
    resposta = await cliente.patch(
        f"/produtos/{dados['casquinha'].id}", json={"preco_centavos": 900}, headers=cabecalho
    )
    assert resposta.status_code == 200
    assert resposta.json()["preco_centavos"] == 900


async def test_edicao_de_preco_grava_auditoria(cliente, dados, entrar, sessao):
    from sqlalchemy import select

    from app.models.cardapio import PrecoHistorico

    cabecalho = await entrar(dados["dono"].id, "senhaforte")
    await cliente.patch(
        f"/produtos/{dados['casquinha'].id}", json={"preco_centavos": 900}, headers=cabecalho
    )

    registros = list((await sessao.execute(select(PrecoHistorico))).scalars())
    assert len(registros) == 1
    assert (registros[0].preco_antigo, registros[0].preco_novo) == (800, 900)
    assert registros[0].usuario_id == dados["dono"].id


async def test_preco_igual_nao_polui_o_historico(cliente, dados, entrar, sessao):
    from sqlalchemy import func, select

    from app.models.cardapio import PrecoHistorico

    cabecalho = await entrar(dados["dono"].id, "senhaforte")
    await cliente.patch(
        f"/produtos/{dados['casquinha'].id}", json={"preco_centavos": 800}, headers=cabecalho
    )

    total = (await sessao.execute(select(func.count(PrecoHistorico.id)))).scalar_one()
    assert total == 0


async def test_criar_produto_em_categoria_inexistente(cliente, dados, entrar):
    cabecalho = await entrar(dados["dono"].id, "senhaforte")
    resposta = await cliente.post(
        "/produtos",
        json={"categoria_id": 999, "nome": "Fantasma", "preco_centavos": 100},
        headers=cabecalho,
    )
    assert resposta.status_code == 404


async def test_preco_negativo_e_recusado(cliente, dados, entrar):
    cabecalho = await entrar(dados["dono"].id, "senhaforte")
    resposta = await cliente.patch(
        f"/produtos/{dados['casquinha'].id}", json={"preco_centavos": -1}, headers=cabecalho
    )
    assert resposta.status_code == 422


async def test_produto_novo_em_categoria_de_bola_ja_nasce_pedindo_sabor(cliente, dados, entrar):
    """A regra existia só no PATCH, e o POST a furava.

    Um produto criado em Sorvetes com o `pede_sabor: false` do schema ficava num
    estado que a tela do dono não conserta: lá o 🍦 é desenhado travado pelo
    nome da categoria, então ele aparecia desligado *e* sem como ligar — com o
    balcão vendendo bola sem perguntar o sabor.
    """
    cabecalho = await entrar(dados["dono"].id, "senhaforte")
    resposta = await cliente.post(
        "/produtos",
        json={
            "categoria_id": dados["categoria"].id,  # "Sorvetes"
            "nome": "Casquinha grande",
            "preco_centavos": 900,
        },
        headers=cabecalho,
    )

    assert resposta.status_code == 201
    assert resposta.json()["pede_sabor"] is True


async def test_pede_sabor_explicito_nao_sobrescreve_fora_das_categorias(
    cliente, dados, entrar, sessao
):
    """A correção acima vale só onde toda bola é obrigatória. Fora dali o
    interruptor continua sendo do dono — a água mineral não pergunta sabor."""
    from app.models import Categoria

    bebidas = Categoria(nome="Bebidas", ordem=9)
    sessao.add(bebidas)
    await sessao.commit()

    cabecalho = await entrar(dados["dono"].id, "senhaforte")
    resposta = await cliente.post(
        "/produtos",
        json={"categoria_id": bebidas.id, "nome": "Água mineral", "preco_centavos": 400},
        headers=cabecalho,
    )

    assert resposta.status_code == 201
    assert resposta.json()["pede_sabor"] is False
