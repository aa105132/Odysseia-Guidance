"""联机网关使用真实 WebSocket 上游验证，不修改账户钱包。"""
import asyncio
import importlib
import threading
from concurrent.futures import Future

import pytest
import websockets
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

module = importlib.import_module('src.chat.features.games.blackjack-web.noname_bridge')


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / 'index.html').write_text('game')
    monkeypatch.setattr(module, 'NONAME_DIST_DIR', tmp_path)
    async def profile():
        return {'user_id': '123456789012345678'}
    app = FastAPI()
    app.include_router(module.create_noname_router(profile))
    with TestClient(app) as test:
        yield test


def test_requires_profile():
    async def denied():
        raise HTTPException(401)
    app = FastAPI()
    app.include_router(module.create_noname_router(denied))
    with TestClient(app) as client:
        assert client.get('/api/noname/status').status_code == 401
        assert client.post('/api/noname/session').status_code == 401


def test_missing_assets(client, monkeypatch, tmp_path):
    monkeypatch.setattr(module, 'NONAME_DIST_DIR', tmp_path / 'absent')
    assert client.get('/api/noname/status').json()['available'] is False
    assert client.post('/api/noname/session').status_code == 503


@pytest.mark.parametrize('origin', ['https://evil.example', 'https://999999999999999999.discordsays.com', 'https://123456789012345678.discordsays.com.evil.example'])
def test_origin_and_expiry(client, monkeypatch, origin):
    monkeypatch.setenv('DISCORD_CLIENT_ID', '123456789012345678')
    token = client.post('/api/noname/session').json()['ticket']
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/noname-ws?ticket=' + token, headers={'origin': origin}):
            pass
    now = module.time.monotonic()
    monkeypatch.setattr(module.time, 'monotonic', lambda: now + 61)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/noname-ws?ticket=' + token, headers={'origin': 'http://testserver'}):
            pass


@pytest.mark.parametrize('origin', ['http://testserver', 'https://123456789012345678.discordsays.com'])
def test_real_text_relay_and_single_use(client, monkeypatch, origin):
    monkeypatch.setenv('DISCORD_CLIENT_ID', '123456789012345678')
    ready = Future()
    loop = asyncio.new_event_loop()
    async def echo(ws):
        async for message in ws:
            await ws.send(message)
    async def start():
        server = await websockets.serve(echo, '127.0.0.1', 0)
        ready.set_result(server)
    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start())
        loop.run_forever()
    thread = threading.Thread(target=run)
    thread.start()
    server = ready.result(timeout=5)
    monkeypatch.setenv('NONAME_WS_URL', f'ws://127.0.0.1:{server.sockets[0].getsockname()[1]}')
    try:
        token = client.post('/api/noname/session').json()['ticket']
        with client.websocket_connect('/noname-ws?ticket=' + token, headers={'origin': origin}) as ws:
            ws.send_text('["server","key","hello"]')
            assert ws.receive_text() == '["server","key","hello"]'
            ws.send_bytes(b'no')
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_text()
            assert closed.value.code == 1009
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/noname-ws?ticket=' + token, headers={'origin': 'http://testserver'}):
                pass
    finally:
        async def stop():
            server.close()
            await server.wait_closed()
        asyncio.run_coroutine_threadsafe(stop(), loop).result(timeout=5)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()
