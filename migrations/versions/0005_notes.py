"""Project memory: notes

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-03 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('notes',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('project_id', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('tags', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_by', sa.String(length=100), nullable=False),
    sa.Column('source_item_id', sa.String(length=32), nullable=True),
    sa.Column('origin_note_id', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.ForeignKeyConstraint(['source_item_id'], ['work_items.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('notes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notes_project_id'), ['project_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notes_status'), ['status'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('notes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notes_status'))
        batch_op.drop_index(batch_op.f('ix_notes_project_id'))
    op.drop_table('notes')
