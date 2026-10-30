"""Spending limits: projects.budget_usd and work_items.budget_usd

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-30 11:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.add_column(sa.Column('budget_usd', sa.Double(), nullable=True))
    with op.batch_alter_table('work_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('budget_usd', sa.Double(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('work_items', schema=None) as batch_op:
        batch_op.drop_column('budget_usd')
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_column('budget_usd')
