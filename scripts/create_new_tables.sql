-- ============================================
-- 手动创建新表的 SQL 脚本
-- 用于在 Alembic 迁移链有问题时直接执行
-- ============================================

-- 1. 创建 shop schema 和 shop_items 表
CREATE SCHEMA IF NOT EXISTS shop;

CREATE TABLE IF NOT EXISTS shop.shop_items (
    id SERIAL NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    price INTEGER NOT NULL,
    category VARCHAR(100) NOT NULL,
    target VARCHAR(50) NOT NULL DEFAULT 'self',
    effect_id VARCHAR(100),
    cg_url JSON,
    is_available INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    PRIMARY KEY (id),
    UNIQUE (name)
);

CREATE INDEX IF NOT EXISTS ix_shop_shop_items_id ON shop.shop_items (id);

-- 2. 创建 thread_settings 表（在 tutorials schema 中）
CREATE TABLE IF NOT EXISTS tutorials.thread_settings (
    id SERIAL NOT NULL,
    thread_id VARCHAR NOT NULL,
    search_mode VARCHAR NOT NULL DEFAULT 'ISOLATED',
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    PRIMARY KEY (id),
    UNIQUE (thread_id)
);

CREATE INDEX IF NOT EXISTS ix_thread_settings_id ON tutorials.thread_settings (id);

-- 3. 创建 user schema 和 user_tool_settings 表
CREATE SCHEMA IF NOT EXISTS "user";

CREATE TABLE IF NOT EXISTS "user".user_tool_settings (
    id SERIAL NOT NULL,
    user_id VARCHAR(50) NOT NULL,
    enabled_tools JSON,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    PRIMARY KEY (id),
    UNIQUE (user_id)
);

CREATE INDEX IF NOT EXISTS ix_user_tool_settings_id ON "user".user_tool_settings (id);

-- 完成
SELECT 'All tables created successfully!' AS status;