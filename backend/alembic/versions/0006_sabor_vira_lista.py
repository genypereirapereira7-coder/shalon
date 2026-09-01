"""O sabor vira lista, e o extra volta a ser escolha

Revision ID: 0006
Revises: 0005

A 0005 tratou o chocolate do milk-shake como receita fechada: o balcão não
perguntava nada. Não é isso — o chocolate é uma terceira opção ao lado dos dois
sabores do dia, e o cliente pode levar um ou misturar dois.

Isso derruba o `MISTO`. Ele era um valor único que significava "os dois
anteriores", e com três opções na lista ele não sabe mais dizer *quais* dois.
No lugar entra uma lista curta de códigos ("SABOR_1,EXTRA"), e o enum de banco
sai junto — enum guarda um valor, e agora são até dois.

Nada do que já foi vendido se perde: o texto impresso (`sabor_snapshot`) nunca
esteve nesta coluna, e os códigos antigos são traduzidos abaixo.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ESCOLHA_SABOR = sa.Enum("SABOR_1", "SABOR_2", "MISTO", name="escolha_sabor")


def upgrade() -> None:
    # "fixo" era a leitura errada do que o chocolate é.
    op.alter_column("produto", "sabor_fixo", new_column_name="sabor_extra")

    op.add_column("pedido_item", sa.Column("sabor_tipos", sa.String(length=40), nullable=True))

    # `MISTO` vira o par que ele sempre quis dizer. `::text` porque a coluna
    # antiga é enum, e comparar enum com literal exige o cast.
    op.execute(
        """
        UPDATE pedido_item
           SET sabor_tipos = CASE sabor_tipo::text
                                WHEN 'MISTO' THEN 'SABOR_1,SABOR_2'
                                ELSE sabor_tipo::text
                             END
         WHERE sabor_tipo IS NOT NULL
        """
    )

    op.drop_column("pedido_item", "sabor_tipo")
    # Só depois de a coluna sair: o tipo não pode ser removido enquanto algo o
    # usa. `checkfirst` deixa a migration repetível se ela morrer no meio.
    ESCOLHA_SABOR.drop(op.get_bind(), checkfirst=True)

    # A 0005 desligou o `pede_sabor` do milk-shake por achar que o sabor era
    # fechado. Religar aqui é o que faz a opção aparecer no balcão — sem isto o
    # chocolate existiria no banco e ninguém o veria na tela.
    op.execute("UPDATE produto SET pede_sabor = true WHERE sabor_extra IS NOT NULL")


def downgrade() -> None:
    ESCOLHA_SABOR.create(op.get_bind(), checkfirst=True)
    op.add_column("pedido_item", sa.Column("sabor_tipo", ESCOLHA_SABOR, nullable=True))

    # Volta só o que cabe num valor único; o resto fica nulo. O texto impresso
    # continua intacto no `sabor_snapshot`, que é o que a comanda usa.
    op.execute(
        """
        UPDATE pedido_item
           SET sabor_tipo = CASE sabor_tipos
                               WHEN 'SABOR_1,SABOR_2' THEN 'MISTO'
                               WHEN 'SABOR_1' THEN 'SABOR_1'
                               WHEN 'SABOR_2' THEN 'SABOR_2'
                               ELSE NULL
                            END::escolha_sabor
         WHERE sabor_tipos IS NOT NULL
        """
    )

    op.drop_column("pedido_item", "sabor_tipos")
    op.alter_column("produto", "sabor_extra", new_column_name="sabor_fixo")
