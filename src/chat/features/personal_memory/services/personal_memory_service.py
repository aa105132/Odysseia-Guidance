import logging
from typing import Optional
from sqlalchemy.future import select
from sqlalchemy import update
from src.database.database import AsyncSessionLocal
from src.database.models import CommunityMemberProfile
from src.chat.config import chat_config

log = logging.getLogger(__name__)


class PersonalMemoryService:
    async def update_and_conditionally_summarize_memory(
        self, user_id: int, user_name: str, user_content: str, ai_response: str
    ):
        """
        核心入口：更新对话历史和计数，并在达到阈值时触发总结。
        所有数据库操作都在ParadeDB中完成。
        如果用户没有档案，会自动创建一个。
        """
        history_to_summarize = None
        async with AsyncSessionLocal() as session:
            async with session.begin():
                stmt = (
                    select(CommunityMemberProfile)
                    .where(CommunityMemberProfile.discord_id == str(user_id))
                    .with_for_update()
                )
                result = await session.execute(stmt)
                profile = result.scalars().first()

                # 如果用户没有档案，自动创建一个基础档案
                if not profile:
                    log.info(f"用户 {user_id} 没有个人档案，正在自动创建...")
                    import uuid
                    profile = CommunityMemberProfile(
                        external_id=f"auto_{user_id}_{uuid.uuid4().hex[:8]}",  # 生成唯一的 external_id
                        discord_id=str(user_id),
                        title=user_name,  # 使用 title 字段存储昵称
                        full_text=f"用户 {user_name} 的自动创建档案",  # full_text 是必需字段
                        personal_message_count=0,
                        history=[],
                    )
                    session.add(profile)
                    await session.flush()  # 确保 profile 被写入数据库
                    log.info(f"已为用户 {user_id} 自动创建个人档案。")

                current_count = getattr(profile, "personal_message_count", 0) or 0
                new_count = current_count + 1
                setattr(profile, "personal_message_count", new_count)

                new_turn = {"role": "user", "parts": [user_content]}
                new_model_turn = {"role": "model", "parts": [ai_response]}

                current_history = getattr(profile, "history", [])
                new_history = list(current_history or [])
                new_history.extend([new_turn, new_model_turn])
                setattr(profile, "history", new_history)

                log.debug(f"用户 {user_id} 的消息计数更新为: {new_count}")

                if new_count >= chat_config.PERSONAL_MEMORY_CONFIG["summary_threshold"]:
                    log.info(f"用户 {user_id} 达到阈值，准备总结。")
                    history_to_summarize = list(new_history)
                    setattr(profile, "personal_message_count", 0)
                    setattr(profile, "history", [])

        if history_to_summarize:
            summary_saved = False
            try:
                summary_saved = await self._summarize_memory(
                    user_id, history_to_summarize
                )
            except Exception as e:
                log.error(
                    f"为用户 {user_id} 生成记忆摘要时发生异常，准备恢复历史记录: {e}",
                    exc_info=True,
                )

            if not summary_saved:
                await self._restore_history_after_failed_summary(
                    user_id, history_to_summarize
                )

    @staticmethod
    def _count_user_turns(conversation_history: list) -> int:
        """统计一段对话历史中的用户轮次数。"""
        return sum(
            1 for turn in (conversation_history or []) if turn.get("role") == "user"
        )

    async def _summarize_memory(self, user_id: int, conversation_history: list) -> bool:
        """私有方法：获取历史，生成摘要，并清空计数和历史。"""
        log.info(f"开始为用户 {user_id} 生成记忆摘要。")

        # 延迟导入，避免在 GeminiService 初始化工具加载阶段触发循环依赖
        from src.chat.services.gemini_service import gemini_service

        async with AsyncSessionLocal() as session:
            stmt = select(CommunityMemberProfile.personal_summary).where(
                CommunityMemberProfile.discord_id == str(user_id)
            )
            result = await session.execute(stmt)
            old_summary = result.scalars().first() or "无"

        dialogue_text = "\n".join(
            f"{'用户' if turn.get('role') == 'user' else 'AI'}: {' '.join(map(str, turn.get('parts', [])))}"
            for turn in conversation_history
        ).strip()

        if not dialogue_text:
            log.warning(f"用户 {user_id} 的对话历史格式化后为空。")
            return False

        # 3. 构建 Prompt 并调用 AI 生成新摘要
        prompt_template = chat_config.PROMPT_CONFIG.get("personal_memory_summary")
        if not prompt_template:
            log.error("未找到 'personal_memory_summary' 的 prompt 模板。")
            return False

        final_prompt = prompt_template.format(
            old_summary=old_summary, dialogue_history=dialogue_text
        )

        # --- [MEMORY DEBUGGER] ---
        def count_summary_lines(summary: str) -> int:
            return len(
                [line for line in summary.split("\n") if line.strip().startswith("-")]
            )

        old_summary_lines = count_summary_lines(old_summary)
        log.info(f"---[MEMORY DEBUGGER]--- 用户 {user_id} 开始总结 ---")
        log.info(f"旧摘要行数: {old_summary_lines}")
        log.info(f"完整的旧摘要:\n{old_summary}")
        log.info(f"用于总结的对话历史:\n{dialogue_text}")
        # --- [MEMORY DEBUGGER] ---

        new_summary = await gemini_service.generate_simple_response(
            prompt=final_prompt,
            generation_config=chat_config.GEMINI_SUMMARY_GEN_CONFIG,
            model_name=chat_config.SUMMARY_MODEL,
            # 摘要属于系统内部流程，失败时应返回 None，避免把错误文案写入长期记忆
            return_error_text=False,
        )

        # 4. 将新摘要保存到数据库
        if new_summary:
            # --- [MEMORY DEBUGGER] ---
            new_summary_lines = count_summary_lines(new_summary)
            log.info(f"---[MEMORY DEBUGGER]--- 用户 {user_id} 总结完毕 ---")
            log.info(f"新摘要行数: {new_summary_lines} (Prompt要求 <= 30)")
            if new_summary_lines > 30:
                log.error("!!!!!!!! MEMORY EXPLOSION DETECTED !!!!!!!!")
                log.error(
                    f"用户 {user_id} 的新摘要行数 ({new_summary_lines}) 超过了30条的硬性限制！"
                )
                log.error(
                    f"完整的失控摘要:\n{new_summary}"
                )  # 使用 ERROR 级别记录失控的摘要
            else:
                log.debug(f"完整的新摘要:\n{new_summary}")
            # --- [MEMORY DEBUGGER] ---
            await self.update_summary_manually(user_id, new_summary)
            log.info(f"用户 {user_id} 的总结流程完成。")
            return True
        else:
            log.error(f"为用户 {user_id} 生成记忆摘要失败，AI 返回空。")
        log.info(f"用户 {user_id} 的总结流程完成。")
        return False

    async def _restore_history_after_failed_summary(
        self, user_id: int, conversation_history: list
    ) -> None:
        """当总结失败时，尽量恢复已清空的历史与计数，避免长期记忆丢失。"""
        restored_history = list(conversation_history or [])
        restored_count = self._count_user_turns(restored_history)

        async with AsyncSessionLocal() as session:
            async with session.begin():
                stmt = (
                    select(CommunityMemberProfile)
                    .where(CommunityMemberProfile.discord_id == str(user_id))
                    .with_for_update()
                )
                result = await session.execute(stmt)
                profile = result.scalars().first()

                if not profile:
                    log.error(f"恢复用户 {user_id} 的个人记忆失败：未找到档案。")
                    return

                current_history = list(getattr(profile, "history", []) or [])
                current_count = getattr(profile, "personal_message_count", 0) or 0

                if current_history:
                    restored_history.extend(current_history)

                setattr(profile, "history", restored_history)
                setattr(profile, "personal_message_count", restored_count + current_count)

        log.warning(
            f"用户 {user_id} 的记忆摘要生成失败，已恢复 {restored_count} 轮历史记录。"
        )

    async def get_memory_summary(self, user_id: int) -> str:
        """根据用户ID从 ParadeDB 获取其个人记忆摘要。"""
        async with AsyncSessionLocal() as session:
            stmt = select(CommunityMemberProfile.personal_summary).where(
                CommunityMemberProfile.discord_id == str(user_id)
            )
            result = await session.execute(stmt)
            summary = result.scalars().first()

            if summary:
                log.debug(f"从 ParadeDB 找到用户 {user_id} 的摘要。")
                return summary
            else:
                log.debug(f"在 ParadeDB 中未找到用户 {user_id} 的摘要。")
                return "该用户当前没有个人记忆摘要。"

    async def update_summary_manually(self, user_id: int, new_summary: str):
        """
        仅手动更新用户的个人记忆摘要，不影响计数或历史记录。
        主要用于管理员手动编辑。
        """
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await self._update_summary(session, user_id, new_summary)
        log.info(f"为用户 {user_id} 手动更新了记忆摘要。")

    async def _update_summary(self, session, user_id: int, new_summary: Optional[str]):
        """私有方法：只更新摘要。"""
        stmt = (
            update(CommunityMemberProfile)
            .where(CommunityMemberProfile.discord_id == str(user_id))
            .values(personal_summary=new_summary)
        )
        await session.execute(stmt)

    async def _reset_history_and_count(self, session, user_id: int):
        """私有方法：只重置计数和历史。"""
        stmt = (
            update(CommunityMemberProfile)
            .where(CommunityMemberProfile.discord_id == str(user_id))
            .values(
                personal_message_count=0,
                history=[],
            )
        )
        await session.execute(stmt)

    async def update_summary_and_reset_history(
        self, user_id: int, new_summary: Optional[str]
    ):
        """
        在 ParadeDB 中更新摘要，同时重置个人消息计数和对话历史。
        (重构后，此函数调用两个独立的私有方法)
        """
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await self._update_summary(session, user_id, new_summary)
                await self._reset_history_and_count(session, user_id)
        log.info(f"为用户 {user_id} 更新了记忆摘要，并重置了计数和历史。")

    async def clear_personal_memory(self, user_id: int):
        """
        清除指定用户的个人记忆摘要、对话历史和消息计数。
        """
        log.info(f"正在为用户 {user_id} 清除个人记忆...")
        await self.update_summary_and_reset_history(user_id, None)
        log.info(f"用户 {user_id} 的个人记忆已清除。")

    async def reset_memory_and_delete_history(self, user_id: int):
        """
        删除对话记录并重置记忆。
        这会清除用户的个人记忆摘要，并删除所有相关的对话历史记录。
        """
        log.info(f"正在为用户 {user_id} 重置记忆并删除对话历史...")
        await self.update_summary_and_reset_history(user_id, None)
        log.info(f"用户 {user_id} 的记忆和对话历史已清除。")

    async def delete_conversation_history(self, user_id: int):
        """
        单纯删除对话记录。
        这仅删除指定用户的对话历史记录和重置消息计数，不影响其个人记忆摘要。
        """
        log.info(f"正在为用户 {user_id} 删除对话历史...")
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await self._reset_history_and_count(session, user_id)
        log.info(f"用户 {user_id} 的对话历史已删除。")

    async def auto_generate_profile(
        self,
        user_id: int,
        user_name: str,
        is_bot: bool,
        avatar_url: Optional[str],
        user_message: str,
        ai_response: str,
    ):
        """
        后台任务：根据头像和首次对话内容，用 AI 自动生成用户初始名片。
        """
        import json
        import aiohttp

        log.info(f"开始为用户 {user_id} 自动生成初始名片...")

        # 1. 下载头像
        avatar_image_data = None
        if avatar_url:
            try:
                async with aiohttp.ClientSession() as http_session:
                    async with http_session.get(avatar_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status == 200:
                            image_bytes = await resp.read()
                            avatar_image_data = {"data": image_bytes, "mime_type": "image/png"}
                            log.debug(f"用户 {user_id} 的头像下载成功 ({len(image_bytes)} bytes)。")
                        else:
                            log.warning(f"下载用户 {user_id} 的头像失败，HTTP {resp.status}。")
            except Exception as e:
                log.warning(f"下载用户 {user_id} 的头像时出错: {e}")

        # 2. 构建 prompt
        prompt_template = chat_config.PROMPT_CONFIG.get("auto_profile_generation")
        if not prompt_template:
            log.error("未找到 'auto_profile_generation' 的 prompt 模板，跳过自动名片生成。")
            return

        final_prompt = prompt_template.format(
            user_name=user_name,
            is_bot="是（Bot用户）" if is_bot else "否（真人用户）",
            user_message=user_message[:500],
            ai_response=ai_response[:500],
        )

        # 3. 调用 AI 生成
        from src.chat.services.gemini_service import gemini_service

        images_param = [avatar_image_data] if avatar_image_data else None
        raw_response = await gemini_service.generate_simple_response(
            prompt=final_prompt,
            generation_config=chat_config.GEMINI_PROFILE_GEN_CONFIG,
            model_name=chat_config.SUMMARY_MODEL,
            images=images_param,
            return_error_text=False,
        )

        if not raw_response:
            log.warning(f"为用户 {user_id} 生成初始名片失败，AI 返回空。")
            return

        # 4. 解析 JSON
        try:
            cleaned = raw_response.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            profile_data = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError) as e:
            log.warning(f"解析用户 {user_id} 的自动名片 JSON 失败: {e}\n原始响应: {raw_response}")
            return

        personality = profile_data.get("personality", "暂无足够信息")
        background = profile_data.get("background", "暂无足够信息")
        preferences = profile_data.get("preferences", "暂无足够信息")

        # 5. 更新数据库
        profile_id = None
        async with AsyncSessionLocal() as session:
            async with session.begin():
                stmt = (
                    select(CommunityMemberProfile)
                    .where(CommunityMemberProfile.discord_id == str(user_id))
                    .with_for_update()
                )
                result = await session.execute(stmt)
                profile = result.scalars().first()

                if not profile:
                    log.warning(f"用户 {user_id} 的档案在生成名片时已不存在，跳过。")
                    return

                if profile.source_metadata:
                    log.info(f"用户 {user_id} 的档案已有 source_metadata（可能已手动创建），跳过自动填充。")
                    return

                full_text = (
                    f"名称: {user_name}\n"
                    f"Discord ID: {user_id}\n"
                    f"性格特点: {personality}\n"
                    f"背景信息: {background}\n"
                    f"喜好偏好: {preferences}"
                )
                source_metadata = {
                    "name": user_name,
                    "discord_id": str(user_id),
                    "personality": personality,
                    "background": background,
                    "preferences": preferences,
                    "source": "auto_generated",
                    "contributor_id": "system",
                }

                profile.full_text = full_text
                profile.source_metadata = source_metadata
                profile.title = user_name
                profile_id = profile.id

        if profile_id:
            log.info(f"已为用户 {user_id} 自动生成初始名片 (profile_id={profile_id})。")
            # 6. 触发 RAG 重建索引
            try:
                from src.chat.features.world_book.services.incremental_rag_service import (
                    incremental_rag_service,
                )
                import asyncio
                asyncio.create_task(
                    incremental_rag_service.process_community_member(profile_id)
                )
                log.info(f"已为用户 {user_id} 的名片触发 RAG 重建索引。")
            except Exception as rag_error:
                log.error(f"为用户 {user_id} 的名片触发 RAG 索引时出错: {rag_error}", exc_info=True)


# 单例实例
personal_memory_service = PersonalMemoryService()
