"""桌游灵石托管：同一 SQLite 事务处理余额、流水和幂等轮次账本。"""

import asyncio
from contextlib import contextmanager
from pathlib import Path
import sqlite3


class InsufficientTableBalance(ValueError):
    def __init__(self, user_id: str, stake: int):
        self.user_id = user_id
        super().__init__(f"玩家 {user_id} 余额不足，每局需要 {stake} 灵石")


class TableWallet:
    def __init__(self, db_path: str):
        self.db_path = db_path

    @contextmanager
    def _transaction(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=15)
        try:
            connection.execute("PRAGMA busy_timeout=15000")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS table_game_escrow (
                    round_key TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    stake INTEGER NOT NULL CHECK(stake > 0),
                    payout INTEGER,
                    status TEXT NOT NULL DEFAULT 'reserved',
                    PRIMARY KEY(round_key, user_id)
                )
            """)
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _credit(connection, user_id: int, amount: int, reason: str):
        if amount == 0:
            return
        connection.execute("UPDATE user_coins SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        connection.execute("INSERT INTO coin_transactions (user_id, amount, reason) VALUES (?, ?, ?)", (user_id, amount, reason))

    def _reserve(self, round_key: str, user_ids: list[str], stake: int, entry_min: int | None = None):
        minimum = max(stake, entry_min if entry_min is not None else stake)
        with self._transaction() as connection:
            existing = connection.execute("SELECT user_id, stake, status FROM table_game_escrow WHERE round_key = ?", (round_key,)).fetchall()
            if existing:
                if {(str(row[0]), row[1], row[2]) for row in existing} != {(uid, stake, "reserved") for uid in user_ids}:
                    raise ValueError("该牌局托管记录与当前开局不一致")
                return
            for uid in user_ids:
                updated = connection.execute(
                    "UPDATE user_coins SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
                    (stake, int(uid), minimum),
                ).rowcount
                if updated != 1:
                    raise InsufficientTableBalance(uid, minimum)
                connection.execute("INSERT INTO coin_transactions (user_id, amount, reason) VALUES (?, ?, ?)", (int(uid), -stake, f"桌游{round_key}开局托管"))
                connection.execute("INSERT INTO table_game_escrow (round_key, user_id, stake) VALUES (?, ?, ?)", (round_key, int(uid), stake))

    async def reserve(self, round_key: str, user_ids: list[str], stake: int, entry_min: int | None = None):
        await asyncio.to_thread(self._reserve, round_key, user_ids, stake, entry_min)

    def _settle(self, round_key: str, payouts: dict[str, int]):
        with self._transaction() as connection:
            rows = connection.execute("SELECT user_id, status, payout FROM table_game_escrow WHERE round_key = ?", (round_key,)).fetchall()
            if not rows or {str(row[0]) for row in rows} != set(payouts):
                raise ValueError("结算玩家与托管记录不一致")
            for user_id, status, old_payout in rows:
                amount = payouts[str(user_id)]
                if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                    raise ValueError("结算金额必须为非负整数")
                if status == "settled":
                    if old_payout != amount:
                        raise ValueError("该牌局已经按不同金额完成结算")
                    continue
                if status != "reserved":
                    raise ValueError("该牌局已退款，不能再次结算")
                self._credit(connection, user_id, amount, f"桌游{round_key}结算返还")
                connection.execute("UPDATE table_game_escrow SET payout = ?, status = 'settled' WHERE round_key = ? AND user_id = ?", (amount, round_key, user_id))

    async def settle(self, round_key: str, payouts: dict[str, int]):
        await asyncio.to_thread(self._settle, round_key, payouts)

    def _refund(self, round_key: str | None):
        with self._transaction() as connection:
            query = "SELECT round_key, user_id, stake FROM table_game_escrow WHERE status = 'reserved'"
            rows = connection.execute(query + (" AND round_key = ?" if round_key else ""), (round_key,) if round_key else ()).fetchall()
            for key, user_id, stake in rows:
                self._credit(connection, user_id, stake, f"桌游{key}未完成牌局退款")
                connection.execute("UPDATE table_game_escrow SET payout = stake, status = 'refunded' WHERE round_key = ? AND user_id = ?", (key, user_id))
            return len(rows)

    async def refund(self, round_key: str):
        return await asyncio.to_thread(self._refund, round_key)

    async def recover(self):
        """单进程启动时退款上次中断的牌局；账本保留防止重复退款。"""
        return await asyncio.to_thread(self._refund, None)
