"""Cardápio: leitura pra todo mundo autenticado, escrita só pro dono."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.dependencias import IdentidadeDep, SessaoDep, SoDono
from app.models.cardapio import Categoria, Produto
from app.models.opcoes import ProdutoOpcaoGrupo
from app.schemas.cardapio import (
    CardapioSaida,
    CategoriaEntrada,
    CategoriaSaida,
    GrupoSaida,
    OpcaoSaida,
    ProdutoEntrada,
    ProdutoPatch,
    ProdutoSaida,
)
from app.servicos.precos import registrar_mudanca

rotas = APIRouter(tags=["cardapio"])


@rotas.get("/cardapio", response_model=CardapioSaida)
async def cardapio(sessao: SessaoDep, _: IdentidadeDep, incluir_inativos: bool = False):
    """Categorias, produtos ativos e os acompanhamentos de cada um.

    Vai tudo numa resposta só de propósito: o PWA guarda isto em cache e
    precisa montar a tela de acompanhamentos sem internet — se as opções
    viessem numa segunda chamada, a venda offline pararia na hora de escolher
    a granola.

    `incluir_inativos` é da tela do dono: sem ele, desativar um produto o faria
    sumir da própria tela que o desativou, sem jeito de reativar. O balcão
    nunca manda esse parâmetro — item fora de linha não pode virar botão de
    venda.
    """
    consulta = (
        select(Categoria)
        .options(selectinload(Categoria.produtos))
        .order_by(Categoria.ordem, Categoria.nome)
    )
    if not incluir_inativos:
        consulta = consulta.where(Categoria.ativo.is_(True))
    categorias = list((await sessao.execute(consulta)).scalars())
    versao = (await sessao.execute(select(func.max(Produto.atualizado_em)))).scalar_one_or_none()

    grupos_por_produto = await _grupos_por_produto(sessao)

    return CardapioSaida(
        versao=versao,
        categorias=[
            CategoriaSaida(
                id=c.id,
                nome=c.nome,
                ordem=c.ordem,
                produtos=[
                    ProdutoSaida(
                        **ProdutoSaida.model_validate(p).model_dump(exclude={"grupos"}),
                        grupos=grupos_por_produto.get(p.id, []),
                    )
                    for p in sorted(c.produtos, key=lambda p: (p.ordem, p.nome))
                    if p.ativo or incluir_inativos
                ],
            )
            for c in categorias
        ],
    )


async def _grupos_por_produto(sessao) -> dict[int, list[GrupoSaida]]:
    """Vínculos produto↔grupo já com a cota e as opções ativas de cada grupo."""
    consulta = select(ProdutoOpcaoGrupo).order_by(
        ProdutoOpcaoGrupo.produto_id, ProdutoOpcaoGrupo.ordem
    )

    saida: dict[int, list[GrupoSaida]] = {}
    for vinculo in (await sessao.execute(consulta)).scalars():
        if not vinculo.grupo.ativo:
            continue
        saida.setdefault(vinculo.produto_id, []).append(
            GrupoSaida(
                id=vinculo.grupo.id,
                nome=vinculo.grupo.nome,
                min_escolhas=vinculo.min_escolhas,
                max_escolhas=vinculo.max_escolhas,
                opcoes=[
                    OpcaoSaida.model_validate(o) for o in vinculo.grupo.opcoes if o.ativo
                ],
            )
        )
    return saida


@rotas.post("/categorias", response_model=CategoriaSaida, status_code=status.HTTP_201_CREATED)
async def criar_categoria(dados: CategoriaEntrada, sessao: SessaoDep, _: SoDono):
    categoria = Categoria(nome=dados.nome, ordem=dados.ordem)
    sessao.add(categoria)
    await sessao.flush()
    return CategoriaSaida(id=categoria.id, nome=categoria.nome, ordem=categoria.ordem, produtos=[])


@rotas.post("/produtos", response_model=ProdutoSaida, status_code=status.HTTP_201_CREATED)
async def criar_produto(dados: ProdutoEntrada, sessao: SessaoDep, _: SoDono):
    if await sessao.get(Categoria, dados.categoria_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Categoria não existe")

    produto = Produto(**dados.model_dump())
    sessao.add(produto)
    await sessao.flush()
    return ProdutoSaida.model_validate(produto)


@rotas.patch("/produtos/{produto_id}", response_model=ProdutoSaida)
async def editar_produto(produto_id: int, dados: ProdutoPatch, sessao: SessaoDep, dono: SoDono):
    produto = await sessao.get(Produto, produto_id)
    if produto is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Produto não existe")

    campos = dados.model_dump(exclude_unset=True)

    # Preço passa pela auditoria: é ela que permite reconstruir depois quanto
    # o item valia quando um pedido offline atrasado subir.
    if (novo := campos.pop("preco_centavos", None)) is not None:
        await registrar_mudanca(sessao, produto, novo, dono.usuario_id)

    for campo, valor in campos.items():
        setattr(produto, campo, valor)

    await sessao.flush()
    # TODO(fase 5): publicar `preco.alterado` no WebSocket pra vendas e cozinha.
    return ProdutoSaida.model_validate(produto)
