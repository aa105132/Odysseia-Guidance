import os
import logging
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from dotenv import load_dotenv

# Basic logging setup
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
log = logging.getLogger(__name__)

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# 始终读取这些变量用于日志显示
db_user = os.getenv("POSTGRES_USER", "postgres")
db_password = os.getenv("POSTGRES_PASSWORD", "password")
db_name = os.getenv("POSTGRES_DB", "yueyue")
db_port = os.getenv("DB_PORT", "5432")
db_host = os.getenv("DB_HOST")

if not DATABASE_URL:
    # 优先使用 DB_HOST 环境变量，其次使用 EXTERNAL_DB_HOST
    if not db_host:
        db_host = os.getenv("EXTERNAL_DB_HOST")
        if db_host:
            log.info(f"Using EXTERNAL_DB_HOST: '{db_host}'")
        elif os.getenv("RUNNING_IN_DOCKER"):
            # Docker 内部但没有指定主机，使用默认值
            db_host = "localhost"
            log.warning("RUNNING_IN_DOCKER is set but DB_HOST/EXTERNAL_DB_HOST not specified, using 'localhost'")
        else:
            db_host = "localhost"
            log.info("Running on host machine, connecting to 'localhost'.")

    DATABASE_URL = (
        f"postgresql+asyncpg://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    )
    log.info(f"Database URL: postgresql+asyncpg://{db_user}:***@{db_host}:{db_port}/{db_name}")
else:
    log.info(f"Using DATABASE_URL from environment (user: {db_user}, host: {db_host or 'from URL'})")

# Bot 主线程和 Dashboard 线程各自运行在不同的 asyncio 事件循环中。
# asyncpg 连接不能跨事件循环复用；若继续使用默认连接池，
# 连接可能在一个 loop 中创建、回收到池中，再在另一个 loop 中被取出，
# 最终触发 “got Future attached to a different loop”。
# 这里显式禁用连接池，确保每次会话都创建并关闭当前 loop 专属连接。
engine = create_async_engine(
    DATABASE_URL,
    echo=True,
    poolclass=NullPool,
)
AsyncSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
