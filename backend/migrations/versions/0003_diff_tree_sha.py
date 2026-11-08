"""diff tree sha

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The exact tree each diff shows, so approval commits that tree and nothing written after it
    op.add_column('diffs', sa.Column('tree_sha', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('diffs', 'tree_sha')
