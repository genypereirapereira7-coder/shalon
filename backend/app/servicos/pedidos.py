"""Criação de pedido: idempotência, numeração do dia e total recalculado.

Três regras que este módulo existe pra garantir:

1. **O servidor não confia no total do celular.** Qualquer um edita o
   JavaScript. O valor gravado é sempre recalculado aqui, pelo preço que o
   produto tinha no instante da venda (`servicos.precos.preco_em`).
2. **O mesmo `id_cliente` nunca vira dois pedidos.** O funcionário aperta
   ENVIAR duas vezes porque a internet oscilou — sai uma comanda só.
3. **A numeração do dia é atômica.** Dois celulares vendendo ao mesmo tempo
   não podem gerar dois "Pedido #37".
"""

import uuid
from collections import OrderedDict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.cardapio import Produto
from app.models.fechamento import FechamentoDia
from app.models.opcoes import Opcao, PedidoItemOpcao, ProdutoOpcaoGrupo
from app.models.pedido import ContadorDia, Pedido, PedidoItem
from app.models.sabor import EscolhaSabor
from app.models.usuario import Usuario
from app.schemas.pedido import ItemEntrada, PedidoEntrada
from app.servicos import sabores
from app.servicos.dia_operacional import atraso_aceitavel, dia_operacional
from app.servicos.precos import preco_em


class PedidoInvalido(Exception):
    """Erro de negócio na criação. A rota traduz pra HTTP."""


async def criar(
    sessao: AsyncSession, entrada: PedidoEntrada, usuario_id: int
) -> tuple[Pedido, bool]:
    """Grava o pedido. Devolve `(pedido, criado_agora)`.

    `criado_agora=False` significa reenvio do mesmo `id_cliente`: o pedido que
    volta é o que já estava no banco. Não faz commit — quem fecha a transação
    é a dependência de sessão.
    """
    if (existente := await por_id_cliente(sessao, entrada.id_cliente)) is not None:
        return existente, False

    # Pedido da fila offline pode chegar horas depois — mas com relógio de
    # celular errado a data_operacional sairia furada e sujaria o fechamento.
    if not atraso_aceitavel(entrada.criado_em_cliente):
        raise PedidoInvalido(
            "Horário do pedido fora da janela aceitável — verifique o relógio do aparelho."
        )

    # Carregado (em vez de só passar o id) porque o cupom e a tela da cozinha
    # mostram o nome do atendente: sem o objeto na sessão, ler `pedido.usuario`
    # depois do flush dispararia lazy load fora do contexto async.
    usuario = await sessao.get(Usuario, usuario_id)
    if usuario is None:
        raise PedidoInvalido("Usuário que emitiu o pedido não existe mais")

    data = dia_operacional(entrada.criado_em_cliente)
    itens, total = await _montar_itens(sessao, entrada.itens, entrada.criado_em_cliente)

    pedido = Pedido(
        id_cliente=entrada.id_cliente,
        numero_dia=await _proximo_numero(sessao, data),
        data_operacional=data,
        usuario=usuario,
        total_centavos=total,
        observacao=entrada.observacao,
        criado_em_cliente=entrada.criado_em_cliente,
        pos_fechamento=await _dia_ja_fechado(sessao, data),
        itens=itens,
    )

    try:
        async with sessao.begin_nested():
            sessao.add(pedido)
            await sessao.flush()
    except IntegrityError:
        # Dois envios do mesmo id_cliente chegaram juntos e o primeiro ganhou
        # a corrida depois do nosso SELECT. A constraint uq_pedido_id_cliente
        # é a rede de segurança: devolvemos o pedido que venceu.
        if (existente := await por_id_cliente(sessao, entrada.id_cliente)) is not None:
            return existente, False
        raise

    return pedido, True


async def por_id_cliente(sessao: AsyncSession, id_cliente: uuid.UUID) -> Pedido | None:
    consulta = select(Pedido).where(Pedido.id_cliente == id_cliente)
    return (await sessao.execute(consulta)).scalar_one_or_none()


def divergiu(pedido: Pedido, total_do_cliente: int | None) -> bool:
    """O celular calculou um total diferente do nosso.

    Não é motivo pra recusar a venda — quase sempre é cardápio velho no cache
    do PWA. Mas o dono precisa saber que aconteceu.
    """
    return total_do_cliente is not None and total_do_cliente != pedido.total_centavos


# ------------------------------------------------------------------ internos

