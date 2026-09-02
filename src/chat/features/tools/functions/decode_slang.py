# -*- coding: utf-8 -*-
"""
网络用语解析工具 — 让月月主动识别和解释互联网缩写/网络用语。

基于 internet-slang-decoder skill 的 SlangDecoder，
覆盖娱乐圈/游戏圈/生活网络/科技互联网/动漫/金融/学术 7大领域，490+ 词条。

用法示例：
  - 解析文本：decode_slang(text="yyds 这个太op了，xswl")
  - 带上下文：decode_slang(text="gg", context="游戏")
  - JSON格式：decode_slang(text="api pr k8s", output_format="json")
  - 添加热词：decode_slang(action="add", abbreviation="gd", full_form="搞对象", meaning="脱单/谈恋爱", domain="lifestyle")
"""

import sys
import os
import json
import logging
from typing import Dict, Any
_SKILL_SCRIPTS_DIR = "/app/data/skills/internet-slang-decoder/scripts"
if _SKILL_SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SKILL_SCRIPTS_DIR)
    sys.path.insert(0, _SKILL_SCRIPTS_DIR)

from src.chat.features.tools.tool_metadata import tool_metadata

log = logging.getLogger(__name__)

# 全局 decoder 实例（懒加载，避免每次调用都重新加载词库）
_decoder_instance = None


def _get_decoder():
    """懒加载 SlangDecoder 单例"""
    global _decoder_instance
    if _decoder_instance is None:
        try:
            from decoder import SlangDecoder
            _decoder_instance = SlangDecoder()
            log.info("SlangDecoder 初始化成功，词库已加载")
        except Exception as e:
            log.error(f"SlangDecoder 初始化失败: {e}")
            raise
    return _decoder_instance


@tool_metadata(
    name="网络用语解析",
    description="解析互联网缩写和网络用语（490+词条，7大领域）。当用户消息包含缩写、网络用语、或询问'xxx是什么意思'时调用",
    emoji="🔍",
    category="理解",
)
async def decode_slang(
    text: str = "",
    context: str = "",
    output_format: str = "text",
    action: str = "decode",
    abbreviation: str = "",
    full_form: str = "",
    meaning: str = "",
    domain: str = "unknown",
    confidence: float = 0.7,
    **kwargs,
) -> Dict[str, Any]:
    """
    网络用语/缩写解析工具。

    能识别和解释各种互联网缩写、网络用语、圈子黑话，覆盖：
    - 娱乐圈/饭圈（yyds, xswl, awsl...）
    - 游戏圈（gg, op, mvp, afk...）
    - 生活/网络（btw, lol, omg, ngl...）
    - 科技/互联网（api, pr, k8s, grpc...）
    - 动漫/二次元（tsundere, isekai, otaku...）
    - 金融/商业（nft, defi, hodl, fomo...）
    - 学术/教育（gpa, sci, phd, mba...）

    ## 什么时候该用这个工具：
    1. 用户消息中出现了你不认识的缩写或网络用语
    2. 用户直接问"xxx是什么意思"、"xxx什么梗"
    3. 你想确认某个缩写在特定领域的含义
    4. 需要消歧多义缩写（如 op 在游戏=超模，在动漫=片头曲）

    ## 操作说明 (action):
    - **decode**: 解析文本中的缩写（默认）
    - **add**: 添加新热词到词库（增量更新，不改动主词库）

    ## 参数说明:
    Args:
        text: 要解析的文本（decode 操作必填）
        context: 上下文提示，帮助领域推断（如"游戏"、"饭圈"、"科技"）
        output_format: 输出格式 text/json/markdown（默认 text）
        action: 操作类型 decode/add
        abbreviation: 要添加的缩写（add 操作必填）
        full_form: 缩写全称（add 操作必填）
        meaning: 含义解释（add 操作必填）
        domain: 领域 entertainment/gaming/lifestyle/tech/anime/finance/academic/unknown
        confidence: 置信度 0-1（add 操作用，默认 0.7）
    """
    try:
        decoder = _get_decoder()
    except Exception as e:
        return {
            "success": False,
            "error": f"解析器初始化失败: {e}",
            "hint": "请检查 skill 文件是否完整：data/skills/internet-slang-decoder/scripts/",
        }

    # === 添加热词 ===
    if action == "add":
        if not abbreviation or not full_form or not meaning:
            return {
                "success": False,
                "error": "add 操作需要 abbreviation, full_form, meaning 三个参数",
            }
        try:
            from decoder import Domain, DOMAIN_CN
            domain_map = {d.value: d for d in Domain}
            domain_enum = domain_map.get(domain.lower(), domain_map["unknown"])
            entry = decoder.db.add_hotword(
                abbreviation=abbreviation,
                full_form=full_form,
                meaning=meaning,
                domain=domain_enum,
                confidence=confidence,
            )
            domain_cn = DOMAIN_CN.get(entry.domain.value, entry.domain.value)
            return {
                "success": True,
                "message": f"热词已添加：{entry.abbreviation} = {entry.full_form}（{domain_cn}）",
                "entry": {
                    "abbreviation": entry.abbreviation,
                    "full_form": entry.full_form,
                    "meaning": entry.meaning,
                    "domain": domain_cn,
                    "confidence": entry.confidence,
                },
            }
        except Exception as e:
            return {"success": False, "error": f"添加热词失败: {e}"}

    # === 解析文本 ===
    if not text:
        return {
            "success": False,
            "error": "decode 操作需要 text 参数",
            "hint": "传入要解析的文本，如 decode_slang(text='yyds xswl')",
        }

    try:
        result = decoder.decode(text, context=context or None)

        # 格式化输出
        if output_format == "json":
            formatted = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        elif output_format == "markdown":
            from decoder import DOMAIN_CN
            formatted = decoder.format_output_markdown(result)
        else:
            formatted = decoder.format_output(result)

        # 检查是否有未识别缩写需要搜索
        unknown = result.get("unknown", [])
        search_queries = result.get("search_queries", [])

        return {
            "success": True,
            "result": result,
            "formatted": formatted,
            "total_found": result.get("total_found", 0),
            "total_unknown": result.get("total_unknown", 0),
            "unknown_abbreviations": unknown,
            "search_queries": search_queries,
            "hint": (
                f"有 {len(unknown)} 个未识别缩写，建议用 web_search 搜索后告知用户"
                if unknown
                else "所有缩写均已识别"
            ),
        }
    except Exception as e:
        log.error(f"解析失败: {e}", exc_info=True)
        return {"success": False, "error": f"解析失败: {e}"}
