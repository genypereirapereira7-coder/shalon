"""Categorias, produtos e auditoria de preço.

Todo dinheiro é inteiro em centavos. Float em dinheiro gera erro de centavo
no fechamento do dia.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, agora


class Categoria(Base):
    __tablename__ = "categoria"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(60), nullable=False)
    ordem: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    produtos: Mapped[list["Produto"]] = relationship(
        back_populates="categoria", order_by="Produto.ordem"
    )


class Produto(Base):
    __tablename__ = "produto"

    id: Mapped[int] = mapped_column(primary_key=True)
    categoria_id: Mapped[int] = mapped_column(ForeignKey("categoria.id"), nullable=False)
    nome: Mapped[str] = mapped_column(String(80), nullable=False)
    preco_centavos: Mapped[int] = mapped_column(Integer, nullable=False)
    cor_botao: Mapped[str | None] = mapped_column(String(9))  # #RRGGBB
    ordem: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Este produto leva bola de sorvete, e por isso a tela de vendas pergunta
    # qual sabor do dia vai nele. É um interruptor por produto e não uma regra
    # por categoria porque a fronteira não é limpa: o milk-shake leva bola, a
    # água não, e o dono é quem sabe o que a máquina dele serve. Marcar produto
    # por produto custa um toque uma vez; adivinhar errado custa uma pergunta
    # boba na cara do cliente em cada garrafa de água vendida.
    pede_sabor: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Sabor que este produto sempre leva, sem perguntar nada a ninguém.
    #
    # O milk-shake da casa é de chocolate: não é escolha do cliente nem sabor
    # do dia, é a receita. Perguntar seria um toque a mais em cada venda pra
    # uma resposta que nunca muda — e deixar em branco faria a comanda sair
    # sem dizer o que a cozinha tem que bater.
    #
    # Ganha do `pede_sabor`: com sabor fixo, o balcão não pergunta. Não tem
    # tela de edição de propósito — é receita, e receita não muda de manhã
    # junto com o que está na máquina. Quem a define é o `seed.py`.
    sabor_fixo: Mapped[str | None] = mapped_column(String(60))

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=agora
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=agora, onupdate=agora
    )

    categoria: Mapped[Categoria] = relationship(back_populates="produtos")

    __table_args__ = (
        CheckConstraint("preco_centavos >= 0", name="preco_nao_negativo"),
    )


class PrecoHistorico(Base):
    """Quem mudou o preço, quando, de quanto pra quanto.

    Serve pra auditoria e — mais importante — pra reconstruir o preço que valia
    num instante passado, quando um pedido sobe atrasado da fila offline.
    """

    __tablename__ = "preco_historico"

    id: Mapped[int] = mapped_column(primary_key=True)
    produto_id: Mapped[int] = mapped_column(
        ForeignKey("produto.id"), nullable=False, index=True
    )
    preco_antigo: Mapped[int] = mapped_column(Integer, nullable=False)
    preco_novo: Mapped[int] = mapped_column(Integer, nullable=False)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=agora, index=True
    )
