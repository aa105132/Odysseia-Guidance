"""Bounded, unverified client-save summaries. Never reads wallet or identity tables.

The board indexes snapshots, not increments. Legacy migration advances a durable
primary-key cursor by at most BACKFILL_BATCH rows per board read; until complete,
boards contain only indexed accounts. Old accounts use a generic display name
until they authenticate. The daily metric is lifetime daily-run wins.
"""

import base64
import binascii
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import math
import re
import sqlite3
from urllib.parse import unquote

from fastapi import HTTPException

MAX_ENCODED = 2 * 1024 * 1024
MAX_DEPTH = 32
MAX_NODES = 100_000
BACKFILL_BATCH = 8
MAX_COUNT = 10**12
METRICS = {"endless": "endless", "wins": "wins", "collection": "collection", "daily": "daily"}
STAT_FIELDS = {
    "play_time": "playTime", "battles": "battles", "classic_played": "classicSessionsPlayed",
    "classic_wins": "sessionsWon", "daily_played": "dailyRunSessionsPlayed",
    "daily_wins": "dailyRunSessionsWon", "endless_played": "endlessSessionsPlayed",
    "endless_wave": "highestEndlessWave", "pokemon_caught": "pokemonCaught",
    "pokemon_hatched": "pokemonHatched", "shiny_caught": "shinyPokemonCaught",
    "trainers_defeated": "trainersDefeated", "eggs_pulled": "eggsPulled",
}


def daily_seed(now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    day = now.date().isoformat()
    reset = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), timezone.utc)
    return {"date": day, "seed": base64.b64encode(day.encode("ascii")).decode("ascii"),
            "resets_at": reset.isoformat()}


def display_name(profile):
    value = profile.get("username") if isinstance(profile, dict) else None
    if not isinstance(value, str):
        return "训练家"
    return "".join(c for c in value[:256] if c.isprintable())[:64].strip() or "训练家"


def _integer(text):
    if len(text) > 128:
        raise ValueError("oversized number")
    return int(text)


def _invalid_constant(value):
    raise ValueError("nonfinite number")


def decode(value, *, plain=False):
    """Reject size/depth before JSON allocation; bound traversal after decoding."""
    if not isinstance(value, str) or len(value) > MAX_ENCODED:
        raise ValueError("encoded size")
    text = base64.b64decode(value, validate=True).decode("utf-8", errors="strict")
    if not plain:
        if re.search(r"%(?![0-9a-fA-F]{2})", text):
            raise ValueError("invalid percent escape")
        text = unquote(text, encoding="utf-8", errors="strict")
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                raise ValueError("JSON depth")
        elif char in "]}":
            depth -= 1
    result = json.loads(text, parse_int=_integer, parse_constant=_invalid_constant)
    pending, count = [result], 0
    while pending:
        item = pending.pop()
        count += 1
        if count > MAX_NODES:
            raise ValueError("JSON nodes")
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, str):
            if len(item) > 65536 or any(0xD800 <= ord(c) <= 0xDFFF for c in item):
                raise ValueError("invalid string")
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("nonfinite number")
    return result


class Sanitizer:
    def __init__(self):
        self.bad = False

    def number(self, value=0, maximum=MAX_COUNT):
        if type(value) not in (int, float) or not 0 <= value <= maximum:
            self.bad = True
            return 0
        return int(value)

    def timestamp(self, value):
        # Milliseconds since Unix epoch; zero/missing is unknown, never a date.
        if value is None or type(value) is int and value == 0:
            return None
        if type(value) not in (int, float) or not 0 < value <= 253402300799999:
            self.bad = True
            return None
        return int(value)

    def mapping(self, value, cap):
        if not isinstance(value, dict) or len(value) > cap:
            self.bad = True
            return {}
        return value

    def array(self, value, cap):
        if not isinstance(value, list):
            self.bad = True
            return []
        if len(value) > cap:
            self.bad = True
        return value[:cap]

    def text(self, value, cap):
        if not isinstance(value, str) or not value or len(value) > cap or not value.isprintable():
            self.bad = True
            return None
        return value

    def positive_attr(self, value):
        if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 128:
            return int(value) > 0
        if type(value) is int and 0 <= value < 2**426:
            return value > 0
        self.bad = True
        return False


