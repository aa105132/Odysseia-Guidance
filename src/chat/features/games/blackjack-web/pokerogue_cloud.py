"""Account-scoped PokéRogue saves; no upstream login, tokens, or wallet access.

Wire with create_pokerogue_router(get_current_user_profile, db_path_provider).
GET/PUT /api/pokerogue/save return {"revision": int, "entries": {str: str}}.
PUT replaces the entire snapshot, including deletions, at the supplied revision.
A stale PUT returns 409 with detail.current_revision; clients must fetch/resolve
before retrying, not blindly overwrite the newer snapshot.

Only the local game's storage keys below are accepted. Server integrators may
supply an explicit allowed_keys collection when a pinned game build needs more
keys; never populate that collection from a request or include auth/token keys.
Both streamed request bytes and serialized entry bytes are limited to 8 MiB by
default (UTF-8), with at most 128 keys of at most 128 characters each.
"""

import asyncio
from contextlib import closing
from datetime import datetime, timezone
import json
import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from . import pokerogue_records as records


MAX_SAVE_BYTES = 8 * 1024 * 1024
# Verified against upstream src/account.ts, system/game-data.ts, utils/data.ts,
# settings-manager.ts, move-touch-controls-handler.ts and i18n.ts (Guest mode).
_SAVE_BASES = {"data", "sessionData", *(f"sessionData{i}" for i in range(5))}
DEFAULT_ALLOWED_KEYS = frozenset({
    *_SAVE_BASES, *(f"{key}_Guest" for key in _SAVE_BASES),
    *(f"{key}_Guest_bak" for key in _SAVE_BASES),
    "settings", "tutorials", "seenDialogues", "mappingConfigs", "daily", "prLang",
    "runHistoryData", "runHistoryData_Guest", "starterPrefs_Guest",
    "settingsGamepad", "touchControlPositionsLandscape", "touchControlPositionsPortrait",
    *(f"touchControl/positions/{orientation}-{side}"
      for orientation in ("landscape", "portrait") for side in ("primary", "secondary")),
})
StorageKey = Annotated[StrictStr, Field(min_length=1, max_length=128)]


class SaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: Annotated[StrictInt, Field(ge=0, le=2**63 - 2)]
    entries: Annotated[dict[StorageKey, StrictStr], Field(max_length=128)]


def _account_id(profile):
    # Same server-supplied user_id profile contract as farm_api. No request field
    # (including query parameters or entry contents) participates in identity.
    value = profile.get("user_id") if isinstance(profile, dict) else None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise HTTPException(status_code=401, detail="Authentication required")
    text = str(value)
    if not text.isascii() or not text.isdecimal() or not 1 <= len(text) <= 19 or int(text) < 1:
        raise HTTPException(status_code=401, detail="Authentication required")
    return str(int(text))


