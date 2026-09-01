"""Entrada e saída de pedido.

O celular manda `id_cliente` e `criado_em_cliente` — os dois vêm de lá porque
o pedido pode ter nascido offline e subido bem depois. O total também vem, mas
só pra conferência: quem manda no valor é o servidor (ver `servicos/pedidos`).
"""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator

from app.models.pedido import StatusPedido
from app.models.sabor import MAX_SABORES, EscolhaSabor
from app.schemas.tipos import Utc


class ItemEntrada(BaseModel):
    produto_id: int
    quantidade: int = Field(ge=1, le=99)
    # Ids das opções escolhidas (acompanhamentos e adicionais). O servidor
    # confere se cabem na cota do produto e recalcula o preço extra.
    opcoes: list[int] = Field(default_factory=list, max_length=30)

    # Quais sabores, nos produtos que pedem. Só os códigos vêm do celular; os
    # nomes são resolvidos no servidor, pelo que valia no instante da venda.
    # Mandar o nome daqui deixaria o aparelho com cache velho gravar
    # "Chocolate" num dia em que a máquina já está com creme.
    sabores: list[EscolhaSabor] = Field(default_factory=list, max_length=MAX_SABORES)

    # O formato antigo, de escolha única, com o `MISTO` que já não existe.
    #
    # Continua aceito porque o app é um PWA: durante um deploy há celulares no
    # balcão rodando a versão anterior do `app.js`, e alguns têm pedido na fila
    # offline montado com ela. Recusar esse formato não seria uma tela
    # desatualizada — seria uma venda já paga voltando com erro.
    # Sem `deprecated=True`: a marca do Pydantic dispara um aviso a cada
    # leitura do campo, e quem lê é o validador abaixo — o log de produção
    # ganharia uma linha de DeprecationWarning por pedido antigo que subir.
    # O aviso é pra quem escreve cliente novo, e está neste comentário.
    sabor: str | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def _aceitar_formato_antigo(self):
        if self.sabores or not self.sabor:
            return self
        antigo = self.sabor.upper()
        if antigo == "MISTO":
            self.sabores = [EscolhaSabor.SABOR_1, EscolhaSabor.SABOR_2]
        elif antigo in {e.value for e in EscolhaSabor}:
            self.sabores = [EscolhaSabor(antigo)]
        # Valor que não é nenhum dos conhecidos vira nenhum sabor, e não um
        # 422: a venda vale mais que a linha do sabor no papel.
        return self


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

    # Como estava na hora da venda. `sabor` é o texto que a comanda imprime
    # ("Morango + Chocolate"); `sabor_tipos` são os códigos que o produziram.
    sabor_tipos: str | None = None
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
