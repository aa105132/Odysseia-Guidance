"""用户灵石红包：托管、领取和退款均在同一 SQLite 事务中完成。"""

import asyncio
from contextlib import contextmanager
import hashlib
import hmac
import secrets
import sqlite3
import time
from typing import Callable
import uuid

from src.chat.utils.database import chat_db_manager


class RedEnvelopeError(ValueError):
    """可直接向用户展示的红包业务错误。"""

    def __init__(self, message: str, code: str = "invalid"):
        self.code = code
        super().__init__(message)


class RedEnvelopeService:
    MAX_AMOUNT = 1_000_000
    MAX_COUNT = 100
    EXPIRES_SECONDS = 24 * 60 * 60
    PUBLISH_TIMEOUT_SECONDS = 5 * 60
    KINDS = {"normal", "lucky", "password"}

    def __init__(self, db_path: str | None = None, clock: Callable = time.time):
        self._db_path = db_path
        self.clock = clock

    @property
    def db_path(self):
        return self._db_path or chat_db_manager.db_path

    @contextmanager
    def _transaction(self):
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            # 与其他灵石扣款共用 SQLite 写锁，防止并发发包或领取导致透支。
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS coin_red_envelopes (
                    id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    sender_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER,
                    kind TEXT NOT NULL CHECK(kind IN ('normal', 'lucky', 'password')),
                    total_amount INTEGER NOT NULL CHECK(total_amount > 0),
                    remaining_amount INTEGER NOT NULL CHECK(remaining_amount >= 0),
                    count INTEGER NOT NULL CHECK(count > 0),
                    remaining_count INTEGER NOT NULL CHECK(remaining_count >= 0),
                    password_hash TEXT,
                    greeting TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active', 'completed', 'expired', 'cancelled')),
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    refunded_amount INTEGER NOT NULL DEFAULT 0
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS coin_red_envelope_claims (
                    envelope_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL CHECK(amount > 0),
                    claimed_at REAL NOT NULL,
                    PRIMARY KEY(envelope_id, user_id)
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS red_envelope_expiry ON coin_red_envelopes(status, expires_at)")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _public(row):
        if row is None:
            return None
        data = dict(row)
        data.pop("password_hash", None)
        return data

    @staticmethod
    def _password_hash(envelope_id: str, password: str) -> str:
        return hashlib.sha256(f"{envelope_id}:{password.strip()}".encode()).hexdigest()

    @staticmethod
    def _credit(connection, user_id: int, amount: int, reason: str) -> int:
        connection.execute(
            "INSERT INTO user_coins (user_id, balance) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET balance = balance + excluded.balance",
            (user_id, amount),
        )
        connection.execute(
            "INSERT INTO coin_transactions (user_id, amount, reason) VALUES (?, ?, ?)",
            (user_id, amount, reason),
        )
        return connection.execute("SELECT balance FROM user_coins WHERE user_id = ?", (user_id,)).fetchone()[0]

    @classmethod
    def _validate(cls, sender_id, guild_id, channel_id, kind, total_amount, count, password, greeting):
        for value in (sender_id, guild_id, channel_id):
            if type(value) is not int or not 0 < value < 2**63:
                raise RedEnvelopeError("红包只能在服务器文字频道中发送。")
        if kind not in cls.KINDS:
            raise RedEnvelopeError("不支持的红包类型。")
        if type(total_amount) is not int or not 1 <= total_amount <= cls.MAX_AMOUNT:
            raise RedEnvelopeError(f"红包总金额须为 1～{cls.MAX_AMOUNT:,} 的整数。")
        if type(count) is not int or not 1 <= count <= cls.MAX_COUNT:
            raise RedEnvelopeError(f"红包份数须为 1～{cls.MAX_COUNT} 的整数。")
        if total_amount < count:
            raise RedEnvelopeError("每份红包至少需要 1 灵石，总金额不能少于份数。")
        if kind != "lucky" and total_amount % count:
            raise RedEnvelopeError("普通红包和口令红包为等额分配，总金额必须能被份数整除。")
        if kind == "password" and (not isinstance(password, str) or not 1 <= len(password.strip()) <= 50):
            raise RedEnvelopeError("口令红包需要 1～50 个字符的口令。")
        if not isinstance(greeting, str) or not 1 <= len(greeting.strip()) <= 100:
            raise RedEnvelopeError("祝福语需要 1～100 个字符。")

    async def create(self, sender_id: int, guild_id: int, channel_id: int, kind: str,
                     total_amount: int, count: int, password: str | None = None,
                     greeting: str = "恭喜发财，大吉大利！", request_id: str | None = None):
        self._validate(sender_id, guild_id, channel_id, kind, total_amount, count, password, greeting)
        return await asyncio.to_thread(
            self._create, sender_id, guild_id, channel_id, kind, total_amount, count,
            password, greeting.strip(), str(request_id) if request_id is not None else uuid.uuid4().hex,
        )

    def _create(self, sender_id, guild_id, channel_id, kind, total_amount, count, password, greeting, request_id):
        with self._transaction() as connection:
            previous = connection.execute("SELECT * FROM coin_red_envelopes WHERE request_id = ?", (request_id,)).fetchone()
            if previous:
                expected = (sender_id, guild_id, channel_id, kind, total_amount, count, greeting)
                actual = tuple(previous[key] for key in ("sender_id", "guild_id", "channel_id", "kind", "total_amount", "count", "greeting"))
                same_password = kind != "password" or hmac.compare_digest(previous["password_hash"], self._password_hash(previous["id"], password))
                if actual != expected or not same_password:
                    raise RedEnvelopeError("该发送请求已经用于另一份红包。", "request_conflict")
                return self._public(previous)
            envelope_id = uuid.uuid4().hex
            now = self.clock()
            updated = connection.execute(
                "UPDATE user_coins SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
                (total_amount, sender_id, total_amount),
            ).rowcount
            if updated != 1:
                raise RedEnvelopeError(f"余额不足，发送该红包需要 {total_amount:,} 灵石。", "insufficient_balance")
            connection.execute("INSERT INTO coin_transactions (user_id, amount, reason) VALUES (?, ?, ?)",
                               (sender_id, -total_amount, f"发红包 {envelope_id} 托管"))
            connection.execute("""
                INSERT INTO coin_red_envelopes
                (id, request_id, sender_id, guild_id, channel_id, kind, total_amount,
                 remaining_amount, count, remaining_count, password_hash, greeting, status, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """, (envelope_id, request_id, sender_id, guild_id, channel_id, kind, total_amount,
                  total_amount, count, count,
                  self._password_hash(envelope_id, password) if kind == "password" else None,
                  greeting, now, now + self.EXPIRES_SECONDS))
            return self._public(connection.execute("SELECT * FROM coin_red_envelopes WHERE id = ?", (envelope_id,)).fetchone())

    def _refund(self, connection, row, status):
        if row["status"] != "active":
            return self._public(row)
        remaining = row["remaining_amount"]
        if remaining:
            self._credit(connection, row["sender_id"], remaining, f"红包 {row['id']} {'过期' if status == 'expired' else '发送失败'}退款")
        connection.execute(
            "UPDATE coin_red_envelopes SET status = ?, refunded_amount = ?, remaining_amount = 0 WHERE id = ?",
            (status, remaining, row["id"]),
        )
        return self._public(connection.execute("SELECT * FROM coin_red_envelopes WHERE id = ?", (row["id"],)).fetchone())

    async def claim(self, envelope_id: str, user_id: int, guild_id: int, channel_id: int,
                    password: str | None = None):
        result = await asyncio.to_thread(self._claim, envelope_id, user_id, guild_id, channel_id, password)
        # 过期退款必须先提交，再向调用者返回业务错误。
        if isinstance(result, RedEnvelopeError):
            raise result
        return result

    def _claim(self, envelope_id, user_id, guild_id, channel_id, password):
        if type(user_id) is not int or not 0 < user_id < 2**63:
            raise RedEnvelopeError("领取用户无效。")
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM coin_red_envelopes WHERE id = ?", (envelope_id,)).fetchone()
            if not row:
                raise RedEnvelopeError("找不到这份红包。", "not_found")
            if row["guild_id"] != guild_id or row["channel_id"] != channel_id:
                raise RedEnvelopeError("请在发送红包的原频道领取。", "wrong_channel")
            previous = connection.execute("SELECT amount FROM coin_red_envelope_claims WHERE envelope_id = ? AND user_id = ?", (envelope_id, user_id)).fetchone()
            if previous:
                raise RedEnvelopeError(f"你已经领过这份红包，获得了 {previous['amount']:,} 灵石。", "already_claimed")
            if row["status"] != "active":
                raise RedEnvelopeError("红包已被领完。" if row["status"] == "completed" else "红包已结束，剩余灵石已退回。", row["status"])
            now = self.clock()
            if now >= row["expires_at"] or (
                row["message_id"] is None and now >= row["created_at"] + self.PUBLISH_TIMEOUT_SECONDS
            ):
                self._refund(connection, row, "expired")
                return RedEnvelopeError("红包已过期，剩余灵石已退回。", "expired")
            if row["message_id"] is None:
                raise RedEnvelopeError("红包尚未发送完成，请稍后重试。", "not_published")
            if row["kind"] == "password":
                if not isinstance(password, str) or not hmac.compare_digest(row["password_hash"], self._password_hash(envelope_id, password)):
                    raise RedEnvelopeError("口令不正确，请重新输入。", "wrong_password")
            if row["remaining_count"] == 1:
                amount = row["remaining_amount"]
            elif row["kind"] == "lucky":
                # 二倍均值，同时为后续每份至少保留 1 灵石。
                maximum = min(row["remaining_amount"] - row["remaining_count"] + 1,
                              2 * row["remaining_amount"] // row["remaining_count"])
                amount = secrets.randbelow(maximum) + 1
            else:
                amount = row["total_amount"] // row["count"]
            balance = self._credit(connection, user_id, amount, f"领取红包 {envelope_id}")
            connection.execute("INSERT INTO coin_red_envelope_claims VALUES (?, ?, ?, ?)", (envelope_id, user_id, amount, now))
            connection.execute("""
                UPDATE coin_red_envelopes SET remaining_amount = remaining_amount - ?,
                remaining_count = remaining_count - 1,
                status = CASE WHEN remaining_count = 1 THEN 'completed' ELSE 'active' END WHERE id = ?
            """, (amount, envelope_id))
            envelope = self._public(connection.execute("SELECT * FROM coin_red_envelopes WHERE id = ?", (envelope_id,)).fetchone())
            return {"amount": amount, "balance": balance, "envelope": envelope}

    async def attach_message(self, envelope_id: str, message_id: int):
        result = await asyncio.to_thread(self._attach_message, envelope_id, message_id)
        if isinstance(result, RedEnvelopeError):
            raise result
        return result

    def _attach_message(self, envelope_id, message_id):
        if type(message_id) is not int or not 0 < message_id < 2**63:
            raise RedEnvelopeError("红包消息编号无效。")
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM coin_red_envelopes WHERE id = ?", (envelope_id,)).fetchone()
            if not row:
                raise RedEnvelopeError("找不到这份红包。", "not_found")
            if row["message_id"] not in (None, message_id):
                raise RedEnvelopeError("红包已经绑定其他消息。")
            if row["status"] != "active":
                raise RedEnvelopeError("红包已经结束。")
            now = self.clock()
            if now >= row["expires_at"] or (
                row["message_id"] is None and now >= row["created_at"] + self.PUBLISH_TIMEOUT_SECONDS
            ):
                self._refund(connection, row, "expired")
                return RedEnvelopeError("红包发布超时，剩余灵石已退回。", "expired")
            connection.execute("UPDATE coin_red_envelopes SET message_id = ? WHERE id = ?", (message_id, envelope_id))
            return self._public(connection.execute("SELECT * FROM coin_red_envelopes WHERE id = ?", (envelope_id,)).fetchone())

    async def cancel(self, envelope_id: str):
        """发布失败时退款；已绑定消息或有人领取的红包不能按发布失败撤销。"""
        return await asyncio.to_thread(self._cancel, envelope_id)

    def _cancel(self, envelope_id):
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM coin_red_envelopes WHERE id = ?", (envelope_id,)).fetchone()
            if not row:
                raise RedEnvelopeError("找不到这份红包。", "not_found")
            if row["status"] != "active":
                return self._public(row)
            if row["message_id"] is not None or row["remaining_count"] != row["count"]:
                raise RedEnvelopeError("已经发布或有人领取的红包不能取消。")
            return self._refund(connection, row, "cancelled")

    async def expire_pending(self):
        """退款过期红包和发布中断的未绑定红包；重复调用不会重复入账。"""
        return await asyncio.to_thread(self._expire_pending)

    def _expire_pending(self):
        now = self.clock()
        with self._transaction() as connection:
            rows = connection.execute("""
                SELECT * FROM coin_red_envelopes WHERE status = 'active' AND
                (expires_at <= ? OR (message_id IS NULL AND created_at <= ?))
            """, (now, now - self.PUBLISH_TIMEOUT_SECONDS)).fetchall()
            return [self._refund(connection, row, "expired") for row in rows]

    async def get(self, envelope_id: str):
        return await asyncio.to_thread(self._get, envelope_id)

    async def initialize(self):
        """在扩展加载时建立红包账本，不接触已有余额。"""
        await asyncio.to_thread(self._initialize)

    def _initialize(self):
        with self._transaction():
            pass

    async def get_by_message(self, guild_id: int, channel_id: int, message_id: int):
        return await asyncio.to_thread(self._get_by_message, guild_id, channel_id, message_id)

    def _get_by_message(self, guild_id, channel_id, message_id):
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM coin_red_envelopes WHERE guild_id = ? AND channel_id = ? AND message_id = ?",
                (guild_id, channel_id, message_id),
            ).fetchone()
            return self._public(row)

    async def list_claims(self, envelope_id: str):
        return await asyncio.to_thread(self._list_claims, envelope_id)

    def _list_claims(self, envelope_id):
        with self._transaction() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT user_id, amount, claimed_at FROM coin_red_envelope_claims "
                "WHERE envelope_id = ? ORDER BY claimed_at, user_id", (envelope_id,),
            ).fetchall()]

    def _get(self, envelope_id):
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM coin_red_envelopes WHERE id = ?", (envelope_id,)).fetchone()
            return self._public(row)

    async def list_active(self):
        return await asyncio.to_thread(self._list_active)

    def _list_active(self):
        with self._transaction() as connection:
            return [self._public(row) for row in connection.execute("SELECT * FROM coin_red_envelopes WHERE status = 'active' AND message_id IS NOT NULL").fetchall()]


red_envelope_service = RedEnvelopeService()
