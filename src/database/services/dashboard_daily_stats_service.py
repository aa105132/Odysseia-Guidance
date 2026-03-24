from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from src.database.models import DashboardDailyStats


class DashboardDailyStatsService:
    @staticmethod
    async def get_daily_stats(
        session: AsyncSession, stats_date: date
    ) -> DashboardDailyStats | None:
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
