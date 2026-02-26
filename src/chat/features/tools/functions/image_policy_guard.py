# -*- coding: utf-8 -*-

import re
from typing import Any, Dict, Optional


_YUEYUE_DIRECT_MARKERS = (
    "月月",
    "画你",
    "画自己",
    "你自己",
    "你本人",
    "yueyue",
)

_YUEYUE_DNA_MARKERS = (
    "green left eye",
    "blue right eye",
    "silver crescent moon hair stick",
    "small sharp triangular earrings",
    "small sharp triangular red and blue earrings",
    "silver crescent moon necklace",
    "white fox ears",
    "fox tail",
)

_EXPLICIT_PRIVATE_PATTERNS = (
    r"露点|露乳|乳头|乳晕|私密部位|私处|阴部|阴蒂|阴唇|阴道|阴茎|生殖器|小穴|龟头|鸡巴|肉棒|肛门|全裸|性交|做爱|口交|手交|自慰|高潮|射精|精液",
    r"\b(?:nude|naked|topless|bottomless|full\s*nude|completely\s*nude)\b",
    r"\b(?:nipple|nipples|areola|pussy|vagina|labia|clitoris|penis|cock|dick|genitals?|anus|anal|sex|fellatio|blowjob|handjob|masturbation|orgasm|ahegao|cumshot)\b",
)

_SAFE_NEGATION_PHRASES = (
    "不露点",
    "别露点",
    "不要露点",
    "禁止露点",
    "不全裸",
    "不要全裸",
    "no nude",
    "not nude",
    "not naked",
    "without nudity",
    "without explicit",
    "no nipples",
    "no sex",
    "without sex",
)


def _normalize_text(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


def _message_content(message: Optional[Any]) -> str:
    if not message:
        return ""
    content = getattr(message, "content", None)
    return _normalize_text(content)


def _is_yueyue_self_request(text: str) -> bool:
    if not text:
        return False
    if any(marker in text for marker in _YUEYUE_DIRECT_MARKERS):
        return True
    dna_hits = sum(1 for marker in _YUEYUE_DNA_MARKERS if marker in text)
    if ("green left eye" in text and "blue right eye" in text) and dna_hits >= 2:
        return True
    return False


def _contains_explicit_private_content(text: str) -> bool:
    if not text:
        return False
    normalized_text = text
    for phrase in _SAFE_NEGATION_PHRASES:
        normalized_text = normalized_text.replace(phrase, " ")
    for pattern in _EXPLICIT_PRIVATE_PATTERNS:
        if re.search(pattern, normalized_text, re.IGNORECASE):
            return True
    return False


def check_yueyue_self_nsfw_violation(
    prompt: str,
    negative_prompt: Optional[str] = None,
    message: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    prompt_text = _normalize_text(prompt)
    negative_text = _normalize_text(negative_prompt)
    message_text = _message_content(message)

    identity_text = "\n".join(filter(None, [prompt_text, message_text]))
    if not _is_yueyue_self_request(identity_text):
        return None

    explicit_check_text = "\n".join(filter(None, [prompt_text, message_text]))
    if not _contains_explicit_private_content(explicit_check_text):
        return None

    if negative_text and _contains_explicit_private_content(negative_text):
        # 负面提示词出现敏感词不作为拦截依据
        pass

    return {
        "generation_failed": True,
        "reason": "yueyue_explicit_content_blocked",
        "hint": (
            "月月自画像规则：仅允许擦边（泳装/内衣/情趣服/诱惑姿势等）。"
            "涉及露点、私密部位直接裸露或明确性行为时必须拒绝，请改为不露点版本再试。"
        ),
    }
