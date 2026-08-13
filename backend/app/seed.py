"""Popula o banco com o cardápio impresso da Shalon.

    python -m app.seed

Idempotente: rodar de novo não duplica nada, e nunca sobrescreve preço que o
dono já mudou pela tela — a única fonte de verdade depois da primeira carga é
o banco. O que está aqui é o cardápio do folheto (agosto/2026); item novo ou
preço novo entra pela tela do dono, não editando este arquivo.

Um detalhe de modelagem que se paga na comanda: os cinco tamanhos de açaí
montado apontam pro *mesmo* grupo de acompanhamentos. Mudar "Bis" de nome uma
vez muda nos cinco. A cota ("4 acompanhamentos") não é do grupo, é do vínculo
produto↔grupo — por isso a mesma lista vale 3 no sundae e 4 no açaí.
"""

import asyncio

from sqlalchemy import select

from app.config import get_config
from app.db import Sessao
from app.models.cardapio import Categoria, Produto
from app.models.opcoes import Opcao, OpcaoGrupo, ProdutoOpcaoGrupo
from app.models.usuario import Papel, Usuario
from app.seguranca import gerar_hash

cfg = get_config()

USUARIOS = [
    # (nome, segredo, papel)
    ("Dono", cfg.senha_dono, Papel.DONO),
    ("João", "1234", Papel.FUNCIONARIO),
    ("Cozinha", "0000", Papel.COZINHA),
    ("Agente de impressão", "0000", Papel.AGENTE),
]

# ---------------------------------------------------------------- opções
#
# Cada grupo é uma lista reaproveitável. `(nome, preco_extra_centavos)` —
# extra 0 é acompanhamento incluído no preço; > 0 é adicional cobrado.

GRUPOS = {
    "Coberturas": [
        ("Morango", 0),
        ("Chocolate", 0),
        ("Caramelo", 0),
        ("Leite condensado", 0),
        ("Groselha", 0),
        ("Kiwi", 0),
        ("Menta", 0),
        ("Limão", 0),
    ],
    "Acompanhamentos do sundae": [
        ("Banana", 0),
        ("Morango", 0),
        ("Granola", 0),
        ("Ovomaltine", 0),
        ("Floco de arroz", 0),
        ("Leite em pó", 0),
        ("Chocoball", 0),
        ("Paçoca", 0),
        ("Amendoim", 0),
        ("Canudo waffer", 0),
        ("Gotas de chocolate", 0),
        ("MM", 0),
        ("Leite condensado", 0),
    ],
    "Adicionais do sundae": [
        ("Geléia de morango", 300),
        ("Geléia de maracujá", 300),
        ("Geléia de abacaxi ao vinho", 300),
        ("Creme de avelã", 300),
    ],
    "Acompanhamentos do açaí montado": [
        ("Amendoim", 0),
        ("Banana", 0),
        ("Bis", 0),
        ("Canudo waffer", 0),
        ("Confetes", 0),
        ("Chocoball", 0),
        ("Flocos de arroz", 0),
        ("Gotas de chocolate", 0),
        ("Granola", 0),
        ("Leite condensado", 0),
        ("Leite em pó", 0),
        ("Morango", 0),
        ("Ovomaltine", 0),
        ("Paçoca", 0),
        ("Sorvete", 0),
    ],
    # Preços diferentes dos do sundae de propósito: o folheto cobra a geléia de
    # morango R$2 no açaí e R$3 no sundae. Por isso são dois grupos.
    "Adicionais do açaí": [
        ("Creme de avelã", 300),
        ("Cereja", 300),
        ("Geléia de abacaxi ao vinho", 300),
        ("Geléia de maracujá", 300),
        ("Geléia de morango", 200),
        ("Fini", 200),
        ("KitKat", 200),
    ],
    "Acompanhamentos do açaí batido": [
        ("Leite condensado", 0),
        ("Leite em pó", 0),
        ("Chocoball", 0),
        ("Ovomaltine", 0),
        ("Paçoca", 0),
        ("Amendoim", 0),
        ("Flocos de arroz", 0),
        ("Banana", 0),
        ("Morango", 0),
    ],
    "Acompanhamentos da cestinha": [
        ("MM", 0),
        ("Canudo waffer", 0),
        ("Granulado", 0),
        ("Kit Kat", 0),
        ("Fini", 0),
        ("Gotas de chocolate", 0),
        ("Chocoball", 0),
        ("Ovomaltine", 0),
        ("Cobertura", 0),
        ("Creme de avelã", 0),
    ],
}

