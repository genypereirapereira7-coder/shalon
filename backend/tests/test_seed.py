"""O que o seed pode e o que o seed não pode sobrescrever.

A divisão é a razão de existir destes testes, porque errar para qualquer um dos
dois lados é caro e silencioso:

**Preço é do dono.** Ele edita pela tela, e o seed que o reescrevesse faria o
cardápio voltar ao preço de agosto toda vez que alguém reiniciasse o servidor —
sem erro nenhum aparecendo, e descoberto no fechamento do caixa.

**Estrutura é do arquivo.** A cota de um grupo ("escolha 1 cobertura") não tem
tela que a edite. Se o seed não a sincronizasse, mexer nela em `seed.py` não
teria efeito em banco nenhum que já existisse — a mudança sumiria no ar, e o
balcão continuaria vendendo casquinha sem ninguém perguntar da cobertura.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import seed
from app.models import Opcao, OpcaoGrupo, Produto, ProdutoOpcaoGrupo

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def semeado(engine, monkeypatch):
    """Banco com o cardápio carregado, e a fábrica de sessões pra conferir."""
    fabrica = async_sessionmaker(engine, expire_on_commit=False)
    # `semear` usa a `Sessao` global de `app.db`, que aponta pro banco de
    # verdade. Aqui ela passa a apontar pro banco em memória do teste.
    monkeypatch.setattr(seed, "Sessao", fabrica)
    await seed.semear(detalhado=False)
    return fabrica


async def _vinculo(sessao, nome_produto: str, nome_grupo: str) -> ProdutoOpcaoGrupo:
    return (
        await sessao.execute(
            select(ProdutoOpcaoGrupo)
            .join(Produto, Produto.id == ProdutoOpcaoGrupo.produto_id)
            .join(OpcaoGrupo, OpcaoGrupo.id == ProdutoOpcaoGrupo.grupo_id)
            .where(Produto.nome == nome_produto, OpcaoGrupo.nome == nome_grupo)
        )
    ).scalar_one()


async def test_rodar_de_novo_nao_duplica_nada(semeado):
    async with semeado() as sessao:
        antes = len((await sessao.execute(select(Produto))).scalars().all())

    await seed.semear(detalhado=False)

    async with semeado() as sessao:
        depois = len((await sessao.execute(select(Produto))).scalars().all())

    assert antes == depois


async def test_a_cota_volta_pro_que_o_arquivo_manda(semeado):
    """Sem isto, mudar uma cota no `seed.py` não valeria em banco já existente.

    O caso real: a cobertura passou a ser obrigatória (min 1). Num banco que já
    rodava com min 0, o vínculo antigo continuaria liberando o ADICIONAR sem
    ninguém escolher, e a comanda sairia sem dizer se o cliente quis cobertura.
    """
    async with semeado() as sessao:
        vinculo = await _vinculo(sessao, "Casquinha", "Coberturas")
        assert vinculo.min_escolhas == 1  # como o arquivo manda

        vinculo.min_escolhas = 0  # o estado de um banco carregado antes
        vinculo.max_escolhas = 3
        await sessao.commit()

    await seed.semear(detalhado=False)

    async with semeado() as sessao:
        corrigido = await _vinculo(sessao, "Casquinha", "Coberturas")
        assert (corrigido.min_escolhas, corrigido.max_escolhas) == (1, 1)


async def test_preco_mudado_pelo_dono_sobrevive_ao_seed(semeado):
    """A metade oposta: aqui o seed tem que ficar quieto."""
    async with semeado() as sessao:
        produto = (
            await sessao.execute(select(Produto).where(Produto.nome == "Casquinha"))
        ).scalar_one()
        produto.preco_centavos = 900  # o dono aumentou pela tela
        await sessao.commit()

    await seed.semear(detalhado=False)

    async with semeado() as sessao:
        produto = (
            await sessao.execute(select(Produto).where(Produto.nome == "Casquinha"))
        ).scalar_one()
        assert produto.preco_centavos == 900


async def test_preco_de_adicional_mudado_pelo_dono_tambem_sobrevive(semeado):
    async with semeado() as sessao:
        opcao = (
            await sessao.execute(
                select(Opcao)
                .join(OpcaoGrupo)
                .where(OpcaoGrupo.nome == "Adicionais do sundae", Opcao.nome == "Creme de avelã")
            )
        ).scalar_one()
        opcao.preco_extra_centavos = 450
        await sessao.commit()

    await seed.semear(detalhado=False)

    async with semeado() as sessao:
        opcao = (
            await sessao.execute(
                select(Opcao)
                .join(OpcaoGrupo)
                .where(OpcaoGrupo.nome == "Adicionais do sundae", Opcao.nome == "Creme de avelã")
            )
        ).scalar_one()
        assert opcao.preco_extra_centavos == 450


async def test_sem_cobertura_e_a_primeira_da_lista(semeado):
    """A saída rápida pra quem não quer cobertura fica onde o dedo já está.

    E precisa existir como opção: é ela que faz a comanda dizer *sem cobertura*
    em letra impressa, em vez de sair muda e deixar quem monta sem saber se o
    cliente recusou ou se o atendente passou reto.
    """
    async with semeado() as sessao:
        opcoes = (
            await sessao.execute(
                select(Opcao)
                .join(OpcaoGrupo)
                .where(OpcaoGrupo.nome == "Coberturas")
                .order_by(Opcao.ordem)
            )
        ).scalars().all()

    assert opcoes[0].nome == "Sem cobertura"
    assert opcoes[0].preco_extra_centavos == 0


async def test_trufado_pede_so_a_borda(semeado):
    """Uma escolha obrigatória: a borda é o que define o item.

    O trufado já veio com cobertura no cardápio (§seed.py), mas isso virou
    escolha demais pro item — só a borda ficou.
    """
    async with semeado() as sessao:
        for nome in ("Casquinha Trufada", "Cascão Trufado"):
            borda = await _vinculo(sessao, nome, "Bordas do trufado")
            assert (borda.min_escolhas, borda.max_escolhas) == (1, 1)

            cobertura = (
                await sessao.execute(
                    select(ProdutoOpcaoGrupo)
                    .join(Produto, Produto.id == ProdutoOpcaoGrupo.produto_id)
                    .join(OpcaoGrupo, OpcaoGrupo.id == ProdutoOpcaoGrupo.grupo_id)
                    .where(Produto.nome == nome, OpcaoGrupo.nome == "Coberturas")
                )
            ).scalar_one_or_none()
            assert cobertura is None

        bordas = (
            await sessao.execute(
                select(Opcao)
                .join(OpcaoGrupo)
                .where(OpcaoGrupo.nome == "Bordas do trufado")
                .order_by(Opcao.ordem)
            )
        ).scalars().all()

    # Incluídas no preço: a borda é o que faz o item ser trufado, não é extra.
    assert [o.nome for o in bordas] == ["Creme de avelã", "Amendoim", "Chocoball", "Ovomaltine"]
    assert all(o.preco_extra_centavos == 0 for o in bordas)
