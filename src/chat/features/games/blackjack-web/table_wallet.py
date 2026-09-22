"""桌游灵石托管：同一 SQLite 事务处理余额、流水和幂等轮次账本。"""

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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
            connection.execute("""
                CREATE TABLE IF NOT EXISTS table_game_results (
                    round_key TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    game_type TEXT NOT NULL,
                    username TEXT NOT NULL,
                    avatar_url TEXT NOT NULL,
                    profit INTEGER NOT NULL,
                    settled_at TEXT NOT NULL,
                    settled_day TEXT NOT NULL,
                    PRIMARY KEY(round_key, user_id)
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS table_results_user_game ON table_game_results(user_id, game_type)")
            connection.execute("CREATE INDEX IF NOT EXISTS table_results_game_day ON table_game_results(game_type, settled_day)")
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

    def _settle(self, round_key: str, payouts: dict[str, int], game_type: str | None = None,
                profiles: dict[str, dict] | None = None):
        now = datetime.now(timezone(timedelta(hours=8)))
        with self._transaction() as connection:
            rows = connection.execute("SELECT user_id, status, payout, stake FROM table_game_escrow WHERE round_key = ?", (round_key,)).fetchall()
            if not rows or {str(row[0]) for row in rows} != set(payouts):
                raise ValueError("结算玩家与托管记录不一致")
            for user_id, status, old_payout, stake in rows:
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
                if game_type:
                    profile = (profiles or {}).get(str(user_id), {})
                    # 和真实返还金额同事务写入，重试、退款和机器人不会重复计入战绩。
                    connection.execute("""
                        INSERT INTO table_game_results
                        (round_key, user_id, game_type, username, avatar_url, profit, settled_at, settled_day)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (round_key, user_id, game_type, profile.get("username") or str(user_id),
                          profile.get("avatar_url") or "", amount - stake,
                          now.isoformat(), now.date().isoformat()))

    async def settle(self, round_key: str, payouts: dict[str, int], *,
                     game_type: str | None = None, profiles: dict[str, dict] | None = None):
        await asyncio.to_thread(self._settle, round_key, payouts, game_type, profiles)

    def _settle_blackjack(self, round_key: str, user_id: str, stake: int,
                          payout: int, profile: dict | None):
        if not round_key or stake <= 0 or payout < 0:
            raise ValueError("21 点结算参数不合法")
        now = datetime.now(timezone(timedelta(hours=8)))
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT profit FROM table_game_results WHERE round_key = ? AND user_id = ?",
                (round_key, int(user_id)),
            ).fetchone()
            if existing is not None:
                if existing[0] != payout - stake:
                    raise ValueError("该牌局已经按不同金额完成结算")
            else:
                profile = profile or {}
                # 21 点下注已经扣除；派彩与战绩同事务提交，失败可安全重试。
                self._credit(connection, int(user_id), payout, "21点游戏结算派彩")
                connection.execute("""
                    INSERT INTO table_game_results
                    (round_key, user_id, game_type, username, avatar_url, profit, settled_at, settled_day)
                    VALUES (?, ?, 'blackjack', ?, ?, ?, ?, ?)
                """, (round_key, int(user_id), profile.get("username") or str(user_id),
                      profile.get("avatar_url") or "", payout - stake,
                      now.isoformat(), now.date().isoformat()))
            row = connection.execute("SELECT balance FROM user_coins WHERE user_id = ?", (int(user_id),)).fetchone()
            if row is None:
                raise ValueError("结算账户不存在")
            return row[0]

    async def settle_blackjack(self, round_key: str, user_id: str, stake: int,
                               payout: int, profile: dict | None = None):
        return await asyncio.to_thread(self._settle_blackjack, round_key, user_id, stake, payout, profile)

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone(timedelta(hours=8))).date().isoformat()

    @staticmethod
    def _results_cte() -> str:
        # 旧托管记录没有日期和游戏类型，只参与全部游戏的累计统计。
        return """WITH results AS (
            SELECT round_key, user_id, game_type, username, avatar_url, profit,
                   settled_at, settled_day, 0 AS legacy FROM table_game_results
            UNION ALL
            SELECT e.round_key, e.user_id, NULL, CAST(e.user_id AS TEXT), '',
                   e.payout - e.stake, '', NULL, 1
            FROM table_game_escrow e WHERE e.status = 'settled'
              AND NOT EXISTS (SELECT 1 FROM table_game_results r
                              WHERE r.round_key = e.round_key AND r.user_id = e.user_id)
        ) """

    def _statistics(self, user_id: str, game_type: str | None):
        day = self._today()
        with self._transaction() as connection:
            row = connection.execute(self._results_cte() + """
                SELECT COUNT(*), COALESCE(SUM(profit > 0), 0),
                       COALESCE(SUM(profit < 0), 0), COALESCE(SUM(profit = 0), 0),
                       COALESCE(SUM(profit), 0),
                       COALESCE(SUM(CASE WHEN settled_day = ? THEN profit ELSE 0 END), 0),
                       COALESCE(SUM(legacy), 0)
                FROM results WHERE user_id = ?
            """ + (" AND game_type = ?" if game_type else ""),
                (day, int(user_id), game_type) if game_type else (day, int(user_id))).fetchone()
        rounds, wins, losses, draws, total, daily, legacy = row
        return {"day": day, "timezone": "Asia/Shanghai", "stats": {
            "rounds": rounds, "wins": wins, "losses": losses, "draws": draws,
            "win_rate": 100 * wins / rounds if rounds else 0,
            "net_profit": total, "today_profit": daily, "legacy_rounds": legacy,
        }}

    async def statistics(self, user_id: str, game_type: str | None = None):
        return await asyncio.to_thread(self._statistics, user_id, game_type)

    def _leaderboard(self, user_id: str, period: str, game_type: str | None, limit: int,
                     excluded_user_ids: tuple[int, ...] = ()):
        if period not in ("today", "all"):
            raise ValueError("排行榜周期必须为 today 或 all")
        day = self._today()
        # 游戏陪玩没有 Discord 正整数账号；已核实的 Discord 机器人也不参与排名。
        conditions, parameters = ["typeof(user_id) = 'integer'", "user_id > 0"], []
        if excluded_user_ids:
            conditions.append(f"user_id NOT IN ({','.join('?' for _ in excluded_user_ids)})")
            parameters.extend(excluded_user_ids)
        if game_type:
            conditions.append("game_type = ?")
            parameters.append(game_type)
        if period == "today":
            conditions.append("settled_day = ?")
            parameters.append(day)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        with self._transaction() as connection:
            rows = connection.execute(self._results_cte() + f"""
                , totals AS (
                    SELECT user_id, COUNT(*) AS rounds, SUM(profit) AS profit,
                           SUM(legacy) AS legacy_rounds
                    FROM results {where} GROUP BY user_id
                ), ranked AS (
                    SELECT *, RANK() OVER (ORDER BY profit DESC) AS rank,
                           ROW_NUMBER() OVER (ORDER BY profit DESC, user_id ASC) AS position,
                           SUM(legacy_rounds) OVER () AS all_legacy_rounds
                    FROM totals
                )
                SELECT rank, CAST(user_id AS TEXT), rounds, profit,
                       COALESCE((SELECT username FROM table_game_results r WHERE r.user_id = ranked.user_id
                        ORDER BY settled_at DESC, round_key DESC LIMIT 1), CAST(user_id AS TEXT)),
                       COALESCE((SELECT avatar_url FROM table_game_results r WHERE r.user_id = ranked.user_id
                        ORDER BY settled_at DESC, round_key DESC LIMIT 1), ''), position,
                       all_legacy_rounds
                FROM ranked WHERE position <= ? OR user_id = ?
                ORDER BY profit DESC, user_id ASC
            """, [*parameters, limit, int(user_id)]).fetchall()
        def entry(row):
            return {"rank": row[0], "user_id": row[1], "rounds": row[2],
                    "net_profit": row[3], "username": row[4], "avatar_url": row[5]}
        return {"period": period, "day": day, "timezone": "Asia/Shanghai",
                "entries": [entry(row) for row in rows if row[6] <= limit],
                "self": next((entry(row) for row in rows if row[1] == str(user_id)), None),
                "legacy_rounds": rows[0][7] if rows else 0}

    async def leaderboard(self, user_id: str, period: str = "today",
                          game_type: str | None = None, limit: int = 20,
                          excluded_user_ids: tuple[int, ...] = ()):
        return await asyncio.to_thread(self._leaderboard, user_id, period, game_type, limit,
                                       excluded_user_ids)

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