def _database_save(path_provider, user_id, save=None, encoded_entries=None, username=None):
    """All SQLite work runs in a worker; each request owns its connection."""
    try:
        with closing(sqlite3.connect(path_provider(), timeout=15, isolation_level=None)) as connection:
            if save is not None:
                # Serialize the read/compare/write across connections AND workers.
                connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS pokerogue_cloud_saves (
                        user_id TEXT PRIMARY KEY,
                        revision INTEGER NOT NULL CHECK (revision >= 1),
                        entries_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                row = connection.execute(
                    "SELECT revision, entries_json FROM pokerogue_cloud_saves WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
                revision = row[0] if row else 0
                if save is None:
                    return {"revision": revision, "entries": json.loads(row[1]) if row else {}}
                if save.revision != revision:
                    raise HTTPException(status_code=409, detail={
                        "message": "Cloud save changed; fetch the latest save before retrying",
                        "current_revision": revision,
                    })
                revision += 1
                records.ensure_schema(connection)
                updated_at = datetime.now(timezone.utc).isoformat()
                connection.execute("""
                    INSERT INTO pokerogue_cloud_saves (user_id, revision, entries_json, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        revision = excluded.revision,
                        entries_json = excluded.entries_json,
                        updated_at = excluded.updated_at
                """, (user_id, revision, encoded_entries, updated_at))
                records.index_snapshot(connection, user_id, revision, save.entries, updated_at, username)
                connection.commit()
                return {"revision": revision, "entries": save.entries}
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
    except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
        # Do not return exception text: SQLite/provider errors may reveal paths.
        raise HTTPException(status_code=503, detail="Cloud save storage temporarily unavailable") from exc


async def _read_save(request, allowed_keys, max_bytes):
    # Content-Length is only an early rejection, never the enforcement boundary.
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            length = int(declared)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from None
        if length < 0:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")
        if length > max_bytes:
            raise HTTPException(status_code=413, detail="Cloud save exceeds byte limit")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_bytes:
            raise HTTPException(status_code=413, detail="Cloud save exceeds byte limit")
        body.extend(chunk)
    try:
        save = SaveRequest.model_validate_json(body)
        if not save.entries.keys() <= allowed_keys:
            unknown = ", ".join(sorted(save.entries.keys() - allowed_keys)[:8])
            raise HTTPException(status_code=422, detail=f"Unknown PokéRogue storage key: {unknown}")
        encoded = json.dumps(save.entries, ensure_ascii=False, separators=(",", ":"))
        size = len(encoded.encode("utf-8"))
    except (ValidationError, ValueError, UnicodeError):
        raise HTTPException(status_code=422, detail="Invalid cloud save JSON or fields") from None
    if size > max_bytes:
        raise HTTPException(status_code=413, detail="Cloud save exceeds byte limit")
    return save, encoded


def create_pokerogue_router(
    get_current_user_profile,
    db_path_provider,
    *,
    allowed_keys=DEFAULT_ALLOWED_KEYS,
    max_bytes=MAX_SAVE_BYTES,
):
    """Create authenticated routes; resolve the existing DB path every request.

    allowed_keys and max_bytes are trusted server configuration, not API inputs.
    The dependency must return a profile dict with a positive numeric user_id.
    """
    allowed_keys = frozenset(allowed_keys)
    if len(allowed_keys) > 128 or any(not isinstance(key, str) or not 1 <= len(key) <= 128 for key in allowed_keys):
        raise ValueError("Invalid server storage allowlist")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    router = APIRouter(prefix="/api/pokerogue", tags=["PokéRogue"])

    @router.get("/save")
    async def get_save(response: Response, user=Depends(get_current_user_profile)):
        user_id = _account_id(user)
        response.headers["Cache-Control"] = "no-store"
        return await asyncio.to_thread(_database_save, db_path_provider, user_id)

    @router.put("/save")
    async def put_save(request: Request, response: Response, user=Depends(get_current_user_profile)):
        user_id = _account_id(user)
        save, encoded = await _read_save(request, allowed_keys, max_bytes)
        response.headers["Cache-Control"] = "no-store"
        return await asyncio.to_thread(_database_save, db_path_provider, user_id, save, encoded, records.display_name(user))

    @router.get("/profile")
    async def get_profile(response: Response, user=Depends(get_current_user_profile)):
        user_id = _account_id(user)
        response.headers["Cache-Control"] = "no-store"
        return await asyncio.to_thread(records.database_read, db_path_provider, user_id, records.display_name(user))

    @router.get("/leaderboard")
    async def get_leaderboard(
        response: Response,
        metric: Literal["endless", "wins", "collection", "daily"] = "endless",
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0, le=10000)] = 0,
        user=Depends(get_current_user_profile),
    ):
        user_id = _account_id(user)
        response.headers["Cache-Control"] = "no-store"
        return await asyncio.to_thread(records.database_read, db_path_provider, user_id,
                                       records.display_name(user), metric=metric, limit=limit, offset=offset)

    @router.get("/daily-seed")
    async def get_daily_seed(response: Response, user=Depends(get_current_user_profile)):
        _account_id(user)
        response.headers["Cache-Control"] = "no-store"
        return records.daily_seed()

    return router
