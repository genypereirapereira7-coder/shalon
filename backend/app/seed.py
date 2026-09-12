"""Popula o banco com o cardápio impresso da Shalon.

    python -m app.seed

Idempotente: rodar de novo não duplica nada. O que está aqui é o cardápio do
folheto (agosto/2026); item novo ou preço novo entra pela tela do dono, não
editando este arquivo.

**O que o seed sobrescreve e o que não.** Preço e ativo são do dono: ele os
edita pela tela, e o seed nunca os toca de volta — seria o cardápio voltando
sozinho ao preço de agosto toda vez que alguém reiniciasse o servidor. Já a
*estrutura* — a ordem das opções e a cota de cada grupo ("escolha 1 cobertura")
— não tem tela nenhuma que a edite: este arquivo é a única fonte, então ele
sincroniza. Sem isso, mexer numa cota aqui não teria efeito em banco nenhum que
já existisse, incluindo o de produção, e a mudança sumiria em silêncio.

Um detalhe de modelagem que se paga na comanda: os cinco tamanhos de açaí
montado apontam pro *mesmo* grupo de acompanhamentos. Mudar "Bis" de nome uma
vez muda nos cinco. A cota ("4 acompanhamentos") não é do grupo, é do vínculo
produto↔grupo — por isso a mesma lista vale 3 no sundae e 4 no açaí.
"""

import asyncio
import secrets

from sqlalchemy import select

from app.config import get_config
from app.db import Sessao
from app.models.base import agora
from app.models.cardapio import CATEGORIAS_SABOR_OBRIGATORIO, Categoria, Produto
from app.models.opcoes import Opcao, OpcaoGrupo, ProdutoOpcaoGrupo
from app.models.usuario import Papel, Usuario
from app.seguranca import conferir_hash, gerar_hash

cfg = get_config()

NOME_DONO = "Adriano"
NOME_AGENTE = "Agente de impressão"

# Sem conta de cozinha: a tela do PC saiu do sistema, e quem imprime a comanda
# agora é o próprio celular que vendeu, pelo RawBT. Criar uma conta com PIN
# 0000 que nenhuma tela usa é porta aberta sem porteiro.
#
# Sem funcionário fixo também: quem vende cria a própria conta pelo link
# "Criar minha conta" na tela de vendas (`POST /auth/cadastro`). Nascer com
# uma "Vanusa" de PIN padrão era conta de ninguém, com senha que todo mundo
# que lê o código conhece.
#
# E, pela mesma regra, **sem senha escrita aqui**. A do dono tinha padrão neste
# repositório e a do agente era "0000" fixo — as duas conhecidas por quem lesse
# o código, as duas aceitas pela tela de login, que está num endereço público.
# Agora as duas vêm de variável de ambiente, e o que falta não é inventado: o
# dono ganha uma sorteada e impressa no log, o agente simplesmente não nasce.

# ---------------------------------------------------------------- opções
#
# Cada grupo é uma lista reaproveitável. `(nome, preco_extra_centavos)` —
# extra 0 é acompanhamento incluído no preço; > 0 é adicional cobrado.

