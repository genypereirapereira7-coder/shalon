"""Entrada e saída de pedido.

O celular manda `id_cliente` e `criado_em_cliente` — os dois vêm de lá porque
o pedido pode ter nascido offline e subido bem depois. O total também vem, mas
só pra conferência: quem manda no valor é o servidor (ver `servicos/pedidos`).
"""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.pedido import StatusPedido
from app.models.sabor import EscolhaSabor
from app.schemas.tipos import Utc


class ItemEntrada(BaseModel):
    produto_id: int
    quantidade: int = Field(ge=1, le=99)
    # Ids das opções escolhidas (acompanhamentos e adicionais). O servidor
    # confere se cabem na cota do produto e recalcula o preço extra.
    opcoes: list[int] = Field(default_factory=list, max_length=30)

    # Qual sabor do dia, nos produtos que pedem. Só o tipo vem do celular; o
    # texto é resolvido no servidor, pelo sabor que valia no instante da venda.
    # Mandar o nome daqui deixaria o aparelho com cache velho gravar
    # "Chocolate" num dia em que a máquina já está com creme.
    sabor: EscolhaSabor | None = None


class PedidoEntrada(BaseModel):
    # Gerado no celular. Reenviar o mesmo uuid não cria um segundo pedido.
    id_cliente: uuid.UUID
    criado_em_cliente: datetime
    itens: list[ItemEntrada] = Field(min_length=1, max_length=60)
    observacao: str | None = Field(default=None, max_length=280)
    # Opcional: o que o celular calculou. Serve só pra detectar divergência
    # (cardápio desatualizado em cache) — nunca é gravado.
    total_centavos: int | None = Field(default=None, ge=0)


class OpcaoEscolhida(BaseModel):
    """O que o cliente escolheu, como estava na hora da venda."""

    opcao_id: int | None
    nome: str
    # "Cobertura", "Acompanhamentos do açaí". É o título do bloco na comanda —
    # sem ele, "Chocolate" pode ser a cobertura ou o sabor da bola.
    grupo: str | None = None
    preco_extra_centavos: int


class ItemSaida(BaseModel):
    produto_id: int
    nome: str
    preco_unit_centavos: int
    quantidade: int
    # (preço do produto + extras) × quantidade
    subtotal_centavos: int
    opcoes: list[OpcaoEscolhida] = []

    # Como estava na hora da venda. `sabor` é o texto que a comanda imprime.
    sabor_tipo: EscolhaSabor | None = None
    sabor: str | None = None


class PedidoSaida(BaseModel):
    id: uuid.UUID
    id_cliente: uuid.UUID
    numero_dia: int
    data_operacional: date
    usuario_id: int
    usuario_nome: str
    status: StatusPedido
    total_centavos: int
    observacao: str | None
    # `Utc` e não `datetime`: a tela da cozinha calcula "há quantos minutos" e
    # o prazo de impressão em cima destes campos. Sem o fuso no JSON, a comanda
    # parece ter sido criada horas no futuro e o alerta nunca acende.
    criado_em: Utc
    criado_em_cliente: Utc
    impresso_em: Utc | None
    cancelado_em: Utc | None
    motivo_cancelamento: str | None
    pos_fechamento: bool
    itens: list[ItemSaida]

    # Reenvio idempotente: o pedido já existia, não foi criado agora. O PWA usa
    # isto pra saber que não deve alarmar o funcionário com "pedido duplicado".
    duplicado: bool = False
    # O total que o celular calculou não bateu com o do servidor. Quase sempre
    # é cardápio velho em cache; vale um aviso na tela do dono.
    total_divergente: bool = False


class StatusEntrada(BaseModel):
    status: StatusPedido


class CancelamentoEntrada(BaseModel):
    # Cancelar sem motivo é o mesmo que sumir com dinheiro sem explicação.
    motivo: str = Field(min_length=3, max_length=280)
