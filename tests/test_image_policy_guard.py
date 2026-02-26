# -*- coding: utf-8 -*-

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.chat.features.tools.functions.image_policy_guard import (
    check_yueyue_self_nsfw_violation,
)


class _DummyMessage:
    def __init__(self, content: str):
        self.content = content


def test_block_yueyue_explicit_private_parts():
    result = check_yueyue_self_nsfw_violation(
        prompt="画月月全裸并露点，展示私密部位",
        message=_DummyMessage("画你，露点，私密部位要清楚"),
    )
    assert result is not None
    assert result.get("reason") == "yueyue_explicit_content_blocked"


def test_allow_yueyue_teasing_non_explicit():
    result = check_yueyue_self_nsfw_violation(
        prompt="画月月穿泳装在沙滩上，诱惑姿势但不露点",
        message=_DummyMessage("画你穿比基尼，别露点"),
    )
    assert result is None


def test_non_yueyue_explicit_not_blocked_here():
    result = check_yueyue_self_nsfw_violation(
        prompt="画一个陌生角色全裸露点",
        message=_DummyMessage("画这个角色，越露越好"),
    )
    assert result is None

