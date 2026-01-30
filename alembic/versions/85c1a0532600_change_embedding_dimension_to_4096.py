"""change_embedding_dimension_to_4096

Revision ID: 85c1a0532600
Revises: 8daf68beb22b
Create Date: 2026-01-30 12:18:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '85c1a0532600'
down_revision: Union[str, None] = '8daf68beb22b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """将所有向量列从 HALFVEC(3072) 改为 HALFVEC(4096)"""
    
    # 1. 删除现有的向量数据（因为维度变了，旧数据不兼容）
    op.execute("UPDATE tutorials.knowledge_chunks SET embedding = NULL")
    op.execute("UPDATE general_knowledge.knowledge_chunks SET embedding = NULL")
    op.execute("UPDATE community.member_chunks SET embedding = NULL")
    
    # 2. 删除旧的 HNSW 索引（它们与旧维度绑定）
    op.execute("DROP INDEX IF EXISTS tutorials.idx_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS general_knowledge.idx_gk_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS community.idx_member_embedding_hnsw")
    
    # 3. 修改列类型为新维度
    op.execute("ALTER TABLE tutorials.knowledge_chunks ALTER COLUMN embedding TYPE halfvec(4096)")
    op.execute("ALTER TABLE general_knowledge.knowledge_chunks ALTER COLUMN embedding TYPE halfvec(4096)")
    op.execute("ALTER TABLE community.member_chunks ALTER COLUMN embedding TYPE halfvec(4096)")
    
    # 4. 重新创建 HNSW 索引
    op.execute("""
        CREATE INDEX idx_embedding_hnsw ON tutorials.knowledge_chunks 
        USING hnsw (embedding halfvec_cosine_ops) 
        WITH (m = 16, ef_construction = 64)
    """)
    op.execute("""
        CREATE INDEX idx_gk_embedding_hnsw ON general_knowledge.knowledge_chunks 
        USING hnsw (embedding halfvec_cosine_ops) 
        WITH (m = 16, ef_construction = 64)
    """)
    op.execute("""
        CREATE INDEX idx_member_embedding_hnsw ON community.member_chunks 
        USING hnsw (embedding halfvec_cosine_ops) 
        WITH (m = 16, ef_construction = 64)
    """)


def downgrade() -> None:
    """将所有向量列从 HALFVEC(4096) 改回 HALFVEC(3072)"""
    
    op.execute("UPDATE tutorials.knowledge_chunks SET embedding = NULL")
    op.execute("UPDATE general_knowledge.knowledge_chunks SET embedding = NULL")
    op.execute("UPDATE community.member_chunks SET embedding = NULL")
    
    op.execute("DROP INDEX IF EXISTS tutorials.idx_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS general_knowledge.idx_gk_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS community.idx_member_embedding_hnsw")
    
    op.execute("ALTER TABLE tutorials.knowledge_chunks ALTER COLUMN embedding TYPE halfvec(3072)")
    op.execute("ALTER TABLE general_knowledge.knowledge_chunks ALTER COLUMN embedding TYPE halfvec(3072)")
    op.execute("ALTER TABLE community.member_chunks ALTER COLUMN embedding TYPE halfvec(3072)")
    
    op.execute("""
        CREATE INDEX idx_embedding_hnsw ON tutorials.knowledge_chunks 
        USING hnsw (embedding halfvec_cosine_ops) 
        WITH (m = 16, ef_construction = 64)
    """)
    op.execute("""
        CREATE INDEX idx_gk_embedding_hnsw ON general_knowledge.knowledge_chunks 
        USING hnsw (embedding halfvec_cosine_ops) 
        WITH (m = 16, ef_construction = 64)
    """)
    op.execute("""
        CREATE INDEX idx_member_embedding_hnsw ON community.member_chunks 
        USING hnsw (embedding halfvec_cosine_ops) 
        WITH (m = 16, ef_construction = 64)
    """)