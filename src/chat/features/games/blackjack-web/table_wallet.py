"""桌游灵石托管：同一 SQLite 事务处理余额、流水和幂等轮次账本。"""

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
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
            connection.execute("""
                CREATE TABLE IF NOT EXISTS table_round_details (
                    round_key TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    stake INTEGER NOT NULL,
                    payout INTEGER NOT NULL,
                    details TEXT NOT NULL,
                    PRIMARY KEY(round_key, user_id)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS table_game_adjustments (
                    round_key TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    operation_id TEXT NOT NULL,
                    original_profit INTEGER NOT NULL CHECK(original_profit > 0),
                    profit_delta INTEGER NOT NULL CHECK(profit_delta = -original_profit),
                    reason TEXT NOT NULL,
                    evidence_sha256 TEXT NOT NULL,
                    coin_transaction_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
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

    def _settle(self, round_key: str, payouts: dict[str, int], game_type: str | None = None,
                profiles: dict[str, dict] | None = None, round_details: dict[str, dict] | None = None):
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
                self._record_round_details(connection, round_key, user_id, stake, amount,
                                           (round_details or {}).get(str(user_id)))
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
                     game_type: str | None = None, profiles: dict[str, dict] | None = None,
                     round_details: dict[str, dict] | None = None):
        await asyncio.to_thread(self._settle, round_key, payouts, game_type, profiles, round_details)

    @staticmethod
    def _record_round_details(connection, round_key: str, user_id: int, stake: int, payout: int,
                              details: dict | None):
        if details is not None and not isinstance(details, dict):
            raise ValueError("牌局详情必须是对象")
        connection.execute("""INSERT INTO table_round_details
            (round_key, user_id, stake, payout, details) VALUES (?, ?, ?, ?, ?)""",
            (round_key, user_id, stake, payout, json.dumps(details or {}, ensure_ascii=False, allow_nan=False)))

    def _settle_blackjack(self, round_key: str, user_id: str, stake: int,
                          payout: int, profile: dict | None, details: dict | None = None):
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
                self._record_round_details(connection, round_key, int(user_id), stake, payout, details)
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
                               payout: int, profile: dict | None = None, details: dict | None = None):
        return await asyncio.to_thread(self._settle_blackjack, round_key, user_id, stake, payout, profile, details)

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone(timedelta(hours=8))).date().isoformat()

    @staticmethod
    def _results_cte() -> str:
        # 旧托管记录没有日期和游戏类型，只参与全部游戏的累计统计。
        return """WITH results AS (
            SELECT r.round_key, r.user_id, r.game_type, r.username, r.avatar_url,
                   r.profit + COALESCE(a.profit_delta, 0) AS profit,
                   r.settled_at, r.settled_day, 0 AS legacy,
                   r.profit AS original_profit, COALESCE(a.profit_delta, 0) AS adjustment,
                   a.reason AS adjustment_reason
            FROM table_game_results r
            LEFT JOIN table_game_adjustments a
              ON a.round_key = r.round_key AND a.user_id = r.user_id
            UNION ALL
            SELECT e.round_key, e.user_id, NULL, CAST(e.user_id AS TEXT), '',
                   e.payout - e.stake, '', NULL, 1, e.payout - e.stake, 0, NULL
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
                       COALESCE(SUM(legacy), 0),
                       MAX(0, COALESCE(MAX(profit), 0)), MAX(0, -COALESCE(MIN(profit), 0)),
                       COALESCE(SUM(CASE WHEN profit > 0 THEN profit ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN profit < 0 THEN -profit ELSE 0 END), 0)
                FROM results WHERE user_id = ?
            """ + (" AND game_type = ?" if game_type else ""),
                (day, int(user_id), game_type) if game_type else (day, int(user_id))).fetchone()
        rounds, wins, losses, draws, total, daily, legacy, max_win, max_loss, total_won, total_lost = row
        return {"day": day, "timezone": "Asia/Shanghai", "stats": {
            "rounds": rounds, "wins": wins, "losses": losses, "draws": draws,
            "win_rate": 100 * wins / rounds if rounds else 0,
            "net_profit": total, "today_profit": daily, "legacy_rounds": legacy,
            "max_win": max_win, "max_loss": max_loss, "total_won": total_won,
            "total_lost": total_lost, "average_profit": total / rounds if rounds else 0,
        }}

    async def statistics(self, user_id: str, game_type: str | None = None):
        return await asyncio.to_thread(self._statistics, user_id, game_type)

    def _leaderboard(self, user_id: str, period: str, game_type: str | None, limit: int,
                     excluded_user_ids: tuple[int, ...] = ()):
        if period not in ("today", "all"):
            raise ValueError("排行榜周期必须为 today 或 all")
        self._validate_paging(limit, 0)
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
                          game_type: str | None = None, limit: int = 100,
                          excluded_user_ids: tuple[int, ...] = ()):
        return await asyncio.to_thread(self._leaderboard, user_id, period, game_type, limit,
                                       excluded_user_ids)

    @staticmethod
    def _validate_paging(limit: int, offset: int):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("每页数量须为 1 至 100 的整数")
        if type(offset) is not int or not 0 <= offset <= 100000:
            raise ValueError("分页位置须为 0 至 100000 的整数")

    @staticmethod
    def _history_entry(row):
        return {"round_key": row[0], "game_type": row[1], "profit": row[2],
                "settled_at": row[3] or None, "legacy": bool(row[4]), "stake": row[5],
                "payout": row[6], "has_details": bool(row[7] and row[7] != "{}"),
                "original_profit": row[8], "adjustment": row[9], "adjustment_reason": row[10]}

    @staticmethod
    def _history_select():
        return """SELECT r.round_key, r.game_type, r.profit, r.settled_at, r.legacy,
                   COALESCE(d.stake, e.stake), COALESCE(d.payout, e.payout), d.details,
                   r.original_profit, r.adjustment, r.adjustment_reason
            FROM results r
            LEFT JOIN table_round_details d ON d.round_key = r.round_key AND d.user_id = r.user_id
            LEFT JOIN table_game_escrow e ON e.round_key = r.round_key AND e.user_id = r.user_id
            WHERE r.user_id = ?"""

    def _history(self, user_id: str, game_type: str | None, limit: int, offset: int):
        self._validate_paging(limit, offset)
        parameters = [int(user_id)]
        condition = ""
        if game_type:
            condition = " AND r.game_type = ?"
            parameters.append(game_type)
        with self._transaction() as connection:
            total = connection.execute(self._results_cte() +
                "SELECT COUNT(*) FROM results r WHERE r.user_id = ?" + condition, parameters).fetchone()[0]
            rows = connection.execute(self._results_cte() + self._history_select() + condition +
                " ORDER BY r.settled_at DESC, r.round_key DESC LIMIT ? OFFSET ?", [*parameters, limit, offset]).fetchall()
        return {"entries": [self._history_entry(row) for row in rows], "total": total,
                "has_more": offset + len(rows) < total, "timezone": "Asia/Shanghai"}

    async def history(self, user_id: str, game_type: str | None = None, limit: int = 20, offset: int = 0):
        return await asyncio.to_thread(self._history, user_id, game_type, limit, offset)

    def _round_detail(self, user_id: str, round_key: str):
        with self._transaction() as connection:
            row = connection.execute(self._results_cte() + self._history_select() +
                " AND r.round_key = ?", (int(user_id), round_key)).fetchone()
        if row is None:
            return None
        entry = self._history_entry(row)
        entry["details"] = json.loads(row[7]) if row[7] else {}
        return entry

    async def round_detail(self, user_id: str, round_key: str):
        return await asyncio.to_thread(self._round_detail, user_id, round_key)

    @staticmethod
    def _transaction_timestamp(value):
        if not value:
            return None
        try:
            moment = datetime.fromisoformat(str(value))
        except ValueError:
            return value
        # SQLite CURRENT_TIMESTAMP 写入 UTC，无时区后缀时补齐，避免浏览器当成本地时间。
        return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).isoformat()

    def _transactions(self, user_id: str, limit: int, offset: int):
        self._validate_paging(limit, offset)
        with self._transaction() as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(coin_transactions)")}
            # 标识符只在固定白名单中选取，兼容正式表和旧测试表。
            identifier = "transaction_id" if "transaction_id" in columns else "id" if "id" in columns else "rowid"
            timestamp = "timestamp" if "timestamp" in columns else "NULL"
            total = connection.execute("SELECT COUNT(*) FROM coin_transactions WHERE user_id = ?", (int(user_id),)).fetchone()[0]
            rows = connection.execute(f"SELECT {identifier}, amount, reason, {timestamp} FROM coin_transactions "
                f"WHERE user_id = ? ORDER BY {identifier} DESC LIMIT ? OFFSET ?", (int(user_id), limit, offset)).fetchall()
            balance = connection.execute("SELECT balance FROM user_coins WHERE user_id = ?", (int(user_id),)).fetchone()
        return {"entries": [{"id": row[0], "amount": row[1], "reason": row[2],
                             "timestamp": self._transaction_timestamp(row[3])} for row in rows],
                "total": total, "has_more": offset + len(rows) < total,
                "balance": balance[0] if balance else None, "timezone": "Asia/Shanghai"}

    async def transactions(self, user_id: str, limit: int = 20, offset: int = 0):
        return await asyncio.to_thread(self._transactions, user_id, limit, offset)

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
