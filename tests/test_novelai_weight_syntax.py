# -*- coding: utf-8 -*-

from src.chat.features.novelai_generation.services.novelai_service import NovelAIService
from src.chat.features.novelai_generation.tag_rules import clamp_danbooru_tags


def test_normalize_weighted_tag_for_match_supports_v45_prefix_syntax():
    assert NovelAIService._normalize_weighted_tag_for_match("1.45::green left eye::") == "green left eye"


def test_normalize_weighted_tag_for_match_supports_old_suffix_syntax():
    assert NovelAIService._normalize_weighted_tag_for_match("silver hair::1.35") == "silver hair"


def test_normalize_weighted_tag_for_match_handles_group_tail_token():
    assert NovelAIService._normalize_weighted_tag_for_match("medium breasts::") == "medium breasts"


def test_split_prompt_for_v4_can_classify_grouped_weight_tokens():
    service = NovelAIService()
    prompt = "masterpiece, outdoors, solo, 1.35::silver hair, medium breasts::, 1.2::moonlight::"

    base_caption, char_captions = service._split_prompt_for_v4(prompt)
    char_caption = char_captions[0]["char_caption"]

    assert "1.35::silver hair" in char_caption
    assert "medium breasts::" in char_caption
    assert "1.2::moonlight::" in base_caption


def test_split_prompt_for_v4_keeps_old_suffix_weight_as_character_tag():
    service = NovelAIService()
    prompt = "masterpiece, outdoors, solo, silver hair::1.35, medium breasts::1.25"

    _, char_captions = service._split_prompt_for_v4(prompt)
    char_caption = char_captions[0]["char_caption"]

    assert "silver hair::1.35" in char_caption
    assert "medium breasts::1.25" in char_caption

def test_clamp_danbooru_tags_keeps_v45_prefix_weight_syntax():
    raw = "1.45::green left eye::, 1.35::silver hair::, 1girl, solo"
    cleaned = clamp_danbooru_tags(raw, max_tags=90)

    assert "1.45::green left eye::" in cleaned
    assert "1.35::silver hair::" in cleaned


def test_clamp_danbooru_tags_only_removes_numbered_list_prefix():
    raw = "1. masterpiece, 2) best quality, 1.45::green left eye::"
    cleaned = clamp_danbooru_tags(raw, max_tags=90)

    assert "masterpiece" in cleaned
    assert "best quality" in cleaned
    assert "1.45::green left eye::" in cleaned