def system_summary(entries):
    stats = dict.fromkeys([*STAT_FIELDS, "collected_species", "seen_species", "achievements", "eggs"], 0)
    achievements, eggs, version = [], [], None
    s = Sanitizer()
    present = "data_Guest" in entries
    if not present:
        return stats, achievements, eggs, version, False, False
    try:
        data = decode(entries["data_Guest"])
        if not isinstance(data, dict):
            raise ValueError("system object")
    except (ValueError, UnicodeError, binascii.Error, RecursionError):
        return stats, achievements, eggs, version, False, True
    game_stats = s.mapping(data.get("gameStats", {}), 256)
    for output, source in STAT_FIELDS.items():
        stats[output] = s.number(game_stats.get(source, 0))
    for species, dex in s.mapping(data.get("dexData", {}), 10000).items():
        if not species.isascii() or not species.isdecimal() or len(species) > 8:
            s.bad = True
            continue
        dex = s.mapping(dex, 64)
        stats["collected_species"] += s.positive_attr(dex.get("caughtAttr", 0))
        stats["seen_species"] += s.positive_attr(dex.get("seenAttr", 0))
    for name, timestamp in s.mapping(data.get("achvUnlocks", {}), 1024).items():
        name = s.text(name, 128)
        timestamp = s.timestamp(timestamp)
        if name is not None and timestamp is not None:
            achievements.append({"id": name, "timestamp": timestamp})
    achievements.sort(key=lambda item: item["id"])
    for egg in s.array(data.get("eggs", []), 1024):
        if not isinstance(egg, dict):
            s.bad = True
            continue
        eggs.append({"tier": s.number(egg.get("tier", 0), 4),
                     "hatch_waves": s.number(egg.get("hatchWaves", 0), 100000),
                     "timestamp": s.timestamp(egg.get("timestamp"))})
    stats["achievements"], stats["eggs"] = len(achievements), len(eggs)
    if data.get("gameVersion") is not None:
        version = s.text(data["gameVersion"], 64)
    return stats, achievements, eggs, version, not s.bad, s.bad


def _session(value, s):
    if not isinstance(value, dict):
        raise ValueError("session object")
    party = []
    for pokemon in s.array(value.get("party", []), 6):
        if not isinstance(pokemon, dict):
            s.bad = True
            continue
        party.append({"species": s.number(pokemon.get("species", 0), 1000000),
                      "level": s.number(pokemon.get("level", 0), 1000000)})
    return {"mode": s.number(value.get("gameMode", 0), 4),
            "wave": s.number(value.get("waveIndex", 0), 1000000),
            "play_time": s.number(value.get("playTime", 0)),
            "timestamp": s.timestamp(value.get("timestamp")), "party": party}


def profile_summary(entries, revision=0, updated_at=None):
    stats, achievements, eggs, version, _, warning = system_summary(entries)
    slots, history = [], []
    s = Sanitizer()
    for slot in range(5):
        key = f"sessionData{slot if slot else ''}_Guest"
        if key not in entries:
            continue
        try:
            slots.append({"slot": slot, **_session(decode(entries[key]), s)})
        except (ValueError, UnicodeError, binascii.Error, RecursionError):
            s.bad = True
    if "runHistoryData_Guest" in entries:
        try:
            raw = entries["runHistoryData_Guest"]
            records = {} if raw == "" else decode(raw)
            if not isinstance(records, dict):
                raise ValueError("history object")
            if len(records) > 25:
                s.bad = True
            # Numeric millisecond IDs only; sort numerically, newest first.
            ids = [key for key in records if key.isascii() and key.isdecimal() and len(key) <= 15
                   and 0 < int(key) <= 253402300799999]
            if len(ids) != len(records):
                s.bad = True
            for key in sorted(ids, key=int, reverse=True)[:25]:
                try:
                    item = records[key]
                    if not isinstance(item, dict) or type(item.get("isVictory")) is not bool:
                        raise ValueError("history record")
                    history.append({"id": key, **_session(item.get("entry"), s), "victory": item["isVictory"]})
                except ValueError:
                    s.bad = True
        except (ValueError, UnicodeError, binascii.Error, RecursionError):
            s.bad = True
    daily = {**daily_seed(), "completed": False}
    if "daily" in entries:
        try:
            completed = decode(entries["daily"], plain=True)
            if not isinstance(completed, list) or len(completed) > 4096 or any(not isinstance(x, str) for x in completed):
                raise ValueError("daily array")
            daily["completed"] = daily["seed"] in completed
        except (ValueError, UnicodeError, binascii.Error, RecursionError):
            s.bad = True
    return {"trust": "client-save", "revision": revision, "updated_at": updated_at,
            "has_save": "data_Guest" in entries,
            "parse_warning": "Some client-save data was invalid or exceeded summary limits." if warning or s.bad else None,
            "stats": stats, "slots": slots, "history": history, "achievements": achievements,
            "eggs": eggs, "daily": daily, "game_version": version}


