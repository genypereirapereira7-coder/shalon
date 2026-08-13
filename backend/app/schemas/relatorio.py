"""Schemas dos relatórios do dono.

Todo valor continua inteiro em centavos. O ticket médio é a única divisão do
sistema, e sai arredondada aqui — deixar o celular dividir daria um centavo de
diferença entre a tela do dono e o fechamento.
"""

from datetime import date

from pydantic import BaseModel, Field

from app.schemas.tipos import Utc


class ItemVendido(BaseModel):
    """Uma linha do ranking: quanto saiu daquele produto e quanto rendeu."""

    produto_id: int
    nome: str
    quantidade: int
    total_centavos: int


class VendaPorAtendente(BaseModel):
    usuario_id: int
    nome: str
    qtd_pedidos: int
    total_centavos: int


class FechamentoSaida(BaseModel):
    data_operacional: date
    total_centavos: int
    qtd_pedidos: int
    fechado_em: Utc
    fechado_por: int
    fechado_por_nome: str

    model_config = {"from_attributes": True}


class ResumoDia(BaseModel):
    """O dia inteiro numa resposta só — é o que a tela "Hoje" recarrega.

    Vai tudo junto de propósito: o celular do dono recarrega isto a cada 15s e
    três chamadas separadas dariam três chances de a tela ficar meio atualizada,
    mostrando o total novo com o ranking velho.
    """

    data_operacional: date

    # Cancelado fica de fora destes três: o dinheiro não entrou.
    total_centavos: int
    qtd_pedidos: int
    ticket_medio_centavos: int

    itens: list[ItemVendido]
    por_atendente: list[VendaPorAtendente]

    # Contados à parte porque não são venda, mas o dono precisa ver.
    cancelados_qtd: int
    cancelados_centavos: int

    # Vendas que subiram da fila offline depois de o caixa fechar. Ficam fora
    # do fechamento imutável, então aparecem separadas ou o caixa não bate.
    pos_fechamento_qtd: int
    pos_fechamento_centavos: int

    # Nulo enquanto o caixa do dia não foi fechado.
    fechamento: FechamentoSaida | None

    # Hora do servidor, não do celular: é ela que a tela mostra em "atualizado
    # às 19:42" quando a conexão cai e os números congelam.
    apurado_em: Utc


class FecharEntrada(BaseModel):
    """Corpo do POST /fechamento. Sem data = fecha o dia operacional corrente."""

    data_operacional: date | None = None
    # O dono confere o total na tela antes de fechar; mandando o valor de volta,
    # o servidor recusa se o número mudou entre a conferência e o toque no botão
    # (uma venda entrou nesse meio). Opcional: sem ele, fecha pelo que houver.
    total_conferido_centavos: int | None = Field(default=None, ge=0)
