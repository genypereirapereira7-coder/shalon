"""Esquema inicial

Revision ID: 0001
Revises:
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    papel = sa.Enum("DONO", "FUNCIONARIO", "COZINHA", "AGENTE", name="papel")
    status_pedido = sa.Enum(
        "RECEBIDO", "EM_PREPARO", "PRONTO", "ENTREGUE", "CANCELADO", name="status_pedido"
    )

    op.create_table(
        "usuario",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(length=80), nullable=False),
        sa.Column("pin_hash", sa.String(length=120), nullable=False),
        sa.Column("papel", papel, nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usuario")),
    )

    op.create_table(
        "sessao_auth",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=False),
        sa.Column("refresh_hash", sa.String(length=120), nullable=False),
        sa.Column("dispositivo", sa.String(length=120), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revogado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("usado_em", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["usuario_id"], ["usuario.id"], name=op.f("fk_sessao_auth_usuario_id_usuario")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessao_auth")),
    )
    op.create_index("ix_sessao_auth_usuario_id", "sessao_auth", ["usuario_id"])
    # A busca do /auth/renovar é por hash: sem índice ela varre a tabela inteira.
    op.create_index(
        "ix_sessao_auth_refresh_hash", "sessao_auth", ["refresh_hash"], unique=True
    )

    op.create_table(
        "categoria",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(length=60), nullable=False),
        sa.Column("ordem", sa.Integer(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categoria")),
    )

    op.create_table(
        "produto",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("categoria_id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(length=80), nullable=False),
        sa.Column("preco_centavos", sa.Integer(), nullable=False),
        sa.Column("cor_botao", sa.String(length=9), nullable=True),
        sa.Column("ordem", sa.Integer(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("preco_centavos >= 0", name=op.f("ck_produto_preco_nao_negativo")),
        sa.ForeignKeyConstraint(
            ["categoria_id"], ["categoria.id"], name=op.f("fk_produto_categoria_id_categoria")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_produto")),
    )

    op.create_table(
        "preco_historico",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("produto_id", sa.Integer(), nullable=False),
        sa.Column("preco_antigo", sa.Integer(), nullable=False),
        sa.Column("preco_novo", sa.Integer(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["produto_id"], ["produto.id"], name=op.f("fk_preco_historico_produto_id_produto")
        ),
        sa.ForeignKeyConstraint(
            ["usuario_id"], ["usuario.id"], name=op.f("fk_preco_historico_usuario_id_usuario")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_preco_historico")),
    )
    op.create_index("ix_preco_historico_produto_id", "preco_historico", ["produto_id"])
    op.create_index("ix_preco_historico_criado_em", "preco_historico", ["criado_em"])

    op.create_table(
        "contador_dia",
        sa.Column("data_operacional", sa.Date(), nullable=False),
        sa.Column("ultimo_numero", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("data_operacional", name=op.f("pk_contador_dia")),
    )

    op.create_table(
        "pedido",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("id_cliente", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("numero_dia", sa.Integer(), nullable=False),
        sa.Column("data_operacional", sa.Date(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=False),
        sa.Column("status", status_pedido, nullable=False),
        sa.Column("total_centavos", sa.Integer(), nullable=False),
        sa.Column("observacao", sa.String(length=280), nullable=True),
        sa.Column("criado_em_cliente", sa.DateTime(timezone=True), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pos_fechamento", sa.Boolean(), nullable=False),
        sa.Column("impresso_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelado_por", sa.Integer(), nullable=True),
        sa.Column("motivo_cancelamento", sa.String(length=280), nullable=True),
        sa.CheckConstraint("total_centavos >= 0", name=op.f("ck_pedido_total_nao_negativo")),
        sa.ForeignKeyConstraint(
            ["usuario_id"], ["usuario.id"], name=op.f("fk_pedido_usuario_id_usuario")
        ),
        sa.ForeignKeyConstraint(
            ["cancelado_por"], ["usuario.id"], name=op.f("fk_pedido_cancelado_por_usuario")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pedido")),
        # Idempotência do envio duplicado vindo do celular.
        sa.UniqueConstraint("id_cliente", name="uq_pedido_id_cliente"),
        # Impede dois "Pedido #37" no mesmo dia, mesmo se o contador falhar.
        sa.UniqueConstraint("data_operacional", "numero_dia", name="uq_pedido_numero_dia"),
    )
    op.create_index("ix_pedido_data_operacional", "pedido", ["data_operacional"])
    op.create_index("ix_pedido_dia_status", "pedido", ["data_operacional", "status"])

    op.create_table(
        "pedido_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pedido_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("produto_id", sa.Integer(), nullable=False),
        sa.Column("nome_snapshot", sa.String(length=80), nullable=False),
        sa.Column("preco_unit_centavos_snapshot", sa.Integer(), nullable=False),
        sa.Column("quantidade", sa.Integer(), nullable=False),
        sa.Column("subtotal_centavos", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantidade > 0", name=op.f("ck_pedido_item_quantidade_positiva")),
        sa.ForeignKeyConstraint(
            ["pedido_id"],
            ["pedido.id"],
            name=op.f("fk_pedido_item_pedido_id_pedido"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["produto_id"], ["produto.id"], name=op.f("fk_pedido_item_produto_id_produto")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pedido_item")),
    )
    op.create_index("ix_pedido_item_pedido_id", "pedido_item", ["pedido_id"])

    op.create_table(
        "fechamento_dia",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("data_operacional", sa.Date(), nullable=False),
        sa.Column("total_centavos", sa.Integer(), nullable=False),
        sa.Column("qtd_pedidos", sa.Integer(), nullable=False),
        sa.Column("fechado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fechado_por", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["fechado_por"], ["usuario.id"], name=op.f("fk_fechamento_dia_fechado_por_usuario")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fechamento_dia")),
        sa.UniqueConstraint("data_operacional", name=op.f("uq_fechamento_dia_data_operacional")),
    )


def downgrade() -> None:
    op.drop_table("fechamento_dia")
    op.drop_table("pedido_item")
    op.drop_table("pedido")
    op.drop_table("contador_dia")
    op.drop_table("preco_historico")
    op.drop_table("produto")
    op.drop_table("categoria")
    op.drop_table("sessao_auth")
    op.drop_table("usuario")
    sa.Enum(name="status_pedido").drop(op.get_bind())
    sa.Enum(name="papel").drop(op.get_bind())