# Atalhos pra montar os produtos: (nome do grupo, min, max). `max=None` é sem
# teto — o caso dos adicionais pagos: leve quantos quiser, cada um cobrado.
COBERTURA = ("Coberturas", 0, 1)
SUNDAE_ACOMP = ("Acompanhamentos do sundae", 0, 3)
SUNDAE_ADIC = ("Adicionais do sundae", 0, None)
ACAI_ACOMP = ("Acompanhamentos do açaí montado", 0, 4)
ACAI_ADIC = ("Adicionais do açaí", 0, None)
BATIDO_ACOMP = ("Acompanhamentos do açaí batido", 0, 3)
CESTINHA_ACOMP = ("Acompanhamentos da cestinha", 0, 3)

# ---------------------------------------------------------------- cardápio
#
# (categoria, ordem, [(produto, preço em centavos, cor do botão, [grupos])])

CARDAPIO = [
    (
        "Sorvetes",
        1,
        [
            ("Casquinha", 700, "#f2b3c8", [COBERTURA]),
            ("Cascão", 1000, "#f2b3c8", [COBERTURA]),
            ("Copo 180ml", 700, "#e8a0b8", [COBERTURA]),
            ("Copo 300ml", 1000, "#e8a0b8", [COBERTURA]),
            ("Copo 500ml", 1500, "#d98da8", [COBERTURA]),
            ("Copo 700ml", 1800, "#d98da8", [COBERTURA]),
        ],
    ),
    (
        "Trufados",
        2,
        [
            # Creme de avelã, amendoim, chocoball e ovomaltine já vão dentro —
            # é o que faz o item ser trufado, não é escolha do cliente.
            ("Casquinha Trufada", 1000, "#b07d4f", [COBERTURA]),
            ("Cascão Trufado", 1300, "#b07d4f", [COBERTURA]),
        ],
    ),
    (
        "Sundae",
        3,
        [
            ("Sundae 300ml", 1500, "#e8618c", [SUNDAE_ACOMP, SUNDAE_ADIC]),
            ("Sundae 500ml", 1800, "#e8618c", [SUNDAE_ACOMP, SUNDAE_ADIC]),
            ("Sundae 700ml", 2000, "#d64a78", [SUNDAE_ACOMP, SUNDAE_ADIC]),
        ],
    ),
    (
        "Açaí montado",
        4,
        [
            ("Açaí montado 300ml", 1700, "#8e6bb0", [ACAI_ACOMP, ACAI_ADIC]),
            ("Açaí montado 500ml", 2500, "#8e6bb0", [ACAI_ACOMP, ACAI_ADIC]),
            ("Açaí montado 700ml", 2800, "#7a5a9c", [ACAI_ACOMP, ACAI_ADIC]),
            ("Açaí tigela", 3000, "#7a5a9c", [ACAI_ACOMP, ACAI_ADIC]),
            ("Açaí pote 1kg", 4000, "#654a82", [ACAI_ACOMP, ACAI_ADIC]),
        ],
    ),
    (
        "Açaí batido",
        5,
        [
            ("Açaí batido 300ml", 1500, "#9b7ac0", [BATIDO_ACOMP]),
            ("Açaí batido 500ml", 2000, "#9b7ac0", [BATIDO_ACOMP]),
            ("Açaí batido 700ml", 2200, "#8e6bb0", [BATIDO_ACOMP]),
        ],
    ),
    (
        "Milk-shake",
        6,
        [
            ("Milk-shake 300ml", 1400, "#e0a96d", []),
            ("Milk-shake 500ml", 1600, "#e0a96d", []),
            ("Milk-shake 700ml", 1800, "#cf9450", []),
        ],
    ),
    (
        "Kids",
        7,
        [
            ("Cestinha Kids", 1400, "#f2a03d", [CESTINHA_ACOMP]),
        ],
    ),
    (
        "Bebidas",
        8,
        [
            ("Água mineral sem gás", 300, "#8fc7e8", []),
            ("Água mineral com gás", 300, "#8fc7e8", []),
        ],
    ),
]


