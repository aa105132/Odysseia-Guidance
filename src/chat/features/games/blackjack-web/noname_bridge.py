"""无名杀联机票据与同源转发，不参与灵石结算。"""
import asyncio
import os
import secrets
import time
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

import websockets
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

NONAME_DIST_DIR = Path(os.getenv("NONAME_DIST_DIR", str(Path(__file__).resolve().parents[5] / "data/noname/dist")))
MAX_MESSAGE = 1024 * 1024


def create_noname_router(profile_dependency):
    router = APIRouter()
    tickets = {}

    def prune():
        now = time.monotonic()
        for token, (_, deadline) in list(tickets.items()):
            if deadline <= now:
                tickets.pop(token, None)

    @router.get('/api/noname/status')
    async def status(user=Depends(profile_dependency)):
        return {'available': (NONAME_DIST_DIR / 'index.html').is_file(), 'version': '1.11.6'}

    @router.post('/api/noname/session')
    async def session(user=Depends(profile_dependency)):
        if not (NONAME_DIST_DIR / 'index.html').is_file():
            raise HTTPException(503, '三国杀资源尚未安装')
        prune()
        if len(tickets) >= 2048:
            raise HTTPException(503, '联机入口繁忙，请稍后重试')
        token = secrets.token_urlsafe(32)
        tickets[token] = (str(user['user_id']), time.monotonic() + 60)
        return {'ticket': token, 'expires_in': 60}

    @router.websocket('/noname-ws')
    async def bridge(client: WebSocket):
        origin = urlsplit(client.headers.get('origin', ''))
        # Discord 的 URL Mapping 会保留活动 Origin，但上游 Host 可能是源站域名。
        app_id = (os.getenv('DISCORD_CLIENT_ID') or os.getenv('VITE_DISCORD_CLIENT_ID') or '').strip()
        discord_origin = bool(app_id.isdecimal() and origin.scheme == 'https'
                              and origin.netloc == f'{app_id}.discordsays.com')
        same_origin = origin.scheme in ('http', 'https') and origin.netloc == client.headers.get('host')
        if origin.path or origin.query or origin.fragment or not (same_origin or discord_origin):
            await client.close(code=1008)
            return
        prune()
        identity = tickets.pop(client.query_params.get('ticket', ''), None)
        if identity is None:
            await client.close(code=1008)
            return
        await client.accept()
        tasks = []
        try:
            async with websockets.connect(os.getenv('NONAME_WS_URL', 'ws://127.0.0.1:8082'), max_size=MAX_MESSAGE, open_timeout=8) as upstream:
                async def to_server():
                    while True:
                        data = await client.receive()
                        if data['type'] == 'websocket.disconnect':
                            return
                        message = data.get('text')
                        if message is None or len(message.encode()) > MAX_MESSAGE:
                            await client.close(code=1009)
                            return
                        await upstream.send(message)

                async def to_client():
                    async for data in upstream:
                        if not isinstance(data, str):
                            await client.close(code=1003)
                            return
                        await client.send_text(data)

                tasks = [asyncio.create_task(to_server()), asyncio.create_task(to_client())]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
        except (OSError, websockets.exceptions.WebSocketException):
            with suppress(RuntimeError, WebSocketDisconnect):
                await client.close(code=1013, reason='联机服务暂不可用，请返回后重试')
        except WebSocketDisconnect:
            pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            with suppress(RuntimeError, WebSocketDisconnect):
                await client.close()

    return router