GRUPOS = {
    "Coberturas": [
        # Primeira da lista, e não a última: é a escolha mais rápida quando o
        # cliente não quer cobertura, e ela precisa estar onde o dedo já está.
        # Existir como opção — em vez de "é só não marcar nada" — é o que deixa
        # a comanda dizer *sem cobertura* em letra impressa. Item que sai sem a
        # linha da cobertura é item em que alguém esqueceu de perguntar, e
        # quem monta não tem como saber a diferença.
        ("Sem cobertura", 0),
        ("Morango", 0),
        ("Chocolate", 0),
        ("Caramelo", 0),
        ("Leite condensado", 0),
        ("Groselha", 0),
        ("Kiwi", 0),
        ("Menta", 0),
        ("Limão", 0),
        ("Maracujá", 0),
    ],
    # O que reveste a borda da casquinha/cascão trufado. Todas incluídas no
    # preço: a borda não é adicional, é o que faz o item ser trufado.
    "Bordas do trufado": [
        ("Creme de avelã", 0),
        ("Amendoim", 0),
        ("Chocoball", 0),
        ("Ovomaltine", 0),
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

# Exatamente uma, e obrigatória — com "Sem cobertura" na lista pra quem não
# quer. Mínimo zero deixava o botão ADICIONAR liberado sem ninguém ter
# perguntado nada, e a comanda saía muda sobre a cobertura: quem montava não
# distinguia "o cliente não quis" de "o atendente passou reto".
COBERTURA = ("Coberturas", 1, 1)

# Mesma regra, pelo mesmo motivo: o trufado sempre tem uma borda, e qual é ela
# é escolha do cliente. Sem obrigar, a comanda sairia sem dizer qual — e aí a
# borda vira chute de quem está montando.
BORDA = ("Bordas do trufado", 1, 1)
SUNDAE_ACOMP = ("Acompanhamentos do sundae", 0, 3)
SUNDAE_ADIC = ("Adicionais do sundae", 0, None)
ACAI_ACOMP = ("Acompanhamentos do açaí montado", 0, 4)
ACAI_ADIC = ("Adicionais do açaí", 0, None)
BATIDO_ACOMP = ("Acompanhamentos do açaí batido", 0, 3)
CESTINHA_ACOMP = ("Acompanhamentos da cestinha", 0, 3)

# ---------------------------------------------------------------- cardápio
#
# (categoria, ordem, [(produto, preço, cor, [grupos], sabor_extra opcional)])
#
# O `sabor_extra` é um sabor a mais que o produto sempre oferece, além dos dois
# do dia. Não tem tela de edição — por isso mora aqui, e por isso o seed o
# sincroniza como sincroniza as cotas dos grupos.

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
            ("Pote 1 litro", 2500, "#c77a96", [COBERTURA]),
        ],
    ),
    (
        "Trufados",
        2,
        [
            # Só a borda: o trufado já vem com o acabamento dela, e oferecer
            # cobertura por cima também virou escolha demais pro item.
            ("Casquinha Trufada", 1000, "#b07d4f", [BORDA]),
            ("Cascão Trufado", 1300, "#b07d4f", [BORDA]),
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
            # O milk-shake tem chocolate sempre disponível, além dos dois
            # sabores do dia. O balcão escolhe um ou mistura dois. Cobertura
            # por cima é o mesmo grupo da casquinha — o doce de café fica de
            # fora porque o dele já é fechado (sabor e tudo mais).
            ("Milk-shake 300ml", 1400, "#e0a96d", [COBERTURA], "Chocolate"),
            ("Milk-shake 500ml", 1600, "#e0a96d", [COBERTURA], "Chocolate"),
            ("Milk-shake 700ml", 1800, "#cf9450", [COBERTURA], "Chocolate"),
            # Sabor fechado, tamanho único — por isso não entra na escada de
            # 300/500/700 dos outros, e não oferece escolha nenhuma.
            ("Milk-shake doce de café", 1800, "#cf9450", []),
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
            ("Coca-cola latinha", 500, "#c0392b", []),
            ("Pepsi latinha", 500, "#1f4e8c", []),
            ("Água saborizada limão", 800, "#a8d84f", []),
        ],
    ),
]


async def _por_nome(sessao, nome: str) -> Usuario | None:
    return (
        await sessao.execute(select(Usuario).where(Usuario.nome == nome))
    ).scalar_one_or_none()