async def semear() -> None:
    async with Sessao() as sessao:
        criados: list[str] = []

        for nome, segredo, papel in USUARIOS:
            existente = (
                await sessao.execute(select(Usuario).where(Usuario.nome == nome))
            ).scalar_one_or_none()
            if existente is None:
                sessao.add(Usuario(nome=nome, pin_hash=gerar_hash(segredo), papel=papel))
                criados.append(f"usuário {nome} ({papel.value})")

        grupos = await _semear_grupos(sessao, criados)
        await _semear_produtos(sessao, grupos, criados)

        await sessao.commit()

    if criados:
        print(f"Criados {len(criados)} registros:")
        for item in criados:
            print(f"  + {item}")
    else:
        print("Nada a fazer — banco já semeado.")

    if not cfg.producao and cfg.senha_dono == "shalon123":
        print("\n  ATENÇÃO: senha do dono é a padrão. Defina SHALON_SENHA_DONO antes de subir.")


# ------------------------------------------------------------------ internos

async def _semear_grupos(sessao, criados: list[str]) -> dict[str, OpcaoGrupo]:
    """Cria os grupos e suas opções. Devolve os grupos por nome."""
    encontrados: dict[str, OpcaoGrupo] = {}

    for ordem, (nome_grupo, opcoes) in enumerate(GRUPOS.items()):
        grupo = (
            await sessao.execute(select(OpcaoGrupo).where(OpcaoGrupo.nome == nome_grupo))
        ).scalar_one_or_none()
        if grupo is None:
            grupo = OpcaoGrupo(nome=nome_grupo, ordem=ordem)
            sessao.add(grupo)
            await sessao.flush()
            criados.append(f"grupo {nome_grupo}")

        # Opção existente não é tocada: se o dono mudou o preço da geléia, quem
        # manda é o banco. O seed só acrescenta o que falta.
        #
        # Consulta direta em vez de `grupo.opcoes`: grupo recém-criado tem a
        # coleção não carregada, e lê-la aqui dispararia lazy load fora do
        # greenlet — o seed morreria na primeira carga, que é justamente a única
        # vez em que ele importa.
        existentes = set(
            (
                await sessao.execute(select(Opcao.nome).where(Opcao.grupo_id == grupo.id))
            ).scalars()
        )
        for i, (nome_opcao, extra) in enumerate(opcoes):
            if nome_opcao in existentes:
                continue
            sessao.add(
                Opcao(
                    grupo_id=grupo.id,
                    nome=nome_opcao,
                    preco_extra_centavos=extra,
                    ordem=i,
                )
            )
            criados.append(f"opção {nome_grupo}: {nome_opcao}")

        encontrados[nome_grupo] = grupo

    await sessao.flush()
    return encontrados


async def _semear_produtos(sessao, grupos: dict[str, OpcaoGrupo], criados: list[str]) -> None:
    for nome_cat, ordem_cat, produtos in CARDAPIO:
        categoria = (
            await sessao.execute(select(Categoria).where(Categoria.nome == nome_cat))
        ).scalar_one_or_none()
        if categoria is None:
            categoria = Categoria(nome=nome_cat, ordem=ordem_cat)
            sessao.add(categoria)
            await sessao.flush()
            criados.append(f"categoria {nome_cat}")

        for i, (nome_prod, preco, cor, vinculos) in enumerate(produtos):
            produto = (
                await sessao.execute(select(Produto).where(Produto.nome == nome_prod))
            ).scalar_one_or_none()
            if produto is None:
                produto = Produto(
                    categoria_id=categoria.id,
                    nome=nome_prod,
                    preco_centavos=preco,
                    cor_botao=cor,
                    ordem=i,
                )
                sessao.add(produto)
                await sessao.flush()
                criados.append(f"produto {nome_prod}")

            for ordem_v, (nome_grupo, minimo, maximo) in enumerate(vinculos):
                grupo = grupos[nome_grupo]
                ja_ligado = (
                    await sessao.execute(
                        select(ProdutoOpcaoGrupo).where(
                            ProdutoOpcaoGrupo.produto_id == produto.id,
                            ProdutoOpcaoGrupo.grupo_id == grupo.id,
                        )
                    )
                ).scalar_one_or_none()
                if ja_ligado is not None:
                    continue
                sessao.add(
                    ProdutoOpcaoGrupo(
                        produto_id=produto.id,
                        grupo_id=grupo.id,
                        min_escolhas=minimo,
                        max_escolhas=maximo,
                        ordem=ordem_v,
                    )
                )
                criados.append(f"{nome_prod} oferece {nome_grupo}")


if __name__ == "__main__":
    asyncio.run(semear())
