# Authoritative training duels (Showdown 0.11.10)

## Install / start

Requires Node **18+** on the Python server's `PATH`; verified locally with Node
24.14.1, npm 11.11.0, Python 3.12.10 on Windows PowerShell. From this directory:

```powershell
npm ci --ignore-scripts
npm test
```

Initial installation used `npm install --ignore-scripts`. The committed v3 lock
pins the complete dependency tree (the local npm registry is npmmirror.com).
Showdown ships its compiled simulator in this npm package; its server and native
optional dependencies are not started or needed. No lifecycle scripts run.
`node_modules/` is ignored and must be installed on each deployment.

There is **no separate simulator server/port**. The Python router starts one
bounded `node --max-old-space-size=192 worker.mjs` subprocess per validation or
choice. Polls use SQLite only. Mount in the existing authenticated FastAPI app:

```python
from .pokerogue_battle import create_pokerogue_battle_router, battle_available

# Optional application startup gate:
if battle_available():
    app.include_router(create_pokerogue_battle_router(
        get_current_user_profile, db_path_provider,
    ))
```

The dependency must be installed in this directory, not the frontend package.
Missing Node/dependency yields a meaningful HTTP 503 if the router is mounted.
The application now mounts this route with lobby authentication and stores rooms
in `pokerogue-battle.db` beside the main database, avoiding wallet/cloud write-lock
contention during simulation. The original experimental `/duel` relay remains
separate and default-off. This integration has not been deployed to production.

## Explicit training-mode rules (show these in the UI)

- Gen 9 **Custom Game** singles, National Dex base species, 1–6 Pokémon;
  automatic team order matches snapshot order. Not a ranked/legality-certified
  Pokémon team: supported legacy moves and arbitrary supported abilities are
  intentionally retained, including PokéRogue's nonstandard learnsets.
- Exact level, IVs (HP/Atk/Def/SpAtk/SpDef/Spe), English nature, gender, shiny,
  numeric moves and ability from snapshot. Unsupported IDs, aliases, duplicate
  moves, impossible genders, arbitrary stats and extra fields are rejected.
- **EVs are explicitly zero. All Pokémon start at full HP, no status, full
  Showdown max PP (including its standard PP-Up multiplier).** No held items;
  friendship is explicitly 255. These are training rules, not imported save
  values. No Terastallization, Mega Evolution, Z-Moves or Dynamax commands.
- Only base forms can be represented by National Dex ID. The exporting UI must
  reject fusion, alternate forms, modifiers, held-item dependencies or other
  unsupported source mechanics rather than silently flatten them. Normal
  in-battle transformations and abilities remain Showdown mechanics.
- No experience, rewards, coins, inventory or cloud-save writes, including on
  victory or leave. This is independent training, not an engine PvP relay.

Open a compatible adventure, then Menu → 好友对战. Create a room or enter its six
digits using left/right to select a digit and up/down to change it. A confirms,
B returns or confirms leaving. Moves appear before switch choices; arrows cycle
them. The UI uses original Phaser windows/icons, not full attack animations.
Fusion, alternate forms, passives and all adventure modifiers are explicitly
rejected. Standard max PP includes Showdown's PP-Up multiplier, not saved PP.

## Public contract / frontend notes

`POST /api/pokerogue/battle`: `create`, `join`, `state`, `choose`, `leave` exactly
as specified by the shared contract. No extra public fields. Authentication uses
server profile `user_id`; names use the existing sanitized profile formatter.
`battle=null` until joined. `players` contains names only.

Use `move N` from the returned `moves[].slot`, or `switch N` from `canSwitch`.
`canSwitch` indexes the **current returned own array**, 1-based: Showdown swaps
party positions when switching. Do not retain initial snapshot party indexes.
No command may contain an opponent seat, move name, target, damage or raw request.
When `waiting=true`, do not submit a new decision; this includes the non-switching
side during a forced switch. A switch decision can progress the revision without
advancing the numerical battle turn. Forced Struggle appears as `move 1`.