async def _semear_dono(sessao, criados: list[str], ajustados: list[str]) -> None:
    """A conta do dono: cria na primeira vez, e troca a senha quando mandarem.

    Trocar a senha de uma conta que já existe é o único ponto em que o seed
    mexe em algo que já estava lá — e é de propósito. A senha antiga estava
    escrita no repositório: sem este caminho, consertar isso numa loja que já
    roda exigiria abrir o banco na mão, porque não há tela que troque a senha
    do dono. Definir `SHALON_SENHA_DONO` e reimplantar passa a ser o conserto.

    Só troca quando a variável existe e a senha é de fato outra: sem isso, um
    seed a cada deploy reescreveria a senha do dono toda vez, e um `bcrypt` por
    arranque a troco de nada.

    As sessões abertas continuam abertas. Quem está com o celular na mão não é
    deslogado por uma troca de senha — o refresh token vive no banco por si e
    não depende dela. Se a intenção for cortar acesso, é a tela de sessões que
    faz isso, e ela existe (`/auth/sessoes`).
    """
    dono = await _por_nome(sessao, NOME_DONO)

    if dono is None:
        # Vazia na primeira semeadura: melhor uma senha que só existe no log
        # deste deploy do que uma que existe no GitHub.
        segredo = cfg.senha_dono or secrets.token_urlsafe(9)
        sessao.add(
            Usuario(
                nome=NOME_DONO,
                pin_hash=gerar_hash(segredo),
                papel=Papel.DONO,
                # `aprovado_em` preenchido: o dono é quem libera os outros, não
                # faria sentido esperar a si mesmo.
                aprovado_em=agora(),
            )
        )
        criados.append(f"usuário {NOME_DONO} ({Papel.DONO.value})")
        if not cfg.senha_dono:
            print(
                "\n  ============================================================\n"
                f"   SENHA DO DONO (usuario: {NOME_DONO})\n"
                f"       {segredo}\n"
                "   Anote agora: ela nao aparece de novo. Pra escolher a sua,\n"
                "   defina SHALON_SENHA_DONO e implante outra vez.\n"
                "  ============================================================\n",
                flush=True,
            )
        return

    if cfg.senha_dono and not conferir_hash(cfg.senha_dono, dono.pin_hash):
        dono.pin_hash = gerar_hash(cfg.senha_dono)
        ajustados.append(f"senha de {NOME_DONO}")


async def _semear_agente(sessao, criados: list[str], ajustados: list[str]) -> None:
    """A conta de máquina do agente de impressão em PC.

    Só existe se alguém pedir. Ela nascia sempre, ativa, com PIN "0000" escrito
    no código — e o `/auth/login` não filtra papel, então qualquer pessoa que
    lesse o repositório entrava com ela e lia os pedidos do dia inteiro. Hoje a
    comanda sai no celular do balcão pelo RawBT e essa conta não serve a
    ninguém, então o padrão passa a ser não existir.

    Quem já tem o agente rodando define `SHALON_SENHA_AGENTE` e nada muda de
    lugar. Quem não tem — o caso de todo mundo — ganha a conta desativada no
    próximo deploy. Desativar e não apagar porque `pedido.impresso_em` pode ter
    sido marcado por ela: o histórico fica, o acesso não.
    """
    agente = await _por_nome(sessao, NOME_AGENTE)

    if agente is None:
        if not cfg.senha_agente:
            return
        sessao.add(
            Usuario(
                nome=NOME_AGENTE,
                pin_hash=gerar_hash(cfg.senha_agente),
                papel=Papel.AGENTE,
                aprovado_em=agora(),
            )
        )
        criados.append(f"usuário {NOME_AGENTE} ({Papel.AGENTE.value})")
        return

    if not cfg.senha_agente:
        if agente.ativo:
            agente.ativo = False
            ajustados.append(f"{NOME_AGENTE} desativado (defina SHALON_SENHA_AGENTE pra usar)")
        return

    if not conferir_hash(cfg.senha_agente, agente.pin_hash):
        agente.pin_hash = gerar_hash(cfg.senha_agente)
        ajustados.append(f"senha de {NOME_AGENTE}")
    if not agente.ativo:
        agente.ativo = True
        ajustados.append(f"{NOME_AGENTE} reativado")


