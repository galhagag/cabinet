"""add room_agents.instructions

Revision ID: f3a8b2c9d1e4
Revises: cca35f727fa3
Create Date: 2026-07-13 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3a8b2c9d1e4'
down_revision: Union[str, Sequence[str], None] = 'cca35f727fa3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'room_agents',
        sa.Column('instructions', sa.Text(), nullable=False, server_default=''),
    )


def downgrade() -> None:
    op.drop_column('room_agents', 'instructions')
