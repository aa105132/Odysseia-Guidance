import os
import logging
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
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
    # 优先使用 DB_HOST 环境变量，其次根据运行环境决定
    if not db_host:
        if os.getenv("RUNNING_IN_DOCKER"):
            # Docker 内部：优先使用用户指定的外部数据库容器名
            # 如果没有指定，再尝试使用内部的 db 服务
            db_host = os.getenv("EXTERNAL_DB_HOST", "odysseia_pg_db")
            log.info(f"Running inside Docker, connecting to '{db_host}' host.")
        else:
            db_host = "localhost"
            log.info("Running on host machine, connecting to 'localhost'.")

    DATABASE_URL = (
        f"postgresql+asyncpg://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    )
    log.info(f"Database URL: postgresql+asyncpg://{db_user}:***@{db_host}:{db_port}/{db_name}")
else:
    log.info(f"Using DATABASE_URL from environment (user: {db_user}, host: {db_host or 'from URL'})")

engine = create_async_engine(DATABASE_URL, echo=True)
AsyncSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
