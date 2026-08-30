"""Pedidos, itens e o contador de numeração diária."""

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, agora
from app.models.sabor import EscolhaSabor
from app.models.usuario import Usuario


class StatusPedido(str, enum.Enum):
    RECEBIDO = "RECEBIDO"
    EM_PREPARO = "EM_PREPARO"
    PRONTO = "PRONTO"
    ENTREGUE = "ENTREGUE"
    CANCELADO = "CANCELADO"


class ContadorDia(Base):
    """Contador de "Pedido #N", uma linha por dia operacional.

    Existe pra tornar a numeração atômica: dois funcionários apertando ENVIAR
    ao mesmo tempo com `SELECT MAX(numero_dia)+1` geram dois pedidos #37.
    O serviço faz `SELECT ... FOR UPDATE` nesta linha antes de incrementar.
    """

    __tablename__ = "contador_dia"

    data_operacional: Mapped[date] = mapped_column(Date, primary_key=True)
    ultimo_numero: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Pedido(Base):
    __tablename__ = "pedido"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # Gerado no celular ANTES de enviar. É a chave de idempotência: se o
    # funcionário aperta ENVIAR duas vezes porque a internet oscilou, o
    # servidor reconhece o mesmo pedido e não imprime duas comandas.
    id_cliente: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    numero_dia: Mapped[int] = mapped_column(Integer, nullable=False)
    data_operacional: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False)
    status: Mapped[StatusPedido] = mapped_column(
        Enum(StatusPedido, name="status_pedido"), nullable=False, default=StatusPedido.RECEBIDO
    )
    total_centavos: Mapped[int] = mapped_column(Integer, nullable=False)
    observacao: Mapped[str | None] = mapped_column(String(280))

    # Quando o celular criou o pedido (pode ser bem antes de chegar aqui, se
    # ficou na fila offline). É daqui que sai a data_operacional e o preço
    # vigente na hora da venda — não da hora de chegada no servidor.
    criado_em_cliente: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=agora
    )

    # Pedido da fila offline que subiu depois do caixa fechado. Entra no banco
    # (a venda existiu), fica fora do fechamento imutável e acende alerta no
    # PWA do dono.
    pos_fechamento: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    impresso_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelado_por: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"))
    motivo_cancelamento: Mapped[str | None] = mapped_column(String(280))

    itens: Mapped[list["PedidoItem"]] = relationship(
        back_populates="pedido", cascade="all, delete-orphan", lazy="selectin"
    )

    # O cupom impresso traz "Atendente: João" e a tela da cozinha também mostra
    # quem vendeu. `foreign_keys` é obrigatório: a tabela tem duas FKs pra
    # usuario (quem vendeu e quem cancelou) e o SQLAlchemy não adivinha qual.
    usuario: Mapped["Usuario"] = relationship(foreign_keys=[usuario_id], lazy="selectin")

    __table_args__ = (
        # Idempotência: o mesmo uuid do celular nunca vira dois pedidos.
        UniqueConstraint("id_cliente", name="uq_pedido_id_cliente"),
        # Rede de segurança da numeração, além do FOR UPDATE no contador.
        UniqueConstraint("data_operacional", "numero_dia", name="uq_pedido_numero_dia"),
        CheckConstraint("total_centavos >= 0", name="total_nao_negativo"),
        Index("ix_pedido_dia_status", "data_operacional", "status"),
    )


class PedidoItem(Base):
    """Item vendido, com nome e preço congelados no momento da venda.

    Sem o snapshot, o relatório de ontem muda sozinho toda vez que o dono
    edita um preço hoje.
    """

    __tablename__ = "pedido_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    pedido_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pedido.id", ondelete="CASCADE"), nullable=False, index=True
    )
    produto_id: Mapped[int] = mapped_column(ForeignKey("produto.id"), nullable=False)

    nome_snapshot: Mapped[str] = mapped_column(String(80), nullable=False)
    preco_unit_centavos_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False)

    # Já inclui os adicionais pagos: (preço do produto + extras) × quantidade.
    subtotal_centavos: Mapped[int] = mapped_column(Integer, nullable=False)

    # Qual sabor do dia foi esta bola. Nulo em tudo que não leva sorvete — e
    # também no que leva, se o dono ainda não tinha preenchido os sabores.
    #
    # Dois campos e não um: o tipo serve pra contar ("quantos mistos saíram
    # hoje?"), e o texto é o que a comanda imprime. O texto é congelado pela
    # mesma razão que o nome e o preço são: amanhã a máquina tem outro sabor, e
    # sem o snapshot a comanda de ontem passaria a dizer o sabor de hoje.
    sabor_tipo: Mapped[EscolhaSabor | None] = mapped_column(
        Enum(EscolhaSabor, name="escolha_sabor")
    )
    sabor_snapshot: Mapped[str | None] = mapped_column(String(130))

    pedido: Mapped[Pedido] = relationship(back_populates="itens")

    # selectin porque toda leitura de pedido precisa delas: a comanda impressa
    # e a tela da cozinha mostram os acompanhamentos junto do item.
    opcoes: Mapped[list["PedidoItemOpcao"]] = relationship(  # noqa: F821
        back_populates="item", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint("quantidade > 0", name="quantidade_positiva"),
    )
