"""Add checkin and bankruptcy fields to user_coins

Revision ID: add_checkin_bankruptcy
Revises: fe7cfa779ace
Create Date: 2026-01-29 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_checkin_bankruptcy'
down_revision: Union[str, None] = 'fe7cfa779ace'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 添加签到相关字段
    op.add_column('user_coins', sa.Column('last_checkin_date', sa.String(50), nullable=True))
    op.add_column('user_coins', sa.Column('checkin_streak', sa.Integer(), nullable=True, server_default='0'))
    
    # 添加破产补贴相关字段
    op.add_column('user_coins', sa.Column('last_bankruptcy_claim', sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column('user_coins', 'last_bankruptcy_claim')
    op.drop_column('user_coins', 'checkin_streak')
    op.drop_column('user_coins', 'last_checkin_date')