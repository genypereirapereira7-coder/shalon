"""Base declarativa e helpers de tempo."""

from datetime import UTC, datetime

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Nomes previsíveis pros constraints — sem isso o Alembic gera migration
# com nome automático e o autogenerate fica ruidoso a cada rodada.
convencao = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=convencao)


def agora() -> datetime:
    """Sempre UTC no banco. A conversão pro fuso da loja é feita na borda."""
    return datetime.now(UTC)


def como_utc(momento: datetime) -> datetime:
    """Garante datetime com fuso.

    O Postgres devolve `timestamptz` já com fuso, mas SQLite (usado nos testes)
    devolve naive — e comparar naive com aware levanta TypeError. Normalizar
    aqui evita que a diferença entre os dois bancos vire bug.
    """
    return momento.replace(tzinfo=UTC) if momento.tzinfo is None else momento
