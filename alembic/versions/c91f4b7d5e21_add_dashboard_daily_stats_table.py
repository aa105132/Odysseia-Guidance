"""add_dashboard_daily_stats_table

Revision ID: c91f4b7d5e21
Revises: 40a238a8bfb3, d4d2d70d3faa
Create Date: 2026-03-22 21:10:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c91f4b7d5e21"
down_revision: Union[str, Sequence[str], None] = ("40a238a8bfb3", "d4d2d70d3faa")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "dashboard_daily_stats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column(
            "channel_message_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "dm_message_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "image_message_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("date"),
    )
    op.create_index(
        op.f("ix_dashboard_daily_stats_date"),
        "dashboard_daily_stats",
        ["date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_dashboard_daily_stats_id"),
        "dashboard_daily_stats",
        ["id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_dashboard_daily_stats_id"), table_name="dashboard_daily_stats")
    op.drop_index(op.f("ix_dashboard_daily_stats_date"), table_name="dashboard_daily_stats")
    op.drop_table("dashboard_daily_stats")