async def _montar_itens(
    sessao: AsyncSession, entradas: list[ItemEntrada], momento: datetime
) -> tuple[list[PedidoItem], int]:
    """Converte os itens do celular em linhas com nome e preço congelados."""
    # A mesma casquinha apertada três vezes vira uma linha "3x" — comanda
    # curta é comanda que a cozinha lê rápido. Mas dois açaís só viram "2x" se
    # levarem os mesmos acompanhamentos: um com granola e outro com paçoca são
    # duas linhas, senão a cozinha monta os dois iguais.
    #
    # O sabor entra na chave pela mesma razão: uma casquinha de chocolate e uma
    # de creme somariam "2x Casquinha" e a cozinha serviria as duas iguais.
    quantidades: OrderedDict[
        tuple[int, tuple[int, ...], tuple[EscolhaSabor, ...]], int
    ] = OrderedDict()
    for item in entradas:
        # A tupla dos sabores entra na chave **na ordem escolhida**: "Morango +
        # Chocolate" e "Chocolate + Morango" são a mesma casquinha, mas o papel
        # sai diferente, e somar as duas numa linha só faria a comanda mentir
        # sobre uma delas.
        chave = (item.produto_id, tuple(sorted(item.opcoes)), tuple(item.sabores))
        quantidades[chave] = quantidades.get(chave, 0) + item.quantidade

    produto_ids = {pid for pid, _, _ in quantidades}
    consulta = select(Produto).where(Produto.id.in_(produto_ids))
    produtos = {p.id: p for p in (await sessao.execute(consulta)).scalars()}

    if faltando := [pid for pid in produto_ids if pid not in produtos]:
        raise PedidoInvalido(f"Produto inexistente: {', '.join(map(str, faltando))}")

    catalogo, vinculos = await _carregar_opcoes(sessao, produto_ids, quantidades)

    # Uma leitura só, fora do laço: o sabor é o mesmo pro pedido inteiro, e
    # buscá-lo por item faria uma consulta por linha da comanda.
    sabor_atual = await sabores.ler(sessao)

    itens: list[PedidoItem] = []
    total = 0
    for (produto_id, opcao_ids, escolhas_sabor), quantidade in quantidades.items():
        produto = produtos[produto_id]
        escolhidas = _conferir_opcoes(produto, vinculos.get(produto_id, []), opcao_ids, catalogo)

        # Sabor em produto que não pede é descartado, não é erro: o celular
        # pode estar com o cardápio velho em cache, de quando o dono ainda
        # marcava este produto. Recusar a venda por isso pararia a fila por uma
        # divergência que não muda preço nem o que o cliente leva.
        sabor_tipos, sabor_texto = (
            sabores.resolver(list(escolhas_sabor), sabor_atual, produto.sabor_extra)
            if produto.pede_sabor
            else (None, None)
        )

        # Produto desativado depois da venda ainda entra: a venda aconteceu.
        # O que não pode é produto que nunca existiu (checado acima).
        preco = await preco_em(sessao, produto, momento)
        extras = sum(o.preco_extra_centavos for o in escolhidas)
        subtotal = (preco + extras) * quantidade
        total += subtotal

        itens.append(
            PedidoItem(
                produto_id=produto.id,
                nome_snapshot=produto.nome,
                preco_unit_centavos_snapshot=preco,
                quantidade=quantidade,
                subtotal_centavos=subtotal,
                sabor_tipos=sabor_tipos,
                sabor_snapshot=sabor_texto,
                opcoes=[
                    PedidoItemOpcao(
                        opcao_id=opcao.id,
                        nome_snapshot=opcao.nome,
                        grupo_snapshot=opcao.grupo.nome if opcao.grupo else None,
                        preco_extra_centavos_snapshot=opcao.preco_extra_centavos,
                    )
                    for opcao in escolhidas
                ],
            )
        )

    return itens, total


async def _carregar_opcoes(
    sessao: AsyncSession,
    produto_ids: set[int],
    quantidades: OrderedDict[tuple[int, tuple[int, ...], tuple[EscolhaSabor, ...]], int],
) -> tuple[dict[int, Opcao], dict[int, list[ProdutoOpcaoGrupo]]]:
    """Busca as opções escolhidas e o que cada produto tem direito de oferecer."""
    escolhidas = {oid for _, ids, _ in quantidades for oid in ids}

    catalogo: dict[int, Opcao] = {}
    if escolhidas:
        # `selectinload` no grupo: a comanda imprime o nome dele como título do
        # bloco, e sem carregar aqui o acesso a `opcao.grupo` dispararia um
        # lazy load dentro do contexto async — que não é um item faltando na
        # comanda, é um MissingGreenlet estourando no meio da venda.
        consulta = (
            select(Opcao)
            .where(Opcao.id.in_(escolhidas))
            .options(selectinload(Opcao.grupo))
        )
        catalogo = {o.id: o for o in (await sessao.execute(consulta)).scalars()}
        if faltando := [oid for oid in escolhidas if oid not in catalogo]:
            raise PedidoInvalido(f"Opção inexistente: {', '.join(map(str, faltando))}")

    vinculos: dict[int, list[ProdutoOpcaoGrupo]] = {}
    consulta = select(ProdutoOpcaoGrupo).where(ProdutoOpcaoGrupo.produto_id.in_(produto_ids))
    for vinculo in (await sessao.execute(consulta)).scalars():
        vinculos.setdefault(vinculo.produto_id, []).append(vinculo)

    return catalogo, vinculos


