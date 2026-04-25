# -*- coding: utf-8 -*-
import logging
import json
import re
import aiohttp
from typing import Optional, Dict, Any
from datetime import datetime, timezone, timedelta

from src.chat.utils.database import chat_db_manager
from src.chat.features.daily_outfit.config.outfit_constants import (
    DEFAULT_OUTFIT_NAME,
    DEFAULT_OUTFIT_DESCRIPTION,
    DEFAULT_OUTFIT_TAGS,
    MAX_CONSECUTIVE_FAILURES,
    OUTFIT_DESIGNER_SYSTEM_PROMPT,
    OUTFIT_DESIGNER_USER_TEMPLATE,
    FORBIDDEN_OUTFIT_TAGS,
    SEASON_MAP,
)

log = logging.getLogger(__name__)

BEIJING_TZ = timezone(timedelta(hours=8))


class OutfitService:
    """管理月月每日换装的核心服务"""

    def __init__(self):
        self._current_name: str = DEFAULT_OUTFIT_NAME
        self._current_description: str = DEFAULT_OUTFIT_DESCRIPTION
        self._current_tags: str = DEFAULT_OUTFIT_TAGS
        self._last_change_time: str = ""
        self._consecutive_failures: int = 0
        self._initialized: bool = False

    async def initialize(self):
        """启动时从数据库加载上次的服装状态"""
        if self._initialized:
            return

        db_desc = await chat_db_manager.get_global_setting("daily_outfit_description")
        db_tags = await chat_db_manager.get_global_setting("daily_outfit_tags")
        db_name = await chat_db_manager.get_global_setting("daily_outfit_name")
        db_time = await chat_db_manager.get_global_setting("daily_outfit_last_change")

        if db_desc and db_tags:
            self._current_description = db_desc
            self._current_tags = db_tags
            self._current_name = db_name or DEFAULT_OUTFIT_NAME
            self._last_change_time = db_time or ""
            log.info(f"已从数据库恢复服装状态: {self._current_name}")
        else:
            log.info("数据库中无历史服装记录，使用默认服装。")

        self._sync_to_config()
        self._initialized = True

    def _sync_to_config(self):
        """将当前状态同步到 chat_config 运行时字典"""
        from src.chat.config import chat_config
        cfg = chat_config.DAILY_OUTFIT_CONFIG
        cfg["CURRENT_OUTFIT_DESCRIPTION"] = self._current_description
        cfg["CURRENT_OUTFIT_TAGS"] = self._current_tags
        cfg["CURRENT_OUTFIT_NAME"] = self._current_name
        cfg["LAST_CHANGE_TIME"] = self._last_change_time

    def get_current_outfit_description(self) -> str:
        return self._current_description or DEFAULT_OUTFIT_DESCRIPTION

    def get_current_outfit_tags(self) -> str:
        return self._current_tags or DEFAULT_OUTFIT_TAGS

    def get_current_outfit_name(self) -> str:
        return self._current_name or DEFAULT_OUTFIT_NAME

    def get_last_change_time(self) -> str:
        return self._last_change_time

    async def apply_outfit(self, name: str, description: str, tags: str):
        """应用新服装并持久化"""
        self._current_name = name
        self._current_description = description
        self._current_tags = tags
        self._last_change_time = datetime.now(BEIJING_TZ).isoformat()
        self._consecutive_failures = 0

        self._sync_to_config()

        await chat_db_manager.set_global_setting("daily_outfit_description", description)
        await chat_db_manager.set_global_setting("daily_outfit_tags", tags)
        await chat_db_manager.set_global_setting("daily_outfit_name", name)
        await chat_db_manager.set_global_setting("daily_outfit_last_change", self._last_change_time)
        log.info(f"已应用新服装: {name}")

    async def revert_to_default(self):
        """恢复默认服装"""
        await self.apply_outfit(DEFAULT_OUTFIT_NAME, DEFAULT_OUTFIT_DESCRIPTION, DEFAULT_OUTFIT_TAGS)
        log.info("已恢复为默认服装。")

    async def design_new_outfit(self, force_style: Optional[str] = None) -> Dict[str, Any]:
        """调用 LLM 设计新服装"""
        from src.chat.config import chat_config
        cfg = chat_config.DAILY_OUTFIT_CONFIG

        api_url = cfg.get("DESIGNER_API_URL", "").strip()
        api_key = cfg.get("DESIGNER_API_KEY", "").strip()
        model = cfg.get("DESIGNER_MODEL", "").strip()

        if not api_url or not api_key or not model:
            raise ValueError("换装 API 未配置完整（需要 URL、Key、Model）")

        now = datetime.now(BEIJING_TZ)
        season = SEASON_MAP.get(now.month, "春季")
        style_pref = force_style or cfg.get("STYLE_PREFERENCE", "")
        custom_prompt = cfg.get("CUSTOM_PROMPT", "")

        user_msg = OUTFIT_DESIGNER_USER_TEMPLATE.format(
            current_date=now.strftime("%Y年%m月%d日 %A"),
            season=season,
            style_line=f"- 风格偏好: {style_pref}" if style_pref else "",
            custom_line=f"- 额外要求: {custom_prompt}" if custom_prompt else "",
        )

        try:
            result = await self._call_openai_compatible(
                api_url=api_url, api_key=api_key, model=model,
                system_prompt=OUTFIT_DESIGNER_SYSTEM_PROMPT,
                user_message=user_msg,
            )
        except Exception as e:
            self._consecutive_failures += 1
            log.error(f"换装 LLM 调用失败 (连续第 {self._consecutive_failures} 次): {e}")
            if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log.warning(f"连续失败 {self._consecutive_failures} 次，回退到默认服装。")
                await self.revert_to_default()
                return {"name": DEFAULT_OUTFIT_NAME, "description": DEFAULT_OUTFIT_DESCRIPTION,
                        "tags": DEFAULT_OUTFIT_TAGS, "reasoning": "连续请求失败，已回退默认服装"}
            raise

        parsed = self._parse_outfit_response(result)
        if not parsed:
            self._consecutive_failures += 1
            log.error(f"换装结果解析失败 (连续第 {self._consecutive_failures} 次)")
            if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log.warning(f"连续失败 {self._consecutive_failures} 次，回退到默认服装��")
                await self.revert_to_default()
                return {"name": DEFAULT_OUTFIT_NAME, "description": DEFAULT_OUTFIT_DESCRIPTION,
                        "tags": DEFAULT_OUTFIT_TAGS, "reasoning": "连续解析失败，已回退默认服装"}
            raise ValueError("LLM 返回的服装数据无法解析")

        cleaned_tags = self._sanitize_tags(parsed.get("tags", ""))
        if not cleaned_tags:
            cleaned_tags = DEFAULT_OUTFIT_TAGS

        await self.apply_outfit(
            name=parsed.get("name", "今日造型"),
            description=parsed.get("description", DEFAULT_OUTFIT_DESCRIPTION),
            tags=cleaned_tags,
        )

        return {
            "name": self._current_name,
            "description": self._current_description,
            "tags": self._current_tags,
            "reasoning": parsed.get("reasoning", ""),
        }

    async def _call_openai_compatible(
        self, api_url: str, api_key: str, model: str,
        system_prompt: str, user_message: str,
    ) -> str:
        """调用 OpenAI 兼容 API"""
        url = api_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            if url.endswith("/v1"):
                url += "/chat/completions"
            elif "/v1" in url:
                url += "/chat/completions"
            else:
                url += "/v1/chat/completions"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.8,
            "max_tokens": 1000,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"API 返回 {resp.status}: {body[:500]}")
                data = await resp.json()

        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("API 返回空 choices")

        return choices[0].get("message", {}).get("content", "")

    def _parse_outfit_response(self, raw: str) -> Optional[Dict[str, Any]]:
        """解析 LLM 返回的 JSON，兼容 markdown code block"""
        if not raw:
            return None

        # 尝试从 markdown code block 中提取
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw, re.DOTALL)
        text = match.group(1).strip() if match else raw.strip()

        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            # 再尝试找第一个 { 到最后一个 }
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    result = json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    log.error(f"无法解析换装 JSON: {text[:300]}")
                    return None
            else:
                return None

        if not isinstance(result, dict):
            return None
        if "description" not in result or "tags" not in result:
            log.error(f"换装 JSON 缺少必要字段: {list(result.keys())}")
            return None
        return result

    def _sanitize_tags(self, tags: str) -> str:
        """过滤掉身体特征和 NSFW tag"""
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        cleaned = []
        for tag in tag_list:
            normalized = tag.lower().strip()
            if normalized in FORBIDDEN_OUTFIT_TAGS:
                log.debug(f"过滤禁止 tag: {tag}")
                continue
            cleaned.append(tag)
        return ", ".join(cleaned)


outfit_service = OutfitService()
