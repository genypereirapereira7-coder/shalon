import uuid

from pydantic import BaseModel, Field

from app.models.usuario import Papel
from app.schemas.tipos import Utc


class LoginEntrada(BaseModel):
    """Nome de usuário e senha — não um id escolhido numa lista.

    A tela de login não mostra mais quem existe. Antes ela listava os usuários
    pra tocar num, o que entregava a qualquer pessoa com o link os nomes de
    quem trabalha na loja e quem é o dono; e no celular do balcão o botão do
    dono ficava ali, convidando. Digitar o nome não vaza nada: quem não sabe o
    nome não tem o que tentar.
    """

    usuario: str = Field(min_length=1, max_length=80)
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
    """Quem está logado agora, pro `/auth/eu`."""

    id: int
    nome: str
    papel: Papel

    model_config = {"from_attributes": True}


class SessaoAtiva(BaseModel):
    """Um aparelho logado, na tela de acesso do dono.

    `Utc` e não `datetime` cru: sem o fuso no JSON o navegador lê a hora UTC
    como local e o "entrou às 14h" aparece três horas no futuro
    (`app/schemas/tipos.py`).
    """

    id: uuid.UUID
    usuario_id: int
    usuario_nome: str
    papel: Papel
    dispositivo: str | None
    criado_em: Utc
    expira_em: Utc

    # "Este é o seu login." O token de acesso carrega o usuário, não a sessão,
    # então dá pra dizer de quem é o aparelho, mas não qual deles é este. A
    # tela usa isto pra avisar antes de o dono derrubar a si mesmo.
    meu_usuario: bool