def _conferir_opcoes(
    produto: Produto,
    vinculos: list[ProdutoOpcaoGrupo],
    opcao_ids: tuple[int, ...],
    catalogo: dict[int, Opcao],
) -> list[Opcao]:
    """Valida a escolha contra a cota do produto e devolve as opções na ordem do cardápio.

    O celular já limita a seleção na tela, mas a checagem tem que existir aqui:
    o JavaScript é do cliente, o preço é nosso. Sem isto, dava pra mandar dez
    adicionais pagos marcados como acompanhamento grátis.
    """
    if len(set(opcao_ids)) != len(opcao_ids):
        raise PedidoInvalido(f"{produto.nome}: a mesma opção veio repetida")

    por_grupo = {vinculo.grupo_id: vinculo for vinculo in vinculos}
    contagem: dict[int, int] = {}
    escolhidas: list[Opcao] = []

    for opcao_id in opcao_ids:
        opcao = catalogo[opcao_id]
        if opcao.grupo_id not in por_grupo:
            raise PedidoInvalido(f"{produto.nome} não aceita a opção “{opcao.nome}”")
        contagem[opcao.grupo_id] = contagem.get(opcao.grupo_id, 0) + 1
        escolhidas.append(opcao)

    for vinculo in vinculos:
        quantas = contagem.get(vinculo.grupo_id, 0)
        if vinculo.max_escolhas is not None and quantas > vinculo.max_escolhas:
            raise PedidoInvalido(
                f"{produto.nome}: {vinculo.grupo.nome} aceita no máximo "
                f"{vinculo.max_escolhas} — vieram {quantas}"
            )
        if quantas < vinculo.min_escolhas:
            raise PedidoInvalido(
                f"{produto.nome}: escolha pelo menos {vinculo.min_escolhas} "
                f"em {vinculo.grupo.nome}"
            )

    # Ordem do cardápio, não a ordem em que o funcionário tocou na tela: a
    # comanda sai sempre igual, e a cozinha lê no automático.
    return sorted(escolhidas, key=lambda o: (o.grupo_id, o.ordem, o.nome))


async def _proximo_numero(sessao: AsyncSession, data) -> int:
    """Próximo "Pedido #N" do dia, incrementado sob lock de linha.

    `SELECT MAX(numero_dia)+1` daria dois #37 pra dois funcionários apertando
    ENVIAR no mesmo segundo. O `FOR UPDATE` no contador serializa isso.
    (Em SQLite, usado nos testes, o lock é no-op — mas lá não há concorrência.)
    """
    consulta = (
        select(ContadorDia)
        .where(ContadorDia.data_operacional == data)
        .with_for_update()
    )
    contador = (await sessao.execute(consulta)).scalar_one_or_none()

    if contador is None:
        try:
            async with sessao.begin_nested():
                contador = ContadorDia(data_operacional=data, ultimo_numero=0)
                sessao.add(contador)
                await sessao.flush()
        except IntegrityError:
            # Primeiro pedido do dia chegou em duplicata: outro criou a linha
            # entre o nosso SELECT e o INSERT. Relê, agora já travando.
            contador = (await sessao.execute(consulta)).scalar_one()

    contador.ultimo_numero += 1
    await sessao.flush()
    return contador.ultimo_numero


async def _dia_ja_fechado(sessao: AsyncSession, data) -> bool:
    """Pedido da fila offline que subiu depois do caixa fechado.

    Entra no banco (a venda existiu), mas fica marcado: o fechamento é imutável
    e o dono precisa ver que aquele dia ganhou uma venda fora do relatório.
    """
    consulta = select(FechamentoDia.id).where(FechamentoDia.data_operacional == data)
    return (await sessao.execute(consulta)).scalar_one_or_none() is not None
