"""Acompanhamentos e adicionais.

O cardápio da Shalon não vende só "Açaí 500ml": vende um açaí de 500ml com
quatro acompanhamentos escolhidos pelo cliente, e possivelmente uma geléia que
custa R$3 a mais. Sem isto no modelo, a comanda chega na cozinha dizendo só o
tamanho — e alguém tem que gritar por cima do balcão o que vai dentro.

Três tabelas, e a razão de cada uma:

`opcao_grupo` é uma **lista reaproveitável** ("Acompanhamentos do açaí"). Os
cinco tamanhos de açaí apontam pra mesma lista; mudar um item muda nos cinco.

`produto_opcao_grupo` liga produto e grupo, e é **onde mora a cota**. O mesmo
grupo vale 3 escolhas no sundae e 4 no açaí montado — a cota é da combinação,
não da lista.

`opcao` guarda o preço extra. Ele fica na opção e não num preço global porque
o próprio cardápio impresso cobra a geléia de morango R$2 no açaí e R$3 no
sundae: são grupos diferentes, cada um com seu preço.
"""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class OpcaoGrupo(Base):
    """Uma lista de escolhas: "Acompanhamentos do sundae", "Adicionais do açaí"."""

    __tablename__ = "opcao_grupo"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(60), nullable=False)

    # Só muda o texto da tela ("escolha 3" x "quer adicionar algo?"). A regra
    # de verdade — quantas escolhas cabem — está no vínculo com o produto.
    ordem: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    opcoes: Mapped[list["Opcao"]] = relationship(
        back_populates="grupo", order_by="Opcao.ordem", lazy="selectin"
    )


class Opcao(Base):
    """Um item escolhível. `preco_extra_centavos = 0` é acompanhamento grátis."""

    __tablename__ = "opcao"

    id: Mapped[int] = mapped_column(primary_key=True)
    grupo_id: Mapped[int] = mapped_column(
        ForeignKey("opcao_grupo.id", ondelete="CASCADE"), nullable=False, index=True
    )
    nome: Mapped[str] = mapped_column(String(60), nullable=False)
    preco_extra_centavos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ordem: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    grupo: Mapped[OpcaoGrupo] = relationship(back_populates="opcoes")

    __table_args__ = (
        CheckConstraint("preco_extra_centavos >= 0", name="preco_extra_nao_negativo"),
    )


class ProdutoOpcaoGrupo(Base):
    """Quais grupos um produto oferece, e quantas escolhas cabem em cada um."""

    __tablename__ = "produto_opcao_grupo"

    id: Mapped[int] = mapped_column(primary_key=True)
    produto_id: Mapped[int] = mapped_column(
        ForeignKey("produto.id", ondelete="CASCADE"), nullable=False, index=True
    )
    grupo_id: Mapped[int] = mapped_column(
        ForeignKey("opcao_grupo.id", ondelete="CASCADE"), nullable=False
    )

    # "3 ACOMPANHAMENTOS" no cardápio impresso = min 0, max 3: a cota é um teto,
    # e ninguém é obrigado a levar granola. `max_escolhas` nulo = sem teto, que
    # é o caso dos adicionais pagos (leve quantos quiser, cada um cobrado).
    min_escolhas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_escolhas: Mapped[int | None] = mapped_column(Integer)
    ordem: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    grupo: Mapped[OpcaoGrupo] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint("produto_id", "grupo_id", name="uq_produto_opcao_grupo_produto_id"),
        CheckConstraint("min_escolhas >= 0", name="min_nao_negativo"),
        CheckConstraint(
            "max_escolhas IS NULL OR max_escolhas >= min_escolhas", name="max_maior_que_min"
        ),
    )


class PedidoItemOpcao(Base):
    """O que o cliente escolheu, congelado como o resto do pedido.

    Mesmo motivo do snapshot em `pedido_item`: se o dono renomear "Geléia de
    morango" ou mudar o preço amanhã, a comanda e o relatório de ontem
    continuam contando o que realmente foi vendido.
    """

    __tablename__ = "pedido_item_opcao"

    id: Mapped[int] = mapped_column(primary_key=True)
    pedido_item_id: Mapped[int] = mapped_column(
        ForeignKey("pedido_item.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Sem cascade de propósito: opção apagada não pode sumir com o histórico.
    opcao_id: Mapped[int | None] = mapped_column(ForeignKey("opcao.id"))

    nome_snapshot: Mapped[str] = mapped_column(String(60), nullable=False)
    preco_extra_centavos_snapshot: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )

    # De que grupo veio esta escolha ("Cobertura", "Acompanhamentos do açaí").
    #
    # Sem isto a comanda imprime uma lista de nomes soltos sob o item, e quem
    # monta não sabe se "Chocolate" é a cobertura ou o sabor da bola — que é
    # exatamente a confusão que aparece quando o produto tem os dois. O nome do
    # grupo é o que dá título a cada bloco do papel.
    #
    # Congelado como o resto: renomear o grupo amanhã não pode reescrever a
    # comanda de ontem.
    grupo_snapshot: Mapped[str | None] = mapped_column(String(60))

    item: Mapped["PedidoItem"] = relationship(back_populates="opcoes")  # noqa: F821

    __table_args__ = (
        CheckConstraint(
            "preco_extra_centavos_snapshot >= 0", name="preco_extra_snapshot_nao_negativo"
        ),
    )
