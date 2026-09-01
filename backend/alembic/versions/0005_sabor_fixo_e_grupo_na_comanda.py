"""Sabor fixo do produto e o nome do grupo na comanda

Revision ID: 0005
Revises: 0004

Duas colunas que consertam a mesma queixa vinda do balcão: a comanda de um
produto com sabor *e* cobertura saía com dois nomes soltos embaixo, e quem
monta não sabia qual era qual.

- `produto.sabor_fixo` é a receita da casa (o milk-shake é de chocolate),
  não o sabor do dia. Não pergunta nada ao balcão e não tem tela de edição.
- `pedido_item_opcao.grupo_snapshot` é o que dá título a cada bloco do papel.

As duas são anuláveis: o que já está no banco continua exatamente como está.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("produto", sa.Column("sabor_fixo", sa.String(length=60), nullable=True))
    op.add_column(
        "pedido_item_opcao",
        sa.Column("grupo_snapshot", sa.String(length=60), nullable=True),
    )

    # O milk-shake da casa é de chocolate. Fica aqui, e não só no `seed.py`,
    # porque o banco de produção já existe: o seed sincroniza estrutura na
    # próxima subida, mas deixar a loja um deploy inteiro imprimindo comanda de
    # milk-shake sem sabor é justamente o que esta migration veio resolver.
    #
    # O "doce de café" fica de fora: sabor fechado, e chocolate não é o dele.
    # Lista explícita em vez de `LIKE 'Milk-shake%' AND NOT LIKE '%café%'`:
    # o padrão com acento depende do encoding com que o banco foi criado, e
    # aqui não há nada a ganhar arriscando isso — os nomes vêm do `seed.py` e
    # não há tela que os renomeie. Se um dia houver, o próprio seed corrige na
    # subida seguinte, porque ele sincroniza este campo.
    op.execute(
        """
        UPDATE produto
           SET sabor_fixo = 'Chocolate'
         WHERE nome IN ('Milk-shake 300ml', 'Milk-shake 500ml', 'Milk-shake 700ml')
           AND sabor_fixo IS NULL
        """
    )

    # Sabor fixo e pergunta de sabor não convivem: com os dois ligados o balcão
    # perguntaria algo que o servidor ignora. Quem marcou o 🍦 no milk-shake
    # antes desta migration tem a marca desfeita agora.
    op.execute("UPDATE produto SET pede_sabor = false WHERE sabor_fixo IS NOT NULL")


def downgrade() -> None:
    op.drop_column("pedido_item_opcao", "grupo_snapshot")
    op.drop_column("produto", "sabor_fixo")
