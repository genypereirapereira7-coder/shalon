"""Cadastro de funcionário e administração de contas pelo dono."""

from pydantic import BaseModel, Field, field_validator

from app.schemas.tipos import Utc


class CadastroEntrada(BaseModel):
    """Conta nova, criada pelo próprio funcionário na tela de vendas.

    A senha é só dígitos e de tamanho fixo — é PIN, não senha: quem digita está
    de pé, no balcão, e um teclado numérico é o que cabe na tela pequena.
    """

    nome: str = Field(min_length=1, max_length=80)
    senha: str = Field(min_length=6, max_length=6)
    dispositivo: str | None = Field(default=None, max_length=120)

    @field_validator("senha")
    @classmethod
    def _so_numeros(cls, valor: str) -> str:
        if not valor.isdigit():
            raise ValueError("A senha deve ter só números")
        return valor


class FuncionarioResumo(BaseModel):
    """Uma linha na lista de funcionários que o dono gerencia."""

    id: int
    nome: str
    ativo: bool
    criado_em: Utc

    model_config = {"from_attributes": True}


class AtivoEntrada(BaseModel):
    ativo: bool
