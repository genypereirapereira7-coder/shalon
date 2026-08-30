"""Aprovação de conta pelo dono

Revision ID: 0003
Revises: 0002

O cadastro na tela de vendas deixa de entrar logado: a conta nasce inativa e
só vale depois que o dono libera. Esta coluna é o que separa "esperando
liberação" de "pausado pelo dono" — sem ela a tela mostra "pausado" nas duas
situações, e liberar quem foi barrado de propósito vira um toque de distância.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "usuario",
        sa.Column("aprovado_em", sa.DateTime(timezone=True), nullable=True),
    )

    # Quem já estava ativo antes desta migration foi aprovado de fato — no
    # mundo anterior, criar a conta era a aprovação. Sem este preenchimento,
    # todo funcionário que já trabalha na loja apareceria como "esperando
    # liberação" no dia do deploy, e o dono teria que reaprovar a equipe
    # inteira pra que ninguém ficasse de fora do expediente.
    op.execute(
        "UPDATE usuario SET aprovado_em = criado_em WHERE ativo = true"
    )


def downgrade() -> None:
    op.drop_column("usuario", "aprovado_em")
