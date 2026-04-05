# -*- coding: utf-8 -*-

import re


_WHITESPACE_RE = re.compile(r"\s+")
_SENTENCE_SPLIT_RE = re.compile(r"([^。！？!?…\n]+)([。！？!?…]+)?")


def _normalize_for_compare(text: str) -> str:
    normalized = _WHITESPACE_RE.sub("", str(text or "").strip())
    normalized = normalized.strip("，,。！？!?~～…、\"'“”‘’")
    return normalized


def collapse_consecutive_duplicate_sentences(text: str) -> str:
    """
    压缩单条回复中连续重复的句子/行，避免模型把同一句话原样复读多次。

    只处理“连续重复”的情况，尽量避免误伤正常排比或前后呼应。
    """
    if not text:
        return text

    processed_lines = []
    previous_line_norm = ""

    for raw_line in str(text).splitlines():
        matches = _SENTENCE_SPLIT_RE.findall(raw_line)
        rebuilt_parts = []
        previous_sentence_norm = ""

        for sentence, punctuation in matches:
            candidate = str(sentence or "").strip()
            if not candidate:
                continue

            candidate_norm = _normalize_for_compare(candidate)
            if candidate_norm and candidate_norm == previous_sentence_norm:
                continue

            rebuilt_parts.append(f"{candidate}{punctuation or ''}")
            previous_sentence_norm = candidate_norm

        rebuilt_line = "".join(rebuilt_parts).strip()
        rebuilt_line_norm = _normalize_for_compare(rebuilt_line)

        if rebuilt_line_norm and rebuilt_line_norm == previous_line_norm:
            continue

        processed_lines.append(rebuilt_line)
        previous_line_norm = rebuilt_line_norm

    return "\n".join(processed_lines).strip()
