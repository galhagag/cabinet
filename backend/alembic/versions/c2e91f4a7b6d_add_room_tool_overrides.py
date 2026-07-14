"""add room_tool_overrides

Revision ID: c2e91f4a7b6d
Revises: b7d4e1a9c3f2
Create Date: 2026-07-14 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c2e91f4a7b6d'
down_revision: Union[str, Sequence[str], None] = 'b7d4e1a9c3f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'room_tool_overrides',
        sa.Column('room_id', sa.String(length=36), nullable=False),
        sa.Column('tool_name', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('room_id', 'tool_name'),
    )


def downgrade() -> None:
    op.drop_table('room_tool_overrides')