async def semear(detalhado: bool = True) -> None:
    """Popula usuários e cardápio. Idempotente.

    `detalhado=False` lista só o resumo. É o que o `dev.py` usa: na primeira
    execução são mais de cem registros, e a lista inteira empurraria pra fora
    da tela justamente os links que aquele script existe pra mostrar.
    """
    async with Sessao() as sessao:
        criados: list[str] = []
        # Separado dos criados porque é outra coisa: aqui o registro já
        # existia e o seed corrigiu a estrutura dele. Somar os dois num número
        # só faria "3 registros criados" aparecer num banco onde nada nasceu.
        ajustados: list[str] = []

        await _semear_dono(sessao, criados, ajustados)
        await _semear_agente(sessao, criados, ajustados)

        grupos = await _semear_grupos(sessao, criados, ajustados)
        await _semear_produtos(sessao, grupos, criados, ajustados)

        await sessao.commit()

        # O agente de impressão precisa do id do próprio usuário no
        # `config.ini` (ele é conta de máquina e não entra por tela de login,
        # onde a lista mostraria o id). Sem imprimir aqui, descobrir esse
        # número vira uma consulta no banco.
        usuarios = (await sessao.execute(select(Usuario).order_by(Usuario.id))).scalars()
        linhas = [f"  {u.id:>3}  {u.papel.value:<12} {u.nome}" for u in usuarios]

    if criados:
        print(f"seed:    {len(criados)} registros criados")
        if detalhado:
            for item in criados:
                print(f"  + {item}")
    if ajustados:
        print(f"seed:    {len(ajustados)} ajustes de estrutura")
        for item in ajustados:
            print(f"  ~ {item}")
    if not criados and not ajustados:
        print("seed:    nada a fazer, banco já semeado")

    print("\nUsuários:")
    print("   id  papel        nome")
    for linha in linhas:
        print(linha)

    if cfg.producao:
        return

    print(
        "\n  A senha do dono vem de SHALON_SENHA_DONO — o `dev.py` define uma"
        "\n  fixa pra rodar local. Em produção, defina a sua no painel: sem ela"
        "\n  o seed sorteia uma e a imprime uma única vez."
        "\n  Funcionário não nasce pelo seed — cria a própria conta pela tela de vendas."
    )


# ------------------------------------------------------------------ internos

async def _semear_grupos(
    sessao, criados: list[str], ajustados: list[str]
) -> dict[str, OpcaoGrupo]:
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

        # O preço de uma opção existente não é tocado: se o dono mudou o valor
        # da geléia, quem manda é o banco. A **ordem**, sim — ela não tem tela
        # que a edite, e sem sincronizar aqui uma opção nova inserida no meio da
        # lista nasceria empatada com a que já ocupava aquele lugar, saindo em
        # posição imprevisível na folha de escolhas.
        #
        # Consulta direta em vez de `grupo.opcoes`: grupo recém-criado tem a
        # coleção não carregada, e lê-la aqui dispararia lazy load fora do
        # greenlet — o seed morreria na primeira carga, que é justamente a única
        # vez em que ele importa.
        existentes = {
            o.nome: o
            for o in (
                await sessao.execute(select(Opcao).where(Opcao.grupo_id == grupo.id))
            ).scalars()
        }
        for i, (nome_opcao, extra) in enumerate(opcoes):
            existente = existentes.get(nome_opcao)
            if existente is None:
                sessao.add(
                    Opcao(
                        grupo_id=grupo.id,
                        nome=nome_opcao,
                        preco_extra_centavos=extra,
                        ordem=i,
                    )
                )
                criados.append(f"opção {nome_grupo}: {nome_opcao}")
            elif existente.ordem != i:
                existente.ordem = i
                ajustados.append(f"opção {nome_grupo}: {nome_opcao} → posição {i}")

        encontrados[nome_grupo] = grupo

    await sessao.flush()
    return encontrados


