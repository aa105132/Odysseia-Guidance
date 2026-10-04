"""Server-authoritative, zero-EV/full-health Showdown training duels.

Only battle_* SQLite tables are touched. Mount with create_pokerogue_battle_router.
Showdown state (including private pending choices and RNG) is persisted atomically;
no resident Node process, cloud saves, wallet operations or client-supplied damage.
"""
import asyncio
from contextlib import closing
from functools import lru_cache
import json
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import threading
import time
from typing import Annotated, Literal, Union

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, TypeAdapter

from .pokerogue_cloud import _account_id
from .pokerogue_records import display_name

SIM_DIR = Path(__file__).with_name("pokerogue-sim")
MAX_BODY_BYTES = 32 * 1024
MAX_OUTPUT_BYTES = 1_500_000
SIM_TIMEOUT = 10
IDLE_SECONDS = 600
LIFETIME_SECONDS = 7200
RETENTION_SECONDS = 3600
MAX_ROOMS = 512
MAX_TURNS = 200
MAX_EPOCHS = 800
RATE_LIMITS = {"admission": 20, "choose": 120, "poll": 180}
_SIM_SLOTS = threading.BoundedSemaphore(4)
RoomCode = Annotated[StrictStr, Field(pattern=r"^[0-9]{6}$", min_length=6, max_length=6)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Pokemon(StrictModel):
    species: Annotated[StrictInt, Field(ge=1, le=1025)]
    level: Annotated[StrictInt, Field(ge=1, le=100)]
    moves: Annotated[list[Annotated[StrictInt, Field(ge=1, le=1000)]], Field(min_length=1, max_length=4)]
    ability: Annotated[StrictInt, Field(ge=1, le=1000)]
    nature: Annotated[StrictStr, Field(min_length=1, max_length=20)]
    ivs: Annotated[list[Annotated[StrictInt, Field(ge=0, le=31)]], Field(min_length=6, max_length=6)]
    gender: Literal["M", "F", "N"]
    shiny: StrictBool


class Snapshot(StrictModel):
    version: Annotated[StrictInt, Field(ge=1, le=1)]
    team: Annotated[list[Pokemon], Field(min_length=1, max_length=6)]


class Create(StrictModel):
    op: Literal["create"]
    snapshot: Snapshot


class Join(StrictModel):
    op: Literal["join"]
    room: RoomCode
    snapshot: Snapshot


class State(StrictModel):
    op: Literal["state", "leave"]
    room: RoomCode


class Choose(StrictModel):
    op: Literal["choose"]
    room: RoomCode
    revision: Annotated[StrictInt, Field(ge=0, le=MAX_EPOCHS)]
    choice: Annotated[StrictStr, Field(pattern=r"^(move [1-4]|switch [1-6])$", max_length=8)]


_REQUEST = TypeAdapter(Annotated[Union[Create, Join, State, Choose], Field(discriminator="op")])


def _encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


@lru_cache(maxsize=4)
def _node_version(node):
    try:
        result = subprocess.run([node, "--version"], capture_output=True, timeout=2, check=True)
        return int(result.stdout.decode().strip().lstrip("v").split(".")[0]) >= 18
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def _node_path():
    bundled = SIM_DIR / "runtime/bin/node"
    return str(bundled) if bundled.is_file() else shutil.which("node")


def battle_available():
    """Optional startup gate; Node >=18 and the pinned installed dependency required."""
    node = _node_path()
    package = SIM_DIR / "node_modules/pokemon-showdown/package.json"
    try:
        return bool(node and (SIM_DIR / "worker.mjs").is_file()
                    and json.loads(package.read_text(encoding="utf-8"))["version"] == "0.11.10"
                    and (SIM_DIR / "node_modules/pokemon-showdown/dist/sim/index.js").is_file()
                    and _node_version(node))
    except (OSError, ValueError, KeyError):
        return False


def _simulate(payload):
    if not battle_available():
        raise HTTPException(503, "Battle simulator unavailable: install Node >=18 and run npm ci --ignore-scripts in pokerogue-sim")
    if not _SIM_SLOTS.acquire(blocking=False):
        raise HTTPException(503, "Battle simulator busy; retry shortly")
    process = None
    threads = []
    output = bytearray()
    overflow = threading.Event()
    try:
        encoded = _encode(payload).encode("utf-8")
        if len(encoded) > 2_000_000:
            raise HTTPException(503, "Battle state limit reached")
        process = subprocess.Popen([_node_path(), "--max-old-space-size=192", str(SIM_DIR / "worker.mjs")],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   cwd=SIM_DIR, shell=False)

        def feed():
            try:
                process.stdin.write(encoded)
                process.stdin.close()
            except (OSError, ValueError):
                pass

        def drain():
            try:
                while True:
                    chunk = process.stdout.read(8192)
                    if not chunk:
                        break
                    if len(output) + len(chunk) > MAX_OUTPUT_BYTES:
                        overflow.set()
                        process.kill()
                        break
                    output.extend(chunk)
            except (OSError, ValueError):
                pass

        threads = [threading.Thread(target=feed, daemon=True), threading.Thread(target=drain, daemon=True)]
        for thread in threads:
            thread.start()
        try:
            process.wait(timeout=SIM_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise HTTPException(503, "Battle simulator timed out; action was not applied") from None
        for thread in threads:
            thread.join(timeout=1)
        if overflow.is_set():
            raise HTTPException(503, "Battle simulator output limit exceeded")
        result = json.loads(output)
        if not isinstance(result, dict):
            raise HTTPException(503, "Battle simulator returned invalid output")
        if result.get("error"):
            raise HTTPException(422 if result.get("invalid") else 503, result["error"])
        if process.returncode:
            raise HTTPException(503, "Battle simulator failed; action was not applied")
        return result
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        raise HTTPException(503, "Battle simulator failed; action was not applied") from exc
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait()
            for thread in threads:
                thread.join(timeout=1)
            process.stdin.close()
            process.stdout.close()
        _SIM_SLOTS.release()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key")
        result[key] = value
    return result


async def _read_request(request):
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            length = int(declared)
            if length < 0:
                raise ValueError()
        except ValueError:
            raise HTTPException(400, "Invalid Content-Length") from None
        if length > MAX_BODY_BYTES:
            raise HTTPException(413, "Battle request exceeds 32 KiB")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            raise HTTPException(413, "Battle request exceeds 32 KiB")
        body.extend(chunk)
    try:
        data = json.loads(body, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite number")))
        return _REQUEST.validate_python(data)
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, "Invalid battle JSON or fields") from None


def _schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS pokerogue_battle_rooms (
        room TEXT PRIMARY KEY, status TEXT NOT NULL, created REAL NOT NULL,
        expires REAL, terminal REAL, data TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS pokerogue_battle_members (
        uid TEXT PRIMARY KEY, room TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS pokerogue_battle_rates (
        uid TEXT NOT NULL, bucket TEXT NOT NULL, started REAL NOT NULL,
        attempts INTEGER NOT NULL, PRIMARY KEY(uid,bucket))""")


def _load(db, code):
    row = db.execute("SELECT * FROM pokerogue_battle_rooms WHERE room=?", (code,)).fetchone()
    if row is None:
        return None
    room = dict(row)
    room["data"] = json.loads(room["data"])
    return room


def _store(db, room):
    db.execute("UPDATE pokerogue_battle_rooms SET status=?,expires=?,terminal=?,data=? WHERE room=?",
               (room["status"], room["expires"], room["terminal"], _encode(room["data"]), room["room"]))


def _terminal(db, room, status, reason, now, winner=None):
    room.update(status=status, expires=None, terminal=now)
    room["data"].update(reason=reason, winner=winner)
    if room["data"]["sim"]:
        room["data"]["sim"]["state"] = None  # No further simulation; release bounded private state.
        for view in room["data"]["sim"]["views"]:
            view.update(waiting=True, moves=[], canSwitch=[], forceSwitch=False)
    db.execute("DELETE FROM pokerogue_battle_members WHERE room=?", (room["room"],))
    _store(db, room)


def _cleanup(db, now):
    for row in db.execute("SELECT room FROM pokerogue_battle_rooms WHERE expires<=?", (now,)).fetchall():
        room = _load(db, row[0])
        reason = "absolute_timeout" if now >= room["created"] + LIFETIME_SECONDS else "idle_timeout"
        _terminal(db, room, "closed", reason, now)
    db.execute("DELETE FROM pokerogue_battle_rooms WHERE terminal<=?", (now - RETENTION_SECONDS,))
    db.execute("DELETE FROM pokerogue_battle_rates WHERE started<=?", (now - 60,))


def _rate(db, uid, op, now):
    bucket = "admission" if op in {"create", "join"} else "choose" if op == "choose" else "poll"
    row = db.execute("SELECT attempts FROM pokerogue_battle_rates WHERE uid=? AND bucket=?", (uid, bucket)).fetchone()
    if row:
        if row[0] >= RATE_LIMITS[bucket]:
            raise HTTPException(429, "Too many battle requests; retry in one minute", headers={"Retry-After": "60"})
        db.execute("UPDATE pokerogue_battle_rates SET attempts=attempts+1 WHERE uid=? AND bucket=?", (uid, bucket))
    else:
        if db.execute("SELECT count(*) FROM pokerogue_battle_rates").fetchone()[0] >= 8192:
            raise HTTPException(503, "Battle request capacity reached")
        db.execute("INSERT INTO pokerogue_battle_rates VALUES (?,?,?,1)", (uid, bucket, now))


def _view(room, uid):
    data = room["data"]
    seat = data["uids"].index(uid)
    sim = data["sim"]
    return {"room": room["room"], "seat": seat, "status": room["status"], "revision": data["revision"],
            "turn": sim["turn"] if sim else 0, "players": data["players"],
            "battle": sim["views"][seat] if sim else None, "log": sim["log"] if sim else [],
            "winner": data["winner"], "reason": data["reason"]}


def _current(db, uid):
    row = db.execute("SELECT room FROM pokerogue_battle_members WHERE uid=?", (uid,)).fetchone()
    return _load(db, row[0]) if row else None


def _touch(db, room, uid, now):
    if room["status"] in {"waiting", "active"}:
        room["expires"] = min(now + IDLE_SECONDS, room["created"] + LIFETIME_SECONDS)
    _store(db, room)
    return _view(room, uid)


def _operate(db, uid, name, request, now):
    if request.op == "create":
        snapshot = request.snapshot.model_dump()
        current = _current(db, uid)
        if current:
            if current["data"]["uids"][0] == uid and current["data"]["snapshots"][0] == snapshot:
                return _touch(db, current, uid, now)
            raise HTTPException(409, "Account already has an active battle")
        if db.execute("SELECT count(*) FROM pokerogue_battle_rooms").fetchone()[0] >= MAX_ROOMS:
            raise HTTPException(503, "Battle room capacity reached")
        _simulate({"op": "validate", "snapshot": snapshot})
        for _ in range(32):
            code = f"{secrets.randbelow(1000000):06d}"
            if not _load(db, code):
                break
        else:
            raise HTTPException(503, "Battle room codes unavailable")
        data = {"uids": [uid, None], "snapshots": [snapshot, None], "players": [{"name": name}, None],
                "revision": 0, "last": [None, None], "sim": None, "winner": None, "reason": None}
        db.execute("INSERT INTO pokerogue_battle_rooms VALUES (?,'waiting',?,?,NULL,?)",
                   (code, now, now + IDLE_SECONDS, _encode(data)))
        db.execute("INSERT INTO pokerogue_battle_members VALUES (?,?)", (uid, code))
        return _view(_load(db, code), uid)
    room = _load(db, request.room)
    if room is None:
        raise HTTPException(404, "Battle room not found")
    data = room["data"]
    seat = data["uids"].index(uid) if uid in data["uids"] else None
    if request.op == "join":
        snapshot = request.snapshot.model_dump()
        if seat == 0:
            raise HTTPException(409, "Cannot join your own battle")
        if seat == 1:
            if data["snapshots"][1] != snapshot:
                raise HTTPException(409, "Battle snapshot is immutable")
            return _touch(db, room, uid, now)
        if room["status"] != "waiting":
            raise HTTPException(409, "Room is not accepting players")
        if _current(db, uid):
            raise HTTPException(409, "Account already has an active battle")
        sim = _simulate({"op": "start", "snapshots": [data["snapshots"][0], snapshot],
                         "seed": [secrets.randbelow(65536) for _ in range(4)]})
        data["sim"] = sim
        data["uids"][1] = uid
        data["snapshots"][1] = snapshot
        data["players"][1] = {"name": name}
        room["status"] = "active"
        db.execute("INSERT INTO pokerogue_battle_members VALUES (?,?)", (uid, room["room"]))
    else:
        if seat is None:
            raise HTTPException(404, "Battle room not found")
        if request.op == "leave":
            if room["status"] in {"waiting", "active"}:
                _terminal(db, room, "closed", "left", now)
        elif request.op == "choose":
            last = data["last"][seat]
            receipt = {"revision": request.revision, "choice": request.choice}
            if last and last["revision"] == request.revision:
                if last != receipt:
                    raise HTTPException(409, "Conflicting choice retransmission")
                return _touch(db, room, uid, now)
            if room["status"] != "active":
                raise HTTPException(409, "Battle is not active")
            if request.revision != data["revision"]:
                raise HTTPException(409, "Stale battle revision")
            sim = _simulate({"op": "choose", "state": data["sim"]["state"], "seat": seat, "choice": request.choice})
            data["sim"] = sim
            data["last"][seat] = receipt
            if sim["progressed"]:
                data["revision"] += 1
            if sim["ended"]:
                _terminal(db, room, "finished", "victory" if sim["winner"] is not None else "tie", now, sim["winner"])
            elif sim["turn"] > MAX_TURNS or data["revision"] >= MAX_EPOCHS:
                _terminal(db, room, "closed", "turn_limit", now)
    return _touch(db, room, uid, now)


def _database(path_provider, uid, name, request):
    try:
        with closing(sqlite3.connect(path_provider(), timeout=15, isolation_level=None)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            try:
                _schema(db)
                now = time.time()
                _cleanup(db, now)
                try:
                    _rate(db, uid, request.op, now)
                except HTTPException:
                    db.commit()
                    raise
                db.execute("SAVEPOINT action")
                try:
                    result = _operate(db, uid, name, request, now)
                except HTTPException:
                    # Persist quota/expiry, but NEVER partial membership or simulator changes.
                    db.execute("ROLLBACK TO action")
                    db.execute("RELEASE action")
                    db.commit()
                    raise
                db.execute("RELEASE action")
                db.commit()
                return result
            except BaseException:
                if db.in_transaction:
                    db.rollback()
                raise
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(503, "Battle storage temporarily unavailable") from exc


def create_pokerogue_battle_router(get_current_user_profile, db_path_provider) -> APIRouter:
    router = APIRouter(prefix="/api/pokerogue", tags=["PokéRogue"])

    @router.post("/battle")
    async def battle(request: Request, response: Response, user=Depends(get_current_user_profile)):
        uid = _account_id(user)
        payload = await _read_request(request)
        response.headers["Cache-Control"] = "no-store"
        return await asyncio.to_thread(_database, db_path_provider, uid, display_name(user), payload)

    return router
