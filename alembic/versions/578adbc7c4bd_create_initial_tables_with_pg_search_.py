"""Create initial tables with pg_search and HNSW

Revision ID: 578adbc7c4bd
Revises:
Create Date: 2025-12-18 12:41:47.299105

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "578adbc7c4bd"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Phase 1: Only install the required extensions.
    This is to ensure they are fully available before being used in a subsequent migration.
    """
    from sqlalchemy import text
    from alembic import context
    
    op.execute("CREATE SCHEMA IF NOT EXISTS tutorials;")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    
    # pg_search 是可选的 - 如果安装失败则跳过（全文搜索功能将不可用）
    try:
        connection = op.get_bind()
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_search;"))
        print("✅ pg_search 扩展安装成功")
    except Exception as e:
        print(f"⚠️ pg_search 扩展安装失败，全文搜索功能将不可用: {e}")
        print("   机器人其他功能仍可正常使用")


def downgrade() -> None:
    """
    Phase 1 Downgrade: Remove the extensions.
    """
    try:
        op.execute("DROP EXTENSION IF EXISTS pg_search;")
    except Exception:
        pass
    op.execute("DROP EXTENSION IF EXISTS vector;")
    op.execute("DROP SCHEMA IF EXISTS tutorials CASCADE;")
