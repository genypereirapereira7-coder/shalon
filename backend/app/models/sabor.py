"""Os dois sabores que a loja está servindo hoje.

Uma sorveteria de bola não vende "sorvete de chocolate" como item de cardápio:
ela vende uma casquinha, e o que vai dentro são os dois sabores que estão na
máquina naquele dia. O cardápio é estável; o sabor muda de manhã.

Por isso isto **não** é um grupo de opções como os acompanhamentos do açaí.
Aqueles são catálogo — "Granola" existe o ano inteiro e o dono edita raramente.
Estes são estado do dia: uma linha só, dois campos, trocados na tela do dono
antes de abrir. Modelar como opção obrigaria a criar e apagar registros de
catálogo todo dia, e a comanda de ontem passaria a mentir quando o sabor de
hoje entrasse no lugar.

Quem escolhe entre eles é o funcionário, item a item, e só nos produtos que o
dono marcou com `pede_sabor`.
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, agora


class EscolhaSabor(str, enum.Enum):
    """O que o cliente pediu. `MISTO` é os dois na mesma casquinha."""

    SABOR_1 = "SABOR_1"
    SABOR_2 = "SABOR_2"
    MISTO = "MISTO"


class SaborDoDia(Base):
    """Linha única (`id=1`): o que está na máquina agora.

    Uma linha e não um histórico porque a pergunta que o sistema faz é sempre
    "o que estou servindo *agora*". O que foi vendido ontem já está congelado
    no `sabor_snapshot` de cada item — é lá que mora o histórico, e ele é o
    único que a comanda e o relatório precisam.

    Os dois campos são anuláveis: loja que ainda não escolheu o sabor do dia
    não pode ficar impedida de vender. A tela simplesmente não pergunta.
    """

    __tablename__ = "sabor_do_dia"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    sabor1: Mapped[str | None] = mapped_column(String(60))
    sabor2: Mapped[str | None] = mapped_column(String(60))

    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=agora, onupdate=agora
    )
    # Quem trocou. O dono é um só hoje, mas o dia em que dois aparelhos
    # editarem isso, saber de qual veio a troca vale o campo.
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"))

    def __repr__(self) -> str:
        return f"<SaborDoDia {self.sabor1!r} / {self.sabor2!r}>"
