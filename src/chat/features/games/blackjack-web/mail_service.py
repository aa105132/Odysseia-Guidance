"""Private compensation inbox; only trusted server code may send mail."""

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib import import_module
import json
import sqlite3
import unicodedata


catalog = import_module("src.chat.features.games.blackjack-web.farm_catalog")
FarmService = import_module("src.chat.features.games.blackjack-web.farm_service").FarmService
MAX_ID = 2**63 - 1
MAX_AMOUNT = 1_000_000
MAX_MATERIAL_ENTRIES = 24
MAX_MATERIAL_QUANTITY = 10_000  # Per mail, including all crops/qualities and duplicates.
MAX_MATERIAL_JSON = 8192
QUALITY_LABELS = {"normal": "凡品", "spirit": "灵品", "celestial": "仙品"}
MESSAGE_FIELDS = ("id", "title", "body", "amount", "created_at", "read_at", "claimed_at")


class MailError(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def positive_id(value):
    if type(value) is not int or not 1 <= value <= MAX_ID:
        raise MailError("ID must be a positive integer")
    return value


def plain_text(value, name, maximum, *, multiline=False):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or not value.strip():
        raise MailError(f"{name} must contain 1..{maximum} characters")
    if any(c in "<>" or (unicodedata.category(c).startswith("C") and
                         not (multiline and c in "\n\r\t")) for c in value):
        raise MailError(f"{name} must be plain text")
    return value


def canonical_materials(materials):
    """Strict [{crop_id, quality, quantity}]; merge duplicates, sort for send identity."""
    if type(materials) is not list or len(materials) > MAX_MATERIAL_ENTRIES:
        raise MailError("Materials must be a list of at most 24 entries")
    counts = {}
    total = 0
    for item in materials:
        if type(item) is not dict or set(item) != {"crop_id", "quality", "quantity"}:
            raise MailError("Material must contain only crop_id, quality and quantity")
        crop_id, quality, quantity = item["crop_id"], item["quality"], item["quantity"]
        if type(crop_id) is not str or crop_id not in catalog.CROP_BY_ID:
            raise MailError("Unknown material crop_id")
        if type(quality) is not str or quality not in QUALITY_LABELS:
            raise MailError("Material quality must be normal, spirit or celestial")
        if type(quantity) is not int or not 1 <= quantity <= MAX_MATERIAL_QUANTITY:
            raise MailError("Material quantity must be an integer in 1..10000")
        total += quantity
        if total > MAX_MATERIAL_QUANTITY:
            raise MailError("Materials total must not exceed 10000")
        key = (crop_id, quality)
        counts[key] = counts.get(key, 0) + quantity
    return [{"crop_id": crop_id, "quality": quality, "quantity": quantity}
            for (crop_id, quality), quantity in sorted(counts.items())]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate material field")
        result[key] = value
    return result


def stored_materials(value):
    # Corrupted attachments never degrade to coin-only or informational mail.
    try:
        if type(value) is not str or len(value) > MAX_MATERIAL_JSON:
            raise ValueError("Invalid material JSON")
        return canonical_materials(json.loads(value, object_pairs_hook=_unique_object))
    except (ValueError, TypeError, RecursionError) as exc:
        raise MailError("Invalid stored mail materials", 409) from exc


class MailService:
    """Async API over short, serialized SQLite transactions on the supplied chat DB.

    Amounts are integer 0..1,000,000. Optional materials=[{crop_id, quality, quantity}]
    allows 0..24 entries, positive integer quantities, at most 10,000 units total.
    Crop IDs come from farm_catalog; qualities are normal/spirit/celestial. Duplicate
    crop/quality entries are merged and sorted for operation-key payload identity.
    Zero coins is claimable only with materials. Sending requires an existing wallet
    and a globally unique operation key. No HTTP endpoint accepts attachments.
    """

    def __init__(self, db_path):
        self.db_path = str(db_path)

    @contextmanager
    def _transaction(self):
        connection = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS game_mail (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_key TEXT NOT NULL UNIQUE,
                    user_id INTEGER NOT NULL CHECK(user_id > 0),
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    amount INTEGER NOT NULL CHECK(typeof(amount) = 'integer' AND amount BETWEEN 0 AND 1000000),
                    created_at TEXT NOT NULL,
                    read_at TEXT,
                    claimed_at TEXT,
                    coin_transaction_id INTEGER,
                    claimed_balance INTEGER,
                    materials TEXT NOT NULL DEFAULT '[]'
                )
            """)
            # Additive migration under the same write lock; legacy rows have no herbs.
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(game_mail)")}
            if "materials" not in columns:
                connection.execute("ALTER TABLE game_mail ADD COLUMN materials TEXT NOT NULL DEFAULT '[]'")
            connection.execute("CREATE INDEX IF NOT EXISTS game_mail_owner_id ON game_mail(user_id, id DESC)")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _message(row):
        materials = [{**item, "crop_name": catalog.CROP_BY_ID[item["crop_id"]]["name"],
                      "quality_label": QUALITY_LABELS[item["quality"]]}
                     for item in stored_materials(row["materials"])]
        return {**{key: row[key] for key in MESSAGE_FIELDS}, "materials": materials}

    @staticmethod
    def _owned(connection, user_id, mail_id):
        row = connection.execute("SELECT * FROM game_mail WHERE id = ? AND user_id = ?",
                                 (mail_id, user_id)).fetchone()
        if row is None:
            raise MailError("Mail not found", 404)
        return row

    @staticmethod
    def _balance(connection, user_id):
        row = connection.execute("SELECT balance FROM user_coins WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            raise MailError("Wallet account missing", 409)
        return row["balance"]

    def _send(self, operation_key, user_id, title, body, amount, materials=None):
        positive_id(user_id)
        plain_text(operation_key, "operation_key", 128)
        plain_text(title, "title", 120)
        plain_text(body, "body", 4000, multiline=True)
        if type(amount) is not int or not 0 <= amount <= MAX_AMOUNT:
            raise MailError("Amount must be an integer in 0..1000000")
        materials = canonical_materials([] if materials is None else materials)
        with self._transaction() as connection:
            existing = connection.execute("SELECT * FROM game_mail WHERE operation_key = ?",
                                          (operation_key,)).fetchone()
            if existing is not None:
                if (tuple(existing[key] for key in ("user_id", "title", "body", "amount")) !=
                        (user_id, title, body, amount) or stored_materials(existing["materials"]) != materials):
                    raise MailError("Operation key already used with different payload", 409)
                return self._message(existing)
            self._balance(connection, user_id)
            cursor = connection.execute("""INSERT INTO game_mail
                (operation_key, user_id, title, body, amount, created_at, materials) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (operation_key, user_id, title, body, amount, self._now(),
                 json.dumps(materials, sort_keys=True, separators=(",", ":"))))
            return self._message(self._owned(connection, user_id, cursor.lastrowid))

    async def send(self, operation_key, user_id, title, body, amount, materials=None):
        """Internal administrative entry point, never exposed by the HTTP router."""
        return await asyncio.to_thread(self._send, operation_key, user_id, title, body, amount, materials)

    def _list_messages(self, user_id, limit, before_id):
        positive_id(user_id)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise MailError("Limit must be an integer in 1..50")
        if before_id is not None:
            positive_id(before_id)
        with self._transaction() as connection:
            condition = "user_id = ?"
            parameters = [user_id]
            if before_id is not None:
                condition += " AND id < ?"
                parameters.append(before_id)
            rows = connection.execute(f"SELECT * FROM game_mail WHERE {condition} ORDER BY id DESC LIMIT ?",
                                      (*parameters, limit + 1)).fetchall()
            unread = connection.execute("SELECT COUNT(*) FROM game_mail WHERE user_id = ? AND read_at IS NULL",
                                        (user_id,)).fetchone()[0]
            pending = connection.execute("SELECT amount, materials FROM game_mail WHERE user_id = ? AND claimed_at IS NULL",
                                         (user_id,))
            # Validate before counting, including attachments on older pages.
            claimable = sum(bool(stored_materials(row["materials"])) or row["amount"] > 0 for row in pending)
            return {"messages": [self._message(row) for row in rows[:limit]],
                    "unread_count": unread, "claimable_count": claimable,
                    "next_before_id": rows[limit - 1]["id"] if len(rows) > limit else None}

    async def list_messages(self, user_id, limit=20, before_id=None):
        return await asyncio.to_thread(self._list_messages, user_id, limit, before_id)

    def _read(self, user_id, mail_id):
        positive_id(user_id)
        positive_id(mail_id)
        with self._transaction() as connection:
            self._owned(connection, user_id, mail_id)
            connection.execute("UPDATE game_mail SET read_at = COALESCE(read_at, ?) WHERE id = ? AND user_id = ?",
                               (self._now(), mail_id, user_id))
            return {"message": self._message(self._owned(connection, user_id, mail_id))}

    async def read(self, user_id, mail_id):
        return await asyncio.to_thread(self._read, user_id, mail_id)

    def _credit_materials(self, connection, user_id, materials, now):
        # Only farm_profiles is needed here. Never enter FarmService._transaction or
        # its public API: the mail's connection owns initialization and all writes.
        connection.execute("""CREATE TABLE IF NOT EXISTS farm_profiles (
            user_id INTEGER PRIMARY KEY, username TEXT NOT NULL, avatar_url TEXT NOT NULL,
            state TEXT NOT NULL, updated_at REAL NOT NULL)""")
        service = FarmService(self.db_path)
        row = connection.execute("SELECT * FROM farm_profiles WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            # The wallet already exists; {} uses the farm's uid/empty-avatar default
            # contract, including starter seeds, but no XP/levels/currency bonuses.
            farm = service._load(connection, user_id, now, profile={})
        else:
            # Preserve even old/unknown state fields and the farmer's real identity.
            farm = dict(row)
            farm["state"] = json.loads(farm["state"])
        inventory = farm["state"]["produce"]
        for item in materials:
            key = f"{item['crop_id']}:{item['quality']}"
            quantity = inventory.get(key, 0)
            if type(quantity) is not int or not 0 <= quantity <= MAX_ID - item["quantity"]:
                raise MailError("Farm inventory quantity out of range", 409)
            service._add_inventory(inventory, key, item["quantity"])
        service._save(connection, farm, now)

    def _claim(self, user_id, mail_id):
        positive_id(user_id)
        positive_id(mail_id)
        with self._transaction() as connection:
            row = self._owned(connection, user_id, mail_id)
            materials = stored_materials(row["materials"])
            balance = self._balance(connection, user_id)
            if row["claimed_at"] is not None:
                return {"message": self._message(row), "balance": balance, "already_claimed": True}
            amount = row["amount"]
            if type(amount) is not int or not 0 <= amount <= MAX_AMOUNT or not (amount or materials):
                raise MailError("Mail has no valid attachment", 409)
            if type(balance) is not int or balance > MAX_ID - amount:
                raise MailError("Wallet balance out of range", 409)
            now = self._now()
            updated = connection.execute("""UPDATE game_mail SET claimed_at = ?, read_at = COALESCE(read_at, ?)
                WHERE id = ? AND user_id = ? AND claimed_at IS NULL""", (now, now, mail_id, user_id)).rowcount
            if updated != 1:
                raise MailError("Mail claim conflict", 409)
            receipt_id = None
            if amount:
                if connection.execute("UPDATE user_coins SET balance = balance + ? WHERE user_id = ?",
                                      (amount, user_id)).rowcount != 1:
                    raise MailError("Wallet account missing", 409)
                receipt = connection.execute("""INSERT INTO coin_transactions (user_id, amount, reason)
                    VALUES (?, ?, ?)""", (user_id, amount, f"Game mail #{mail_id} compensation"))
                receipt_id = receipt.lastrowid
            if materials:
                self._credit_materials(connection, user_id, materials, datetime.fromisoformat(now).timestamp())
            connection.execute("""UPDATE game_mail SET coin_transaction_id = ?, claimed_balance = ?
                WHERE id = ? AND user_id = ?""", (receipt_id, balance + amount, mail_id, user_id))
            return {"message": self._message(self._owned(connection, user_id, mail_id)),
                    "balance": balance + amount, "already_claimed": False}

    async def claim(self, user_id, mail_id):
        return await asyncio.to_thread(self._claim, user_id, mail_id)