async def _semear_produtos(
    sessao,
    grupos: dict[str, OpcaoGrupo],
    criados: list[str],
    ajustados: list[str],
) -> None:
    for nome_cat, ordem_cat, produtos in CARDAPIO:
        categoria = (
            await sessao.execute(select(Categoria).where(Categoria.nome == nome_cat))
        ).scalar_one_or_none()
        if categoria is None:
            categoria = Categoria(nome=nome_cat, ordem=ordem_cat)
            sessao.add(categoria)
            await sessao.flush()
            criados.append(f"categoria {nome_cat}")

        for i, linha in enumerate(produtos):
            # Tupla de 4 ou de 5: só quem tem sabor fixo carrega o quinto item.
            nome_prod, preco, cor, vinculos = linha[:4]
            sabor_extra = linha[4] if len(linha) > 4 else None

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
                    sabor_extra=sabor_extra,
                    # Sabor extra só serve pra quem pergunta o sabor: ele é uma
                    # terceira opção na lista, não uma receita fechada.
                    pede_sabor=bool(sabor_extra)
                    or nome_cat in CATEGORIAS_SABOR_OBRIGATORIO,
                )
                sessao.add(produto)
                await sessao.flush()
                criados.append(f"produto {nome_prod}")
            elif produto.sabor_extra != sabor_extra:
                # Estrutura, e portanto sincronizada: nenhuma tela edita isto,
                # então este arquivo é a única fonte. Sem o ajuste, mudar a
                # lista aqui não teria efeito em banco nenhum que já existe.
                produto.sabor_extra = sabor_extra
                ajustados.append(
                    f"{nome_prod}: sabor extra {sabor_extra or 'removido'}"
                )

            # Produto com sabor extra e sem `pede_sabor` esconderia a própria
            # opção que acabou de ganhar: a lista existiria e ninguém a veria.
            # Mesma lógica pras categorias obrigatórias: um produto criado antes
            # desta regra existir não pode ficar pra trás só porque já existia.
            if (sabor_extra or nome_cat in CATEGORIAS_SABOR_OBRIGATORIO) and not produto.pede_sabor:
                produto.pede_sabor = True
                ajustados.append(f"{nome_prod}: passa a perguntar o sabor")

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
                    # A cota mora só aqui — nenhuma tela a edita. Corrigir o
                    # vínculo existente é o que faz mudar `COBERTURA` neste
                    # arquivo valer também nos bancos que já rodam.
                    atual = (ja_ligado.min_escolhas, ja_ligado.max_escolhas, ja_ligado.ordem)
                    if atual != (minimo, maximo, ordem_v):
                        ja_ligado.min_escolhas = minimo
                        ja_ligado.max_escolhas = maximo
                        ja_ligado.ordem = ordem_v
                        ajustados.append(
                            f"{nome_prod}: {nome_grupo} agora "
                            f"{minimo}–{maximo if maximo is not None else '∞'}"
                        )
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

            # O inverso da sincronização acima: um grupo que saiu da lista
            # aqui (o trufado que deixou de oferecer cobertura, por exemplo)
            # precisa sair também do banco que já rodava — senão este arquivo
            # não é mais "a única fonte", é só a fonte de quem nunca mudou de
            # ideia.
            grupos_atuais = {grupos[nome_grupo].id for nome_grupo, _, _ in vinculos}
            vinculados = (
                await sessao.execute(
                    select(ProdutoOpcaoGrupo).where(
                        ProdutoOpcaoGrupo.produto_id == produto.id
                    )
                )
            ).scalars()
            for vinculo in vinculados:
                if vinculo.grupo_id not in grupos_atuais:
                    ajustados.append(f"{nome_prod}: não oferece mais {vinculo.grupo.nome}")
                    await sessao.delete(vinculo)


if __name__ == "__main__":
    asyncio.run(semear())
