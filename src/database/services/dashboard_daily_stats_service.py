import asyncio
import logging
from datetime import date

from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from src.database.models import Base, DashboardDailyStats


log = logging.getLogger(__name__)


class DashboardDailyStatsService:
    _table_ready_lock: asyncio.Lock | None = None
    _table_ready_checked: bool = False

    @classmethod
    def _get_table_ready_lock(cls) -> asyncio.Lock:
        if cls._table_ready_lock is None:
            cls._table_ready_lock = asyncio.Lock()
        return cls._table_ready_lock

    @staticmethod
    def _is_missing_dashboard_daily_stats_table_error(error: Exception) -> bool:
        if not isinstance(error, ProgrammingError):
            return False

        orig = getattr(error, "orig", None)
        sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
        error_text = " ".join(
            str(part) for part in (error, orig) if part is not None
        ).lower()

        return (
            "dashboard_daily_stats" in error_text
            and (
                sqlstate == "42P01"
                or "does not exist" in error_text
                or "undefinedtable" in error_text
            )
        )

    @staticmethod
    async def _ensure_dashboard_daily_stats_table(session: AsyncSession) -> None:
        bind = getattr(session, "bind", None)
        if bind is None:
            raise RuntimeError(
                "AsyncSession 未绑定数据库引擎，无法自动创建 dashboard_daily_stats 表。"
            )

        async with bind.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: Base.metadata.create_all(
                    sync_conn,
                    tables=[DashboardDailyStats.__table__],
                    checkfirst=True,
                )
            )

        log.warning("检测到 dashboard_daily_stats 表缺失，已自动补建。")

    @classmethod
    async def ensure_table_ready(
        cls,
        session: AsyncSession,
        *,
        force: bool = False,
    ) -> None:
        if cls._table_ready_checked and not force:
            return

        async with cls._get_table_ready_lock():
            if cls._table_ready_checked and not force:
                return

            await cls._ensure_dashboard_daily_stats_table(session)
            cls._table_ready_checked = True

    @classmethod
    async def get_daily_stats(
        cls, session: AsyncSession, stats_date: date
    ) -> DashboardDailyStats | None:
        await cls.ensure_table_ready(session)

        try:
            result = await session.execute(
                select(DashboardDailyStats).filter(DashboardDailyStats.date == stats_date)
            )
        except ProgrammingError as error:
            if not cls._is_missing_dashboard_daily_stats_table_error(error):
                raise

            await session.rollback()
            await cls.ensure_table_ready(session, force=True)
            result = await session.execute(
                select(DashboardDailyStats).filter(DashboardDailyStats.date == stats_date)
            )
        return result.scalars().first()

    @staticmethod
    async def create_daily_stats(
        session: AsyncSession,
        stats_date: date,
        channel_messages: int = 0,
        dm_messages: int = 0,
        image_messages: int = 0,
    ) -> DashboardDailyStats:
        record = DashboardDailyStats(
            date=stats_date,
            channel_message_count=channel_messages,
            dm_message_count=dm_messages,
            image_message_count=image_messages,
        )
        session.add(record)
        await session.commit()
        return record

    @staticmethod
    async def update_daily_stats(
        session: AsyncSession,
        record: DashboardDailyStats,
        channel_messages: int = 0,
        dm_messages: int = 0,
        image_messages: int = 0,
    ) -> DashboardDailyStats:
        record.channel_message_count += channel_messages
        record.dm_message_count += dm_messages
        record.image_message_count += image_messages
        await session.commit()
        return record

    @classmethod
    async def increment_message_stats(
        cls,
        session: AsyncSession,
        stats_date: date,
        channel_messages: int = 0,
        dm_messages: int = 0,
        image_messages: int = 0,
    ) -> DashboardDailyStats:
        record = await cls.get_daily_stats(session, stats_date)
        if record:
            return await cls.update_daily_stats(
                session,
                record,
                channel_messages=channel_messages,
                dm_messages=dm_messages,
                image_messages=image_messages,
            )
        return await cls.create_daily_stats(
            session,
            stats_date,
            channel_messages=channel_messages,
            dm_messages=dm_messages,
            image_messages=image_messages,
        )


dashboard_daily_stats_service = DashboardDailyStatsService()
