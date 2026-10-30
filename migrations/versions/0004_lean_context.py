"""Per-item lean context: work_items.lean_context

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-30 16:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('work_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('lean_context', sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('work_items', schema=None) as batch_op:
        batch_op.drop_column('lean_context')
