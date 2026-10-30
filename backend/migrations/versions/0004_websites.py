"""Restaurant websites: draft and published content, version history, uploaded images.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-30
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "websites",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("location_id", sa.String(36), sa.ForeignKey("locations.id", ondelete="CASCADE"), nullable=False,
                  unique=True),
        sa.Column("slug", sa.String(60), nullable=False, unique=True),
        sa.Column("custom_domain", sa.String(253), unique=True),
        sa.Column("draft", sa.JSON, nullable=False), sa.Column("published", sa.JSON),
        ts("published_at"), sa.Column("published_by", sa.String(64)), ts("created_at"), ts("updated_at"),
    )
    op.create_table(
        "website_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("website_id", sa.String(36), sa.ForeignKey("websites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content", sa.JSON, nullable=False), sa.Column("published_by", sa.String(64)), ts("created_at"),
    )
    op.create_index("ix_website_versions_site_created", "website_versions", ["website_id", "created_at"])
    op.create_table(
        "site_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("location_id", sa.String(36), sa.ForeignKey("locations.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("content_type", sa.String(50), nullable=False), sa.Column("data", sa.LargeBinary, nullable=False),
        sa.Column("size", sa.Integer, nullable=False), ts("created_at"),
    )


def downgrade() -> None:
    op.drop_table("site_assets")
    op.drop_table("website_versions")
    op.drop_table("websites")