Both sides submit the same `revision`; only simulator progression increments it.
The first submission remains private and does not change the opponent view.
The most recent per-seat receipt supports identical retries even after progress;
conflicting duplicates or older stale epochs return 409. Fetch state after 409.
Illegal simulator choices return 422. Every successful action returns latest view.
Poll about once per second. Own HP is exact; opponent HP and all public log HP
fractions are percentages (`maxhp=100`), rounded up for surviving Pokémon.
Logs are sanitized Showdown protocol lines, newest 20 only. `p1`/`Seat 0` and
`p2`/`Seat 1` identify seats; render as text, never HTML.

Statuses: `waiting`, `active`, `finished`, `closed`. Reasons: `victory`, `tie`,
`left`, `idle_timeout`, `absolute_timeout`, `turn_limit`, or null. Leaving an open
room closes it with null winner; repeat leave is harmless. A finished result is
not overwritten by leave.

## Persistence and limits

SQLite `BEGIN IMMEDIATE` serializes all room transitions across app workers.
`Battle.toJSON()` / `Battle.fromJSON()` persist the real Showdown RNG, effects,
requests, pending choices and state; they are never sent to the browser. Tests
round-trip through JSON between choices and restore a pending battle in a fresh
Python interpreter. Keep this dependency version pinned while active rooms exist;
future simulator upgrades need draining rooms or a state migration.

One active room per account, cryptographic six-digit room codes, at most 512
rooms including terminal retention. Idle TTL 10 minutes, renewed by member
polls/actions; absolute lifetime 2 hours; terminal retention 1 hour. At most 200
attack turns and 800 completed decision epochs. Internal histories are additionally
bounded to 20,000 protocol lines / 2,000 inputs and total subprocess output to
1.5 MB; input state to 2 MB. HTTP bodies are at most 32 KiB. Node timeout is 10s,
heap ceiling 192 MiB, at most four subprocesses per Python worker (SQLite also
serializes access to a shared DB). There is no shell command interpolation.

Persisted per-account/minute quotas: 20 admission attempts, 120 choices, 180
poll/leave requests; rate table capped at 8192 rows. Expiry/quota maintenance is
committed even on rejected actions; a savepoint rolls back all battle mutations
on simulator/validation failure. 429 includes Retry-After. 503 leaves actions
unapplied and may be retried.

## Verification

From repository root:

```powershell
python -m pytest tests/test_pokerogue_battle.py -q -s
```

Real simulation tests cover switches, trapping, Disable, faint/forced switch,
six-member victory, serialized pending choices/RNG, legacy moves, and 65 completed
turns through PP depletion into Struggle. ASGI tests cover auth, exact response
shape, hidden choices, retry/conflict semantics, concurrent routers, fresh-process
restore, expiry, rate/capacity/turn bounds, timeout/output-limit rollback, missing
Node, and unchanged cloud-save/wallet rows.

Integration acceptance: 192 combined Python tests, 9 native UI/model tests,
7 real simulator tests, and 50 existing/bridge browser regressions passed.
A separate real two-engine keyboard test passed create/join, voluntary switch,
attacks, faint/forced switch, identical victory display, and unchanged cloud
entries/revisions. Its isolated fixture derives from a real classic save with
explicitly adjusted test levels/moves. Runtime assets came from the actual local
build; authenticated battle/save requests used the real SQLite/Showdown backend.
The two mobile regression cases cover the existing solo leaderboard, not a
physical-device PvP acceptance. Production deployment remains separate.

## License

Pokémon Showdown 0.11.10 is MIT licensed, copyright (c) 2011–2024 Guangcong Luo
and other contributors. Its full license is included by npm at
`node_modules/pokemon-showdown/LICENSE`; preserve it when distributing the
installed dependency. This wrapper does not copy Showdown's damage implementation.
