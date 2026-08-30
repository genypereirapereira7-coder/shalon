"""Usuários, papéis e sessões de autenticação."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, agora, como_utc


class Papel(str, enum.Enum):
    """Quem é quem no roteamento do WebSocket e nas permissões da API.

    COZINHA e AGENTE existem porque a tela do PC e o programa de impressão
    também autenticam e recebem eventos — não são "funcionário".
    """

    DONO = "DONO"
    FUNCIONARIO = "FUNCIONARIO"
    COZINHA = "COZINHA"
    AGENTE = "AGENTE"


class Usuario(Base):
    __tablename__ = "usuario"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(80), nullable=False)
    # PIN de 4-6 dígitos (funcionário) ou senha (dono) — sempre bcrypt.
    pin_hash: Mapped[str] = mapped_column(String(120), nullable=False)
    papel: Mapped[Papel] = mapped_column(Enum(Papel, name="papel"), nullable=False)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=agora
    )

    # Quando o dono liberou esta conta. `None` = nunca liberada, e é isso que
    # separa duas situações que o `ativo=False` sozinho confunde: a conta que
    # acabou de se cadastrar e espera, e a conta que o dono pausou de
    # propósito. Sem a distinção, a tela do dono mostra "pausado" nas duas e
    # ele acaba liberando com um toque justamente quem ele tinha barrado.
    #
    # Conta que nasce pelo seed já vem liberada: o dono existe antes do
    # sistema subir, e a conta de máquina do agente não passa por tela nenhuma.
    aprovado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sessoes: Mapped[list["SessaoAuth"]] = relationship(back_populates="usuario")

    def __repr__(self) -> str:
        return f"<Usuario {self.id} {self.nome} {self.papel.value}>"


class SessaoAuth(Base):
    """Refresh token de um aparelho pareado.

    O access token dura 30 min. Sem isto, o funcionário seria deslogado no meio
    do expediente com fila de cliente na frente. Guardado como hash pra que um
    vazamento do banco não vire acesso.
    """

    __tablename__ = "sessao_auth"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False)
    refresh_hash: Mapped[str] = mapped_column(String(120), nullable=False)
    dispositivo: Mapped[str | None] = mapped_column(String(120))
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=agora
    )
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revogado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    usado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    usuario: Mapped[Usuario] = relationship(back_populates="sessoes")

    __table_args__ = (Index("ix_sessao_auth_usuario_id", "usuario_id"),)

    @property
    def valida(self) -> bool:
        return self.revogado_em is None and como_utc(self.expira_em) > agora()
