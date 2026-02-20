import json
import re

import pytest

from src.chat.config import emoji_config


def _snapshot_mappings():
    return [(pattern.pattern, list(emojis)) for pattern, emojis in emoji_config.EMOJI_MAPPINGS]


def _restore_mappings(snapshot):
    emoji_config.EMOJI_MAPPINGS.clear()
    emoji_config.EMOJI_MAPPINGS.extend(
        (re.compile(pattern_text), list(emojis)) for pattern_text, emojis in snapshot
    )


@pytest.fixture(autouse=True)
def restore_default_mappings_after_test():
    snapshot = _snapshot_mappings()
    yield
    _restore_mappings(snapshot)


def test_save_emoji_mappings_to_file(tmp_path):
    emoji_config.EMOJI_MAPPINGS.clear()
    emoji_config.EMOJI_MAPPINGS.append(
        (emoji_config.compile_placeholder_pattern('<测试>'), ['<:test:123456>'])
    )

    target = tmp_path / 'emoji_mappings.json'
    emoji_config.save_emoji_mappings_to_file(str(target))

    payload = json.loads(target.read_text(encoding='utf-8'))
    assert payload == [
        {
            'placeholder': '<测试>',
            'discord_emojis': ['<:test:123456>'],
        }
    ]


def test_load_emoji_mappings_from_file(tmp_path):
    target = tmp_path / 'emoji_mappings.json'
    payload = [
        {
            'placeholder': '<嘿嘿>',
            'discord_emojis': ['<:heihei:1>'],
        },
        {
            'placeholder': '<开心>',
            'discord_emojis': ['<:happy:2>', '<:happy2:3>'],
        },
    ]
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')

    loaded = emoji_config.load_emoji_mappings_from_file(str(target))
    assert loaded is True

    assert [
        emoji_config.pattern_to_placeholder(pattern)
        for pattern, _ in emoji_config.EMOJI_MAPPINGS
    ] == ['<嘿嘿>', '<开心>']
    assert emoji_config.EMOJI_MAPPINGS[1][1] == ['<:happy:2>', '<:happy2:3>']


def test_load_emoji_mappings_from_missing_file_returns_false(tmp_path):
    missing_file = tmp_path / 'not_exists.json'
    assert emoji_config.load_emoji_mappings_from_file(str(missing_file)) is False
