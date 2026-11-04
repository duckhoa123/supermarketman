"""Website SEO plan: whether a site is on the paid SEO plan, and its Stripe subscription.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("websites", sa.Column("seo_plan", sa.JSON))


def downgrade() -> None:
    op.drop_column("websites", "seo_plan")
