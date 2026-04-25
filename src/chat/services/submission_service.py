# -*- coding: utf-8 -*-
import logging
import json
import sqlite3
import os
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
import discord

from src import config
from src.chat.config import chat_config
from src.chat.services.review_service import review_service
from src.chat.features.odysseia_coin.service.coin_service import coin_service
from src.chat.features.admin_panel.services.db_services import (
    get_parade_db_connection,
    get_cursor,
)
from src.chat.features.world_book.services.incremental_rag_service import (
    incremental_rag_service,
)
import asyncio

log = logging.getLogger(__name__)


class SubmissionService:
    """处理所有类型内容提交的服务"""

    def _get_db_connection(self):
        """获取世界书数据库的连接"""
        try:
            db_path = os.path.join(config.DATA_DIR, "world_book.sqlite3")
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error as e:
            log.error(f"连接到世界书数据库失败: {e}", exc_info=True)
            return None

    async def _create_pending_entry(
        self,
        entry_type: str,
        interaction: discord.Interaction,
        entry_data: Dict[str, Any],
    ) -> Optional[int]:
        """
        将提交的数据作为待审核条目存入数据库。
        这是所有提交类型的通用内部方法。
        """
        conn = self._get_db_connection()
        if not conn:
            return None

        try:
            cursor = conn.cursor()

            # --- 动态获取审核配置 ---
            review_config_key = ""
            if entry_type in ["general_knowledge", "community_member"]:
                review_config_key = "review_settings"
            elif entry_type == "work_event":
                review_config_key = "work_event_review_settings"

            if not review_config_key:
                log.error(f"未知的提交类型 '{entry_type}'，无法找到审核配置。")
                return None

            review_settings = chat_config.WORLD_BOOK_CONFIG.get(review_config_key, {})
            duration_minutes = review_settings.get(
                "review_duration_minutes", 1
            )  # 默认为1分钟
            expires_at = datetime.utcnow() + timedelta(minutes=duration_minutes)

            data_json = json.dumps(entry_data, ensure_ascii=False)

            cursor.execute(
                """
                INSERT INTO pending_entries
                (entry_type, data_json, channel_id, guild_id, proposer_id, expires_at, message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    entry_type,
                    data_json,
                    interaction.channel_id,
                    interaction.guild_id,
                    interaction.user.id,
                    expires_at.isoformat(),
                    -1,  # 临时 message_id，将在审核服务中更新
                ),
            )

            pending_id = cursor.lastrowid
            conn.commit()
            log.info(
                f"已创建待审核条目 #{pending_id} (类型: {entry_type})，提交者: {interaction.user.id}"
            )
            return pending_id

        except sqlite3.Error as e:
            log.error(f"创建待审核条目时发生数据库错误: {e}", exc_info=True)
            conn.rollback()
            return None
        finally:
            if conn:
                conn.close()

    async def submit_general_knowledge(
        self,
        interaction: discord.Interaction,
        knowledge_data: Dict[str, Any],
        purchase_info: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """
        提交一条新的通用知识以供审核。

        Args:
            interaction: The discord interaction from the user.
            knowledge_data: A dict containing the knowledge details ('category_name', 'title', 'content_text', etc.).
            purchase_info: (Optional) A dict with purchase details if submitted via the shop.

        Returns:
            The ID of the pending entry if successful, otherwise None.
        """
        # 如果有购买信息，将其添加到主数据中以便于后续退款等操作
        if purchase_info:
            knowledge_data["purchase_info"] = purchase_info

        pending_id = await self._create_pending_entry(
            "general_knowledge", interaction, knowledge_data
        )

        if pending_id:
            # 调用 ReviewService 来启动审核流程
            assert review_service is not None
            asyncio.create_task(review_service.start_review(pending_id))
            log.info(f"通用知识提交成功，待审核ID: {pending_id}。已启动审核流程。")

        return pending_id

    async def _direct_save_community_member(
        self,
        member_data: Dict[str, Any],
        proposer_id: int,
    ) -> Optional[int]:
        """直接将社区成员档案写入 ParadeDB，跳过审核流程。"""
        parade_conn = None
        try:
            parade_conn = get_parade_db_connection()
            if not parade_conn:
                raise Exception("无法获取 Parade DB 连接。")
            parade_cursor = get_cursor(parade_conn)

            profile_user_id = member_data.get("discord_id")
            if not profile_user_id:
                raise ValueError("社区成员档案缺少 discord_id。")

            parade_cursor.execute(
                "SELECT id FROM community.member_profiles WHERE discord_id = %s",
                (str(profile_user_id),),
            )
            existing_member = parade_cursor.fetchone()

            updated_name = member_data.get("name", "").strip()
            full_text = f"""
名称: {updated_name}
Discord ID: {profile_user_id}
性格特点: {member_data.get("personality", "").strip()}
背景信息: {member_data.get("background", "").strip()}
喜好偏好: {member_data.get("preferences", "").strip()}
            """.strip()
            source_metadata = {
                "name": updated_name,
                "discord_id": str(profile_user_id),
                "personality": member_data.get("personality", "").strip(),
                "background": member_data.get("background", "").strip(),
                "preferences": member_data.get("preferences", "").strip(),
                "source": "community_submission",
                "contributor_id": str(proposer_id),
                "original_submission": member_data,
            }

            if existing_member:
                old_entry_id = existing_member["id"]
                log.info(
                    f"检测到用户 {profile_user_id} 已有档案 (ID: {old_entry_id})，将执行更新操作。"
                )
                parade_cursor.execute(
                    """
                    UPDATE community.member_profiles
                    SET title = %s, full_text = %s, source_metadata = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        updated_name,
                        full_text,
                        json.dumps(source_metadata, ensure_ascii=False),
                        old_entry_id,
                    ),
                )
                new_entry_id = old_entry_id
                asyncio.create_task(
                    incremental_rag_service.delete_entry(new_entry_id)
                )
            else:
                external_id = f"direct_{proposer_id}_{profile_user_id}"
                parade_cursor.execute(
                    """
                    INSERT INTO community.member_profiles (external_id, discord_id, title, full_text, source_metadata, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                    RETURNING id
                    """,
                    (
                        external_id,
                        str(profile_user_id),
                        updated_name,
                        full_text,
                        json.dumps(source_metadata, ensure_ascii=False),
                    ),
                )
                result = parade_cursor.fetchone()
                if not result:
                    raise Exception("插入社区成员到 ParadeDB 后未能取回新 ID。")
                new_entry_id = result["id"]

            parade_conn.commit()
            log.info(
                f"社区成员档案 '{updated_name}' (ID: {new_entry_id}) 已直接写入数据库。"
            )

            asyncio.create_task(
                incremental_rag_service.process_community_member(new_entry_id)
            )
            return new_entry_id

        except Exception as e:
            if parade_conn:
                parade_conn.rollback()
            log.error(f"直接保存社区成员档案时出错: {e}", exc_info=True)
            return None
        finally:
            if parade_conn:
                parade_conn.close()

    async def submit_community_member(
        self,
        interaction: discord.Interaction,
        member_data: Dict[str, Any],
        purchase_info: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """处理社区成员档案的提交（直接入库，无需审核）"""
        log.info(
            f"用户 {interaction.user.id} 正在提交社区成员档案: {member_data.get('name')}"
        )

        new_entry_id = await self._direct_save_community_member(
            member_data=member_data,
            proposer_id=interaction.user.id,
        )

        if new_entry_id:
            log.info(
                f"社区成员档案 '{member_data.get('name')}' 已直接收录 (ID: {new_entry_id})。"
            )

        return new_entry_id

    async def submit_personal_profile_from_purchase(
        self,
        interaction: discord.Interaction,
        profile_data: Dict[str, Any],
        purchase_info: Dict[str, Any],
    ) -> Tuple[bool, str]:
        """
        处理从商店购买的个人档案提交（直接入库，无需审核）。

        Returns:
            A tuple of (success, message).
        """
        item_id = purchase_info.get("item_id")
        price = purchase_info.get("price")

        if not item_id or not price:
            log.error(f"个人档案购买信息不完整: item_id={item_id}, price={price}")
            return False, "❌ 内部错误：商品信息不完整，无法完成购买。"

        # 1. 扣款
        success, message, new_balance, _, _, _ = await coin_service.purchase_item(
            user_id=interaction.user.id,
            guild_id=interaction.guild.id if interaction.guild else 0,
            item_id=item_id,
        )

        if not success:
            log.warning(
                f"用户 {interaction.user.id} 购买个人档案商品 (ID: {item_id}) 失败: {message}"
            )
            return False, f"购买失败：{message}"

        log.info(
            f"用户 {interaction.user.id} 成功购买个人档案商品，花费 {price}，新余额 {new_balance}。"
        )

        # 2. 直接写入数据库
        new_entry_id = await self._direct_save_community_member(
            member_data=profile_data,
            proposer_id=interaction.user.id,
        )

        if not new_entry_id:
            # 写入失败，退款
            await coin_service.add_coins(
                user_id=interaction.user.id,
                amount=price,
                reason=f"个人档案写入失败自动退款 (item_id: {item_id})",
            )
            log.error(
                f"为用户 {interaction.user.id} 直接保存个人档案失败，已自动退款 {price}。"
            )
            return False, "❌ 保存名片时发生错误，你的购买费用已自动退还。"

        log.info(
            f"用户 {interaction.user.id} 的个人名片已直接收录 (ID: {new_entry_id})。"
        )

        return True, "✅ 你的名片已成功收录！现在就已经生效啦～"

    async def submit_work_event(
        self,
        interaction: discord.Interaction,
        event_data: Dict[str, Any],
        purchase_info: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """提交一个新的自定义工作/卖屁股事件以供审核。"""
        if purchase_info:
            event_data["purchase_info"] = purchase_info

        pending_id = await self._create_pending_entry(
            "work_event", interaction, event_data
        )

        if pending_id:
            assert review_service is not None
            asyncio.create_task(review_service.start_review(pending_id))
            log.info(
                f"自定义工作事件提交成功，待审核ID: {pending_id}。已启动审核流程。"
            )

        return pending_id


# 创建 SubmissionService 的单例
submission_service = SubmissionService()
