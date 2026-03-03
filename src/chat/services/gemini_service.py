# -*- coding: utf-8 -*-

import os
import copy
import logging
from typing import Optional, Dict, List, Callable, Any
import asyncio
from functools import wraps
from concurrent.futures import ThreadPoolExecutor
import json
from datetime import datetime
from zoneinfo import ZoneInfo
import re
import base64
import ipaddress
import aiohttp
from urllib.parse import urlparse

from src.chat.services.regex_service import regex_service

from PIL import Image
import io

# 导入新库
from google import genai
from google.genai import types
from google.genai import errors as genai_errors

# 导入数据库管理器和提示词配置
from src.chat.utils.database import chat_db_manager
from src.chat.config import chat_config as app_config
from src.chat.utils.prompt_utils import replace_emojis
from src.chat.services.prompt_service import prompt_service
from src.chat.services.key_rotation_service import (
    KeyRotationService,
    NoAvailableKeyError,
)
from src.chat.features.tools.services.tool_service import ToolService
from src.chat.features.tools.tool_loader import load_tools_from_directory
from src.chat.features.chat_settings.services.chat_settings_service import (
    chat_settings_service,
)
from src.chat.utils.image_utils import sanitize_image, extract_image_frames_for_ai
from src.database.services.token_usage_service import token_usage_service
from src.database.database import AsyncSessionLocal


log = logging.getLogger(__name__)

# --- 设置专门用于记录无效 API 密钥的 logger ---
# 确保 data 目录存在
if not os.path.exists("data"):
    os.makedirs("data")

# 创建一个新的 logger 实例
invalid_key_logger = logging.getLogger("invalid_api_keys")
invalid_key_logger.setLevel(logging.ERROR)

# 创建文件处理器，将日志写入到 data/invalid_api_keys.log
# 使用 a 模式表示追加写入
fh = logging.FileHandler("data/invalid_api_keys.log", mode="a", encoding="utf-8")
fh.setLevel(logging.ERROR)

# 创建格式化器并设置
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
fh.setFormatter(formatter)

# 为 logger 添加处理器
# 防止重复添加处理器
if not invalid_key_logger.handlers:
    invalid_key_logger.addHandler(fh)


def _api_key_handler(func: Callable) -> Callable:
    """
    一个装饰器，用于优雅地处理 API 密钥的获取、释放和重试逻辑。
    实现了两层重试：
    1. 外层循环：持续获取可用密钥，如果所有密钥都在冷却，则会等待。
    2. 内层循环：对获取到的单个密钥，在遇到可重试错误时，会根据配置进行多次尝试。
    """

    @wraps(func)
    async def wrapper(self: "GeminiService", *args, **kwargs):
        max_key_rotations = max(1, app_config.API_RETRY_CONFIG.get("MAX_KEY_ROTATION_RETRIES", 3))
        for rotation_attempt in range(max_key_rotations):
            key_obj = None
            try:
                key_obj = await self.key_rotation_service.acquire_key()
                client = self._create_client_with_key(key_obj.key)
                max_attempts = max(1, app_config.API_RETRY_CONFIG["MAX_ATTEMPTS_PER_KEY"])
                for attempt in range(max_attempts):
                    try:
                        log.info(
                            f"使用密钥 ...{key_obj.key[-4:]} (尝试 {attempt + 1}/{max_attempts}) 调用 {func.__name__}"
                        )

                        kwargs["client"] = client
                        result = await func(self, *args, **kwargs)

                        is_blocked_by_safety = False
                        if isinstance(result, types.GenerateContentResponse):
                            self._handle_safety_ratings(result, key_obj.key)
                            if (
                                not result.parts
                                and result.prompt_feedback
                                and result.prompt_feedback.block_reason
                            ):
                                is_blocked_by_safety = True

                        if is_blocked_by_safety:
                            log.warning(
                                f"密钥 ...{key_obj.key[-4:]} 因安全策略被阻止 (原因: {result.prompt_feedback.block_reason if result.prompt_feedback else '未知'})。"
                            )
                            break

                        await self.key_rotation_service.release_key(key_obj.key, success=True)
                        return result

                    except (genai_errors.ClientError, genai_errors.ServerError) as e:
                        error_str = str(e)
                        match = re.match(r"(\d{3})", error_str)
                        status_code = int(match.group(1)) if match else None

                        retryable_status_codes = {403, 429, 500, 502, 503, 504, 520, 522, 524}
                        is_retryable = status_code in retryable_status_codes

                        if (
                            not is_retryable
                            and isinstance(e, genai_errors.ServerError)
                            and any(code in error_str for code in ["500", "502", "503", "504", "520", "522", "524"])
                        ):
                            is_retryable = True

                        if is_retryable and attempt < max_attempts - 1:
                            delay = app_config.API_RETRY_CONFIG["RETRY_DELAY_SECONDS"]
                            log.warning(
                                f"密钥 ...{key_obj.key[-4:]} 遇到可重试错误 (状态码: {status_code})，{delay} 秒后重试。"
                            )
                            await asyncio.sleep(delay)
                            continue

                        log.error(
                            f"使用密钥 ...{key_obj.key[-4:]} 时发生API错误 (状态码: {status_code}): {e}",
                            exc_info=True,
                        )
                        break

                    except Exception as e:
                        log.error(
                            f"使用密钥 ...{key_obj.key[-4:]} 时发生未知错误: {e}",
                            exc_info=True,
                        )
                        await self.key_rotation_service.release_key(key_obj.key, success=True)
                        if func.__name__ == "generate_embedding":
                            return None
                        if not kwargs.get("return_error_text", True):
                            return None
                        return "呜哇，有点晕嘞，等我休息一会儿 <伤心>"

                if key_obj:
                    await self.key_rotation_service.release_key(key_obj.key, success=True)

                # 避免单个故障密钥导致的紧循环
                await asyncio.sleep(0.5)

            except NoAvailableKeyError:
                log.error(
                    "所有API密钥均不可用，且 acquire_key 未能成功等待。这是异常情况。"
                )
                if not kwargs.get("return_error_text", True):
                    return None
                return "啊啊啊服务器要爆炸啦！现在有点忙不过来，你过一会儿再来找我玩吧！<生气>"

        # 所有密钥轮换尝试均已耗尽
        log.error(
            f"已达到最大密钥轮换重试次数 ({max_key_rotations})，所有尝试均失败。"
        )
        if not kwargs.get("return_error_text", True):
            return None
        return "呜...连续请求都失败了，服务可能暂时不太稳定，请稍后再试试吧 <委屈>"

    return wrapper


