"""Fechamento automático

Revision ID: 0007
Revises: 0006

Uma coluna só: dizer se o caixa daquele dia foi fechado por alguém ou sozinho,
na hora marcada. O histórico do dono mostra o nome de quem fechou, e sem isto um
fechamento automático apareceria assinado por uma pessoa que estava dormindo.

`server_default` e não só `default`: o padrão do SQLAlchemy só vale pra linha
inserida pelo Python, e as linhas que já existem no banco da loja precisam nascer
com `false` — todas elas foram fechadas à mão.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "fechamento_dia",
        sa.Column("automatico", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("fechamento_dia", "automatico")
