"""add room archived_at

Revision ID: a1c4e7f92b56
Revises: b7d4e1a9c3f2
Create Date: 2026-07-16 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1c4e7f92b56'
down_revision: Union[str, Sequence[str], None] = 'b7d4e1a9c3f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('rooms') as batch_op:
        batch_op.add_column(
            sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table('rooms') as batch_op:
        batch_op.drop_column('archived_at')
