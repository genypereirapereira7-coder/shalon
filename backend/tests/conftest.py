"""Fixtures. Os testes rodam em SQLite na memória — sem docker, sem Postgres.

O que é específico do Postgres (o `SELECT ... FOR UPDATE` da numeração de
pedidos, fase 2) ganha teste de integração próprio contra o banco real.
"""

import os

os.environ.setdefault("SHALON_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SHALON_JWT_SEGREDO", "segredo-de-teste-com-mais-de-32-bytes-pra-hmac-sha256")
os.environ.setdefault("SHALON_AMBIENTE", "teste")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db import get_sessao  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Base,
    Categoria,
    Opcao,
    OpcaoGrupo,
    Papel,
    Produto,
    ProdutoOpcaoGrupo,
    Usuario,
)
from app.seguranca import gerar_hash, trava_login  # noqa: E402


@pytest_asyncio.fixture
async def engine():
    # StaticPool: sem ele cada conexão abriria um banco em memória diferente.
    motor = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with motor.begin() as conexao:
        await conexao.run_sync(Base.metadata.create_all)
    yield motor
    await motor.dispose()


@pytest_asyncio.fixture
async def sessao(engine):
    fabrica = async_sessionmaker(engine, expire_on_commit=False)
    async with fabrica() as s:
        yield s


@pytest_asyncio.fixture
async def cliente(engine):
    fabrica = async_sessionmaker(engine, expire_on_commit=False)

    async def sessao_de_teste():
        async with fabrica() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_sessao] = sessao_de_teste
    trava_login._por_chave.clear()  # um teste de força bruta não pode travar o seguinte

    transporte = ASGITransport(app=app)
    async with AsyncClient(transport=transporte, base_url="http://teste") as c:
        yield c

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def dados(sessao):
    """Um dono, um funcionário, os dois usuários de máquina e um cardápio mínimo."""
    dono = Usuario(nome="Dona Shalon", pin_hash=gerar_hash("senhaforte"), papel=Papel.DONO)
    joao = Usuario(nome="João", pin_hash=gerar_hash("1234"), papel=Papel.FUNCIONARIO)
    # A tela do PC e o programa de impressão também autenticam — não são gente,
    # mas têm papel próprio no roteamento e nas permissões.
    cozinha = Usuario(nome="Cozinha", pin_hash=gerar_hash("0000"), papel=Papel.COZINHA)
    agente = Usuario(nome="Agente", pin_hash=gerar_hash("0000"), papel=Papel.AGENTE)
    categoria = Categoria(nome="Sorvetes", ordem=1)
    sessao.add_all([dono, joao, cozinha, agente, categoria])
    await sessao.flush()

    casquinha = Produto(
        categoria_id=categoria.id, nome="Casquinha 1 bola", preco_centavos=800, ordem=0
    )
    acai = Produto(categoria_id=categoria.id, nome="Açaí 500ml", preco_centavos=1800, ordem=1)
    inativo = Produto(
        categoria_id=categoria.id, nome="Fora de linha", preco_centavos=500, ordem=2, ativo=False
    )
    sessao.add_all([casquinha, acai, inativo])
    await sessao.commit()

    return {
        "dono": dono,
        "joao": joao,
        "cozinha": cozinha,
        "agente": agente,
        "categoria": categoria,
        "casquinha": casquinha,
        "acai": acai,
        "inativo": inativo,
    }


@pytest_asyncio.fixture
async def opcoes(sessao, dados):
    """Acompanhamentos grátis e adicionais pagos, como no cardápio real.

    O açaí ganha os dois grupos (cota de 2 acompanhamentos, adicionais à
    vontade) e a casquinha ganha uma cobertura obrigatória — é ela que testa
    o `min_escolhas`.
    """
    acompanhamentos = OpcaoGrupo(nome="Acompanhamentos", ordem=1)
    adicionais = OpcaoGrupo(nome="Adicionais", ordem=2)
    coberturas = OpcaoGrupo(nome="Cobertura", ordem=3)
    sessao.add_all([acompanhamentos, adicionais, coberturas])
    await sessao.flush()

    granola = Opcao(grupo_id=acompanhamentos.id, nome="Granola", ordem=0)
    pacoca = Opcao(grupo_id=acompanhamentos.id, nome="Paçoca", ordem=1)
    fora_de_linha = Opcao(
        grupo_id=acompanhamentos.id, nome="Kiwi", ordem=2, ativo=False
    )
    geleia = Opcao(
        grupo_id=adicionais.id, nome="Geléia de morango", preco_extra_centavos=300, ordem=0
    )
    avela = Opcao(
        grupo_id=adicionais.id, nome="Creme de avelã", preco_extra_centavos=300, ordem=1
    )
    chocolate = Opcao(grupo_id=coberturas.id, nome="Chocolate", ordem=0)
    sessao.add_all([granola, pacoca, fora_de_linha, geleia, avela, chocolate])

    sessao.add_all([
        ProdutoOpcaoGrupo(
            produto_id=dados["acai"].id, grupo_id=acompanhamentos.id,
            min_escolhas=0, max_escolhas=2, ordem=0,
        ),
        # Sem teto: leve quantos adicionais quiser, cada um é cobrado.
        ProdutoOpcaoGrupo(
            produto_id=dados["acai"].id, grupo_id=adicionais.id,
            min_escolhas=0, max_escolhas=None, ordem=1,
        ),
        ProdutoOpcaoGrupo(
            produto_id=dados["casquinha"].id, grupo_id=coberturas.id,
            min_escolhas=1, max_escolhas=1, ordem=0,
        ),
    ])
    await sessao.commit()

    return {
        "acompanhamentos": acompanhamentos,
        "adicionais": adicionais,
        "coberturas": coberturas,
        "granola": granola,
        "pacoca": pacoca,
        "inativa": fora_de_linha,
        "geleia": geleia,
        "avela": avela,
        "chocolate": chocolate,
    }


@pytest.fixture
def entrar(cliente):
    """Faz login e devolve o header Authorization pronto."""

    async def _entrar(usuario_id: int, segredo: str) -> dict[str, str]:
        resposta = await cliente.post(
            "/auth/login", json={"usuario_id": usuario_id, "segredo": segredo}
        )
        assert resposta.status_code == 200, resposta.text
        return {"Authorization": f"Bearer {resposta.json()['acesso']}"}

    return _entrar
