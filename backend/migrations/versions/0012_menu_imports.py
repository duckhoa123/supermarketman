"""Menu imports: a menu uploaded to set up recipes, with the AI's suggestions under review.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "menu_imports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("location_id", sa.String(36), sa.ForeignKey("locations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("uploaded_by", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("file_name", sa.String(300)),
        sa.Column("content_type", sa.String(100)),
        sa.Column("file_bytes", sa.LargeBinary),
        sa.Column("dishes", sa.JSON, nullable=False),
        sa.Column("ingredients", sa.JSON, nullable=False),
        sa.Column("progress", sa.JSON),
        sa.Column("warnings", sa.JSON, nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("result", sa.JSON),
        sa.Column("confirmed_by", sa.String(64)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_menu_imports_location_id", "menu_imports", ["location_id"])


def downgrade() -> None:
    op.drop_index("ix_menu_imports_location_id", table_name="menu_imports")
    op.drop_table("menu_imports")
