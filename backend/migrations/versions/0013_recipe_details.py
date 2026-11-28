"""Recipe details for menu and prep items: prep + cook time, status, allergen and storage notes.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("prep_minutes", sa.Integer))
    op.add_column("items", sa.Column("recipe_status", sa.String(16)))
    op.add_column("items", sa.Column("allergen_notes", sa.Text))
    op.add_column("items", sa.Column("storage_notes", sa.Text))


def downgrade() -> None:
    for c in ("storage_notes", "allergen_notes", "recipe_status", "prep_minutes"):
        op.drop_column("items", c)
