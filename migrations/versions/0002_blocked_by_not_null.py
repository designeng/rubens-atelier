"""work_items.blocked_by NOT NULL, as in the model

The pre-Alembic startup added this column to existing tables as nullable JSON, so those
databases hold NULL in old rows; databases created later already have NOT NULL.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-30 10:10:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("UPDATE work_items SET blocked_by = '[]' WHERE blocked_by IS NULL")
    with op.batch_alter_table('work_items', schema=None) as batch_op:
        batch_op.alter_column('blocked_by', existing_type=sa.JSON(), nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('work_items', schema=None) as batch_op:
        batch_op.alter_column('blocked_by', existing_type=sa.JSON(), nullable=True)
