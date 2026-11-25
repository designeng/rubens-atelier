"""Drift guards: work_items.planned_files, review_sha, review_version; projects.scope_check, merge_without_ci

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-25 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0010'
down_revision: Union[str, Sequence[str], None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.add_column(sa.Column('scope_check', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column('merge_without_ci', sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table('work_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('planned_files', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('review_sha', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('review_version', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('work_items', schema=None) as batch_op:
        batch_op.drop_column('review_version')
        batch_op.drop_column('review_sha')
        batch_op.drop_column('planned_files')
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_column('merge_without_ci')
        batch_op.drop_column('scope_check')
