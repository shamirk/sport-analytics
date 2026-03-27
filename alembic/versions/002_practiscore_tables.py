"""practiscore tables

Revision ID: 002
Revises: 001
Create Date: 2026-03-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "practiscore_matches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("match_name", sa.String(length=300), nullable=False),
        sa.Column("match_date", sa.Date(), nullable=True),
        sa.Column("match_level", sa.SmallInteger(), nullable=True),
        sa.Column("division", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("practiscore_match_id", sa.String(length=200), nullable=True),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column("total_competitors", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_practiscore_matches_member_id", "practiscore_matches", ["member_id"])
    op.create_index("ix_practiscore_matches_match_date", "practiscore_matches", ["match_date"])

    op.create_table(
        "practiscore_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("shooter_name", sa.String(length=200), nullable=False),
        sa.Column("member_number", sa.String(length=20), nullable=True),
        sa.Column("division", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("classification", sa.String(length=5), nullable=True),
        sa.Column("total_points", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("total_time", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("percent_of_winner", sa.Numeric(precision=7, scale=4), nullable=True),
        sa.Column("placement", sa.Integer(), nullable=True),
        sa.Column("is_queried_member", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["practiscore_matches.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_practiscore_results_match_id", "practiscore_results", ["match_id"])
    op.create_index("ix_practiscore_results_member_number", "practiscore_results", ["member_number"])


def downgrade() -> None:
    op.drop_index("ix_practiscore_results_member_number", table_name="practiscore_results")
    op.drop_index("ix_practiscore_results_match_id", table_name="practiscore_results")
    op.drop_table("practiscore_results")
    op.drop_index("ix_practiscore_matches_match_date", table_name="practiscore_matches")
    op.drop_index("ix_practiscore_matches_member_id", table_name="practiscore_matches")
    op.drop_table("practiscore_matches")
