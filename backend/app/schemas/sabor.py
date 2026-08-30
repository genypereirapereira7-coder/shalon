"""Os sabores que a loja está servindo hoje."""

from pydantic import BaseModel, Field, field_validator

from app.schemas.tipos import Utc


class SaborDoDiaEntrada(BaseModel):
    """O que o dono digita na tela dele.

    Vazio é resposta válida: apagar os dois campos é como a loja diz "hoje não
    tenho sabor definido", e a tela de vendas volta a não perguntar nada.
    """

    sabor1: str | None = Field(default=None, max_length=60)
    sabor2: str | None = Field(default=None, max_length=60)

    @field_validator("sabor1", "sabor2")
    @classmethod
    def _limpar(cls, valor: str | None) -> str | None:
        """Espaço nas pontas some, e campo em branco vira nulo.

        Quem digita está com pressa antes de abrir a loja. Um `"Chocolate "`
        com espaço sobrando viraria um sabor diferente de `"Chocolate"` na
        comanda impressa, e ninguém entenderia por quê.
        """
        if valor is None:
            return None
        limpo = valor.strip()
        return limpo or None


class SaborDoDiaSaida(BaseModel):
    sabor1: str | None
    sabor2: str | None
    atualizado_em: Utc | None = None

    @property
    def definido(self) -> bool:
        return bool(self.sabor1 or self.sabor2)
