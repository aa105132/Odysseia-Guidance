from datetime import datetime, timedelta, timezone
from src.chat.utils.database import chat_db_manager
from src.chat.utils.time_utils import get_start_of_today_utc
from src.chat.config.chat_config import CONFESSION_CONFIG


class ConfessionService:
    def __init__(self):
        self.db_manager = chat_db_manager

    async def record_confession(self, user_id: str):
        """记录一次忏悔事件"""
        query = "INSERT INTO confession_log (user_id, timestamp) VALUES (?, ?)"
        await self.db_manager._execute(
            self.db_manager._db_transaction,
            query,
            (user_id, datetime.now(timezone.utc).isoformat()),
            commit=True,
        )
        await self.db_manager.increment_confession_count()

    async def can_confess(self, user_id: str) -> tuple[bool, str]:
        """
        检查用户是否可以忏悔。
        返回一个元组 (can_confess: bool, message: str)
        """
        # 1. 检查最近一次忏悔时间（冷却时间）
        query1 = "SELECT timestamp FROM confession_log WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1"
        last_confession_row = await self.db_manager._execute(
            self.db_manager._db_transaction, query1, (user_id,), fetch="one"
        )

        now_utc = datetime.now(timezone.utc)
        if last_confession_row:
            last_confession_time = datetime.fromisoformat(
                last_confession_row[0]
            ).replace(tzinfo=timezone.utc)
            time_since_last = now_utc - last_confession_time
            cooldown_duration = timedelta(seconds=CONFESSION_CONFIG["COOLDOWN_SECONDS"])
            if time_since_last < cooldown_duration:
                remaining_time = cooldown_duration - time_since_last
                hours, remainder = divmod(remaining_time.seconds, 3600)
                minutes, _ = divmod(remainder, 60)

                if hours > 0:
                    cooldown_message = f"{hours}小时{minutes}分钟"
                else:
                    cooldown_message = f"{minutes}分钟"
                return (
                    False,
                    f"喂喂，谁要一直听你忏悔啊…**{cooldown_message}** 以后再来！",
                )

        # 2. 检查今天（北京时间）的忏悔次数
        start_of_today_utc = get_start_of_today_utc()

        query2 = (
            "SELECT COUNT(*) FROM confession_log WHERE user_id = ? AND timestamp >= ?"
        )
        count_row = await self.db_manager._execute(
            self.db_manager._db_transaction,
            query2,
            (user_id, start_of_today_utc.isoformat()),
            fetch="one",
        )

        confessions_today = count_row[0] if count_row else 0

        if confessions_today >= 3:
            return False, "今天说够多啦，我也不是天天想听你忏悔的…明天再来！"

        return True, ""


confession_service = ConfessionService()
