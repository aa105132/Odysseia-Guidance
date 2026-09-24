"""自定义房间固定额度覆盖高余额、直接API及自动续局；只使用临时钱包。"""
import asyncio
import copy
import sqlite3
from contextlib import AsyncExitStack
import pytest
from test_table_multiplayer_api import api, USER_IDS, _clients, _post, _room, _balances


@pytest.mark.parametrize('game_type', ['texas','golden_flower','landlord','guandan','mahjong','sichuan_mahjong'])
def test_rich_player_cannot_bypass_fixed_limits_or_mutate_ready_room(api, game_type):
    async def scenario():
        uid=USER_IDS[0]
        with sqlite3.connect(api.db_path) as c:
            c.execute('UPDATE user_coins SET balance=10000000')
        async with AsyncExitStack() as stack:
            client=(await _clients(stack,api,[uid]))[uid]
            for base,limit in [(21,2000),(20,2001),(26,2600)]:
                response=await _post(client,'create',game_type=game_type,mode='solo',room_tier='custom',base_stake=base,loss_limit=limit)
                assert response.status_code==422,response.text
                assert not api.module.table_service.rooms
            room=_room(await _post(client,'create',game_type=game_type,mode='solo',room_tier='custom',base_stake=20,loss_limit=2000))
            rid=room['room_id']
            _room(await _post(client,'ready',room_id=rid,ready=True))
            before=copy.deepcopy(api.module.table_service._room(rid))
            for base,limit in [(21,2000),(20,2001),(26,2600)]:
                response=await _post(client,'settings',room_id=rid,base_stake=base,loss_limit=limit)
                assert response.status_code==422,response.text
                assert api.module.table_service._room(rid)==before
            assert _balances(api,[uid])[uid]==10000000
            with sqlite3.connect(api.db_path) as c:
                assert c.execute('SELECT count(*) FROM coin_transactions').fetchone()[0]==0
            started=_room(await _post(client,'start',room_id=rid))
            assert started['state']=='playing' and started['loss_limit']==2000 and started['base_stake']==20
            assert _balances(api,[uid])[uid]==10000000-2000
    asyncio.run(scenario())


@pytest.mark.parametrize('automatic',[False,True])
def test_legacy_over_cap_room_cannot_reserve_on_manual_or_auto_start(api,automatic):
    async def scenario():
        uid=USER_IDS[0]
        with sqlite3.connect(api.db_path) as c:c.execute('UPDATE user_coins SET balance=10000000')
        async with AsyncExitStack() as stack:
            client=(await _clients(stack,api,[uid]))[uid]
            room=_room(await _post(client,'create',game_type='guandan',mode='solo',room_tier='custom',auto_start_when_ready=automatic))
            rid=room['room_id'];legacy=api.module.table_service._room(rid)
            legacy.base_stake,legacy.buy_in,legacy.entry_min=10000,200000,200000
            if not automatic:_room(await _post(client,'ready',room_id=rid,ready=True))
            before=copy.deepcopy(legacy)
            response=await _post(client,'ready' if automatic else 'start',room_id=rid,**({'ready':True} if automatic else {}))
            assert response.status_code==400,response.text
            assert api.module.table_service._room(rid)==before
            assert _balances(api,[uid])[uid]==10000000
            with sqlite3.connect(api.db_path) as c:
                assert c.execute('SELECT count(*) FROM coin_transactions').fetchone()[0]==0
    asyncio.run(scenario())
