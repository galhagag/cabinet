"""add room_skill_overrides

Revision ID: b7d4e1a9c3f2
Revises: f3a8b2c9d1e4
Create Date: 2026-07-13 12:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7d4e1a9c3f2'
down_revision: Union[str, Sequence[str], None] = 'f3a8b2c9d1e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'room_skill_overrides',
        sa.Column('room_id', sa.String(length=36), nullable=False),
        sa.Column('skill_id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['skill_id'], ['agent_skills.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('room_id', 'skill_id'),
    )


def downgrade() -> None:
    op.drop_table('room_skill_overrides')
