# -*- coding: utf-8 -*-

from src.chat.utils.response_dedup_utils import (
    collapse_consecutive_duplicate_sentences,
)


def test_collapse_consecutive_duplicate_sentences_removes_same_sentence_repeat():
    raw = "我真的不理你了啦！我真的不理你了啦！你自己玩去吧。"
    cleaned = collapse_consecutive_duplicate_sentences(raw)

    assert cleaned == "我真的不理你了啦！你自己玩去吧。"


def test_collapse_consecutive_duplicate_sentences_removes_duplicate_lines_only_when_adjacent():
    raw = "哼，不跟你说了。\n哼，不跟你说了。\n算了，再给你一次机会。"
    cleaned = collapse_consecutive_duplicate_sentences(raw)

    assert cleaned == "哼，不跟你说了。\n算了，再给你一次机会。"
