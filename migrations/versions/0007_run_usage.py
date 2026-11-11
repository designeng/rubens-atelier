"""Record each run's token usage, last context size and failed tool calls

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-11 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    'input_tokens', 'cache_read_tokens', 'cache_write_tokens', 'output_tokens', 'context_tokens', 'failed_tools'
)


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('runs', schema=None) as batch_op:
        for name in COLUMNS:
            batch_op.add_column(sa.Column(name, sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('runs', schema=None) as batch_op:
        for name in reversed(COLUMNS):
            batch_op.drop_column(name)
