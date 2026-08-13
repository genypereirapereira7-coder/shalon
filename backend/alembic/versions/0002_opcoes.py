"""Acompanhamentos e adicionais

Revision ID: 0002
Revises: 0001
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "opcao_grupo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(length=60), nullable=False),
        sa.Column("ordem", sa.Integer(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_opcao_grupo")),
    )

    op.create_table(
        "opcao",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("grupo_id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(length=60), nullable=False),
        sa.Column("preco_extra_centavos", sa.Integer(), nullable=False),
        sa.Column("ordem", sa.Integer(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "preco_extra_centavos >= 0", name=op.f("ck_opcao_preco_extra_nao_negativo")
        ),
        sa.ForeignKeyConstraint(
            ["grupo_id"],
            ["opcao_grupo.id"],
            name=op.f("fk_opcao_grupo_id_opcao_grupo"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_opcao")),
    )
    op.create_index("ix_opcao_grupo_id", "opcao", ["grupo_id"])

    op.create_table(
        "produto_opcao_grupo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("produto_id", sa.Integer(), nullable=False),
        sa.Column("grupo_id", sa.Integer(), nullable=False),
        sa.Column("min_escolhas", sa.Integer(), nullable=False),
        sa.Column("max_escolhas", sa.Integer(), nullable=True),
        sa.Column("ordem", sa.Integer(), nullable=False),
        sa.CheckConstraint("min_escolhas >= 0", name=op.f("ck_produto_opcao_grupo_min_nao_negativo")),
        sa.CheckConstraint(
            "max_escolhas IS NULL OR max_escolhas >= min_escolhas",
            name=op.f("ck_produto_opcao_grupo_max_maior_que_min"),
        ),
        sa.ForeignKeyConstraint(
            ["produto_id"],
            ["produto.id"],
            name=op.f("fk_produto_opcao_grupo_produto_id_produto"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["grupo_id"],
            ["opcao_grupo.id"],
            name=op.f("fk_produto_opcao_grupo_grupo_id_opcao_grupo"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_produto_opcao_grupo")),
        sa.UniqueConstraint(
            "produto_id", "grupo_id", name=op.f("uq_produto_opcao_grupo_produto_id")
        ),
    )
    op.create_index("ix_produto_opcao_grupo_produto_id", "produto_opcao_grupo", ["produto_id"])

    op.create_table(
        "pedido_item_opcao",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pedido_item_id", sa.Integer(), nullable=False),
        # Nulável e sem cascade: apagar uma opção do cardápio não pode levar
        # junto o registro do que foi vendido ontem.
        sa.Column("opcao_id", sa.Integer(), nullable=True),
        sa.Column("nome_snapshot", sa.String(length=60), nullable=False),
        sa.Column("preco_extra_centavos_snapshot", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "preco_extra_centavos_snapshot >= 0",
            name=op.f("ck_pedido_item_opcao_preco_extra_snapshot_nao_negativo"),
        ),
        sa.ForeignKeyConstraint(
            ["pedido_item_id"],
            ["pedido_item.id"],
            name=op.f("fk_pedido_item_opcao_pedido_item_id_pedido_item"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["opcao_id"], ["opcao.id"], name=op.f("fk_pedido_item_opcao_opcao_id_opcao")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pedido_item_opcao")),
    )
    op.create_index(
        "ix_pedido_item_opcao_pedido_item_id", "pedido_item_opcao", ["pedido_item_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_pedido_item_opcao_pedido_item_id", table_name="pedido_item_opcao")
    op.drop_table("pedido_item_opcao")
    op.drop_index("ix_produto_opcao_grupo_produto_id", table_name="produto_opcao_grupo")
    op.drop_table("produto_opcao_grupo")
    op.drop_index("ix_opcao_grupo_id", table_name="opcao")
    op.drop_table("opcao")
    op.drop_table("opcao_grupo")
