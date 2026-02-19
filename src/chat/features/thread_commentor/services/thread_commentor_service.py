# -*- coding: utf-8 -*-

import logging
import discord
from typing import Optional, List
import sqlite3
import json
import os

from src import config
from src.chat.services.gemini_service import gemini_service
from src.chat.config.thread_prompts import (
    get_random_praise_prompt,
    get_random_auto_chat_prompt,
)
from src.chat.config.prompts import (
    PROMPT_CONFIG,
)  # <--- 修正：从新的位置导入 PROMPT_CONFIG
from datetime import datetime, timezone, timedelta
from src.chat.utils.prompt_utils import replace_emojis, get_thread_commentor_persona
from src.chat.utils.database import chat_db_manager
from src.chat.features.odysseia_coin.service.coin_service import coin_service
from src.chat.features.world_book.services.world_book_service import world_book_service
from src.chat.config import chat_config

log = logging.getLogger(__name__)


class ThreadCommentorService:
    """处理新帖子评价功能的服务"""

    def __init__(self):
        self.world_book_db_path = os.path.join(config.DATA_DIR, "world_book.sqlite3")

    async def _get_user_memory(self, user_id: int) -> str:
        """
        从世界书和主数据库中获取用户的个人记忆。
        """
        memory_parts = []

        # 1. 从世界书数据库获取用户档案
        try:
            with sqlite3.connect(self.world_book_db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT content_json FROM community_members WHERE discord_number_id = ?",
                    (str(user_id),),
                )
                row = cursor.fetchone()
                if row and row["content_json"]:
                    profile = json.loads(row["content_json"])
                    profile_text = (
                        f"用户的公开档案：\n"
                        f"- 昵称: {profile.get('name', '未知')}\n"
                        f"- 性格: {profile.get('personality', '未知')}\n"
                        f"- 背景: {profile.get('background', '未知')}\n"
                        f"- 偏好: {profile.get('preferences', '未知')}"
                    )
                    memory_parts.append(profile_text)
        except Exception as e:
            log.error(f"从世界书数据库为用户 {user_id} 获取档案时出错: {e}")

        # 2. 从主数据库获取对话摘要
        try:
            user_profile = await chat_db_manager.get_user_profile(user_id)
            if user_profile and user_profile["personal_summary"]:
                summary_text = (
                    f"我与该用户的过往对话摘要：\n{user_profile['personal_summary']}"
                )
                memory_parts.append(summary_text)
        except Exception as e:
            log.error(f"从主数据库为用户 {user_id} 获取摘要时出错: {e}")

        if not memory_parts:
            return "关于这位用户，我暂时还没有任何记忆。"

        return "\n\n---\n\n".join(memory_parts)

    async def praise_new_thread(
        self, thread: discord.Thread, user_id: int, user_nickname: str
    ) -> Optional[str]:
        """
        针对新创建的帖子生成一段结合用户记忆的个性化夸奖。
        """
        try:
            # 检查用户是否禁用了暖贴功能
            if await coin_service.has_withered_sunflower(user_id):
                log.info(
                    f"用户 {user_id} 已禁用暖贴功能，跳过对帖子 '{thread.name}' 的评价。"
                )
                return None

            # 检查用户是否禁用了帖子回复功能
            if await coin_service.blocks_thread_replies(user_id):
                log.info(
                    f"用户 {user_id} 已禁用帖子回复功能，跳过对帖子 '{thread.name}' 的评价。"
                )
                return None

            # 1. 获取帖子的初始消息
            if thread.starter_message:
                first_message = thread.starter_message
            else:
                first_message = await thread.fetch_message(thread.id)

            if not first_message or not first_message.content:
                log.info(
                    f"帖子 '{thread.name}' (ID: {thread.id}) 没有有效的初始消息内容，跳过评价。"
                )
                return None

            # 2. 准备帖子内容
            title = thread.name
            tags = ", ".join([tag.name for tag in thread.applied_tags])
            content = first_message.content
            max_content_length = 1500
            if len(content) > max_content_length:
                content = content[:max_content_length] + "..."
            thread_full_content = f"标题: {title}\n标签: {tags}\n内容: {content}"

            # 3. 获取用户记忆
            user_memory = await self._get_user_memory(user_id)

            # 4. 新增：调用 RAG 服务进行世界书搜索
            rag_context = ""
            try:
                log.info(f"开始为帖子 '{title}' 的内容进行 RAG 搜索...")
                rag_results = await world_book_service.find_entries(
                    latest_query=thread_full_content,
                    user_id=user_id,
                    guild_id=thread.guild.id,
                    user_name=user_nickname,
                    n_results=chat_config.RAG_N_RESULTS_THREAD_COMMENTOR,  # 最多获取10个相关条目
                    max_distance=0.7,
                )
                if rag_results:
                    rag_context_parts = [
                        "为了帮助你更好地理解帖子中可能提到的社区术语，这里有一些相关的背景知识："
                    ]
                    for result in rag_results:
                        entry_title = result.get("metadata", {}).get(
                            "title", "未知标题"
                        )
                        entry_content = result.get("document", "无内容")
                        rag_context_parts.append(
                            f"- **{entry_title}**: {entry_content}"
                        )

                    rag_context = "\n".join(rag_context_parts)
                    log.info(
                        f"RAG 搜索成功，为帖子 '{title}' 找到了 {len(rag_results)} 个相关条目。"
                    )
                else:
                    log.info(f"RAG 搜索没有为帖子 '{title}' 找到相关条目。")
            except Exception as e:
                log.error(
                    f"为帖子 '{title}' 进行 RAG 搜索时发生错误: {e}", exc_info=True
                )

            # 5. 准备调用所需的所有信息片段
            core_persona = get_thread_commentor_persona()
            task_prompt = get_random_praise_prompt().format(user_nickname=user_nickname)

            log.info(f"为帖子 '{title}' 构建带有破限功能的统一上下文，即将调用AI服务。")

            # 6. 手动构建带有“破限”逻辑的对话历史
            conversation_history = [
                {
                    "role": "user",
                    "parts": [PROMPT_CONFIG["default"]["JAILBREAK_USER_PROMPT"]],
                },
                {
                    "role": "model",
                    "parts": [PROMPT_CONFIG["default"]["JAILBREAK_MODEL_RESPONSE"]],
                },
                {"role": "user", "parts": [core_persona]},
                {"role": "model", "parts": ["好的，我是月月，已经准备好了"]},
                {"role": "user", "parts": [user_memory]},
                {"role": "model", "parts": ["关于你的事情，我当然都记得"]},
            ]

            # 如果有 RAG 结果，则注入
            if rag_context:
                conversation_history.append({"role": "user", "parts": [rag_context]})
                conversation_history.append(
                    {"role": "model", "parts": ["哦哦，原来是这样！我明白了！"]}
                )

            conversation_history.extend(
                [
                    {"role": "user", "parts": [task_prompt]},
                    {"role": "model", "parts": ["好的，我记下了。"]},
                ]
            )

            # 注入最终指令到最后一条 model 消息
            beijing_tz = timezone(timedelta(hours=8))
            current_beijing_time = datetime.now(beijing_tz).strftime(
                "%Y年%m月%d日 %H:%M"
            )
            final_injection_content = PROMPT_CONFIG["default"][
                "JAILBREAK_FINAL_INSTRUCTION"
            ].format(
                guild_name=thread.guild.name,
                location_name=thread.parent.name if thread.parent else "未知版区",
                current_time=current_beijing_time,
            )

            last_model_message = conversation_history[-1]
            if last_model_message["role"] == "model" and last_model_message["parts"]:
                last_model_message["parts"][0] += f" {final_injection_content}"

            # 添加最终的用户输入（帖子内容）
            conversation_history.append(
                {"role": "user", "parts": [thread_full_content]}
            )

            # 7. 调用重构后的 Gemini 服务方法
            praise_text = await gemini_service.generate_thread_praise(
                conversation_history=conversation_history
            )

            if praise_text:
                # 使用 prompt_utils 中的函数来处理表情符号
                processed_praise = replace_emojis(praise_text)
                log.info(f"成功为帖子 '{title}' 生成并处理后评价: {processed_praise}")
                return processed_praise
            else:
                log.warning(f"为帖子 '{title}' 生成评价时返回了空内容。")
                return None

        except discord.errors.NotFound:
            log.warning(f"无法找到帖子 {thread.id} 的初始消息，可能已被删除。")
            return None
        except Exception as e:
            log.error(
                f"为帖子 '{thread.name}' (ID: {thread.id}) 生成评价时发生意外错误: {e}",
                exc_info=True,
            )
            return None

    def _format_recent_messages_for_prompt(
        self, recent_messages: List[discord.Message], limit: int
    ) -> str:
        """将最近消息格式化为可供模型理解的上下文文本。"""
        selected_messages = recent_messages[-limit:] if limit > 0 else recent_messages
        context_lines: List[str] = []

        for message in selected_messages:
            content = (message.content or "").strip()
            if not content:
                if message.attachments:
                    content = f"[发送了 {len(message.attachments)} 个附件]"
                elif message.embeds:
                    content = f"[发送了 {len(message.embeds)} 个嵌入内容]"
                else:
                    continue

            if len(content) > 220:
                content = content[:220] + "..."

            author_name = getattr(message.author, "display_name", None) or getattr(
                message.author, "name", "未知用户"
            )
            role_label = "机器人" if message.author.bot else "成员"
            context_lines.append(f"{role_label} {author_name}: {content}")

        if not context_lines:
            return "最近没有可用的聊天内容。"

        return "\n".join(context_lines)

    async def generate_auto_target_message(
        self,
        target: discord.Thread | discord.TextChannel,
        recent_messages: List[discord.Message],
        is_idle_call: bool = False,
        idle_minutes: int = 0,
        target_user_mention: Optional[str] = None,
    ) -> Optional[str]:
        """基于目标会话（帖子或普通文本频道）生成自动暖场/召回发言。"""
        try:
            is_thread = isinstance(target, discord.Thread)

            # 帖子场景下尊重帖主对暖贴的偏好；普通频道不做此限制
            if is_thread and target.owner_id:
                if await coin_service.has_withered_sunflower(target.owner_id):
                    log.info(
                        f"帖子 '{target.name}' 的作者禁用了暖贴功能，跳过自动发言。"
                    )
                    return None

                if await coin_service.blocks_thread_replies(target.owner_id):
                    log.info(
                        f"帖子 '{target.name}' 的作者禁用了帖子回复，跳过自动发言。"
                    )
                    return None

            context_limit = int(
                chat_config.THREAD_COMMENTOR_CONFIG.get(
                    "AUTO_CHAT_CONTEXT_MESSAGE_LIMIT", 20
                )
            )
            formatted_context = self._format_recent_messages_for_prompt(
                recent_messages, context_limit
            )

            if is_thread:
                location_name = target.parent.name if target.parent else "未知版区"
                extra_info = ", ".join([tag.name for tag in target.applied_tags]) or "无"
                task_title = target.name
                context_header = (
                    f"帖子标题: {target.name}\n"
                    f"所在版区: {location_name}\n"
                    f"标签: {extra_info}\n"
                )
                target_kind = "帖子"
            else:
                location_name = target.category.name if target.category else "无分类"
                extra_info = f"频道ID: {target.id}"
                task_title = target.name
                context_header = (
                    f"频道名称: {target.name}\n"
                    f"频道分类: {location_name}\n"
                    f"附加信息: {extra_info}\n"
                )
                target_kind = "频道"

            target_hint = (
                f"如果语气自然，可以轻轻点名 {target_user_mention} 一起聊，但不要强制@所有人。"
                if target_user_mention
                else "没有合适点名对象时，使用自然的群体邀请语即可，不要生硬@人。"
            )

            prompt_template = get_random_auto_chat_prompt(is_idle_call)
            if not is_thread:
                prompt_template = prompt_template.replace("帖子", "频道").replace(
                    "讨论串", "聊天频道"
                )

            task_prompt = prompt_template.format(
                thread_title=task_title,
                idle_minutes=max(1, int(idle_minutes)),
                target_hint=target_hint,
            )

            core_persona = get_thread_commentor_persona()
            conversation_history = [
                {
                    "role": "user",
                    "parts": [PROMPT_CONFIG["default"]["JAILBREAK_USER_PROMPT"]],
                },
                {
                    "role": "model",
                    "parts": [PROMPT_CONFIG["default"]["JAILBREAK_MODEL_RESPONSE"]],
                },
                {"role": "user", "parts": [core_persona]},
                {"role": "model", "parts": ["好的，我是月月，已经准备好了"]},
                {"role": "user", "parts": [task_prompt]},
                {
                    "role": "model",
                    "parts": ["明白了，我会以自然的聊天语气接住话题。"],
                },
            ]

            # 注入最终指令到最后一条 model 消息
            beijing_tz = timezone(timedelta(hours=8))
            current_beijing_time = datetime.now(beijing_tz).strftime(
                "%Y年%m月%d日 %H:%M"
            )
            final_injection_content = PROMPT_CONFIG["default"][
                "JAILBREAK_FINAL_INSTRUCTION"
            ].format(
                guild_name=target.guild.name,
                location_name=location_name,
                current_time=current_beijing_time,
            )

            last_model_message = conversation_history[-1]
            if last_model_message["role"] == "model" and last_model_message["parts"]:
                last_model_message["parts"][0] += f" {final_injection_content}"

            target_context = (
                f"目标类型: {target_kind}\n"
                f"{context_header}"
                f"最近聊天记录:\n{formatted_context}"
            )
            conversation_history.append({"role": "user", "parts": [target_context]})

            generated_text = await gemini_service.generate_thread_praise(
                conversation_history=conversation_history
            )
            if not generated_text:
                return None

            return replace_emojis(generated_text)
        except Exception as e:
            log.error(
                f"为目标 '{getattr(target, 'name', '未知')}' 生成自动发言时出错: {e}",
                exc_info=True,
            )
            return None

    async def generate_auto_thread_message(
        self,
        thread: discord.Thread,
        recent_messages: List[discord.Message],
        is_idle_call: bool = False,
        idle_minutes: int = 0,
        target_user_mention: Optional[str] = None,
    ) -> Optional[str]:
        """兼容旧调用：为帖子生成自动发言。"""
        return await self.generate_auto_target_message(
            target=thread,
            recent_messages=recent_messages,
            is_idle_call=is_idle_call,
            idle_minutes=idle_minutes,
            target_user_mention=target_user_mention,
        )


thread_commentor_service = ThreadCommentorService()
