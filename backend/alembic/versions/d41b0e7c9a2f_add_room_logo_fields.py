"""add room logo fields

Revision ID: d41b0e7c9a2f
Revises: b7d4e1a9c3f2
Create Date: 2026-07-15 14:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd41b0e7c9a2f'
down_revision: Union[str, Sequence[str], None] = 'b7d4e1a9c3f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('rooms') as batch_op:
        batch_op.add_column(
            sa.Column('logo_blob_path', sa.String(length=1024), nullable=True),
        )
        batch_op.add_column(
            sa.Column(
                'logo_source',
                sa.String(length=16),
                nullable=False,
                server_default='none',
            ),
        )
        batch_op.create_check_constraint(
            'ck_rooms_logo_source',
            "logo_source IN ('pending', 'auto', 'custom', 'none')",
        )


def downgrade() -> None:
    with op.batch_alter_table('rooms') as batch_op:
        batch_op.drop_constraint('ck_rooms_logo_source', type_='check')
        batch_op.drop_column('logo_source')
        batch_op.drop_column('logo_blob_path')