def ensure_schema(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS pokerogue_cloud_saves (
        user_id TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK (revision >= 1),
        entries_json TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS pokerogue_records (
        user_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, username TEXT NOT NULL,
        valid INTEGER NOT NULL, endless INTEGER NOT NULL, wins INTEGER NOT NULL,
        collection INTEGER NOT NULL, daily INTEGER NOT NULL, updated_at TEXT NOT NULL)""")
    for metric in METRICS:
        connection.execute(f"CREATE INDEX IF NOT EXISTS pokerogue_records_{metric} "
                           f"ON pokerogue_records(valid, {metric} DESC, user_id ASC)")
    connection.execute("""CREATE TABLE IF NOT EXISTS pokerogue_records_migration (
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1), cursor TEXT NOT NULL, done INTEGER NOT NULL)""")
    connection.execute("INSERT OR IGNORE INTO pokerogue_records_migration VALUES (1, '', 0)")


def index_snapshot(connection, user_id, revision, entries, updated_at, username=None):
    stats, _, _, _, valid, _ = system_summary(entries)
    connection.execute("""INSERT INTO pokerogue_records
        (user_id, revision, username, valid, endless, wins, collection, daily, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET revision=excluded.revision,
        username=CASE WHEN ? IS NULL THEN pokerogue_records.username ELSE excluded.username END,
        valid=excluded.valid, endless=excluded.endless, wins=excluded.wins,
        collection=excluded.collection, daily=excluded.daily, updated_at=excluded.updated_at""",
        (user_id, revision, username or "训练家", int(valid), stats["endless_wave"], stats["classic_wins"],
         stats["collected_species"], stats["daily_wins"], updated_at, username))


def _backfill(connection):
    cursor, done = connection.execute("SELECT cursor, done FROM pokerogue_records_migration WHERE singleton=1").fetchone()
    if done:
        return
    rows = connection.execute("""SELECT s.user_id, s.revision, s.entries_json, s.updated_at, r.revision
        FROM pokerogue_cloud_saves s LEFT JOIN pokerogue_records r ON r.user_id=s.user_id
        WHERE s.user_id > ? ORDER BY s.user_id ASC LIMIT ?""", (cursor, BACKFILL_BATCH)).fetchall()
    for uid, revision, raw, updated_at, indexed in rows:
        if indexed == revision:
            continue
        try:
            entries = json.loads(raw) if len(raw) <= 8 * 1024 * 1024 else {}
            if not isinstance(entries, dict):
                entries = {}
        except (ValueError, RecursionError):
            entries = {}
        index_snapshot(connection, uid, revision, entries, updated_at)
    connection.execute("UPDATE pokerogue_records_migration SET cursor=?, done=? WHERE singleton=1",
                       (rows[-1][0] if rows else cursor, int(len(rows) < BACKFILL_BATCH)))


def database_read(path_provider, user_id, username, *, metric=None, limit=20, offset=0):
    try:
        with closing(sqlite3.connect(path_provider(), timeout=15, isolation_level=None)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                ensure_schema(connection)
                if metric is None:
                    row = connection.execute("SELECT revision, entries_json, updated_at FROM pokerogue_cloud_saves WHERE user_id=?",
                                             (user_id,)).fetchone()
                    entries = json.loads(row[1]) if row else {}
                    if row:
                        index_snapshot(connection, user_id, row[0], entries, row[2], username)
                    result = profile_summary(entries, row[0] if row else 0, row[2] if row else None)
                else:
                    column = METRICS[metric]  # Only a server allowlisted SQL identifier.
                    _backfill(connection)
                    # A legacy caller need not wait for the migration cursor to
                    # reach them. At most one additional primary-key lookup/decode.
                    legacy_self = connection.execute("""SELECT s.revision, s.entries_json, s.updated_at
                        FROM pokerogue_cloud_saves s LEFT JOIN pokerogue_records r ON r.user_id=s.user_id
                        WHERE s.user_id=? AND r.user_id IS NULL""", (user_id,)).fetchone()
                    if legacy_self:
                        index_snapshot(connection, user_id, legacy_self[0], json.loads(legacy_self[1]),
                                       legacy_self[2], username)
                    connection.execute("UPDATE pokerogue_records SET username=? WHERE user_id=?", (username, user_id))
                    rows = connection.execute(f"SELECT user_id, username, {column} FROM pokerogue_records "
                                              f"WHERE valid=1 ORDER BY {column} DESC, user_id ASC LIMIT ? OFFSET ?",
                                              (limit, offset)).fetchall()
                    entries = [{"rank": offset + i + 1, "username": name, "value": value, "is_self": uid == user_id}
                               for i, (uid, name, value) in enumerate(rows)]
                    own = connection.execute(f"SELECT username, {column} FROM pokerogue_records WHERE user_id=? AND valid=1",
                                             (user_id,)).fetchone()
                    own_entry = None
                    if own:
                        rank = 1 + connection.execute(f"SELECT COUNT(*) FROM pokerogue_records WHERE valid=1 AND "
                                                      f"({column}>? OR ({column}=? AND user_id<?))",
                                                      (own[1], own[1], user_id)).fetchone()[0]
                        own_entry = {"rank": rank, "username": own[0], "value": own[1], "is_self": True}
                    total = connection.execute("SELECT COUNT(*) FROM pokerogue_records WHERE valid=1").fetchone()[0]
                    result = {"trust": "client-save", "metric": metric, "entries": entries, "self": own_entry,
                              "total": total, "has_more": offset + len(entries) < total,
                              "updated_at": datetime.now(timezone.utc).isoformat()}
                connection.commit()
                return result
            except BaseException:
                connection.rollback()
                raise
    except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="Cloud save storage temporarily unavailable") from exc
