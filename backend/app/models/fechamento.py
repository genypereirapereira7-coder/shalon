"""Fechamento de caixa — imutável depois de gravado."""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, agora


class FechamentoDia(Base):
    __tablename__ = "fechamento_dia"

    id: Mapped[int] = mapped_column(primary_key=True)
    data_operacional: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    total_centavos: Mapped[int] = mapped_column(Integer, nullable=False)
    qtd_pedidos: Mapped[int] = mapped_column(Integer, nullable=False)
    fechado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=agora
    )
    fechado_por: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False)

    # Fechado sozinho, na hora marcada, ou por alguém que tocou no botão.
    #
    # O `fechado_por` continua apontando pro dono mesmo no automático: é a conta
    # sob cuja autoridade o caixa fecha, e deixá-lo vazio custaria tornar a
    # coluna anulável — o que no SQLite significa reconstruir a tabela. Quem
    # conta a verdade pra quem lê o histórico é este campo, e a tela do dono usa
    # ele em vez do nome.
    automatico: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
