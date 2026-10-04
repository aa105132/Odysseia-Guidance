"""Read-only, bounded classic Noname room summaries for the shared directory."""
import asyncio
import json
import os
import re
import time
from pathlib import Path

import websockets

MAX_MESSAGE = 1024 * 1024
WARNING = '三国杀房间列表暂不可用，请稍后刷新'


def room_summaries(message):
    if not isinstance(message, list) or len(message) < 2 or message[0] != 'roomlist' or not isinstance(message[1], list):
        raise ValueError('Invalid room list')
    result = []
    seen = set()
    for row in message[1]:
        if not isinstance(row, list) or len(row) < 5:
            continue
        host, _, config, count, room_id = row[:5]
        if not isinstance(config, dict) or not isinstance(room_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', room_id):
            continue
        if (config.get('mode') != 'identity' or config.get('identity_mode') != 'normal'
                or config.get('characterPack') != ['standard'] or config.get('cardPack') != ['standard']):
            continue
        capacity = config.get('number') or config.get('player_number') or 8
        if isinstance(capacity, str) and capacity in ('2', '4', '6', '8'):
            capacity = int(capacity)
        if type(capacity) is not int or capacity not in (2, 4, 6, 8) or type(count) is not int or not 1 <= count <= capacity:
            continue
        if room_id in seen or not isinstance(host, str):
            continue
        seen.add(room_id)
        playing = bool(config.get('gameStarted'))
        result.append(dict(room_id=room_id, game_type='noname', host_username=host[:80] or '牌友',
                           host_avatar_url='', player_count=count, max_players=capacity,
                           state='playing' if playing else 'waiting', can_join=not playing and count < capacity,
                           room_tier='classic', base_stake=None, entry_min=0, loss_limit=None,
                           turn_timeout_seconds=None, is_member=False, updated_at=0))
    return result


class NonameDirectory:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.expires = 0.0
        self.key = None
        self.cached = ([], '')

    async def list_rooms(self, dist: Path):
        if not (dist / 'index.html').is_file():
            return [], ''
        address = os.getenv('NONAME_WS_URL', 'ws://127.0.0.1:8082')
        key = (str(dist), address)
        async with self.lock:
            if self.key != key or time.monotonic() >= self.expires:
                try:
                    # No join/create messages or user tickets: read the initial public snapshot only.
                    async with asyncio.timeout(2):
                        async with websockets.connect(address, max_size=MAX_MESSAGE, open_timeout=1, close_timeout=.2) as socket:
                            raw = await socket.recv()
                            if not isinstance(raw, str):
                                raise ValueError('Expected text frame')
                            rows = room_summaries(json.loads(raw))
                    self.cached = (rows, '')
                except (OSError, TimeoutError, ValueError, websockets.exceptions.WebSocketException):
                    self.cached = ([], WARNING)  # Never advertise stale rooms after an outage.
                self.key = key
                self.expires = time.monotonic() + 3
            rows, warning = self.cached
            return [dict(row) for row in rows], warning


directory = NonameDirectory()
