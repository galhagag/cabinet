"""add message edit chain

Revision ID: 8c4f8c3a91de
Revises: b7d4e1a9c3f2
Create Date: 2026-07-14 11:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8c4f8c3a91de'
down_revision: Union[str, Sequence[str], None] = 'b7d4e1a9c3f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('messages') as batch_op:
        batch_op.add_column(sa.Column('edit_of_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('superseded_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_foreign_key(
            'fk_messages_edit_of_id_messages',
            'messages',
            ['edit_of_id'],
            ['id'],
            ondelete='SET NULL',
        )


def downgrade() -> None:
    with op.batch_alter_table('messages') as batch_op:
        batch_op.drop_constraint('fk_messages_edit_of_id_messages', type_='foreignkey')
        batch_op.drop_column('superseded_at')
        batch_op.drop_column('edit_of_id')