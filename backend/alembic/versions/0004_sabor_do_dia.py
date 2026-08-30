"""Sabor do dia

Revision ID: 0004
Revises: 0003

Três coisas: a linha única com os sabores que a máquina está servindo, o
interruptor por produto que diz quem pergunta o sabor, e o par de campos que
congela a escolha em cada item vendido.

Tudo anulável e com padrão seguro: a loja que já está no ar continua vendendo
exatamente como antes até o dono marcar o primeiro produto.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Criado à mão e não pelo `add_column`: no Postgres o tipo enum é um objeto do
# banco, e `add_column` com `sa.Enum` nem sempre o cria antes de usá-lo — a
# migration falha com "type does not exist" no meio do deploy. `checkfirst`
# deixa a migration repetível se ela morrer entre as duas operações.
ESCOLHA_SABOR = sa.Enum("SABOR_1", "SABOR_2", "MISTO", name="escolha_sabor")


def upgrade() -> None:
    op.create_table(
        "sabor_do_dia",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sabor1", sa.String(length=60), nullable=True),
        sa.Column("sabor2", sa.String(length=60), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuario.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # `server_default` e não só `default`: o default do SQLAlchemy só vale pra
    # linha que o Python cria. Sem o default do banco, esta coluna NOT NULL não
    # tem o que gravar nas linhas de produto que já existem, e a migration
    # falha no meio do deploy com a loja aberta.
    op.add_column(
        "produto",
        sa.Column(
            "pede_sabor",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    ESCOLHA_SABOR.create(op.get_bind(), checkfirst=True)
    op.add_column("pedido_item", sa.Column("sabor_tipo", ESCOLHA_SABOR, nullable=True))
    op.add_column(
        "pedido_item", sa.Column("sabor_snapshot", sa.String(length=130), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("pedido_item", "sabor_snapshot")
    op.drop_column("pedido_item", "sabor_tipo")
    ESCOLHA_SABOR.drop(op.get_bind(), checkfirst=True)
    op.drop_column("produto", "pede_sabor")
    op.drop_table("sabor_do_dia")
