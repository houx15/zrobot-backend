"""add image_id to homework_correction_history

Revision ID: 20260123_add_image_id
Revises: 
Create Date: 2026-01-23
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260123_add_image_id"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "homework_correction_history",
        sa.Column("image_id", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("homework_correction_history", "image_id")
