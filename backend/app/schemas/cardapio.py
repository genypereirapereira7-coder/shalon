from pydantic import BaseModel, Field

from app.schemas.tipos import Utc


class OpcaoSaida(BaseModel):
    id: int
    nome: str
    # 0 = acompanhamento incluído; > 0 = adicional cobrado à parte.
    preco_extra_centavos: int

    model_config = {"from_attributes": True}


class GrupoSaida(BaseModel):
    """Um grupo de escolhas já com a cota deste produto.

    A cota vem do vínculo produto↔grupo, não do grupo: a mesma lista de
    acompanhamentos vale 3 escolhas no sundae e 4 no açaí montado.
    """

    id: int
    nome: str
    min_escolhas: int
    max_escolhas: int | None  # nulo = sem teto (adicionais pagos)
    opcoes: list[OpcaoSaida]


class ProdutoSaida(BaseModel):
    id: int
    categoria_id: int
    nome: str
    preco_centavos: int
    cor_botao: str | None
    ordem: int
    ativo: bool
    # A tela de vendas usa isto pra decidir se pergunta o sabor do dia.
    pede_sabor: bool = False
    # Um sabor a mais que este produto oferece, além dos dois do dia.
    sabor_extra: str | None = None
    grupos: list[GrupoSaida] = []

    model_config = {"from_attributes": True}


class CategoriaSaida(BaseModel):
    id: int
    nome: str
    ordem: int
    produtos: list[ProdutoSaida]

    model_config = {"from_attributes": True}


class CardapioSaida(BaseModel):
    """O PWA guarda isto em cache e continua vendendo sem internet.

    `versao` é o carimbo da última alteração: o celular compara com o que tem
    guardado pra saber se precisa baixar de novo.
    """

    # `Utc` e não `datetime`: os dois PWAs mostram isto como
    # "Cardápio de 12/08/2026 19:42" no painel. Sem o fuso no JSON o navegador
    # lê a hora UTC como local e o dono vê o cardápio datado três horas no
    # futuro. Ver `app/schemas/tipos.py`.
    versao: Utc | None
    categorias: list[CategoriaSaida]


class ProdutoEntrada(BaseModel):
    categoria_id: int
    nome: str = Field(min_length=1, max_length=80)
    preco_centavos: int = Field(ge=0)
    cor_botao: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    ordem: int = 0
    pede_sabor: bool = False


class ProdutoPatch(BaseModel):
    nome: str | None = Field(default=None, min_length=1, max_length=80)
    preco_centavos: int | None = Field(default=None, ge=0)
    cor_botao: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    ordem: int | None = None
    ativo: bool | None = None
    pede_sabor: bool | None = None


class CategoriaEntrada(BaseModel):
    nome: str = Field(min_length=1, max_length=60)
    ordem: int = 0
