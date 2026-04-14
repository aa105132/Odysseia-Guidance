# -*- coding: utf-8 -*-

import asyncio
import os
import sys
import types
from unittest.mock import MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

_MISSING = object()
_ORIGINAL_MODULES = {}


def _remember_module(module_name: str):
    if module_name not in _ORIGINAL_MODULES:
        _ORIGINAL_MODULES[module_name] = sys.modules.get(module_name, _MISSING)


def _restore_stubbed_modules():
    for module_name, original_module in _ORIGINAL_MODULES.items():
        if original_module is _MISSING:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = original_module


def _tool_metadata_passthrough(**_kwargs):
    def decorator(func):
        return func

    return decorator


_remember_module("src.chat.features.tools.tool_metadata")
_remember_module("src.chat.features.tools.functions.summarize_channel")

summarize_channel_stub = types.ModuleType(
    "src.chat.features.tools.functions.summarize_channel"
)
summarize_channel_stub.text_to_newspaper_brief_image = MagicMock(
    return_value=b"fake-png"
)
sys.modules["src.chat.features.tools.functions.summarize_channel"] = (
    summarize_channel_stub
)
sys.modules["src.chat.features.tools.tool_metadata"] = types.SimpleNamespace(
    tool_metadata=_tool_metadata_passthrough
)

from src.chat.features.tools.functions.render_newspaper_brief import (
    render_newspaper_brief,
)

_restore_stubbed_modules()


def test_render_newspaper_brief_uses_fallback_title_when_missing():
    result = asyncio.run(
        render_newspaper_brief(
            body="这是一段测试摘要。",
            subtitle="月月大人的特别表彰",
            section_name="社区忠诚度荣誉榜",
        )
    )

    assert result["title"] == "月月大人的特别表彰"
    assert result["image_data"]["data"] == b"fake-png"

