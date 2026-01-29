"""Add checkin and bankruptcy fields to user_coins

Revision ID: add_checkin_bankruptcy
Revises: fe7cfa779ace
Create Date: 2026-01-29 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = 'add_checkin_bankruptcy'
down_revision: Union[str, None] = 'fe7cfa779ace'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 检查 user_coins 表是否存在
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()
    
    if 'user_coins' not in tables:
        # 创建 user_coins 表
        op.create_table(
            'user_coins',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('user_id', sa.BigInteger(), nullable=False, unique=True),
            sa.Column('balance', sa.BigInteger(), nullable=False, server_default='0'),
            sa.Column('last_checkin_date', sa.String(50), nullable=True),
            sa.Column('checkin_streak', sa.Integer(), nullable=True, server_default='0'),
            sa.Column('last_bankruptcy_claim', sa.String(50), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        )
        print("✅ 创建了 user_coins 表")
    else:
        # 表已存在，添加新列
        columns = [col['name'] for col in inspector.get_columns('user_coins')]
        
        if 'last_checkin_date' not in columns:
            op.add_column('user_coins', sa.Column('last_checkin_date', sa.String(50), nullable=True))
            print("✅ 添加了 last_checkin_date 列")
        
        if 'checkin_streak' not in columns:
            op.add_column('user_coins', sa.Column('checkin_streak', sa.Integer(), nullable=True, server_default='0'))
            print("✅ 添加了 checkin_streak 列")
        
        if 'last_bankruptcy_claim' not in columns:
            op.add_column('user_coins', sa.Column('last_bankruptcy_claim', sa.String(50), nullable=True))
            print("✅ 添加了 last_bankruptcy_claim 列")


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()
    
    if 'user_coins' in tables:
        columns = [col['name'] for col in inspector.get_columns('user_coins')]
        
        if 'last_bankruptcy_claim' in columns:
            op.drop_column('user_coins', 'last_bankruptcy_claim')
        if 'checkin_streak' in columns:
            op.drop_column('user_coins', 'checkin_streak')
        if 'last_checkin_date' in columns:
            op.drop_column('user_coins', 'last_checkin_date')