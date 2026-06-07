# -*- coding: utf-8 -*-

"""
春节红包工具
"""

import logging
import random
from typing import Any, Dict, Tuple

import discord
from discord import ui

from src.chat.config.chat_config import SPRING_FESTIVAL_CONFIG
from src.chat.features.odysseia_coin.service.coin_service import coin_service
from src.chat.features.tools.tool_availability import is_spring_festival_in_date_window
from src.chat.features.tools.tool_metadata import tool_metadata
from src.chat.utils.database import chat_db_manager, get_beijing_today_str
from src.chat.utils.prompt_utils import replace_emojis

log = logging.getLogger(__name__)


def _get_reward_range() -> Tuple[int, int]:
    """读取并修正红包金额范围。"""
    min_reward = int(SPRING_FESTIVAL_CONFIG.get("min_reward", 500))
    max_reward = int(SPRING_FESTIVAL_CONFIG.get("max_reward", 1000))

    if min_reward <= 0:
        min_reward = 1
    if max_reward < min_reward:
        max_reward = min_reward
    return min_reward, max_reward


class RedEnvelopeView(ui.View):
    """红包领取视图"""

    def __init__(
        self,
        user_id: int,
        blessing_text: str,
        button_label: str,
        claimed_label: str,
        dm_title: str,
        daily_limit_enabled: bool,
        reward_reason: str,
    ):
        super().__init__(timeout=3600)  # 1 小时有效
        self.user_id = user_id
        self.blessing_text = blessing_text
        self.button_label_text = button_label
        self.claimed_label_text = claimed_label
        self.dm_title = dm_title
        self.daily_limit_enabled = daily_limit_enabled
        self.reward_reason = reward_reason
        self.claimed = False

    @ui.button(
        label="开启红包",
        style=discord.ButtonStyle.success,
        custom_id="spring_festival_red_envelope_claim",
    )
    async def claim_button(self, interaction: discord.Interaction, button: ui.Button):
        """用户点击领取红包"""
        try:
            # 始终同步一次按钮文案，确保与配置保持一致
            button.label = self.button_label_text

            if interaction.user.id != self.user_id:
                await interaction.response.send_message("这不是你的红包。", ephemeral=True)
                return

            if self.claimed:
                await interaction.response.send_message("这个红包已经领取过了。", ephemeral=True)
                return

            # 每日限制检查（北京时间）
            today = get_beijing_today_str()
            if self.daily_limit_enabled:
                last_date = await chat_db_manager.get_last_red_envelope_date(self.user_id)
                if last_date == today:
                    await interaction.response.send_message(
                        "你今天已经领取过红包了，明天再来吧。",
                        ephemeral=True,
                    )
                    return

            # 发放奖励
            min_reward, max_reward = _get_reward_range()
            amount = random.randint(min_reward, max_reward)
            await coin_service.add_coins(
                user_id=self.user_id,
                amount=amount,
                reason=self.reward_reason,
            )

            # 记录今日领取
            if self.daily_limit_enabled:
                await chat_db_manager.set_last_red_envelope_date(self.user_id, today)

            self.claimed = True
            button.disabled = True
            button.label = self.claimed_label_text

            if interaction.message and interaction.message.embeds:
                embed = interaction.message.embeds[0]
                embed.title = f"{self.dm_title}已开启"
                embed.color = discord.Color.gold()
                embed.description = (
                    f"你收到了 **{amount} 灵石**。\n\n"
                    f"> {self.blessing_text}"
                )
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                await interaction.response.send_message(
                    f"你收到了 **{amount} 灵石**。\n\n> {self.blessing_text}",
                    ephemeral=True,
                )

            log.info(f"用户 {self.user_id} 领取新春红包成功，获得 {amount} 灵石")

        except Exception as e:
            log.error(f"处理红包领取时出错: {e}", exc_info=True)
            if interaction.response.is_done():
                await interaction.followup.send("领取红包时发生错误，请稍后再试。", ephemeral=True)
            else:
                await interaction.response.send_message(
                    "领取红包时发生错误，请稍后再试。",
                    ephemeral=True,
                )


