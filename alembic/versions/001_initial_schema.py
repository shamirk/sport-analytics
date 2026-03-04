"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-03-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_number", sa.String(length=20), nullable=False),
        sa.Column("last_scraped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_members_member_number"), "members", ["member_number"], unique=True)

    op.create_table(
        "divisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("abbreviation", sa.String(length=10), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("abbreviation"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "classifier_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("division_id", sa.Integer(), nullable=False),
        sa.Column("classifier_number", sa.String(length=10), nullable=False),
        sa.Column("classifier_name", sa.String(length=200), nullable=True),
        sa.Column("match_name", sa.String(length=200), nullable=True),
        sa.Column("match_date", sa.Date(), nullable=True),
        sa.Column("hit_factor", sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column("points", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("percentage", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("classification_at_time", sa.String(length=2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["division_id"], ["divisions.id"]),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_classifier_results_member_id", "classifier_results", ["member_id"])
    op.create_index("ix_classifier_results_match_date", "classifier_results", ["match_date"])

    op.create_table(
        "current_classifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("division_id", sa.Integer(), nullable=False),
        sa.Column("classification_class", sa.String(length=2), nullable=False),
        sa.Column("percentage", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["division_id"], ["divisions.id"]),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_current_classifications_member_id", "current_classifications", ["member_id"])

    op.create_table(
        "match_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("division_id", sa.Integer(), nullable=False),
        sa.Column("match_name", sa.String(length=200), nullable=False),
        sa.Column("match_date", sa.Date(), nullable=True),
        sa.Column("match_level", sa.SmallInteger(), nullable=True),
        sa.Column("placement", sa.Integer(), nullable=True),
        sa.Column("total_competitors", sa.Integer(), nullable=True),
        sa.Column("percent_finish", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["division_id"], ["divisions.id"]),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_match_results_member_id", "match_results", ["member_id"])
    op.create_index("ix_match_results_match_date", "match_results", ["match_date"])


def downgrade() -> None:
    op.drop_table("match_results")
    op.drop_table("current_classifications")
    op.drop_table("classifier_results")
    op.drop_table("divisions")
    op.drop_index(op.f("ix_members_member_number"), table_name="members")
    op.drop_table("members")
