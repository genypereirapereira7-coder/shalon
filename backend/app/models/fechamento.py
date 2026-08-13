"""Fechamento de caixa — imutável depois de gravado."""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer
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