@tool_metadata(
    name="发送红包",
    description="发送新春红包到用户私信",
    emoji="🧧",
    category="春节活动",
)
async def spring_festival_red_envelope(
    blessing_text: str,
    **kwargs,
) -> Dict[str, Any]:
    """
    发送新春红包给当前用户（私信）。

    [调用指南]
    - 仅在用户表达新春祝福场景时调用；
    - blessing_text 必填，用于红包开启后的祝福文案；
    - 每日限制与金额范围由配置控制。
    """
    enabled = bool(SPRING_FESTIVAL_CONFIG.get("enabled", True))
    if not enabled:
        return {
            "success": False,
            "message": "新春活动当前未启用。",
            "is_daily_limit": False,
            "amount": 0,
        }

    if not is_spring_festival_in_date_window():
        return {
            "success": False,
            "message": "当前不在新春红包活动期间。",
            "is_daily_limit": False,
            "amount": 0,
        }

    user_id = kwargs.get("user_id")
    if not user_id:
        return {
            "success": False,
            "message": "无法获取当前用户 ID。",
            "is_daily_limit": False,
            "amount": 0,
        }

    try:
        target_id = int(user_id)
    except (ValueError, TypeError):
        return {
            "success": False,
            "message": f"无效的用户 ID: {user_id}",
            "is_daily_limit": False,
            "amount": 0,
        }

    daily_limit_enabled = bool(SPRING_FESTIVAL_CONFIG.get("daily_limit_enabled", True))
    if daily_limit_enabled:
        today = get_beijing_today_str()
        last_date = await chat_db_manager.get_last_red_envelope_date(target_id)
        if last_date == today:
            return {
                "success": False,
                "message": "用户今日已领取过红包，请明天再来。",
                "is_daily_limit": True,
                "amount": 0,
            }

    bot = kwargs.get("bot")
    guild = kwargs.get("guild")

    if not bot:
        return {
            "success": False,
            "message": "Bot 实例不可用，无法发送私信。",
            "is_daily_limit": False,
            "amount": 0,
        }

    # 文案配置
    dm_title = str(SPRING_FESTIVAL_CONFIG.get("dm_title", "新春红包")).strip() or "新春红包"
    dm_description = (
        str(
            SPRING_FESTIVAL_CONFIG.get(
                "dm_description", "你收到了一份新春祝福，点击按钮开启吧。"
            )
        ).strip()
        or "你收到了一份新春祝福，点击按钮开启吧。"
    )
    button_label = (
        str(SPRING_FESTIVAL_CONFIG.get("button_label", "开启红包")).strip() or "开启红包"
    )
    claimed_label = (
        str(SPRING_FESTIVAL_CONFIG.get("claimed_label", "已领取")).strip() or "已领取"
    )
    reward_reason = (
        str(SPRING_FESTIVAL_CONFIG.get("reward_reason", "新春红包奖励")).strip()
        or "新春红包奖励"
    )

    processed_blessing = replace_emojis(blessing_text)

    embed = discord.Embed(
        title=dm_title,
        description=dm_description,
        color=discord.Color.gold(),
    )
    embed.set_footer(
        text="每人每天限领一次" if daily_limit_enabled else "活动期间可领取"
    )

    view = RedEnvelopeView(
        user_id=target_id,
        blessing_text=processed_blessing,
        button_label=button_label,
        claimed_label=claimed_label,
        dm_title=dm_title,
        daily_limit_enabled=daily_limit_enabled,
        reward_reason=reward_reason,
    )

    try:
        user = None
        if guild:
            user = guild.get_member(target_id)

        if not user:
            user = bot.get_user(target_id)

        if not user:
            user = await bot.fetch_user(target_id)

        if not user:
            return {
                "success": False,
                "message": f"无法找到用户 {target_id}。",
                "is_daily_limit": False,
                "amount": 0,
            }

        # 动态同步按钮文案
        if view.children:
            first_button = view.children[0]
            if isinstance(first_button, ui.Button):
                first_button.label = button_label

        await user.send(embed=embed, view=view)
        log.info(f"已向用户 {target_id} 发送新春红包私信。")

        return {
            "success": True,
            "message": "红包私信已发送。",
            "is_daily_limit": False,
            "amount": 0,
        }

    except discord.Forbidden:
        log.warning(f"无法向用户 {target_id} 发送私信（可能关闭了私信）。")
        return {
            "success": False,
            "message": "无法向该用户发送私信（可能关闭了私信权限）。",
            "is_daily_limit": False,
            "amount": 0,
        }
    except Exception as e:
        log.error(f"发送新春红包私信失败: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"发送私信失败: {str(e)}",
            "is_daily_limit": False,
            "amount": 0,
        }
