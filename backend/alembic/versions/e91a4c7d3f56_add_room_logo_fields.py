"""add rooms.logo_blob_path and rooms.logo_source

Revision ID: e91a4c7d3f56
Revises: b7d4e1a9c3f2
Create Date: 2026-07-14 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e91a4c7d3f56'
down_revision: Union[str, Sequence[str], None] = 'b7d4e1a9c3f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'rooms', sa.Column('logo_blob_path', sa.String(length=1024), nullable=True)
    )
    # server_default='none', NOT 'pending': this backfills pre-existing rooms
    # (which will never get a background fetch run for them) to the terminal
    # "no logo" state. New rooms always pass logo_source explicitly via the
    # ORM's Python-side default="pending" in models.py, so this server-side
    # default only ever applies to the one-time backfill here.
    op.add_column(
        'rooms',
        sa.Column(
            'logo_source', sa.String(length=16), nullable=False, server_default='none'
        ),
    )


def downgrade() -> None:
    op.drop_column('rooms', 'logo_source')
    op.drop_column('rooms', 'logo_blob_path')
