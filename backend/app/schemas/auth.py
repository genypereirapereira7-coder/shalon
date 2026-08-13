from pydantic import BaseModel, Field

from app.models.usuario import Papel


class LoginEntrada(BaseModel):
    usuario_id: int
    segredo: str = Field(min_length=4, max_length=72)  # bcrypt trunca em 72 bytes
    dispositivo: str | None = Field(default=None, max_length=120)


class RenovarEntrada(BaseModel):
    refresh: str


class TokensSaida(BaseModel):
    acesso: str
    refresh: str
    expira_em: int  # segundos de validade do token de acesso
    usuario_id: int
    nome: str
    papel: Papel


class UsuarioPublico(BaseModel):
    """O que a tela de login mostra antes de alguém digitar o PIN."""

    id: int
    nome: str
    papel: Papel

    model_config = {"from_attributes": True}
