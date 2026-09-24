"""模型不能把可结束的顺子拆单，且保留合理拆端点和紧急跟牌。"""
import json
from copy import deepcopy
import pytest
from test_jev_team_strategy import gd_game, context_and_candidates, executed_pattern, evaluation, guandan, llm, jev

STRAIGHT=['Club3#0','Diamond4#0','Heart5#0','Spade6#0','Club7#0']

@pytest.mark.parametrize('extras',[[],['Club9#0','Diamond9#0'],['ClubK#0']])
def test_free_lead_keeps_complete_straight_and_removes_wasteful_single_splits(extras):
    game=gd_game(STRAIGHT+extras)
    context,candidates=context_and_candidates('guandan',game)
    assert any(executed_pattern(game,a)['kind']=='straight' for a in candidates.values())
    for action in candidates.values():
        played=executed_pattern(game,action)
        assert not (played['kind']=='single' and action['cards'][0] in STRAIGHT)
    if not extras:
        assert all(len(a['cards'])==5 for a in candidates.values())
    assert llm.GameLLMClient._validate_action({'action':'play','cards':[STRAIGHT[0]]},context) is None


def test_sequence_plan_reaches_actual_jev_criteria():
    game=gd_game(STRAIGHT+['Club9#0','Diamond9#0'])
    context,candidates=context_and_candidates('guandan',game)
    payload,criteria=evaluation(context,candidates)
    assert payload['state']['current_decision']['estimated_current_plays']==2
    straight=next((name,action) for name,action in candidates.items() if executed_pattern(game,action)['kind']=='straight')
    assert criteria[straight[0]]['estimated_remaining_plays']==1
    assert criteria[straight[0]]['estimated_extra_plays']==0


@pytest.mark.parametrize('hand,card',[(STRAIGHT+['Spade8#0'],'Club3#0'),(STRAIGHT+['Heart2#0'],'Heart5#0')])
def test_endpoints_and_wildcard_replacement_remain_allowed(hand,card):
    game=gd_game(hand)
    context,_=context_and_candidates('guandan',game)
    action={'action':'play','cards':[card]}
    assert jev.strategic_action_allowed(context,action)
    assert llm.GameLLMClient._validate_action(action,context)==action
    deepcopy(game).act('a',**action)


def test_straight_can_be_split_to_block_opponent_final_single():
    game=gd_game(STRAIGHT)
    game.hands['b']=['Diamond6#1']
    game.last_pattern=guandan.GuandanPattern('single',4,1)
    game.last_play={'user_id':'d','cards':['Club4#1'],**game.last_pattern.to_dict()}
    context,candidates=context_and_candidates('guandan',game)
    assert any(a.get('cards')==['Club7#0'] for a in candidates.values())
    assert jev.strategic_action_allowed(context,{'action':'play','cards':['Club7#0']})


def test_opponent_small_single_reply_explains_low_cost_and_remaining_pairs():
    game=gd_game(['Club4#0','Club9#0','Diamond9#0','ClubQ#0','DiamondQ#0'])
    game.last_pattern=guandan.GuandanPattern('single',3,1)
    game.last_play={'user_id':'d','cards':['Club3#1'],**game.last_pattern.to_dict()}
    context,candidates=context_and_candidates('guandan',game)
    _,criteria=evaluation(context,candidates)
    name=next(name for name,a in candidates.items() if a.get('cards')==['Club4#0'])
    assert criteria[name]['estimated_remaining_plays']==2
    assert criteria[name]['estimated_extra_plays']==0
    assert not criteria[name]['overtakes_teammate']
    assert game.suggest_action('a')['cards']==['Club4#0']
