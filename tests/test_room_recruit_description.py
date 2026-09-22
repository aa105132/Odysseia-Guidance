"""频道招募文案快照不访问真实 Discord。"""
from test_blackjack_multiplayer_api import api

def test_recruit_description_includes_game_occupancy_and_bots(api):
    text = api.module._recruit_description('ROOM01', 123, '房主', {
        'players': [{'is_bot': False}, {'is_bot': True}], 'max_players': 8, 'state': 'playing',
    })
    assert '多人 21 点' in text and '不能爆牌' in text
    assert '2/8 人' in text and '真人 1、月月 AI 1' in text
    assert '对局中' in text and 'ROOM01' in text