class GeminiService:
    """Gemini AI 服务类，使用数据库存储用户对话上下文"""

    SAFETY_PENALTY_MAP: Dict[str, int] = {
        "HARM_PROBABILITY_UNSPECIFIED": 0,
        "NEGLIGIBLE": 0,
        "LOW": 5,
        "MEDIUM": 15,
        "HIGH": 30,
    }

    def __init__(self):
        self.bot = None  # 用于存储 Discord Bot 实例

        # --- (新) SDK 底层调试日志 ---
        # 根据最新指南 (2025)，开启此选项可查看详细的 HTTP 请求/响应
        if app_config.DEBUG_CONFIG.get("LOG_SDK_HTTP_REQUESTS", False):
            log.info("已开启 google-genai SDK 底层 DEBUG 日志。")
            # 设置基础日志记录器以捕获 httpx 的调试信息
            logging.basicConfig(level=logging.DEBUG)
        # --- 密钥轮换服务 ---
        # 优先使用 GOOGLE_API_KEYS_LIST，其次使用 GEMINI_API_KEYS
        google_api_keys_str = os.getenv("GOOGLE_API_KEYS_LIST", "") or os.getenv("GEMINI_API_KEYS", "")
        if not google_api_keys_str:
            log.error("GOOGLE_API_KEYS_LIST 或 GEMINI_API_KEYS 环境变量未设置！服务将无法运行。")
            # 在这种严重配置错误下，抛出异常以阻止应用启动
            raise ValueError("GOOGLE_API_KEYS_LIST or GEMINI_API_KEYS is not set.")

        # 先移除整个字符串两端的空格和引号，以支持 "key1,key2" 格式
        # 同时移除换行符和回车符，避免 header injection 问题
        processed_keys_str = google_api_keys_str.strip().strip('"').replace('\n', '').replace('\r', '')
        api_keys = [key.strip().replace('\n', '').replace('\r', '') for key in processed_keys_str.split(",") if key.strip()]
        self.key_rotation_service = KeyRotationService(api_keys)
        log.info(
            f"GeminiService 初始化并由 KeyRotationService 管理 {len(api_keys)} 个密钥。"
        )
        
        # 保存原始密钥字符串以便热更新时比较
        self._current_keys_hash = hash(google_api_keys_str)

        self.default_model_name = app_config.GEMINI_MODEL
        self.executor = ThreadPoolExecutor(
            max_workers=app_config.MAX_CONCURRENT_REQUESTS
        )
        self.user_request_timestamps: Dict[int, List[datetime]] = {}
        self.safety_settings = [
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                threshold=types.HarmBlockThreshold.BLOCK_NONE,
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                threshold=types.HarmBlockThreshold.BLOCK_NONE,
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                threshold=types.HarmBlockThreshold.BLOCK_NONE,
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                threshold=types.HarmBlockThreshold.BLOCK_NONE,
            ),
        ]

        # --- 工具配置 (模块化标准) ---
        # 1. 使用加载器动态发现所有工具
        self.available_tools, self.tool_map = load_tools_from_directory(
            "src/chat/features/tools/functions"
        )

        # 2. 实例化工具服务，并传入工具映射
        self.tool_service = ToolService(
            bot=self.bot, tool_map=self.tool_map, tool_declarations=self.available_tools
        )

        log.info("--- 工具加载完成 (模块化) ---")
        log.info(
            f"已加载 {len(self.available_tools)} 个工具: {list(self.tool_map.keys())}"
        )
        log.info("------------------------------------")

    def set_bot(self, bot):
        """注入 Discord Bot 实例。"""
        self.bot = bot
        log.info("Discord Bot 实例已成功注入 GeminiService。")
        # 关键：同时将 bot 实例注入到 ToolService 中
        self.tool_service.bot = bot
        self.last_called_tools: List[str] = []
        log.info("Discord Bot 实例已成功注入 ToolService。")
    
    def reload_api_keys(self, new_keys_str: str = None) -> dict:
        """
        热更新 API 密钥。
        
        Args:
            new_keys_str: 新的密钥字符串（逗号分隔），如果为 None 则从环境变量重新加载
            
        Returns:
            dict: 包含更新状态的字典
        """
        try:
            # 如果没有传入新密钥，尝试从环境变量重新加载
            if new_keys_str is None:
                # 重新读取 .env 文件
                from dotenv import load_dotenv
                load_dotenv(override=True)
                new_keys_str = os.getenv("GOOGLE_API_KEYS_LIST", "") or os.getenv("GEMINI_API_KEYS", "")
            
            if not new_keys_str:
                return {"success": False, "error": "未找到 API 密钥配置"}
            
            # 检查是否有变化
            new_hash = hash(new_keys_str)
            if new_hash == self._current_keys_hash:
                return {"success": True, "message": "密钥未变化，无需更新", "count": len(self.key_rotation_service.keys)}
            
            # 清理并解析新密钥
            processed_keys_str = new_keys_str.strip().strip('"').replace('\n', '').replace('\r', '')
            api_keys = [key.strip().replace('\n', '').replace('\r', '') for key in processed_keys_str.split(",") if key.strip()]
            
            if not api_keys:
                return {"success": False, "error": "解析后没有有效的 API 密钥"}
            
            # 更新密钥轮换服务
            self.key_rotation_service = KeyRotationService(api_keys)
            self._current_keys_hash = new_hash
            
            log.info(f"✅ API 密钥已热更新，共 {len(api_keys)} 个密钥")
            return {"success": True, "message": f"已更新 {len(api_keys)} 个 API 密钥", "count": len(api_keys)}
            
        except Exception as e:
            log.error(f"热更新 API 密钥失败: {e}")
            return {"success": False, "error": str(e)}

    def _create_client_with_key(self, api_key: str):
        """使用给定的 API 密钥动态创建一个 Gemini 客户端实例。"""
        base_url = os.getenv("GEMINI_API_BASE_URL")
        if base_url:
            log.info(f"使用自定义 Gemini API 端点: {base_url}")
            # 根据用户提供的文档，正确的方法是使用 types.HttpOptions
            # Cloudflare Worker 需要 /gemini 后缀，所以我们不移除它
            http_options = types.HttpOptions(base_url=base_url)
            return genai.Client(api_key=api_key, http_options=http_options)
        else:
            log.info("使用默认 Gemini API 端点。")
            return genai.Client(api_key=api_key)

    async def get_user_conversation_history(
        self, user_id: int, guild_id: int
    ) -> List[Dict]:
        """从数据库获取用户的对话历史"""
        context = await chat_db_manager.get_ai_conversation_context(user_id, guild_id)
        if context and context.get("conversation_history"):
            return context["conversation_history"]
        return []

    # --- Refactored Cooldown Logic ---

    # --- Static Helper Methods for Serialization ---
    @staticmethod
    def _serialize_for_logging(obj):
        """自定义序列化函数，用于截断长文本以进行日志记录。"""
        if isinstance(obj, dict):
            return {
                key: GeminiService._serialize_for_logging(value)
                for key, value in obj.items()
            }
        elif isinstance(obj, list):
            return [GeminiService._serialize_for_logging(item) for item in obj]
        elif isinstance(obj, str) and len(obj) > 200:
            return obj[:200] + "..."
        elif isinstance(obj, Image.Image):
            return f"<PIL.Image object: mode={obj.mode}, size={obj.size}>"
        else:
            try:
                json.JSONEncoder().default(obj)
                return obj
            except TypeError:
                return str(obj)

    @staticmethod
    def _serialize_parts_for_error_logging(obj):
        """自定义序列化函数，用于在出现问题时记录请求体。"""
        if isinstance(obj, types.Part):
            if obj.text:
                return {"type": "text", "content": obj.text}
            elif obj.inline_data:
                return {
                    "type": "image",
                    "mime_type": obj.inline_data.mime_type,
                    "data_size": len(obj.inline_data.data)
                    if obj.inline_data and obj.inline_data.data
                    else 0,
                }
        elif isinstance(obj, Image.Image):
            return f"<PIL.Image object: mode={obj.mode}, size={obj.size}>"
        try:
            return json.JSONEncoder().default(obj)
        except TypeError:
            return str(obj)

    @staticmethod
    def _serialize_parts_for_logging_full(content: types.Content):
        """自定义序列化函数，用于完整记录 Content 对象。"""
        serialized_parts = []
        if content.parts:
            for part in content.parts:
                if part.text:
                    serialized_parts.append({"type": "text", "content": part.text})
                elif part.inline_data and part.inline_data.data:
                    serialized_parts.append(
                        {
                            "type": "image",
                            "mime_type": part.inline_data.mime_type,
                            "data_size": len(part.inline_data.data),
                            "data_preview": part.inline_data.data[:50].hex()
                            + "...",  # 记录数据前50字节的十六进制预览
                        }
                    )
                elif part.file_data:
                    serialized_parts.append(
                        {
                            "type": "file",
                            "mime_type": part.file_data.mime_type,
                            "file_uri": part.file_data.file_uri,
                        }
                    )
                else:
                    serialized_parts.append(
                        {"type": "unknown_part", "content": str(part)}
                    )
        return {"role": content.role, "parts": serialized_parts}

    # --- Refactored generate_response and its helpers ---
    def _prepare_api_contents(self, conversation: List[Dict]) -> List[types.Content]:
        """将对话历史转换为 API 所需的 Content 对象列表。"""
        processed_contents = []
        for turn in conversation:
            role = turn.get("role")
            parts_data = turn.get("parts", [])
            if not (role and parts_data):
                continue

            processed_parts = []
            for part_item in parts_data:
                if isinstance(part_item, str):
                    processed_parts.append(types.Part(text=part_item))
                elif isinstance(part_item, Image.Image):
                    buffered = io.BytesIO()
                    part_item.save(buffered, format="PNG")
                    img_bytes = buffered.getvalue()
                    processed_parts.append(
                        types.Part(
                            inline_data=types.Blob(
                                mime_type="image/png", data=img_bytes
                            )
                        )
                    )

            if processed_parts:
                processed_contents.append(
                    types.Content(role=role, parts=processed_parts)
                )
        return processed_contents

    async def _post_process_response(
        self, raw_response: str, user_id: int, guild_id: int
    ) -> str:
        """对 AI 的原始回复进行清理和处理。"""
        # 1. Clean various reply prefixes and tags
        reply_prefix_pattern = re.compile(
            r"^\s*([\[［]【回复|回复}\s*@.*?[\)）\]］])\s*", re.IGNORECASE
        )
        formatted = reply_prefix_pattern.sub("", raw_response)
        formatted = re.sub(
            r"<CURRENT_USER_MESSAGE_TO_REPLY.*?>", "", formatted, flags=re.IGNORECASE
        )
        formatted = regex_service.clean_ai_output(formatted)

        # 2. Remove old Discord emoji codes (like :emoji_name:)
        discord_emoji_pattern = re.compile(r":\w+:")
        formatted = discord_emoji_pattern.sub("", formatted)

        # 3. 清理 AI 错误输出的纯数字ID格式 <123456789>
        # 注意：保留完整的 Discord 表情格式 <:name:id> 和 <a:name:id>，因为用户可能让 AI 发送指定表情
        formatted = re.sub(r"<\d{15,}>", "", formatted)  # 只移除纯数字ID（Discord ID 至少15位）

        # 4. Replace custom emoji placeholders using the centralized function
        formatted = replace_emojis(formatted)

        return formatted

    def _extract_urls_from_text(self, text: str) -> List[str]:
        """从文本中提取 URL（支持 Markdown 链接与裸链接）。"""
        if not text:
            return []

        urls: List[str] = []

        markdown_link_pattern = re.compile(r"\[[^\]]+\]\((https?://[^\s\)]+)\)")
        bare_url_pattern = re.compile(r"(https?://[^\s<>\]\)]+)")

        for m in markdown_link_pattern.finditer(text):
            url = m.group(1).strip().rstrip(".,;:!?")
            if url and url not in urls:
                urls.append(url)

        for m in bare_url_pattern.finditer(text):
            url = m.group(1).strip().rstrip(".,;:!?")
            if url and url not in urls:
                urls.append(url)

        return urls

    def _extract_markdown_links_from_text(self, text: str) -> List[tuple]:
        """从文本中提取 Markdown 链接，返回 (title, url) 元组列表。
        同时也捕获裸 URL（标题设为'来源链接'）。"""
        if not text:
            return []

        links: List[tuple] = []
        seen_urls: set = set()

        # 先提取 Markdown 格式链接 [title](url)
        markdown_link_pattern = re.compile(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)")
        for m in markdown_link_pattern.finditer(text):
            title = m.group(1).strip()
            url = m.group(2).strip().rstrip(".,;:!?")
            if url and url not in seen_urls:
                seen_urls.add(url)
                links.append((title, url))

        # 再提取裸 URL（去重）
        bare_url_pattern = re.compile(r"(https?://[^\s<>\]\)]+)")
        for m in bare_url_pattern.finditer(text):
            url = m.group(1).strip().rstrip(".,;:!?")
            if url and url not in seen_urls:
                seen_urls.add(url)
                links.append(("来源链接", url))

        return links

    @staticmethod
    def _is_private_or_local_url(url: str) -> bool:
        """判断 URL 是否为本地/内网地址。"""
        if not url:
            return True

        try:
            parsed = urlparse(url.strip())
            hostname = (parsed.hostname or "").strip().lower().rstrip(".")
            if not hostname:
                return True

            # 常见本地/内网域名
            local_hosts = {"localhost", "localhost.localdomain"}
            local_suffixes = (".local", ".lan", ".internal", ".home", ".corp")
            if hostname in local_hosts or any(hostname.endswith(s) for s in local_suffixes):
                return True

            try:
                ip = ipaddress.ip_address(hostname)
                return (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_multicast
                    or ip.is_reserved
                    or ip.is_unspecified
                )
            except ValueError:
                # 非 IP 的公网域名
                return False
        except Exception:
            # 解析异常时保守处理，避免泄露内网地址
            return True

    def _append_message_sources_if_needed(
        self, response_text: str, source_links: List[tuple]
    ) -> str:
        """
        在最终回复末尾补充"消息源"链接（Markdown 格式 [标题](URL)）：
        - 若回复里已包含全部来源链接，则保持不变。
        - 若缺失来源链接，则追加"消息源"区块。
        """
        if not source_links:
            return response_text

        # 去重
        unique_links: List[tuple] = []
        seen: set = set()
        for title, url in source_links:
            cleaned_url = (url or "").strip().rstrip(".,;:!?")
            if not cleaned_url:
                continue
            if self._is_private_or_local_url(cleaned_url):
                log.debug(f"已过滤内网/本地来源链接: {cleaned_url}")
                continue
            if cleaned_url not in seen:
                seen.add(cleaned_url)
                unique_links.append((title, cleaned_url))

        if not unique_links:
            return response_text

        existing_urls = self._extract_urls_from_text(response_text)
        missing_links = [(t, u) for t, u in unique_links if u not in existing_urls]

        if not missing_links:
            return response_text

        source_lines = ["", "消息源："]
        for idx, (title, url) in enumerate(missing_links[:10], 1):
            source_lines.append(f"{idx}. [{title}](<{url}>)")

        return response_text.rstrip() + "\n" + "\n".join(source_lines)

    @staticmethod
    def _build_tool_call_signature(tool_name: str, tool_args: Any) -> str:
        """构建稳定的工具调用签名，用于检测重复调用。"""
        normalized_args = ""
        if isinstance(tool_args, str):
            normalized_args = tool_args.strip()
        else:
            try:
                normalized_args = json.dumps(
                    tool_args,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
            except Exception:
                normalized_args = str(tool_args)

        return f"{tool_name}:{normalized_args}"

    @staticmethod
    def _build_web_search_skip_message(reason: str) -> str:
        """当检测到 web_search 循环时，返回给模型的强约束提示。"""
        return (
            f"[web_search 已跳过] {reason}\n"
            "请不要再次调用 web_search。"
            "直接基于当前已返回的搜索结果组织最终回答；"
            "若信息不足，明确说明缺失点并请用户补充关键词。"
        )
    @staticmethod
    def _is_summary_or_search_tool(tool_name: str) -> bool:
        normalized = str(tool_name or "").strip().lower()
        if not normalized or normalized == "generate_voice":
            return False

        explicit_tools = {
            "web_search",
            "summarize_channel",
            "search_channel_history",
            "search_forum_threads",
            "query_tutorial_knowledge_base",
        }
        if normalized in explicit_tools:
            return True

        return ("search" in normalized) or ("summar" in normalized) or ("summary" in normalized)

    @classmethod
    def _should_block_generate_voice_for_info_context(
        cls,
        called_tool_names: Optional[List[str]] = None,
        current_turn_tool_names: Optional[List[str]] = None,
    ) -> bool:
        all_names: List[str] = []
        if called_tool_names:
            all_names.extend(str(name).strip() for name in called_tool_names if str(name).strip())
        if current_turn_tool_names:
            all_names.extend(
                str(name).strip() for name in current_turn_tool_names if str(name).strip()
            )

        return any(cls._is_summary_or_search_tool(name) for name in all_names)

    @staticmethod
    def _build_voice_skip_message_for_info_context() -> str:
        return (
            "[语音调用已跳过] 当前处于搜索/总结场景。"
            "请不要调用 generate_voice，直接使用文字回复。"
            "即使用户语气激烈或带辱骂，也保持文字输出。"
        )

    @staticmethod
    def _exception_chain_contains_timeout(error: BaseException) -> bool:
        """判断异常链中是否包含超时信号（含 TimeoutError 文本为空的场景）。"""
        visited: set[int] = set()
        current: Optional[BaseException] = error

        while current is not None:
            current_id = id(current)
            if current_id in visited:
                break
            visited.add(current_id)

            if isinstance(current, asyncio.TimeoutError):
                return True

            class_name = current.__class__.__name__.lower()
            error_text = str(current).lower()
            if "timeout" in class_name or "timed out" in error_text or "超时" in error_text:
                return True

            current = current.__cause__ or current.__context__

        return False

    def _handle_safety_ratings(
        self, response: types.GenerateContentResponse, key: str
    ) -> int:
        """检查响应的安全评分并返回相应的惩罚值。"""
        total_penalty = 0
        if not response.candidates:
            return 0

        candidate = response.candidates[0]
        if candidate.safety_ratings:
            for rating in candidate.safety_ratings:
                # 将枚举值转换为字符串键
                category_name = (
                    rating.category.name.replace("HARM_CATEGORY_", "")
                    if rating.category
                    else "UNKNOWN"
                )
                severity_name = (
                    rating.probability.name if rating.probability else "UNKNOWN"
                )

                penalty = self.SAFETY_PENALTY_MAP.get(severity_name, 0)
                if penalty > 0:
                    log.warning(
                        f"密钥 ...{key[-4:]} 收到安全警告。类别: {category_name}, 严重性: {severity_name}, 惩罚: {penalty}"
                    )
                    total_penalty += penalty
        return total_penalty

    @staticmethod
    def _is_no_thinking_model(*model_names: Optional[str]) -> bool:
        for model_name in model_names:
            if isinstance(model_name, str) and 'nothinking' in model_name.lower():
                return True
        return False

    @staticmethod
    def _extract_unsupported_param_from_error(error_message: str) -> Optional[str]:
        if not error_message:
            return None

        lowered = error_message.lower()
        marker = 'generation_config.'
        marker_index = lowered.find(marker)
        if marker_index != -1:
            tail = lowered[marker_index + len(marker):]
            param_chars = []
            for char in tail:
                if char.isalnum() or char == '_':
                    param_chars.append(char)
                else:
                    break
            if param_chars:
                return ''.join(param_chars).lstrip('_')

        unsupported_marker = 'unsupported parameter'
        marker_index = lowered.find(unsupported_marker)
        if marker_index != -1:
            tail = lowered[marker_index + len(unsupported_marker):]
            while tail and not (tail[0].isalnum() or tail[0] == '_'):
                tail = tail[1:]

            param_chars = []
            for char in tail:
                if char.isalnum() or char in ('_', '.'):
                    param_chars.append(char)
                else:
                    break
            if param_chars:
                return ''.join(param_chars).split('.')[-1].lstrip('_')

        support_marker = 'parameter '
        marker_index = lowered.find(support_marker)
        if marker_index != -1 and 'is not supported' in lowered[marker_index:]:
            tail = lowered[marker_index + len(support_marker):]
            while tail and not (tail[0].isalnum() or tail[0] == '_'):
                tail = tail[1:]

            param_chars = []
            for char in tail:
                if char.isalnum() or char in ('_', '.'):
                    param_chars.append(char)
                else:
                    break
            if param_chars:
                return ''.join(param_chars).split('.')[-1].lstrip('_')

        return None

    def _drop_unsupported_generation_param(
        self,
        param_name: str,
        gen_config_data: Dict[str, Any],
        thinking_config_data: Optional[Dict[str, Any]],
    ) -> tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        normalized_param = param_name.split('.')[-1].lstrip('_')

        if normalized_param == 'thinking_config':
            if thinking_config_data is not None:
                return True, None, 'thinking_config'
            return False, thinking_config_data, None

        if normalized_param in gen_config_data:
            gen_config_data.pop(normalized_param, None)
            return True, thinking_config_data, normalized_param

        if thinking_config_data and normalized_param in thinking_config_data:
            thinking_config_data.pop(normalized_param, None)
            if not thinking_config_data:
                thinking_config_data = None
            return True, thinking_config_data, f'thinking_config.{normalized_param}'

        if normalized_param.startswith('thinking') and thinking_config_data is not None:
            return True, None, 'thinking_config'

        return False, thinking_config_data, None

    def _build_generate_content_config(
        self,
        gen_config_data: Dict[str, Any],
        thinking_config_data: Optional[Dict[str, Any]],
    ) -> types.GenerateContentConfig:
        gen_config = types.GenerateContentConfig(
            **gen_config_data,
            safety_settings=self.safety_settings,
        )
        if thinking_config_data:
            gen_config.thinking_config = types.ThinkingConfig(**thinking_config_data)
        return gen_config

    def _build_openai_chat_payload(
        self,
        model_name: str,
        messages: List[Dict[str, Any]],
        max_tokens: int,
        temperature: Optional[float] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        disabled_fields: Optional[set[str]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            'model': model_name,
            'messages': messages,
            'max_tokens': max_tokens,
        }
        if temperature is not None:
            payload['temperature'] = temperature
        if tools:
            payload['tools'] = tools
            payload['tool_choice'] = 'auto'

        for field in disabled_fields or set():
            payload.pop(field, None)

        return payload

    @staticmethod
    def _build_openai_image_content_parts(
        images: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        """构建 OpenAI 兼容消息中的图片 content parts。"""
        if not images:
            return []

        max_images = app_config.IMAGE_PROCESSING_CONFIG.get("MAX_IMAGES_PER_MESSAGE", 9)
        parts: List[Dict[str, Any]] = []

        for img_data in images[:max_images]:
            try:
                img_bytes = img_data.get("data") or img_data.get("bytes")
                if not img_bytes:
                    continue

                mime_type = str(img_data.get("mime_type") or "image/png").strip() or "image/png"
                img_base64 = base64.b64encode(img_bytes).decode("utf-8")
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{img_base64}"
                        },
                    }
                )
            except Exception as e:
                log.warning(f"构建 OpenAI 图片 part 失败，已跳过该图片: {e}")

        return parts

    @staticmethod
    def _build_gemini_parts_from_images(
        images: Optional[List[Dict[str, Any]]],
    ) -> List[types.Part]:
        """构建 Gemini SDK 的图片 parts，支持 GIF 拆帧。"""
        if not images:
            return []

        max_images = app_config.IMAGE_PROCESSING_CONFIG.get("MAX_IMAGES_PER_MESSAGE", 9)
        max_gif_frames = app_config.IMAGE_PROCESSING_CONFIG.get("GIF_MAX_FRAMES", 4)
        parts: List[types.Part] = []

        for idx, img_data in enumerate(images[:max_images], start=1):
            try:
                img_bytes = img_data.get("data") or img_data.get("bytes")
                if not img_bytes:
                    continue

                mime_type = str(img_data.get("mime_type") or "image/png").strip() or "image/png"
                frames, frame_meta = extract_image_frames_for_ai(
                    image_bytes=img_bytes,
                    mime_type=mime_type,
                    max_gif_frames=max_gif_frames,
                )

                if frame_meta.get("is_animated"):
                    sampled_frames = frame_meta.get("sampled_frames", len(frames))
                    total_frames = frame_meta.get("total_frames", len(frames))
                    parts.append(
                        types.Part(
                            text=(
                                f"参考图{idx}为GIF，已抽取关键帧 {sampled_frames}/{total_frames} 参与分析。"
                            )
                        )
                    )

                for frame in frames:
                    with io.BytesIO() as output_buffer:
                        frame.save(output_buffer, format="PNG")
                        frame_bytes = output_buffer.getvalue()
                    parts.append(
                        types.Part(
                            inline_data=types.Blob(
                                mime_type="image/png",
                                data=frame_bytes,
                            )
                        )
                    )
            except Exception as e:
                log.warning(f"构建 Gemini 图片 part 失败，已跳过第 {idx} 张参考图: {e}")

        return parts

    @staticmethod
    def _build_gemini_contents_from_messages(
        messages: List[Dict[str, str]],
        images: Optional[List[Dict[str, Any]]] = None,
    ) -> List[types.Content]:
        """将 messages 列表转换为 Gemini SDK 的 Contents 列表。
        messages 格式: [{"role": "user"|"model", "content": "..."}]
        """
        contents = []
        target_user_index: Optional[int] = None
        if images:
            for idx in range(len(messages) - 1, -1, -1):
                role = messages[idx].get("role", "user")
                if role == "user":
                    target_user_index = idx
                    break
            if target_user_index is None and messages:
                target_user_index = len(messages) - 1

        for msg_index, msg in enumerate(messages):
            role = msg.get("role", "user")
            # 将 "assistant" 映射为 Gemini 的 "model"
            if role == "assistant":
                role = "model"
            content_text = msg.get("content", "")

            parts: List[types.Part] = []
            if content_text:
                parts.append(types.Part(text=content_text))

            if (
                images
                and target_user_index is not None
                and msg_index == target_user_index
            ):
                parts.extend(GeminiService._build_gemini_parts_from_images(images))

            if not parts:
                parts = [types.Part(text="")]

            contents.append(
                types.Content(
                    role=role,
                    parts=parts,
                )
            )
        return contents

    async def _generate_sync_content_with_param_fallback(
        self,
        client: Any,
        model_name: str,
        prompt: str,
        generation_config: Dict[str, Any],
        messages: Optional[List[Dict[str, str]]] = None,
        images: Optional[List[Dict[str, Any]]] = None,
    ) -> Any:
        loop = asyncio.get_event_loop()
        gen_config_data = copy.deepcopy(generation_config)
        thinking_config_data = gen_config_data.pop('thinking_config', None)
        gen_config_params = {**gen_config_data, 'safety_settings': self.safety_settings}

        if self._is_no_thinking_model(model_name):
            thinking_config_data = None

        gen_config = types.GenerateContentConfig(**gen_config_params)
        if thinking_config_data:
            gen_config.thinking_config = types.ThinkingConfig(**thinking_config_data)

        # 构建 contents：优先使用 messages（多轮预填充），否则回退到单个 prompt
        if messages:
            contents = self._build_gemini_contents_from_messages(
                messages,
                images=images,
            )
            log.debug(f"_generate_sync_content_with_param_fallback 使用多轮 messages ({len(messages)} 条)")
        elif images:
            prompt_parts: List[types.Part] = []
            if prompt:
                prompt_parts.append(types.Part(text=prompt))
            prompt_parts.extend(self._build_gemini_parts_from_images(images))
            if not prompt_parts:
                prompt_parts = [types.Part(text="请根据参考图生成结果。")]
            contents = [types.Content(role="user", parts=prompt_parts)]
        else:
            contents = [prompt]

        for _ in range(4):
            try:
                final_contents = contents  # 闭包捕获
                return await loop.run_in_executor(
                    self.executor,
                    lambda: client.models.generate_content(
                        model=model_name,
                        contents=final_contents,
                        config=gen_config,
                    ),
                )
            except genai_errors.ClientError as e:
                unsupported_param = self._extract_unsupported_param_from_error(str(e))
                if unsupported_param:
                    removed, thinking_config_data, removed_key = (
                        self._drop_unsupported_generation_param(
                            unsupported_param,
                            gen_config_params,
                            thinking_config_data,
                        )
                    )
                    if removed:
                        gen_config = types.GenerateContentConfig(**gen_config_params)
                        if thinking_config_data:
                            gen_config.thinking_config = types.ThinkingConfig(
                                **thinking_config_data
                            )
                        log.warning(
                            f'gemini simple auto-drop unsupported param: {removed_key or unsupported_param}'
                        )
                        continue
                raise

        raise RuntimeError('generate_content retries exhausted')

    async def _post_openai_chat_completion_with_fallback(
        self,
        api_url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
        timeout_seconds: int,
        disabled_payload_fields: set[str],
        log_prefix: str,
    ) -> Dict[str, Any]:
        retry_config = app_config.API_RETRY_CONFIG
        max_attempts = max(
            1, int(retry_config.get("OPENAI_COMPAT_MAX_ATTEMPTS", 3))
        )
        retry_base_delay = max(
            0.2,
            float(retry_config.get("OPENAI_COMPAT_RETRY_BASE_DELAY_SECONDS", 1.0)),
        )
        retryable_status_codes = {
            int(str(code).strip())
            for code in retry_config.get(
                "OPENAI_COMPAT_RETRYABLE_STATUS_CODES",
                ["408", "425", "429", "500", "502", "503", "504", "520", "522", "524"],
            )
            if str(code).strip().isdigit()
        }
        protected_fields = {"model", "messages", "tools", "tool_choice"}
        last_error_text = ""

        for attempt in range(max_attempts):
            effective_payload = payload.copy()
            for field in disabled_payload_fields:
                effective_payload.pop(field, None)

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        api_url,
                        headers=headers,
                        json=effective_payload,
                        timeout=aiohttp.ClientTimeout(total=timeout_seconds),
                    ) as response:
                        try:
                            response_text = await response.text()
                        except (aiohttp.ClientError, asyncio.TimeoutError) as read_error:
                            read_error_text = str(read_error).strip()
                            read_error_repr = (
                                f"{type(read_error).__name__}: {read_error_text}"
                                if read_error_text
                                else type(read_error).__name__
                            )
                            last_error_text = read_error_repr
                            log.warning(
                                f"{log_prefix} failed to read response body "
                                f"(attempt {attempt + 1}/{max_attempts}): {read_error_repr}"
                            )
                            if attempt < max_attempts - 1:
                                await asyncio.sleep(min(2.0, 0.5 * (attempt + 1)))
                                continue
                            raise Exception(
                                f"{log_prefix} failed to read response body: {read_error_repr}"
                            )

                        if response.status == 200:
                            try:
                                return json.loads(response_text)
                            except json.JSONDecodeError as decode_error:
                                body_preview = response_text[:500].replace("\n", "\\n")
                                last_error_text = (
                                    f"invalid json: {decode_error}; body={body_preview}"
                                )
                                log.warning(
                                    f"{log_prefix} returned invalid json "
                                    f"(attempt {attempt + 1}/{max_attempts}): {decode_error}; "
                                    f"body_preview={body_preview}"
                                )
                                if attempt < max_attempts - 1:
                                    await asyncio.sleep(min(2.0, 0.5 * (attempt + 1)))
                                    continue
                                raise Exception(
                                    f"{log_prefix} returned invalid json: {decode_error}"
                                )

                        error_text = response_text
                        last_error_text = error_text
                        status_code = int(response.status)

                        if status_code in retryable_status_codes and attempt < max_attempts - 1:
                            delay = min(retry_base_delay * (2 ** attempt), 8.0)
                            log.warning(
                                f"{log_prefix} returned retryable status {status_code} "
                                f"(attempt {attempt + 1}/{max_attempts}), retry after {delay:.1f}s"
                            )
                            await asyncio.sleep(delay)
                            continue

                        log.error(f"{log_prefix} returned {status_code}: {error_text}")

                        unsupported_param = self._extract_unsupported_param_from_error(
                            error_text
                        )
                        if (
                            unsupported_param
                            and unsupported_param in effective_payload
                            and unsupported_param not in protected_fields
                            and unsupported_param not in disabled_payload_fields
                        ):
                            disabled_payload_fields.add(unsupported_param)
                            log.warning(
                                f"{log_prefix} dropped unsupported param: {unsupported_param}"
                            )
                            continue

                        try:
                            error_json = json.loads(error_text)
                            error_msg = error_json.get("error", {}).get(
                                "message", error_text
                            )
                        except Exception:
                            error_msg = error_text

                        raise Exception(f"API returned {response.status}: {error_msg}")

            except (aiohttp.ClientError, asyncio.TimeoutError) as request_error:
                request_error_text = str(request_error).strip()
                request_error_repr = (
                    f"{type(request_error).__name__}: {request_error_text}"
                    if request_error_text
                    else type(request_error).__name__
                )
                last_error_text = request_error_repr
                log.warning(
                    f"{log_prefix} request failed "
                    f"(attempt {attempt + 1}/{max_attempts}): {request_error_repr}"
                )
                if attempt < max_attempts - 1:
                    delay = min(retry_base_delay * (2 ** attempt), 8.0)
                    await asyncio.sleep(delay)
                    continue
                raise Exception(f"{log_prefix} request failed: {request_error_repr}")

        raise Exception(f"{log_prefix} retries exhausted: {last_error_text}")

    async def generate_response(
        self,
        user_id: int,
        guild_id: int,
        message: str,
        channel: Optional[Any] = None,
        replied_message: Optional[str] = None,
        images: Optional[List[Dict]] = None,
        user_name: str = "用户",
        channel_context: Optional[List[Dict]] = None,
        world_book_entries: Optional[List[Dict]] = None,
        personal_summary: Optional[str] = None,
        affection_status: Optional[Dict[str, Any]] = None,
        user_profile_data: Optional[Dict[str, Any]] = None,
        guild_name: str = "未知服务器",
        location_name: str = "未知位置",
        model_name: Optional[str] = None,
        discord_message: Optional[Any] = None,  # Discord Message对象，用于工具调用时添加反应
        user_id_for_settings: Optional[str] = None,
    ) -> str:
        """
        AI 回复生成的分发器。
        如果选择了自定义模型，则优先尝试自定义端点；如果失败，则自动回退到官方 API。
        """
        # 判断是否应该使用自定义端点：
        # 1. 模型名在预定义的 CUSTOM_GEMINI_ENDPOINTS 中
        # 2. 或者 Dashboard 配置了全局 API URL（存储在 _db_api_url）
        custom_endpoint_from_model = bool(
            model_name and model_name in app_config.CUSTOM_GEMINI_ENDPOINTS
        )
        custom_endpoint_from_global_url = bool(getattr(app_config, "_db_api_url", None))
        use_custom_endpoint = custom_endpoint_from_model or custom_endpoint_from_global_url

        if custom_endpoint_from_global_url:
            log.info(
                f"检测到 Dashboard 配置的自定义 API URL，将为模型 '{model_name}' 使用自定义端点。"
            )

        # 稳定性优化：
        # 当仅依赖 Dashboard 全局 URL 且格式是 openai 时，
        # 直接走 OpenAI 兼容路径，避免误入官方 Gemini SDK 路径导致参数不兼容。
        api_format = str(getattr(app_config, "_db_api_format", None) or "gemini").strip().lower()
        if (
            use_custom_endpoint
            and (not custom_endpoint_from_model)
            and custom_endpoint_from_global_url
            and api_format in {"openai", "openai_compatible", "openai-compatible"}
        ):
            direct_model_name = model_name or self.default_model_name
            log.info(
                "检测到 Dashboard 全局 OpenAI 兼容端点且当前模型无专属端点配置，"
                "将直接使用 OpenAI 兼容路径（跳过官方 API 回退）。"
                f"模型: '{direct_model_name}'。"
            )
            return await self._generate_with_custom_endpoint(
                user_id=user_id,
                guild_id=guild_id,
                message=message,
                channel=channel,
                replied_message=replied_message,
                images=images,
                user_name=user_name,
                channel_context=channel_context,
                world_book_entries=world_book_entries,
                personal_summary=personal_summary,
                affection_status=affection_status,
                user_profile_data=user_profile_data,
                guild_name=guild_name,
                location_name=location_name,
                model_name=direct_model_name,
                discord_message=discord_message,
                user_id_for_settings=user_id_for_settings,
            )

        if use_custom_endpoint:
            log.info(f"检测到自定义模型 '{model_name}'，将优先尝试使用自定义端点。")
            max_attempts = 2  # 1次主尝试 + 1次重试
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    log.info(
                        f"尝试使用自定义端点 '{model_name}' (尝试 {attempt + 1}/{max_attempts})"
                    )
                    return await self._generate_with_custom_endpoint(
                        user_id=user_id,
                        guild_id=guild_id,
                        message=message,
                        channel=channel,
                        replied_message=replied_message,
                        images=images,
                        user_name=user_name,
                        channel_context=channel_context,
                        world_book_entries=world_book_entries,
                        personal_summary=personal_summary,
                        affection_status=affection_status,
                        user_profile_data=user_profile_data,
                        guild_name=guild_name,
                        location_name=location_name,
                        model_name=model_name,
                        discord_message=discord_message,
                        user_id_for_settings=user_id_for_settings,
                    )
                except Exception as e:
                    last_exception = e
                    log.warning(
                        f"使用自定义端点 '{model_name}' (尝试 {attempt + 1}/{max_attempts}) 失败: {e}"
                    )
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(1)  # 在重试前稍作等待

            # 如果所有尝试都失败了，则执行回退逻辑
            fallback_model_name = self.default_model_name

            if api_format in {"openai", "openai_compatible", "openai-compatible"}:
                log.warning(
                    f"自定义端点 '{model_name}' 的所有 {max_attempts} 次尝试均失败。最终错误: {last_exception}. "
                    "当前为 OpenAI 兼容格式，将回退到 OpenAI 兼容路径。"
                )
                return await self._generate_with_custom_endpoint(
                    user_id=user_id,
                    guild_id=guild_id,
                    message=message,
                    channel=channel,
                    replied_message=replied_message,
                    images=images,
                    user_name=user_name,
                    channel_context=channel_context,
                    world_book_entries=world_book_entries,
                    personal_summary=personal_summary,
                    affection_status=affection_status,
                    user_profile_data=user_profile_data,
                    guild_name=guild_name,
                    location_name=location_name,
                    model_name=fallback_model_name,
                    discord_message=discord_message,
                    user_id_for_settings=user_id_for_settings,
                )

            log.warning(
                f"自定义端点 '{model_name}' 的所有 {max_attempts} 次尝试均失败。最终错误: {last_exception}. "
                "将回退到官方 API。"
            )
            log.info(f"回退到官方 API，使用默认模型 '{fallback_model_name}'。")

            return await self._generate_with_official_api(
                user_id=user_id,
                guild_id=guild_id,
                message=message,
                channel=channel,
                replied_message=replied_message,
                images=images,
                user_name=user_name,
                channel_context=channel_context,
                world_book_entries=world_book_entries,
                personal_summary=personal_summary,
                affection_status=affection_status,
                user_profile_data=user_profile_data,
                guild_name=guild_name,
                location_name=location_name,
                model_name=fallback_model_name,  # 关键：使用固定的回退模型
                discord_message=discord_message,
                user_id_for_settings=user_id_for_settings,
            )

        # 对于非自定义模型或回退失败后的默认路径
        log.info(
            f"使用模型 '{model_name or self.default_model_name}'，将使用官方 API 逻辑。"
        )
        return await self._generate_with_official_api(
            user_id=user_id,
            guild_id=guild_id,
            message=message,
            channel=channel,
            replied_message=replied_message,
            images=images,
            user_name=user_name,
            channel_context=channel_context,
            world_book_entries=world_book_entries,
            personal_summary=personal_summary,
            affection_status=affection_status,
            user_profile_data=user_profile_data,
            guild_name=guild_name,
            location_name=location_name,
            model_name=model_name,
            discord_message=discord_message,
            user_id_for_settings=user_id_for_settings,
        )

    async def _generate_with_custom_endpoint(
        self,
        user_id: int,
        guild_id: int,
        message: str,
        channel: Optional[Any] = None,
        replied_message: Optional[str] = None,
        images: Optional[List[Dict]] = None,
        user_name: str = "用户",
        channel_context: Optional[List[Dict]] = None,
        world_book_entries: Optional[List[Dict]] = None,
        personal_summary: Optional[str] = None,
        affection_status: Optional[Dict[str, Any]] = None,
        user_profile_data: Optional[Dict[str, Any]] = None,
        guild_name: str = "未知服务器",
        location_name: str = "未知位置",
        model_name: Optional[str] = None,
        discord_message: Optional[Any] = None,
        user_id_for_settings: Optional[str] = None,
    ) -> str:
        """
        [新增] 使用自定义端点 (例如公益站) 生成 AI 回复。
        此方法不使用密钥轮换，直接根据配置创建客户端。
        失败时会抛出异常，由调用方 (generate_response) 处理回退逻辑。
        """
        if not model_name:
            raise ValueError("调用自定义端点时需要提供 model_name。")
        
        endpoint_config = app_config.CUSTOM_GEMINI_ENDPOINTS.get(model_name, {}).copy()
        
        # 从 chat_config 中获取 Dashboard 保存的全局配置
        global_api_url = getattr(app_config, '_db_api_url', None) or os.getenv("GEMINI_API_BASE_URL", "")
        global_api_key = getattr(app_config, '_db_api_key', None) or os.getenv("GEMINI_API_KEYS", "")
        
        # 如果端点配置为空（模型名不在预定义列表中），创建一个使用全局配置的端点
        if not endpoint_config:
            log.info(f"模型 '{model_name}' 不在预定义端点列表中，将使用 Dashboard 全局配置。")
            endpoint_config = {
                "base_url": global_api_url,
                "api_key": global_api_key,
                "model_name": model_name,  # 直接使用用户指定的模型名
            }
        else:
            # 如果端点配置中缺少 base_url 或 api_key，从全局配置补充
            if not endpoint_config.get("base_url") and global_api_url:
                endpoint_config["base_url"] = global_api_url
                log.info(f"  - 使用全局 API URL: {global_api_url[:30]}...")
            if not endpoint_config.get("api_key") and global_api_key:
                endpoint_config["api_key"] = global_api_key
                log.info(f"  - 使用全局 API Key")
        
        # 再次检查配置是否完整
        if not endpoint_config.get("base_url") or not endpoint_config.get("api_key"):
            error_msg = (
                f"模型 '{model_name}' 的自定义端点配置不完整。"
                "请在 Dashboard 中设置 API URL 和 API Key，或设置环境变量。"
            )
            log.error(error_msg)
            raise ValueError(error_msg)

        # 获取 API 格式配置
        api_format = str(getattr(app_config, '_db_api_format', None) or "gemini").strip().lower()
        log.info(f"正在为自定义端点创建客户端: {endpoint_config['base_url']} (格式: {api_format})")
        
        # 如果是 OpenAI 兼容格式，使用 OpenAI 客户端
        if api_format in {"openai", "openai_compatible", "openai-compatible"}:
            return await self._generate_with_openai_compatible(
                user_id=user_id,
                guild_id=guild_id,
                message=message,
                channel=channel,
                replied_message=replied_message,
                images=images,
                user_name=user_name,
                channel_context=channel_context,
                world_book_entries=world_book_entries,
                personal_summary=personal_summary,
                affection_status=affection_status,
                user_profile_data=user_profile_data,
                guild_name=guild_name,
                location_name=location_name,
                model_name=endpoint_config.get("model_name") or model_name,
                discord_message=discord_message,
                api_url=endpoint_config["base_url"],
                api_key=endpoint_config["api_key"],
            )
        
        # Gemini 格式：使用 Gemini SDK
        http_options = types.HttpOptions(base_url=endpoint_config["base_url"])
        client = genai.Client(
            api_key=endpoint_config["api_key"], http_options=http_options
        )

        # --- [重构] 针对自定义端点的图片净化 ---
        # 只有在调用自定义端点时才执行此操作，因为官方API可以处理这些图片。
        # 使用顺序处理策略：一张一张处理，处理完一张释放内存，再处理下一张
        sanitized_images_for_endpoint = []
        if images:
            total_images = len(images)
            max_images = app_config.IMAGE_PROCESSING_CONFIG.get(
                "MAX_IMAGES_PER_MESSAGE", 9
            )
            sequential_processing = app_config.IMAGE_PROCESSING_CONFIG.get(
                "SEQUENTIAL_PROCESSING", True
            )

            log.info(f"检测到 {total_images} 张图片，将为自定义端点进行净化处理。")

            # 限制处理的图片数量
            images_to_process = images[:max_images]
            if total_images > max_images:
                log.warning(
                    f"图片数量 ({total_images}) 超过最大限制 ({max_images})，将只处理前 {max_images} 张。"
                )

            for idx, img_data in enumerate(images_to_process, 1):
                source = img_data.get("source")
                mime_type = (img_data.get("mime_type") or "").lower()
                is_attachment_source = source in ("attachment", "replied_attachment")
                is_gif_image = "gif" in mime_type

                # GIF 需要在 prompt_service 中进行拆帧，不能在这里先净化成静态 WEBP。
                if is_attachment_source and is_gif_image:
                    log.info(
                        f"第 {idx}/{len(images_to_process)} 张图片为 GIF，保留原图用于后续自动拆帧。"
                    )
                    sanitized_images_for_endpoint.append(img_data)
                    continue

                # --- [优化] 仅当图片来源是用户附件/回复附件且非 GIF 时才进行净化 ---
                if is_attachment_source:
                    try:
                        # [修复] 增加健壮性，同时检查 'data' 和 'bytes' 键
                        image_bytes = img_data.get("data") or img_data.get("bytes")
                        if not image_bytes:
                            log.warning(
                                f"附件图片数据字典中缺少 'data' 或 'bytes' 键，已跳过。Keys: {list(img_data.keys())}"
                            )
                            continue

                        log.info(f"正在处理第 {idx}/{len(images_to_process)} 张图片...")
                        sanitized_bytes, new_mime_type = sanitize_image(image_bytes)

                        # [内存优化] 处理完成后立即删除原始图片数据引用
                        # 这有助于垃圾回收器及时释放内存
                        if "data" in img_data:
                            del img_data["data"]
                        if "bytes" in img_data:
                            del img_data["bytes"]

                        # [修复] 保持键名一致性，使用 'data' 存储净化后的字节
                        sanitized_images_for_endpoint.append(
                            {
                                "data": sanitized_bytes,
                                "mime_type": new_mime_type,
                                "source": source or "attachment",
                            }
                        )

                        log.info(f"第 {idx}/{len(images_to_process)} 张图片处理完成。")

                        # [内存优化] 如果启用了顺序处理，在每张图片处理后强制垃圾回收
                        if sequential_processing:
                            import gc

                            gc.collect()

                    except Exception as e:
                        # 如果净化失败，记录错误并通知用户
                        log.error(
                            f"为自定义端点净化第 {idx} 张图片时失败: {e}", exc_info=True
                        )
                        return "呜哇，这张图好像有点问题，我处理不了…可以换一张试试吗？<伤心>"
                else:
                    # 对于非附件图片（如表情），直接使用原始数据
                    sanitized_images_for_endpoint.append(img_data)

        # 复用核心生成逻辑。从此方法抛出的任何异常都将由 generate_response 捕获。
        return await self._execute_generation_cycle(
            user_id=user_id,
            guild_id=guild_id,
            message=message,
            channel=channel,
            replied_message=replied_message,
            images=sanitized_images_for_endpoint
            if images
            else None,  # 如果有图片，则使用净化后的版本
            user_name=user_name,
            channel_context=channel_context,
            world_book_entries=world_book_entries,
            personal_summary=personal_summary,
            affection_status=affection_status,
            user_profile_data=user_profile_data,
            guild_name=guild_name,
            location_name=location_name,
            prompt_model_name=model_name,  # 传递用于选择 Prompt 的原始模型名称
            api_model_name=endpoint_config.get("model_name")
            or self.default_model_name,  # 传递用于调用 API 的真实模型名称
            client=client,
            discord_message=discord_message,
            user_id_for_settings=user_id_for_settings,
        )

    @_api_key_handler
    async def _generate_with_official_api(
        self,
        user_id: int,
        guild_id: int,
        message: str,
        channel: Optional[Any] = None,
        replied_message: Optional[str] = None,
        images: Optional[List[Dict]] = None,
        user_name: str = "用户",
        channel_context: Optional[List[Dict]] = None,
        world_book_entries: Optional[List[Dict]] = None,
        personal_summary: Optional[str] = None,
        affection_status: Optional[Dict[str, Any]] = None,
        user_profile_data: Optional[Dict[str, Any]] = None,
        guild_name: str = "未知服务器",
        location_name: str = "未知位置",
        model_name: Optional[str] = None,
        client: Any = None,
        discord_message: Optional[Any] = None,
        user_id_for_settings: Optional[str] = None,
    ) -> str:
        """
        [重构] 使用官方 API 密钥池生成 AI 回复。
        此方法由 _api_key_handler 装饰器管理，负责密钥轮换和重试。
        """
        if not client:
            raise ValueError("装饰器未能提供客户端实例。")

        # 将所有生成逻辑委托给核心方法
        return await self._execute_generation_cycle(
            user_id=user_id,
            guild_id=guild_id,
            message=message,
            channel=channel,
            replied_message=replied_message,
            images=images,
            user_name=user_name,
            channel_context=channel_context,
            world_book_entries=world_book_entries,
            personal_summary=personal_summary,
            affection_status=affection_status,
            user_profile_data=user_profile_data,
            guild_name=guild_name,
            location_name=location_name,
            prompt_model_name=model_name,  # 对于官方 API，prompt 和 api 模型名称相同
            api_model_name=model_name,
            client=client,
            discord_message=discord_message,
            user_id_for_settings=user_id_for_settings,
        )

    async def _load_novelai_preset_context(self, user_id: int) -> Dict[str, List[str]]:
        """加载当前用户可见的 NovelAI 画师串预设名（用户+管理员）。"""

        def _normalize_names(presets: List[Dict[str, Any]], limit: int) -> List[str]:
            names: List[str] = []
            seen = set()
            for preset in presets:
                name = str(preset.get("name") or "").strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                names.append(name)
                if len(names) >= limit:
                    break
            return names

        user_preset_names: List[str] = []
        admin_preset_names: List[str] = []

        try:
            user_presets = await chat_db_manager.get_novelai_presets(user_id)
            user_preset_names = _normalize_names(user_presets, limit=25)
        except Exception as e:
            log.warning(f"加载用户画师串预设名失败: {e}")

        try:
            admin_presets = await chat_db_manager.get_novelai_admin_presets()
            admin_preset_names = _normalize_names(admin_presets, limit=30)
        except Exception as e:
            log.warning(f"加载管理员画师串预设名失败: {e}")

        if not user_preset_names and not admin_preset_names:
            return {}

        return {
            "user_preset_names": user_preset_names,
            "admin_preset_names": admin_preset_names,
        }

    async def _load_comfyui_choice_context(self, user_id: int) -> Dict[str, List[str]]:
        """加载 ComfyUI 可用底模/VAE/CLIP/LoRA 列表用于提示词注入。"""
        comfy_enabled = bool(app_config.COMFYUI_CONFIG.get("ENABLED", False))
        if not comfy_enabled:
            return {}

        def _dedupe(items: List[str], limit: int) -> List[str]:
            names: List[str] = []
            seen = set()
            for raw_name in items:
                name = str(raw_name or "").strip()
                if not name:
                    continue
                key = name.lower()
                if key in seen:
                    continue
                seen.add(key)
                names.append(name)
                if len(names) >= limit:
                    break
            return names

        api_model_names: List[str] = []
        api_vae_names: List[str] = []
        api_clip_names: List[str] = []
        api_lora_names: List[str] = []

        try:
            from src.chat.features.image_generation.services.comfyui_service import (
                comfyui_service,
            )

            if comfyui_service.is_server_ready():
                try:
                    api_model_names = await comfyui_service.get_available_model_names()
                except Exception as error:
                    log.warning(f"读取 ComfyUI 底模列表失败: {error}")

                try:
                    api_vae_names = await comfyui_service.get_available_vae_names()
                except Exception as error:
                    log.warning(f"读取 ComfyUI VAE 列表失败: {error}")

                try:
                    api_clip_names = await comfyui_service.get_available_clip_names()
                except Exception as error:
                    log.warning(f"读取 ComfyUI CLIP 列表失败: {error}")

                try:
                    api_lora_names = await comfyui_service.get_available_lora_names()
                except Exception as error:
                    log.warning(f"读取 ComfyUI LoRA 列表失败: {error}")
        except Exception as error:
            log.warning(f"加载 ComfyUI 服务失败，跳过底模/LoRA 列表注入: {error}")

        merged_model_names = _dedupe(api_model_names, limit=120)
        merged_vae_names = _dedupe(api_vae_names, limit=120)
        merged_clip_names = _dedupe(api_clip_names, limit=120)
        merged_lora_names = _dedupe(api_lora_names, limit=160)

        if not merged_model_names and not merged_vae_names and not merged_clip_names and not merged_lora_names:
            return {}

        return {
            "available_model_names": merged_model_names,
            "available_vae_names": merged_vae_names,
            "available_clip_names": merged_clip_names,
            "available_lora_names": merged_lora_names,
        }

    async def _execute_generation_cycle(
        self,
        user_id: int,
        guild_id: int,
        message: str,
        channel: Optional[Any],
        replied_message: Optional[str],
        images: Optional[List[Dict]],
        user_name: str,
        channel_context: Optional[List[Dict]],
        world_book_entries: Optional[List[Dict]],
        personal_summary: Optional[str],
        affection_status: Optional[Dict[str, Any]],
        user_profile_data: Optional[Dict[str, Any]],
        guild_name: str,
        location_name: str,
        prompt_model_name: Optional[str],
        api_model_name: Optional[str],
        client: Any,
        discord_message: Optional[Any] = None,
        user_id_for_settings: Optional[str] = None,
    ) -> str:
        """
        [新增] 核心的 AI 生成周期，包含上下文构建、工具调用循环和响应处理。
        此方法被 _generate_with_official_api 和 _generate_with_custom_endpoint 复用。
        """
        # --- 模型使用计数 ---
        # 使用 prompt_model_name (表面模型名) 进行计数，而不是 api_model_name (真实模型名)
        model_to_count = prompt_model_name or self.default_model_name
        await chat_settings_service.increment_model_usage(model_to_count)

        # 1. 构建完整的对话提示
        novelai_preset_context = await self._load_novelai_preset_context(user_id)
        comfyui_choice_context = await self._load_comfyui_choice_context(user_id)
        final_conversation = prompt_service.build_chat_prompt(
            user_name=user_name,
            message=message,
            replied_message=replied_message,
            images=images,
            channel_context=channel_context,
            world_book_entries=world_book_entries,
            affection_status=affection_status,
            personal_summary=personal_summary,
            user_profile_data=user_profile_data,
            guild_name=guild_name,
            location_name=location_name,
            model_name=prompt_model_name,
            channel=channel,  # 传递 channel 对象
            user_id=user_id,  # 传递用户ID用于识别和主人验证
            novelai_preset_context=novelai_preset_context,
            comfyui_choice_context=comfyui_choice_context,
        )

        # 3. 准备 API 调用参数 (重构)
        model_key = prompt_model_name or "default"
        gen_config_data = copy.deepcopy(app_config.MODEL_GENERATION_CONFIG.get(
            model_key, app_config.MODEL_GENERATION_CONFIG["default"]
        ))

        log.info(f"正在为模型 '{model_key}' 加载生成配置。")

        # 从配置中提取 thinking_config，剩下的作为 generation_config 的参数
        thinking_config_data = gen_config_data.pop("thinking_config", None)
        gen_config_params = {**gen_config_data, "safety_settings": self.safety_settings}

        if self._is_no_thinking_model(model_key, api_model_name):
            thinking_config_data = None

        # --- [新增] 动态开启 Google 搜索和 URL 上下文工具 ---
        # 1. 初始化一个工具配置列表
        enabled_tools = []

        # 2. 添加 Google 搜索和 URL 阅读工具 (暂时禁用以恢复功能)
        # enabled_tools.append(types.Tool(google_search=types.GoogleSearch()))
        # enabled_tools.append(types.Tool(url_context=types.UrlContext()))
        # log.info("已为本次调用启用 Google 搜索工具。")

        # 3. 根据上下文动态获取函数工具
        dynamic_tools = await self.tool_service.get_dynamic_tools_for_context(
            user_id_for_settings=user_id_for_settings
        )
        if dynamic_tools:
            enabled_tools.extend(dynamic_tools)
            log.info(f"已根据上下文合并 {len(dynamic_tools)} 个动态函数工具。")

        # 4. 如果最终有工具被启用，则配置到生成参数中
        if enabled_tools:
            gen_config_params["tools"] = enabled_tools
            # 保持手动调用模式，让我们可以控制工具的执行流程
            gen_config_params["automatic_function_calling"] = (
                types.AutomaticFunctionCallingConfig(disable=True)
            )
            log.info("已启用手动工具调用模式，并集成了原生搜索及自定义函数。")

        gen_config = types.GenerateContentConfig(**gen_config_params)

        # 根据提取的 thinking_config_data 动态构建 ThinkingConfig
        if thinking_config_data:
            gen_config.thinking_config = types.ThinkingConfig(**thinking_config_data)
            log.info(
                f"已为模型 '{model_key}' 启用思维链 (Thinking)，配置: {thinking_config_data}"
            )

        # 4. 准备初始对话历史
        conversation_history = self._prepare_api_contents(final_conversation)

        if app_config.DEBUG_CONFIG["LOG_AI_FULL_CONTEXT"]:
            log.info(f"--- 初始 AI 上下文 (用户 {user_id}) ---")
            log.info(
                json.dumps(
                    [
                        self._serialize_parts_for_logging_full(c)
                        for c in conversation_history
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            log.info("------------------------------------")

        # 5. 实现手动、顺序工具调用循环
        called_tool_names = []
        thinking_was_used = False
        max_calls = 5
        max_web_search_calls = 1
        web_search_call_count = 0
        executed_web_search_signatures: set[str] = set()
        web_search_source_links: List[tuple] = []
        for i in range(max_calls):
            log_detailed = app_config.DEBUG_CONFIG.get(
                "LOG_DETAILED_GEMINI_PROCESS", False
            )
            if log_detailed:
                log.info(f"--- [工具调用循环: 第 {i + 1}/{max_calls} 次] ---")

            response = None
            for attempt in range(4):
                try:
                    response = await client.aio.models.generate_content(
                        model=(api_model_name or self.default_model_name),
                        contents=conversation_history,
                        config=gen_config,
                    )
                except genai_errors.ClientError as e:
                    unsupported_param = self._extract_unsupported_param_from_error(str(e))
                    if unsupported_param:
                        removed, thinking_config_data, removed_key = (
                            self._drop_unsupported_generation_param(
                                unsupported_param,
                                gen_config_params,
                                thinking_config_data,
                            )
                        )
                        if removed:
                            gen_config = types.GenerateContentConfig(**gen_config_params)
                            if thinking_config_data:
                                gen_config.thinking_config = types.ThinkingConfig(
                                    **thinking_config_data
                                )
                            log.warning(
                                f'gemini auto-drop unsupported param: {removed_key or unsupported_param}'
                            )
                            continue
                    raise
                if response and (
                    (response.candidates and response.candidates[0].content)
                    or (hasattr(response, "function_calls") and response.function_calls)
                ):
                    break
                log.warning(f"模型返回空响应 (尝试 {attempt + 1}/2)。将在1秒后重试...")
                if attempt < 3:
                    await asyncio.sleep(1)

            if log_detailed:
                if response and response.candidates:
                    candidate = response.candidates[0]
                    if candidate and candidate.content and candidate.content.parts:
                        for part in candidate.content.parts:
                            if hasattr(part, "thought") and part.thought:
                                thinking_was_used = True
                                log.info("--- 模型思考过程 (Thinking) ---")
                                log.info(part.text)
                                log.info("---------------------------------")

            function_calls = (
                response.function_calls
                if response and hasattr(response, "function_calls")
                else None
            )

            if not function_calls:
                if log_detailed:
                    log.info("--- 模型决策：直接生成文本回复 (未调用工具) ---")
                    log.info("模型返回了最终文本响应，工具调用流程结束。")
                break

            if log_detailed:
                log.info("--- 模型决策：建议进行工具调用 ---")
                for call in function_calls:
                    args_str = json.dumps(dict(call.args), ensure_ascii=False, indent=2)
                    log.info(f"  - 工具名称: {call.name}")
                    log.info(f"  - 调用参数:\n{args_str}")
                log.info("------------------------------------")

            for call in function_calls:
                called_tool_names.append(call.name)

            if (
                response
                and response.candidates
                and response.candidates[0].content
                and response.candidates[0].content.parts
            ):
                conversation_history.append(response.candidates[0].content)

            if log_detailed:
                log.info(f"准备执行 {len(function_calls)} 个工具调用...")

            current_turn_tool_names = [
                str(call.name).strip()
                for call in function_calls
                if call and getattr(call, "name", None)
            ]

            tool_result_parts = []
            prepared_results: List[Any] = []
            coroutine_indices: List[int] = []
            web_search_executed_in_current_turn = False
            for call in function_calls:
                if (
                    call.name == "generate_voice"
                    and self._should_block_generate_voice_for_info_context(
                        called_tool_names=called_tool_names,
                        current_turn_tool_names=current_turn_tool_names,
                    )
                ):
                    log.info("搜索/总结场景已触发语音保护：跳过 generate_voice 调用。")
                    prepared_results.append(
                        types.Part.from_function_response(
                            name="generate_voice",
                            response={
                                "result": self._build_voice_skip_message_for_info_context()
                            },
                        )
                    )
                    continue

                if call.name == "web_search":
                    raw_call_args = getattr(call, "args", {}) or {}
                    try:
                        parsed_call_args = dict(raw_call_args)
                    except Exception:
                        parsed_call_args = raw_call_args

                    call_signature = self._build_tool_call_signature(
                        "web_search", parsed_call_args
                    )

                    if web_search_call_count >= max_web_search_calls:
                        log.warning(
                            "检测到 web_search 超过调用上限，已拦截重复搜索请求。"
                        )
                        prepared_results.append(
                            types.Part.from_function_response(
                                name="web_search",
                                response={
                                    "result": self._build_web_search_skip_message(
                                        f"本轮对话中 web_search 调用已达到上限 ({max_web_search_calls} 次)。"
                                    )
                                },
                            )
                        )
                        continue

                    if call_signature in executed_web_search_signatures:
                        log.warning(
                            "检测到重复的 web_search 参数，已拦截本次重复搜索请求。"
                        )
                        prepared_results.append(
                            types.Part.from_function_response(
                                name="web_search",
                                response={
                                    "result": self._build_web_search_skip_message(
                                        "检测到相同参数的重复 web_search 请求。"
                                    )
                                },
                            )
                        )
                        continue

                    executed_web_search_signatures.add(call_signature)
                    web_search_call_count += 1
                    web_search_executed_in_current_turn = True
                prepared_results.append(
                    self.tool_service.execute_tool_call(
                        tool_call=call,
                        channel=channel,
                        user_id=user_id,
                        log_detailed=log_detailed,
                        message=discord_message,
                        user_id_for_settings=user_id_for_settings,
                        current_turn_tool_names=current_turn_tool_names,
                    )
                )
                coroutine_indices.append(len(prepared_results) - 1)

            if web_search_executed_in_current_turn and discord_message:
                try:
                    await discord_message.add_reaction("🔍")
                except Exception:
                    pass

            if coroutine_indices:
                coroutine_results = await asyncio.gather(
                    *(prepared_results[idx] for idx in coroutine_indices),
                    return_exceptions=True,
                )
                for idx, coroutine_result in zip(coroutine_indices, coroutine_results):
                    prepared_results[idx] = coroutine_result

            results = prepared_results

            for idx, result in enumerate(results):
                # 获取对应的工具调用名称
                actual_tool_name = function_calls[idx].name if idx < len(function_calls) else "unknown_tool"

                if isinstance(result, Exception):
                    log.error(f"执行工具时发生异常: {result}", exc_info=result)
                    tool_result_parts.append(
                        types.Part.from_function_response(
                            name=actual_tool_name,
                            response={
                                "error": f"An exception occurred during tool execution: {str(result)}"
                            },
                        )
                    )
                # 处理图片类型的 Part（inline_data）
                elif (
                    isinstance(result, types.Part)
                    and hasattr(result, 'inline_data')
                    and result.inline_data
                ):
                    # 这是图片工具返回的图片数据，直接添加到结果中
                    log.info(f"检测到工具 '{actual_tool_name}' 返回的 inline_data，已添加到工具结果。")
                    tool_result_parts.append(result)
                    # 根据工具类型生成不同的提示信息
                    if actual_tool_name == "get_user_avatar":
                        response_hint = (
                            "已获取用户头像图片。上面的图片就是该用户的 Discord 头像。"
                            "请仔细分析图中的外观特征（发色、发型、瞳色、服饰风格等），"
                            "以便在后续为用户生成图片时参考这些视觉特征。"
                        )
                    else:
                        response_hint = "图片已成功生成并展示给用户。请用自己的语气告诉用户图片已经画好了。"
                    # 同时添加一个 FunctionResponse 告诉模型处理结果
                    tool_result_parts.append(
                        types.Part.from_function_response(
                            name=actual_tool_name,
                            response={"result": response_hint},
                        )
                    )
                # 确保 result 是 Part 类型，并且其 function_response 和 response 属性都存在
                elif (
                    isinstance(result, types.Part)
                    and result.function_response
                    and result.function_response.response
                ):
                    tool_name = result.function_response.name
                    # 首先检查是否有错误信息
                    error_message = result.function_response.response.get("error")
                    if error_message:
                        # 如果有错误信息，直接使用错误信息作为结果
                        original_result = error_message
                        log.info(f"工具返回错误信息: {error_message}")
                    else:
                        original_result = result.function_response.response.get(
                            "result", {}
                        )

                    # web_search 执行完成，先移除 🔍 再加 ☑️ reaction
                    if (
                        tool_name == "web_search"
                        and discord_message
                        and not (
                            isinstance(original_result, str)
                            and original_result.startswith("[web_search 已跳过]")
                        )
                    ):
                        try:
                            await discord_message.remove_reaction("🔍", discord_message.guild.me)
                        except Exception:
                            pass
                        try:
                            await discord_message.add_reaction("☑️")
                        except Exception:
                            pass

                    # 记录 web_search 工具返回的来源链接（标题+URL），用于最终回复兜底展示
                    if tool_name == "web_search":
                        search_result_text = (
                            original_result
                            if isinstance(original_result, str)
                            else json.dumps(original_result, ensure_ascii=False)
                        )
                        extracted_links = self._extract_markdown_links_from_text(search_result_text)
                        seen_urls = {u for _, u in web_search_source_links}
                        for title, url in extracted_links:
                            if url not in seen_urls:
                                seen_urls.add(url)
                                web_search_source_links.append((title, url))

                    # --- 新增：处理工具返回的头像图片 ---
                    if isinstance(original_result, dict):
                        profile = original_result.get("profile", {})
                        if "avatar_image_base64" in profile:
                            log.info(
                                "检测到工具返回的 avatar_image_base64，正在处理为图片 Part。"
                            )
                            try:
                                image_bytes = base64.b64decode(
                                    profile["avatar_image_base64"]
                                )
                                # 创建一个新的图片 Part
                                image_part = types.Part(
                                    inline_data=types.Blob(
                                        mime_type="image/png", data=image_bytes
                                    )
                                )
                                tool_result_parts.append(image_part)
                                # 从原始结果中移除，避免冗余
                                del profile["avatar_image_base64"]
                            except Exception as e:
                                log.error(
                                    f"处理 avatar_image_base64 时出错: {e}",
                                    exc_info=True,
                                )
                    # --- 图片处理结束 ---

                    response_content: Dict[str, Any]

                    if isinstance(original_result, (dict, list)):
                        response_content = {"result": original_result}
                    else:
                        safe_tool_name = tool_name or "unknown_tool"
                        safe_result_str = str(original_result or "")

                        wrapped_result_str = (
                            prompt_service.build_tool_result_wrapper_prompt(
                                safe_tool_name,
                                safe_result_str,
                            )
                        )
                        response_content = {"result": wrapped_result_str}

                    # 只有在 response_content['result'] 真正有内容时才创建 FunctionResponse Part
                    # 这样可以避免在只有图片的情况下，发送一个空的、无意义的文本结果 Part
                    if response_content.get("result"):
                        new_response_part = types.Part.from_function_response(
                            name=tool_name or "unknown_tool",
                            response=response_content,
                        )
                        tool_result_parts.append(new_response_part)

                else:
                    log.warning(f"接收到未知的工具执行结果类型: {type(result)}")

            if log_detailed:
                log.info(
                    f"已收集 {len(tool_result_parts)} 个工具执行结果，准备将其返回给模型。"
                )
                try:
                    # --- [新增调试日志] ---
                    # 序列化并打印工具返回的详细内容
                    results_for_log = []
                    for part in tool_result_parts:
                        if part and part.function_response:
                            results_for_log.append(
                                {
                                    "name": part.function_response.name,
                                    "response": self._serialize_for_logging(
                                        part.function_response.response
                                    ),
                                }
                            )
                    log.info(
                        f"--- [工具结果详细内容] ---\n{json.dumps(results_for_log, ensure_ascii=False, indent=2)}"
                    )
                    # --- [日志结束] ---
                except Exception as e:
                    log.error(f"序列化工具结果用于日志记录时出错: {e}")

            # --- 检查是否有工具标记了 skip_ai_response（生图/生视频成功时跳过后续AI回复） ---
            skip_ai_response = False
            for part in tool_result_parts:
                if (
                    isinstance(part, types.Part)
                    and part.function_response
                    and part.function_response.response
                ):
                    result_data = part.function_response.response.get("result", {})
                    if isinstance(result_data, dict) and result_data.get("skip_ai_response"):
                        skip_ai_response = True
                        log.info(f"工具 '{part.function_response.name}' 标记了 skip_ai_response，跳过后续AI回复。")
                        break
            
            if skip_ai_response:
                self.last_called_tools = called_tool_names
                log.info("生成工具已成功完成并直接发送内容，无需后续AI回复。")
                return None
            # --- skip_ai_response 检查结束 ---

            conversation_history.append(
                types.Content(role="tool", parts=tool_result_parts)
            )

            if i == max_calls - 1:
                log.warning("已达到最大工具调用限制，流程终止。")
                self.last_called_tools = called_tool_names
                return "哎呀，我好像陷入了一个复杂的思考循环里，我们换个话题聊聊吧！"

        if response and response.parts:
            final_thought = ""
            final_text = ""
            for part in response.parts or []:
                if hasattr(part, "thought") and part.thought:
                    thinking_was_used = True
                    final_thought += part.text
                elif hasattr(part, "text"):
                    final_text += part.text

            if log_detailed:
                if final_thought:
                    log.info("--- 模型最终回复的思考过程 ---")
                    log.info(final_thought.strip())
                    log.info("-----------------------------")
                else:
                    log.info("--- 模型最终回复未提供明确的思考过程。---")

            raw_ai_response = final_text.strip()

            if raw_ai_response:
                from src.chat.services.context_service import context_service

                await context_service.update_user_conversation_history(
                    user_id, guild_id, message if message else "", raw_ai_response
                )
                formatted_response = await self._post_process_response(
                    raw_ai_response, user_id, guild_id
                )
                if "web_search" in called_tool_names:
                    formatted_response = self._append_message_sources_if_needed(
                        formatted_response, web_search_source_links
                    )
                # --- 新增：记录 Token 使用情况 ---
                await self._record_token_usage(
                    client=client,
                    model_name=api_model_name or self.default_model_name,
                    input_contents=conversation_history,
                    output_text=raw_ai_response,
                )
                total_tokens = 0
                if response and response.usage_metadata:
                    total_tokens = response.usage_metadata.total_token_count

                log.info("--- Gemini API 请求摘要 ---")
                log.info(
                    f"  - 本次调用是否使用思考功能: {'是' if thinking_was_used else '否'}"
                )
                if thinking_was_used:
                    log.info(f"  - 思考过程Token消耗: {total_tokens}")

                if called_tool_names:
                    unique_tools = sorted(list(set(called_tool_names)))
                    log.info(
                        f"  - 调用了 {len(unique_tools)} 个工具: {', '.join(unique_tools)}"
                    )
                else:
                    log.info("  - 未调用任何工具。")
                log.info("--------------------------")

                self.last_called_tools = called_tool_names
                return formatted_response

        self.last_called_tools = called_tool_names
        if (
            response
            and response.prompt_feedback
            and response.prompt_feedback.block_reason
        ):
            try:
                conversation_for_log = json.dumps(
                    GeminiService._serialize_for_logging(final_conversation),
                    ensure_ascii=False,
                    indent=2,
                )
                full_response_for_log = str(response)
                log.warning(
                    f"用户 {user_id} 的请求被安全策略阻止，原因: {response.prompt_feedback.block_reason if response.prompt_feedback else '未知'}\n"
                    f"--- 完整的对话历史 ---\n{conversation_for_log}\n"
                    f"--- 完整的 API 响应 ---\n{full_response_for_log}"
                )
            except Exception as log_e:
                log.error(f"序列化被阻止的请求用于日志记录时出错: {log_e}")
                log.warning(
                    f"用户 {user_id} 的请求被安全策略阻止，原因: {response.prompt_feedback.block_reason if response.prompt_feedback else '未知'} (详细内容记录失败)"
                )
            return "呜啊! 这个太色情啦,我不看我不看"
        else:
            log.warning(f"未能为用户 {user_id} 生成有效回复。")
            return "哎呀，我好像没太明白你的意思呢～可以再说清楚一点吗？✨"
        return "哎呀，我好像没太明白你的意思呢～可以再说清楚一点吗？✨"

    def _convert_tools_to_openai_format(self) -> List[Dict]:
        """
        将 Gemini 格式的工具转换为 OpenAI 格式。
        使用函数的签名和文档字符串自动生成 OpenAI 工具定义。
        """
        import inspect
        from typing import get_type_hints, get_origin, get_args, Union
        from pydantic import BaseModel
        
        openai_tools = []
        
        for func in self.available_tools:
            try:
                # 获取函数签名和文档字符串
                sig = inspect.signature(func)
                doc = inspect.getdoc(func) or ""
                
                # 构建参数模式
                properties = {}
                required = []
                
                # 尝试获取类型提示
                try:
                    hints = get_type_hints(func)
                except Exception:
                    hints = {}
                
                for param_name, param in sig.parameters.items():
                    # 跳过特殊参数
                    if param_name in ('kwargs', 'args', 'bot', 'channel', 'message'):
                        continue
                    
                    param_type = hints.get(param_name, param.annotation)
                    
                    # 确定参数类型
                    param_schema = {"type": "string"}  # 默认类型
                    
                    if param_type != inspect.Parameter.empty:
                        origin = get_origin(param_type)
                        
                        if param_type == str:
                            param_schema = {"type": "string"}
                        elif param_type == int:
                            param_schema = {"type": "integer"}
                        elif param_type == float:
                            param_schema = {"type": "number"}
                        elif param_type == bool:
                            param_schema = {"type": "boolean"}
                        elif origin == list or param_type == list:
                            param_schema = {"type": "array", "items": {"type": "string"}}
                        elif origin == Union:
                            # 处理 Optional 类型
                            args = get_args(param_type)
                            non_none_args = [a for a in args if a is not type(None)]
                            if non_none_args:
                                first_arg = non_none_args[0]
                                if first_arg == str:
                                    param_schema = {"type": "string"}
                                elif first_arg == int:
                                    param_schema = {"type": "integer"}
                                elif hasattr(first_arg, '__mro__') and BaseModel in first_arg.__mro__:
                                    # Pydantic 模型
                                    try:
                                        param_schema = first_arg.model_json_schema()
                                    except:
                                        param_schema = {"type": "object"}
                        elif hasattr(param_type, '__mro__') and BaseModel in param_type.__mro__:
                            # Pydantic 模型
                            try:
                                param_schema = param_type.model_json_schema()
                            except:
                                param_schema = {"type": "object"}
                    
                    properties[param_name] = param_schema
                    
                    # 检查是否为必需参数
                    if param.default == inspect.Parameter.empty:
                        required.append(param_name)
                
                # 构建 OpenAI 工具定义
                tool_def = {
                    "type": "function",
                    "function": {
                        "name": func.__name__,
                        "description": doc.split("\n")[0] if doc else f"调用 {func.__name__} 工具",
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": required,
                        }
                    }
                }
                
                openai_tools.append(tool_def)
                
            except Exception as e:
                log.warning(f"转换工具 {func.__name__} 到 OpenAI 格式时出错: {e}")
                continue
        
        return openai_tools

    async def _generate_with_openai_compatible(
        self,
        user_id: int,
        guild_id: int,
        message: str,
        channel: Optional[Any] = None,
        replied_message: Optional[str] = None,
        images: Optional[List[Dict]] = None,
        user_name: str = "用户",
        channel_context: Optional[List[Dict]] = None,
        world_book_entries: Optional[List[Dict]] = None,
        personal_summary: Optional[str] = None,
        affection_status: Optional[Dict[str, Any]] = None,
        user_profile_data: Optional[Dict[str, Any]] = None,
        guild_name: str = "未知服务器",
        location_name: str = "未知位置",
        model_name: Optional[str] = None,
        discord_message: Optional[Any] = None,
        api_url: str = "",
        api_key: str = "",
    ) -> str:
        """
        使用 OpenAI 兼容的 API 生成回复。
        用于支持 OpenAI 格式的第三方服务（如 Claude API 代理）。
        支持工具调用循环。
        """
        log.info(f"使用 OpenAI 兼容 API 生成回复: {api_url}, 模型: {model_name}")
        
        # 构建完整的对话提示
        novelai_preset_context = await self._load_novelai_preset_context(user_id)
        comfyui_choice_context = await self._load_comfyui_choice_context(user_id)
        final_conversation = prompt_service.build_chat_prompt(
            user_name=user_name,
            message=message,
            replied_message=replied_message,
            images=images,
            channel_context=channel_context,
            world_book_entries=world_book_entries,
            affection_status=affection_status,
            personal_summary=personal_summary,
            user_profile_data=user_profile_data,
            guild_name=guild_name,
            location_name=location_name,
            model_name=model_name,
            channel=channel,
            user_id=user_id,
            novelai_preset_context=novelai_preset_context,
            comfyui_choice_context=comfyui_choice_context,
        )
        
        # 转换为 OpenAI 格式的 messages
        messages = self._convert_conversation_to_openai_messages(final_conversation)
        
        # 确保消息顺序正确
        messages = self._fix_message_order_for_openai(messages)
        
        # 获取生成参数
        model_key = model_name or "default"
        gen_config = app_config.MODEL_GENERATION_CONFIG.get(
            model_key, app_config.MODEL_GENERATION_CONFIG.get("default", {})
        )
        temperature = gen_config.get("temperature", 1.0)
        max_tokens = gen_config.get("max_output_tokens", 8192)
        
        # 转换工具定义
        openai_tools = self._convert_tools_to_openai_format()
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        
        # 智能处理 URL 路径
        base_api_url = api_url.rstrip("/")
        if not base_api_url.endswith("/chat/completions"):
            if "/v1" in base_api_url and not base_api_url.endswith("/v1"):
                base_api_url = base_api_url + "/chat/completions"
            elif base_api_url.endswith("/v1"):
                base_api_url = base_api_url + "/chat/completions"
            else:
                base_api_url = base_api_url + "/v1/chat/completions"
        
        # 工具调用循环
        max_tool_calls = 5
        max_web_search_calls = 1
        web_search_call_count = 0
        executed_web_search_signatures: set[str] = set()
        called_tool_names = []
        web_search_source_links: List[tuple] = []
        disabled_payload_fields: set[str] = set()
        
        for iteration in range(max_tool_calls):
            payload = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            
            # 添加工具定义（如果有的话）
            if openai_tools:
                payload["tools"] = openai_tools
                payload["tool_choice"] = "auto"
            
            payload = self._build_openai_chat_payload(
                model_name=model_name,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                tools=openai_tools,
                disabled_fields=disabled_payload_fields,
            )

            # 调试日志
            if app_config.DEBUG_CONFIG.get("LOG_AI_FULL_CONTEXT", False):
                log.info(f"OpenAI API 请求 URL: {base_api_url}")
                log.info(f"OpenAI API 消息数量: {len(messages)}, 迭代: {iteration + 1}")
                if openai_tools:
                    log.info(f"OpenAI API 工具数量: {len(openai_tools)}")
            
            try:
                result = await self._post_openai_chat_completion_with_fallback(
                    api_url=base_api_url,
                    headers=headers,
                    payload=payload,
                    timeout_seconds=max(
                        30,
                        int(
                            app_config.API_RETRY_CONFIG.get(
                                "OPENAI_COMPAT_CHAT_TIMEOUT_SECONDS", 180
                            )
                        ),
                    ),
                    disabled_payload_fields=disabled_payload_fields,
                    log_prefix=(
                        f"OpenAI 兼容 API chat.completions "
                        f"(iteration {iteration + 1}/{max_tool_calls})"
                    ),
                )

                if "choices" not in result or len(result["choices"]) == 0:
                    log.warning(f"OpenAI 兼容 API 返回空响应: {result}")
                    return "哎呀，我好像没太明白你的意思呢～可以再说清楚一点吗？"

                choice = result["choices"][0]
                message_response = choice.get("message", {})

                # 记录 Token 使用
                if "usage" in result:
                    usage = result["usage"]
                    log.info(
                        f"OpenAI API Token 使用: 输入={usage.get('prompt_tokens', 0)}, "
                        f"输出={usage.get('completion_tokens', 0)}"
                    )

                # 检查是否有工具调用
                tool_calls = message_response.get("tool_calls", [])

                if tool_calls:
                    log.info(f"OpenAI API 返回 {len(tool_calls)} 个工具调用")

                    # 将助手消息添加到对话历史
                    messages.append(
                        {
                            "role": "assistant",
                            "content": message_response.get("content") or "",
                            "tool_calls": tool_calls,
                        }
                    )

                    current_turn_tool_names = [
                        str(call.get("function", {}).get("name", "")).strip()
                        for call in tool_calls
                        if str(call.get("function", {}).get("name", "")).strip()
                    ]

                    # 执行每个工具调用
                    # 注意：必须先执行完整轮所有工具，再统一处理 skip_ai_response。
                    # 否则当第一项工具（如 generate_voice）返回 skip_ai_response=True 时，
                    # 会提前中断后续工具（如 generate_image_comfyui）。
                    skip_ai_response_requested = False
                    for tool_call in tool_calls:
                        tool_name = tool_call.get("function", {}).get("name", "")
                        tool_args_str = tool_call.get("function", {}).get("arguments", "{}")
                        tool_call_id = tool_call.get("id", "")

                        called_tool_names.append(tool_name)

                        log.info(f"执行工具: {tool_name}, 参数: {tool_args_str}")

                        try:
                            tool_args = json.loads(tool_args_str)
                        except json.JSONDecodeError:
                            tool_args = {}

                        web_search_executed = False

                        if (
                            tool_name == "generate_voice"
                            and self._should_block_generate_voice_for_info_context(
                                called_tool_names=called_tool_names,
                                current_turn_tool_names=current_turn_tool_names,
                            )
                        ):
                            log.info("搜索/总结场景已触发语音保护：跳过 generate_voice 调用。")
                            tool_result = self._build_voice_skip_message_for_info_context()

                        # 执行工具（包含 web_search 防循环保护）
                        elif tool_name == "web_search":
                            call_signature = self._build_tool_call_signature(
                                tool_name, tool_args
                            )
                            if web_search_call_count >= max_web_search_calls:
                                log.warning(
                                    "OpenAI 工具循环中 web_search 超过调用上限，已拦截。"
                                )
                                tool_result = self._build_web_search_skip_message(
                                    f"本轮对话中 web_search 调用已达到上限 ({max_web_search_calls} 次)。"
                                )
                            elif call_signature in executed_web_search_signatures:
                                log.warning(
                                    "OpenAI 工具循环中检测到重复 web_search 参数，已拦截。"
                                )
                                tool_result = self._build_web_search_skip_message(
                                    "检测到相同参数的重复 web_search 请求。"
                                )
                            else:
                                executed_web_search_signatures.add(call_signature)
                                web_search_call_count += 1
                                web_search_executed = True
                                if discord_message:
                                    try:
                                        await discord_message.add_reaction("🔍")
                                    except Exception:
                                        pass
                                tool_result = await self._execute_openai_tool_call(
                                    tool_name=tool_name,
                                    tool_args=tool_args,
                                    channel=channel,
                                    user_id=user_id,
                                    discord_message=discord_message,
                                    current_turn_tool_names=current_turn_tool_names,
                                )
                        else:
                            tool_result = await self._execute_openai_tool_call(
                                tool_name=tool_name,
                                tool_args=tool_args,
                                channel=channel,
                                user_id=user_id,
                                discord_message=discord_message,
                                current_turn_tool_names=current_turn_tool_names,
                            )

                        # web_search 执行完成，先移除 🔍 再加 ☑️ reaction
                        if tool_name == "web_search" and web_search_executed and discord_message:
                            try:
                                await discord_message.remove_reaction("🔍", discord_message.guild.me)
                            except Exception:
                                pass
                            try:
                                await discord_message.add_reaction("☑️")
                            except Exception:
                                pass

                        # 记录 web_search 工具返回的来源链接（标题+URL），用于最终回复兜底展示
                        if tool_name == "web_search":
                            search_result_text = (
                                json.dumps(tool_result, ensure_ascii=False)
                                if isinstance(tool_result, (dict, list))
                                else str(tool_result)
                            )
                            extracted_links = self._extract_markdown_links_from_text(search_result_text)
                            seen_urls = {u for _, u in web_search_source_links}
                            for title, url in extracted_links:
                                if url not in seen_urls:
                                    seen_urls.add(url)
                                    web_search_source_links.append((title, url))

                        # 将工具结果添加到对话历史
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call_id,
                                "content": (
                                    json.dumps(tool_result, ensure_ascii=False)
                                    if isinstance(tool_result, (dict, list))
                                    else str(tool_result)
                                ),
                            }
                        )

                        # 检查是否有工具标记了 skip_ai_response（本轮工具执行完后统一处理）
                        if isinstance(tool_result, dict) and tool_result.get("skip_ai_response"):
                            log.info(
                                f"OpenAI 工具 '{tool_name}' 标记了 skip_ai_response，"
                                "将在本轮工具全部执行后跳过后续AI回复。"
                            )
                            skip_ai_response_requested = True

                    if skip_ai_response_requested:
                        self.last_called_tools = called_tool_names
                        return None

                    # 继续循环以获取最终响应
                    continue

                # 没有工具调用，返回最终响应
                raw_response = message_response.get("content", "")

                # 记录调用的工具
                if called_tool_names:
                    self.last_called_tools = called_tool_names
                    log.info(
                        f"OpenAI 工具调用循环完成，共调用了 {len(called_tool_names)} "
                        f"个工具: {called_tool_names}"
                    )

                # 后处理
                final_response = await self._post_process_response(
                    raw_response, user_id, guild_id
                )
                if "web_search" in called_tool_names:
                    final_response = self._append_message_sources_if_needed(
                        final_response, web_search_source_links
                    )
                return final_response

            except Exception as e:
                log.error(f"OpenAI 兼容 API 调用失败: {e}", exc_info=True)
                raise
        
        # 达到最大工具调用次数
        log.warning(f"OpenAI 工具调用循环达到最大次数 {max_tool_calls}")
        return "呜...思考太多次了，脑子有点转不过来，请重新问一下吧！"
    
    def _convert_conversation_to_openai_messages(self, final_conversation: List[Dict]) -> List[Dict]:
        """
        将内部对话格式转换为 OpenAI 消息格式。
        """
        messages = []
        system_content_parts = []
        
        for turn in final_conversation:
            role = turn.get("role")
            parts = turn.get("parts", [])
            
            content_parts = []
            image_parts = []
            
            for part in parts:
                if isinstance(part, str):
                    content_parts.append(part)
                elif isinstance(part, Image.Image):
                    try:
                        buffered = io.BytesIO()
                        if part.mode in ('RGBA', 'LA', 'P'):
                            part = part.convert('RGB')
                        part.save(buffered, format="JPEG", quality=85)
                        img_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
                        image_parts.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{img_base64}"
                            }
                        })
                    except Exception as img_error:
                        log.warning(f"处理图片时出错: {img_error}")
                        content_parts.append("[图片处理失败]")
                elif isinstance(part, dict):
                    if "data" in part or "bytes" in part:
                        try:
                            img_bytes = part.get("data") or part.get("bytes")
                            img_base64 = base64.b64encode(img_bytes).decode("utf-8")
                            mime_type = part.get("mime_type", "image/jpeg")
                            image_parts.append({
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{img_base64}"
                                }
                            })
                        except Exception as img_error:
                            log.warning(f"处理图片字典时出错: {img_error}")
                            content_parts.append("[图片处理失败]")
            
            text_content = "\n".join(content_parts) if content_parts else ""
            
            if role == "model":
                role = "assistant"
            elif role == "system":
                if text_content:
                    system_content_parts.append(text_content)
                continue
            
            if not text_content and not image_parts:
                continue
            
            if image_parts:
                content = []
                if text_content:
                    content.append({"type": "text", "text": text_content})
                content.extend(image_parts)
                messages.append({"role": role, "content": content})
            else:
                messages.append({"role": role, "content": text_content})
        
        if system_content_parts:
            system_content = "\n\n".join(system_content_parts)
            messages.insert(0, {"role": "system", "content": system_content})
        
        return messages
    
    async def _execute_openai_tool_call(
        self,
        tool_name: str,
        tool_args: Dict,
        channel: Optional[Any] = None,
        user_id: Optional[int] = None,
        discord_message: Optional[Any] = None,
        current_turn_tool_names: Optional[List[str]] = None,
    ) -> Any:
        """
        执行 OpenAI 格式的工具调用。
        """
        tool_function = self.tool_map.get(tool_name)
        
        if not tool_function:
            log.error(f"找不到工具 '{tool_name}' 的实现。")
            return {"error": f"Tool '{tool_name}' not found."}
        
        try:
            # 注入上下文
            tool_args["bot"] = self.bot
            if user_id is not None:
                tool_args["user_id"] = str(user_id)
            if channel is not None:
                tool_args["channel"] = channel
            if discord_message is not None:
                tool_args["message"] = discord_message
            if current_turn_tool_names is not None:
                tool_args["current_turn_tool_names"] = [
                    str(name).strip()
                    for name in current_turn_tool_names
                    if str(name).strip()
                ]
            
            # 执行工具
            result = await tool_function(**tool_args)
            
            # 处理结果
            if isinstance(result, (dict, list, str, int, float, bool)):
                return result
            elif hasattr(result, 'function_response') and result.function_response:
                # Gemini 格式的 Part 响应
                return result.function_response.response
            else:
                return str(result)
                
        except Exception as e:
            log.error(f"执行工具 '{tool_name}' 时发生错误: {e}", exc_info=True)
            return {"error": str(e)}
    
    def _fix_message_order_for_openai(self, messages: List[Dict]) -> List[Dict]:
        """
        修复消息顺序以符合 OpenAI/Claude API 要求。
        - system 消息必须在开头
        - user 和 assistant 消息必须交替
        - 不能有连续的相同角色消息
        """
        if not messages:
            return messages
        
        fixed = []
        system_msgs = []
        other_msgs = []
        
        # 分离 system 消息和其他消息
        for msg in messages:
            if msg.get("role") == "system":
                system_msgs.append(msg)
            else:
                other_msgs.append(msg)
        
        # 合并所有 system 消息为一条
        if system_msgs:
            combined_system = "\n\n".join(
                msg.get("content", "") if isinstance(msg.get("content"), str)
                else "[系统消息]"
                for msg in system_msgs
            )
            fixed.append({"role": "system", "content": combined_system})
        
        # 处理其他消息，合并连续相同角色的消息
        prev_role = None
        for msg in other_msgs:
            role = msg.get("role")
            content = msg.get("content")
            
            if role == prev_role and fixed:
                # 合并到上一条消息
                last_msg = fixed[-1]
                if isinstance(last_msg.get("content"), str) and isinstance(content, str):
                    last_msg["content"] = last_msg["content"] + "\n\n" + content
                elif isinstance(content, str):
                    # 上一条是多模态，当前是文本
                    if isinstance(last_msg.get("content"), list):
                        last_msg["content"].append({"type": "text", "text": content})
                # 其他情况保持原样
            else:
                fixed.append(msg)
                prev_role = role
        
        # 确保第一条非系统消息是 user
        non_system_start = 1 if fixed and fixed[0].get("role") == "system" else 0
        if len(fixed) > non_system_start and fixed[non_system_start].get("role") == "assistant":
            # 在 assistant 前插入一个空的 user 消息
            fixed.insert(non_system_start, {"role": "user", "content": "[对话开始]"})
        
        return fixed
    
    async def generate_embedding(
        self,
        text: str,
        task_type: str = "retrieval_document",
        title: Optional[str] = None,
    ) -> Optional[List[float]]:
        """
        为给定文本生成嵌入向量。
        支持多种提供商: gemini, openai, siliconflow
        """
        if not text or not text.strip():
            log.warning(
                f"generate_embedding 接收到空文本！text: '{text}', task_type: '{task_type}'"
            )
            return None

        # 获取嵌入配置
        embed_config = app_config.EMBEDDING_CONFIG
        if not embed_config.get("ENABLED", True):
            log.warning("向量嵌入功能已禁用")
            return None
        
        provider = embed_config.get("PROVIDER", "gemini")
        api_key = embed_config.get("API_KEY") or os.getenv("GEMINI_API_KEYS", "").split(",")[0].strip()
        base_url = embed_config.get("BASE_URL")
        model_name = embed_config.get("MODEL_NAME", "gemini-embedding-001")
        
        if not api_key:
            log.error("未配置向量嵌入 API 密钥")
            return None
        
        try:
            if provider == "gemini":
                return await self._generate_gemini_embedding(text, task_type, title, api_key, base_url, model_name)
            elif provider in ["openai", "siliconflow"]:
                return await self._generate_openai_compatible_embedding(text, api_key, base_url, model_name, provider)
            else:
                log.error(f"不支持的嵌入提供商: {provider}")
                return None
        except Exception as e:
            log.error(f"生成向量嵌入时发生错误 ({provider}): {e}", exc_info=True)
            return None
    
    async def _generate_gemini_embedding(
        self,
        text: str,
        task_type: str,
        title: Optional[str],
        api_key: str,
        base_url: Optional[str],
        model_name: str,
    ) -> Optional[List[float]]:
        """使用 Gemini API 生成嵌入"""
        try:
            # 创建客户端
            if base_url:
                http_options = types.HttpOptions(base_url=base_url)
                client = genai.Client(api_key=api_key, http_options=http_options)
            else:
                client = genai.Client(api_key=api_key)
            
            loop = asyncio.get_event_loop()
            embed_config = types.EmbedContentConfig(task_type=task_type)
            if title and task_type == "retrieval_document":
                embed_config.title = title

            embedding_result = await loop.run_in_executor(
                self.executor,
                lambda: client.models.embed_content(
                    model=model_name,
                    contents=[types.Part(text=text)],
                    config=embed_config,
                ),
            )

            if embedding_result and embedding_result.embeddings:
                return embedding_result.embeddings[0].values
            return None
        except Exception as e:
            log.error(f"Gemini 嵌入生成失败: {e}")
            return None
    
    async def _generate_openai_compatible_embedding(
        self,
        text: str,
        api_key: str,
        base_url: Optional[str],
        model_name: str,
        provider: str,
    ) -> Optional[List[float]]:
        """使用 OpenAI 兼容 API 生成嵌入 (支持硅基流动等)"""
        import aiohttp
        
        # 根据提供商设置默认 URL
        if not base_url:
            if provider == "siliconflow":
                base_url = "https://api.siliconflow.cn/v1"
            else:
                base_url = "https://api.openai.com/v1"
        
        url = f"{base_url.rstrip('/')}/embeddings"
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": model_name,
            "input": text,
            "encoding_format": "float",
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        if "data" in data and len(data["data"]) > 0:
                            embedding = data["data"][0].get("embedding")
                            if embedding:
                                return embedding
                        log.warning(f"OpenAI 兼容 API 返回无效响应: {data}")
                        return None
                    else:
                        error_text = await response.text()
                        log.error(f"OpenAI 兼容嵌入 API 错误 ({response.status}): {error_text}")
                        return None
        except Exception as e:
            log.error(f"OpenAI 兼容嵌入请求失败: {e}")
            return None

    async def generate_text(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        model_name: Optional[str] = None,
        client: Any = None,
    ) -> Optional[str]:
        """
        一个用于简单文本生成的精简方法。
        不涉及对话历史或上下文，仅根据输入提示生成文本。
        非常适合用于如"查询重写"等内部任务。

        此方法会自动检测 Dashboard 配置的 API 格式并使用相应的 API。

        Args:
            prompt: 提供给模型的输入提示。
            temperature: 控制生成文本的随机性。如果为 None，则使用 config 中的默认值。
            model_name: 指定要使用的模型。如果为 None，则使用默认的聊天模型。

        Returns:
            生成的文本字符串，如果失败则返回 None。
        """
        # 构建生成配置
        gen_config_params = app_config.GEMINI_TEXT_GEN_CONFIG.copy()
        if temperature is not None:
            gen_config_params["temperature"] = temperature
        
        final_model_name = model_name or self.default_model_name
        
        # 使用 generate_simple_response 统一处理 API 选择
        return await self.generate_simple_response(
            prompt=prompt,
            generation_config=gen_config_params,
            model_name=final_model_name,
            return_error_text=False,
        )

    async def generate_simple_response(
        self,
        prompt: str,
        generation_config: Dict,
        model_name: Optional[str] = None,
        client: Any = None,
        messages: Optional[List[Dict[str, str]]] = None,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        api_format: Optional[str] = None,
        images: Optional[List[Dict[str, Any]]] = None,
        return_error_text: bool = True,
    ) -> Optional[str]:
        """
        一个用于单次、非对话式文本生成的方法，允许传入完整的生成配置和可选的模型名称。
        非常适合用于如"礼物回应"、"投喂"、"个人记忆摘要"、"查询重写"等需要自定义生成参数的一次性任务。

        此方法会自动检测 Dashboard 配置的 API 格式：
        - 如果是 "openai" 格式，使用 OpenAI 兼容 API
        - 如果是 "gemini" 格式，使用 Gemini SDK

        Args:
            prompt: 提供给模型的完整输入提示。当 messages 不为 None 时，此参数被忽略。
            generation_config: 一个包含生成参数的字典 (e.g., temperature, max_output_tokens).
            model_name: (可选) 指定要使用的模型。如果为 None，则使用默认的聊天模型。
            messages: (可选) 多轮对话消息列表，格式为 [{"role": "user"|"model"|"assistant", "content": "..."}]。
                      当提供此参数时，将使用多轮对话模式，支持预填充上下文（如限制解除对话）。
                      Gemini SDK 使用 role="user"/"model"，OpenAI 兼容 API 自动映射 "model"->"assistant"。
            images: (可选) 参考图片列表，格式为 [{"data"|"bytes": 图片字节, "mime_type": "image/png"}]。
                    当提供时会作为多模态输入附加到最后一条用户消息（或单轮 prompt）。
            api_url: (可选) 覆盖默认 API URL。常用于特定功能使用独立 LLM 端点。
            api_key: (可选) 覆盖默认 API Key。留空则沿用主配置。
            api_format: (可选) 覆盖 API 格式，仅支持 "gemini"/"openai"。留空则沿用主配置。
            return_error_text: 发生异常时是否返回面向用户的错误文案。False 时返回 None。

        Returns:
            生成的文本字符串，如果失败则返回 None。
        """
        # 获取 API 格式配置（支持调用方覆盖）
        resolved_api_format = (
            api_format or getattr(app_config, "_db_api_format", None) or "gemini"
        ).strip().lower()
        if resolved_api_format not in {"gemini", "openai"}:
            log.warning(
                f"generate_simple_response 收到未知 api_format={resolved_api_format}，回退为 gemini"
            )
            resolved_api_format = "gemini"

        # 先确定请求模型，再尝试做“别名 -> 实际模型名”解析
        requested_model_name = model_name or self.default_model_name
        final_model_name = requested_model_name

        endpoint_config = {}
        if isinstance(requested_model_name, str):
            endpoint_config = app_config.CUSTOM_GEMINI_ENDPOINTS.get(
                requested_model_name, {}
            ).copy()

        endpoint_api_url = str(endpoint_config.get("base_url", "") or "").strip()
        endpoint_api_key = str(endpoint_config.get("api_key", "") or "").strip()
        endpoint_model_name = str(endpoint_config.get("model_name", "") or "").strip()

        if endpoint_model_name:
            final_model_name = endpoint_model_name
            if endpoint_model_name != requested_model_name:
                log.info(
                    f"generate_simple_response 检测到模型别名 '{requested_model_name}'，"
                    f"实际调用模型将使用 '{endpoint_model_name}'"
                )

        # 优先级：显式入参 > 模型专属端点配置 > Dashboard 全局配置 > 环境变量
        resolved_api_url = api_url
        if resolved_api_url is None:
            resolved_api_url = (
                endpoint_api_url
                or getattr(app_config, "_db_api_url", None)
                or os.getenv("GEMINI_API_BASE_URL", "")
            )

        resolved_api_key = api_key
        if resolved_api_key is None:
            resolved_api_key = (
                endpoint_api_key
                or getattr(app_config, "_db_api_key", None)
                or os.getenv("GEMINI_API_KEYS", "")
            )

        lowered_api_url = (resolved_api_url or "").lower()
        looks_like_gemini_endpoint = (
            "generativelanguage.googleapis.com" in lowered_api_url
            or "aiplatform.googleapis.com" in lowered_api_url
            or "/v1beta" in lowered_api_url
        )
        if resolved_api_format == "openai" and looks_like_gemini_endpoint:
            log.warning(
                "generate_simple_response detected Gemini-like endpoint with openai format; forcing gemini format"
            )
            resolved_api_format = "gemini"

        # 调试日志
        has_messages = messages is not None and len(messages) > 0
        log.debug(
            "generate_simple_response 配置检查: "
            f"api_format={resolved_api_format}, "
            f"api_url={'已配置' if resolved_api_url else '未配置'}, "
            f"api_key={'已配置' if resolved_api_key else '未配置'}, "
            f"requested_model={requested_model_name}, "
            f"api_model={final_model_name}, "
            f"has_messages={has_messages}"
        )
        
        # 如果是 OpenAI 兼容格式，使用 OpenAI 客户端
        if resolved_api_format == "openai" and resolved_api_url and resolved_api_key:
            log.info(f"generate_simple_response 使用 OpenAI 兼容 API: {resolved_api_url[:30]}..., 模型: {final_model_name}")
            return await self._generate_simple_with_openai_compatible(
                prompt=prompt,
                generation_config=generation_config,
                model_name=final_model_name,
                api_url=resolved_api_url,
                api_key=resolved_api_key,
                messages=messages,
                images=images,
                return_error_text=return_error_text,
            )
        
        # 使用 Gemini SDK，优先使用 Dashboard 配置的 URL 和 Key
        if resolved_api_url and resolved_api_key:
            log.info(f"generate_simple_response 使用 Gemini SDK (自定义端点): {resolved_api_url[:30]}..., 模型: {final_model_name}")
            return await self._generate_simple_with_gemini_custom(
                prompt=prompt,
                generation_config=generation_config,
                model_name=final_model_name,
                api_url=resolved_api_url,
                api_key=resolved_api_key,
                messages=messages,
                images=images,
                return_error_text=return_error_text,
            )
        
        # 回退：如果没有 Dashboard 配置，使用 key rotation
        log.info(f"generate_simple_response 使用 Gemini SDK (Key Rotation), 模型: {final_model_name}")
        return await self._generate_simple_with_gemini_key_rotation(
            prompt=prompt,
            generation_config=generation_config,
            model_name=final_model_name,
            messages=messages,
            images=images,
            return_error_text=return_error_text,
        )
    
    async def _generate_simple_with_gemini_custom(
        self,
        prompt: str,
        generation_config: Dict,
        model_name: str,
        api_url: str,
        api_key: str,
        messages: Optional[List[Dict[str, str]]] = None,
        images: Optional[List[Dict[str, Any]]] = None,
        return_error_text: bool = True,
    ) -> Optional[str]:
        """
        使用 Gemini SDK 和自定义端点生成简单响应（内部方法）。
        使用 Dashboard 配置的 API URL 和 API Key。
        """
        try:
            # 创建使用自定义端点的客户端
            http_options = types.HttpOptions(base_url=api_url)
            client = genai.Client(api_key=api_key, http_options=http_options)
            
            response = await self._generate_sync_content_with_param_fallback(
                client=client,
                model_name=model_name,
                prompt=prompt,
                generation_config=generation_config,
                messages=messages,
                images=images,
            )

            if response.parts:
                return response.text.strip()

            log.warning(f"_generate_simple_with_gemini_custom 未能生成有效内容。API 响应: {response}")
            if response.prompt_feedback and response.prompt_feedback.block_reason:
                log.warning(
                    f"请求可能被安全策略阻止，原因: {response.prompt_feedback.block_reason}"
                )

            return None
        except Exception as e:
            log.error(f"Gemini SDK (自定义端点) 调用失败: {e}", exc_info=True)
            if return_error_text:
                return "抱歉，AI服务遇到了一个意料之外的错误，请稍后再试。"
            return None
    
    @_api_key_handler
    async def _generate_simple_with_gemini_key_rotation(
        self,
        prompt: str,
        generation_config: Dict,
        model_name: str,
        client: Any = None,
        messages: Optional[List[Dict[str, str]]] = None,
        images: Optional[List[Dict[str, Any]]] = None,
        return_error_text: bool = True,
    ) -> Optional[str]:
        """
        使用 Gemini SDK 和 Key Rotation 生成简单响应（内部方法）。
        仅在没有 Dashboard 配置时作为回退方案使用。
        """
        if not client:
            raise ValueError("装饰器未能提供客户端实例。")

        response = await self._generate_sync_content_with_param_fallback(
            client=client,
            model_name=model_name,
            prompt=prompt,
            generation_config=generation_config,
            messages=messages,
            images=images,
        )

        if response.parts:
            return response.text.strip()

        log.warning(f"_generate_simple_with_gemini_key_rotation 未能生成有效内容。API 响应: {response}")
        if response.prompt_feedback and response.prompt_feedback.block_reason:
            log.warning(
                f"请求可能被安全策略阻止，原因: {response.prompt_feedback.block_reason}"
            )

        return None
    
    async def _generate_simple_with_openai_compatible(
        self,
        prompt: str,
        generation_config: Dict,
        model_name: str,
        api_url: str,
        api_key: str,
        messages: Optional[List[Dict[str, str]]] = None,
        images: Optional[List[Dict[str, Any]]] = None,
        return_error_text: bool = True,
    ) -> Optional[str]:
        """
        使用 OpenAI 兼容 API 生成简单响应（内部方法）。
        用于摘要、查询重写等简单任务。支持多轮预填充对话。
        """
        temperature = generation_config.get("temperature", 0.5)
        max_tokens = generation_config.get("max_output_tokens", 2000)
        
        # 构建消息格式：优先使用 messages（多轮预填充），否则回退到单个 prompt
        if messages:
            # 将 Gemini 格式的 "model" role 映射为 OpenAI 的 "assistant"
            openai_messages = []
            for msg in messages:
                role = msg.get("role", "user")
                if role == "model":
                    role = "assistant"
                openai_messages.append({"role": role, "content": msg.get("content", "")})
            log.debug(f"OpenAI 兼容 API 使用多轮 messages ({len(openai_messages)} 条)")
        else:
            openai_messages = [
                {"role": "user", "content": prompt}
            ]

        image_content_parts = self._build_openai_image_content_parts(images)
        if image_content_parts:
            target_index: Optional[int] = None
            for idx in range(len(openai_messages) - 1, -1, -1):
                if openai_messages[idx].get("role") == "user":
                    target_index = idx
                    break

            if target_index is None:
                openai_messages.append({"role": "user", "content": []})
                target_index = len(openai_messages) - 1

            existing_content = openai_messages[target_index].get("content", "")
            if isinstance(existing_content, list):
                merged_content = list(existing_content)
            else:
                merged_content = []
                text_content = str(existing_content or "").strip()
                if text_content:
                    merged_content.append({"type": "text", "text": text_content})

            merged_content.extend(image_content_parts)
            openai_messages[target_index]["content"] = merged_content
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        
        # 智能处理 URL 路径
        api_url = api_url.rstrip("/")
        if not api_url.endswith("/chat/completions"):
            if "/v1" in api_url and not api_url.endswith("/v1"):
                api_url = api_url + "/chat/completions"
            elif api_url.endswith("/v1"):
                api_url = api_url + "/chat/completions"
            else:
                api_url = api_url + "/v1/chat/completions"
        
        payload = {
            "model": model_name,
            "messages": openai_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        disabled_payload_fields: set[str] = set()

        try:
            result = await self._post_openai_chat_completion_with_fallback(
                api_url=api_url,
                headers=headers,
                payload=payload,
                timeout_seconds=max(
                    20,
                    int(
                        app_config.API_RETRY_CONFIG.get(
                            "OPENAI_COMPAT_SIMPLE_TIMEOUT_SECONDS", 120
                        )
                    ),
                ),
                disabled_payload_fields=disabled_payload_fields,
                log_prefix="OpenAI 兼容 API (简单响应)",
            )

            if "choices" in result and len(result["choices"]) > 0:
                content = result["choices"][0].get("message", {}).get("content", "")
                if "usage" in result:
                    usage = result["usage"]
                    log.info(
                        f"OpenAI API (简单响应) Token 使用: "
                        f"输入={usage.get('prompt_tokens', 0)}, "
                        f"输出={usage.get('completion_tokens', 0)}"
                    )
                return content.strip() if content else None

            log.warning(f"OpenAI 兼容 API (简单响应) 返回空响应: {result}")
            return None
        except Exception as e:
            if self._exception_chain_contains_timeout(e):
                log.error(f"OpenAI 兼容 API (简单响应) 请求超时: {e}")
                if return_error_text:
                    return "抱歉，AI服务响应超时，请稍后再试。"
                return None

            log.error(f"OpenAI 兼容 API (简单响应) 调用失败: {e}", exc_info=True)
            if return_error_text:
                return "抱歉，AI服务遇到了一个意料之外的错误，请稍后再试。"
            return None

    async def generate_thread_praise(
        self, conversation_history: List[Dict[str, Any]], client: Any = None
    ) -> Optional[str]:
        """
        专用于生成帖子夸奖的方法。
        统一复用 generate_simple_response 的路由能力，
        以便自动遵循 Dashboard 中的 API 格式（Gemini / OpenAI 兼容）。

        Args:
            conversation_history: 完整的对话历史列表。
            client: 保留参数以兼容旧调用，当前逻辑不再依赖该参数。

        Returns:
            生成的夸奖文本，如果失败则返回 None。
        """
        if not conversation_history:
            return None

        praise_config = app_config.GEMINI_THREAD_PRAISE_CONFIG.copy()
        thinking_budget = praise_config.pop("thinking_budget", None)

        final_model_name = self.default_model_name
        if thinking_budget is not None and not self._is_no_thinking_model(final_model_name):
            praise_config["thinking_config"] = {
                "include_thoughts": True,
                "thinking_budget": thinking_budget,
            }
            log.info(f"已为暖贴功能注入思维链配置 (Thinking)，预算: {thinking_budget}。")

        messages: List[Dict[str, str]] = []
        for turn in conversation_history:
            role = str(turn.get("role", "")).strip().lower()
            if role not in {"user", "model", "assistant"}:
                continue

            parts = turn.get("parts", [])
            text_fragments: List[str] = []

            if isinstance(parts, list):
                for part in parts:
                    if isinstance(part, str):
                        cleaned_part = part.strip()
                        if cleaned_part:
                            text_fragments.append(cleaned_part)
                    elif isinstance(part, dict):
                        maybe_text = part.get("text")
                        if isinstance(maybe_text, str):
                            cleaned_part = maybe_text.strip()
                            if cleaned_part:
                                text_fragments.append(cleaned_part)
            elif isinstance(parts, str):
                cleaned_part = parts.strip()
                if cleaned_part:
                    text_fragments.append(cleaned_part)

            if not text_fragments:
                continue

            messages.append(
                {
                    "role": role,
                    "content": "\n".join(text_fragments),
                }
            )

        if not messages:
            log.warning("generate_thread_praise 未构建出有效 messages，已跳过调用。")
            return None

        generated_text = await self.generate_simple_response(
            prompt="",
            generation_config=praise_config,
            model_name=final_model_name,
            messages=messages,
            return_error_text=False,
        )

        if not generated_text:
            return None

        return regex_service.clean_ai_output(generated_text).strip()

    async def summarize_for_rag(
        self,
        latest_query: str,
        user_name: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        根据用户的最新发言和可选的对话历史，生成一个用于RAG搜索的独立查询。

        Args:
            latest_query: 用户当前发送的最新消息。
            user_name: 提问用户的名字。
            conversation_history: (可选) 包含多轮对话的列表。

        Returns:
            一个精炼后的、适合向量检索的查询字符串。
        """
        if not latest_query:
            log.info("RAG summarization called with no latest_query.")
            return ""

        prompt = prompt_service.build_rag_summary_prompt(
            latest_query, user_name, conversation_history
        )
        summarized_query = await self.generate_text(
            prompt, temperature=0.0, model_name=app_config.QUERY_REWRITING_MODEL
        )

        if not summarized_query:
            log.info("RAG查询总结失败，将直接使用用户的原始查询。")
            return latest_query.strip()

        return summarized_query.strip().strip('"')

    async def clear_user_context(self, user_id: int, guild_id: int):
        """清除指定用户的对话上下文"""
        await chat_db_manager.clear_ai_conversation_context(user_id, guild_id)
        log.info(f"已清除用户 {user_id} 在服务器 {guild_id} 的对话上下文")

    def is_available(self) -> bool:
        """检查AI服务是否可用"""
        return self.key_rotation_service is not None

    @_api_key_handler
    async def generate_text_with_image(
        self, prompt: str, image_bytes: bytes, mime_type: str, client: Any = None
    ) -> Optional[str]:
        """
        一个用于简单图文生成的精简方法。
        不涉及对话历史或上下文，仅根据输入提示和图片生成文本。
        非常适合用于如“投喂”等一次性功能。

        Args:
            prompt: 提供给模型的输入提示。
            image_bytes: 图片的字节数据。
            mime_type: 图片的 MIME 类型 (e.g., 'image/jpeg', 'image/png').

        Returns:
            生成的文本字符串，如果失败则返回 None。
        """
        if not client:
            raise ValueError("装饰器未能提供客户端实例。")

        request_contents: List[Any] = [prompt]
        try:
            frames, frame_meta = extract_image_frames_for_ai(
                image_bytes=image_bytes,
                mime_type=mime_type,
                max_gif_frames=app_config.IMAGE_PROCESSING_CONFIG.get(
                    "GIF_MAX_FRAMES", 4
                ),
            )

            if frame_meta.get("is_animated"):
                request_contents.append(
                    "这是一个动态图输入，已抽取关键帧 "
                    f"{frame_meta.get('sampled_frames', len(frames))}/"
                    f"{frame_meta.get('total_frames', len(frames))} 供分析。"
                )

            for frame in frames:
                with io.BytesIO() as output_buffer:
                    frame.save(output_buffer, format="PNG")
                    frame_bytes = output_buffer.getvalue()
                request_contents.append(
                    types.Part(
                        inline_data=types.Blob(
                            mime_type="image/png", data=frame_bytes
                        )
                    )
                )
        except Exception as e:
            log.error(f"处理图文输入时出错: {e}", exc_info=True)
            return "呜哇，我的眼睛跟不上啦！有点看花眼了"

        gen_config = types.GenerateContentConfig(
            **app_config.GEMINI_VISION_GEN_CONFIG, safety_settings=self.safety_settings
        )

        response = await client.aio.models.generate_content(
            model=self.default_model_name, contents=request_contents, config=gen_config
        )

        if response.parts:
            return response.text.strip()
        elif response.prompt_feedback and response.prompt_feedback.block_reason:
            log.warning(
                f"图文生成请求被安全策略阻止: {response.prompt_feedback.block_reason}"
            )
            return "为啥要投喂色图啊喂"

        log.warning(f"未能为图文生成有效回复。Response: {response}")
        return "我好像没看懂这张图里是什么，可以换一张或者稍后再试试吗？"

    async def generate_confession_response(
        self, prompt: str
    ) -> Optional[str]:
        """
        专用于生成忏悔回应的方法。
        优先使用 Dashboard 中配置的自定义 API URL 和 Key，
        如果未配置则回退到官方 API 密钥池。
        """
        # 检查是否有 Dashboard 配置的自定义端点
        global_api_url = getattr(app_config, '_db_api_url', None)
        global_api_key = getattr(app_config, '_db_api_key', None)
        
        if global_api_url and global_api_key:
            # 使用 Dashboard 配置的自定义端点
            log.info(f"忏悔功能: 使用 Dashboard 配置的自定义端点: {global_api_url[:30]}...")
            return await self._generate_confession_with_custom_endpoint(prompt, global_api_url, global_api_key)
        else:
            # 回退到官方 API 密钥池
            log.info("忏悔功能: 使用官方 API 密钥池")
            return await self._generate_confession_with_official_api(prompt)
    
    async def _generate_confession_with_custom_endpoint(
        self, prompt: str, api_url: str, api_key: str
    ) -> Optional[str]:
        """
        使用自定义端点生成忏悔回应。
        """
        gen_config = types.GenerateContentConfig(
            **app_config.GEMINI_CONFESSION_GEN_CONFIG,
            safety_settings=self.safety_settings,
        )
        final_model_name = self.default_model_name

        if app_config.DEBUG_CONFIG["LOG_AI_FULL_CONTEXT"]:
            log.info("--- 忏悔功能 · 完整 AI 上下文 (自定义端点) ---")
            log.info(prompt)
            log.info("------------------------------------")

        try:
            http_options = types.HttpOptions(base_url=api_url)
            client = genai.Client(api_key=api_key, http_options=http_options)
            
            response = await client.aio.models.generate_content(
                model=final_model_name, contents=[prompt], config=gen_config
            )

            if response.parts:
                return response.text.strip()

            log.warning(
                f"generate_confession_response (自定义端点) 未能生成有效内容。API 响应: {response}"
            )
            if response.prompt_feedback and response.prompt_feedback.block_reason:
                log.warning(
                    f"请求可能被安全策略阻止，原因: {response.prompt_feedback.block_reason}"
                )
            return None
        except Exception as e:
            log.error(f"忏悔功能使用自定义端点失败: {e}", exc_info=True)
            # 自定义端点失败时，尝试回退到官方 API
            log.info("忏悔功能: 自定义端点失败，尝试回退到官方 API 密钥池...")
            return await self._generate_confession_with_official_api(prompt)
    
    @_api_key_handler
    async def _generate_confession_with_official_api(
        self, prompt: str, client: Any = None
    ) -> Optional[str]:
        """
        使用官方 API 密钥池生成忏悔回应。
        """
        if not client:
            raise ValueError("装饰器未能提供客户端实例。")

        gen_config = types.GenerateContentConfig(
            **app_config.GEMINI_CONFESSION_GEN_CONFIG,
            safety_settings=self.safety_settings,
        )
        final_model_name = self.default_model_name

        if app_config.DEBUG_CONFIG["LOG_AI_FULL_CONTEXT"]:
            log.info("--- 忏悔功能 · 完整 AI 上下文 (官方 API) ---")
            log.info(prompt)
            log.info("------------------------------------")

        response = await client.aio.models.generate_content(
            model=final_model_name, contents=[prompt], config=gen_config
        )

        if response.parts:
            return response.text.strip()

        log.warning(
            f"generate_confession_response (官方 API) 未能生成有效内容。API 响应: {response}"
        )
        if response.prompt_feedback and response.prompt_feedback.block_reason:
            log.warning(
                f"请求可能被安全策略阻止，原因: {response.prompt_feedback.block_reason}"
            )

        return None

    async def _record_token_usage(
        self,
        client: Any,  # 使用 Any 来避免 Pylance 对动态 client 的错误
        model_name: str,
        input_contents: List[types.Content],
        output_text: str,
    ):
        """记录 API 调用的 Token 使用情况到数据库。"""
        try:
            # 尝试使用 count_tokens API，如果不支持则使用估算
            try:
                input_token_response = await client.aio.models.count_tokens(  # type: ignore
                    model=model_name, contents=input_contents
                )
                output_token_response = await client.aio.models.count_tokens(  # type: ignore
                    model=model_name, contents=[output_text]
                )
                input_tokens = input_token_response.total_tokens
                output_tokens = output_token_response.total_tokens
            except Exception as count_error:
                # 代理站可能不支持 count_tokens，使用估算
                # 中文约每字符 1.5 token，英文约每 4 字符 1 token
                log.debug(f"count_tokens API 不可用，使用估算: {count_error}")
                input_text = str(input_contents)
                input_tokens = self._estimate_tokens(input_text)
                output_tokens = self._estimate_tokens(output_text)
            
            total_tokens = input_tokens + output_tokens

            # 获取当前日期并更新数据库
            usage_date = datetime.now(ZoneInfo("Asia/Shanghai")).date()
            async with AsyncSessionLocal() as session:
                usage_record = await token_usage_service.get_token_usage(
                    session, usage_date
                )
                if usage_record:
                    await token_usage_service.update_token_usage(
                        session,
                        usage_record,
                        input_tokens,
                        output_tokens,
                        total_tokens,
                    )
                else:
                    await token_usage_service.create_token_usage(
                        session,
                        usage_date,
                        input_tokens,
                        output_tokens,
                        total_tokens,
                    )
            log.info(
                f"Token usage recorded: Input={input_tokens}, Output={output_tokens}, Total={total_tokens}"
            )
        except Exception as e:
            log.error(f"Failed to record token usage: {e}", exc_info=True)

    def _estimate_tokens(self, text: str) -> int:
        """估算文本的 token 数量。
        
        当 count_tokens API 不可用时使用此方法。
        中文约每字符 1.5 token，英文约每 4 字符 1 token。
        """
        if not text:
            return 0
        
        # 统计中文字符和非中文字符
        chinese_chars = 0
        other_chars = 0
        for char in text:
            if '\u4e00' <= char <= '\u9fff':
                chinese_chars += 1
            else:
                other_chars += 1
        
        # 估算 token 数
        chinese_tokens = int(chinese_chars * 1.5)
        other_tokens = int(other_chars / 4) + 1
        
        return chinese_tokens + other_tokens


# 全局实例
gemini_service = GeminiService()
