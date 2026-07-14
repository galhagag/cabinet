"""add messages.tool_invocations

Revision ID: d581a3c9f0e2
Revises: c2e91f4a7b6d
Create Date: 2026-07-14 09:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd581a3c9f0e2'
down_revision: Union[str, Sequence[str], None] = 'c2e91f4a7b6d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('messages', sa.Column('tool_invocations', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('messages', 'tool_invocations')
