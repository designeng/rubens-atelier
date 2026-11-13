"""Whether the PR conflicts with its base, for the board: work_items.pr_conflicts

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-13 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0009'
down_revision: Union[str, Sequence[str], None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('work_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('pr_conflicts', sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('work_items', schema=None) as batch_op:
        batch_op.drop_column('pr_conflicts')
