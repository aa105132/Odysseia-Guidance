# -*- coding: utf-8 -*-

import io
import logging
from typing import Optional

import discord

from src.chat.config.chat_config import COMFYUI_CONFIG
from src.chat.features.image_generation.services.comfyui_service import comfyui_service
from src.chat.features.odysseia_coin.service.coin_service import coin_service
from src.chat.utils.prompt_utils import replace_emojis

log = logging.getLogger(__name__)

GENERATING_EMOJI = '🎨'
SUCCESS_EMOJI = '✅'
FAILED_EMOJI = '❌'


def _set_embed_author(embed: discord.Embed, message: Optional[discord.Message], request_user: Optional[discord.abc.User]) -> None:
    author_user = request_user
    if not author_user and message and hasattr(message, 'author') and message.author:
        author_user = message.author

    if not author_user:
        return

    author_name = getattr(author_user, 'display_name', None) or getattr(author_user, 'name', None)
    author_avatar = getattr(author_user, 'display_avatar', None)
    author_icon_url = getattr(author_avatar, 'url', None) if author_avatar else None

    if author_name:
        embed.set_author(name=author_name, icon_url=author_icon_url)


async def generate_image_comfyui(
    prompt: str,
    negative_prompt: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    steps: Optional[int] = None,
    cfg: Optional[float] = None,
    sampler: Optional[str] = None,
    scheduler: Optional[str] = None,
    seed: Optional[int] = None,
    lora: Optional[str] = None,
    lora_strength: Optional[float] = None,
    workflow_path: Optional[str] = None,
    preview_message: Optional[str] = None,
    success_message: Optional[str] = None,
    **kwargs,
) -> dict:
    '''
    使用 ComfyUI 工作流生成图片。

    当默认绘图引擎是 comfyui 时，优先调用此工具。
    支持常见参数：步数、分辨率、CFG、采样器、调度器、seed、LoRA。
    '''
    message: Optional[discord.Message] = kwargs.get('message')
    channel = kwargs.get('channel')
    user_id = kwargs.get('user_id')
    request_user = kwargs.get('request_user')
    current_turn_tool_names = set(
        str(name).strip()
        for name in (kwargs.get('current_turn_tool_names') or [])
        if str(name).strip()
    )

    parsed_user_id: Optional[int] = None
    if user_id is not None:
        try:
            parsed_user_id = int(str(user_id).strip())
        except (TypeError, ValueError):
            parsed_user_id = None

    async def add_reaction(emoji: str):
        if message:
            try:
                await message.add_reaction(emoji)
            except Exception:
                pass

    async def remove_reaction(emoji: str):
        if message and message.guild and message.guild.me:
            try:
                await message.remove_reaction(emoji, message.guild.me)
            except Exception:
                pass

    comfy_enabled = bool(COMFYUI_CONFIG.get('ENABLED', False))
    if not comfy_enabled:
        return {
            'generation_failed': True,
            'reason': 'comfyui_disabled',
            'hint': 'ComfyUI 功能当前已关闭。请提示用户去 Dashboard 启用后再试。',
        }

    if not comfyui_service.is_server_ready():
        return {
            'generation_failed': True,
            'reason': 'comfyui_unavailable',
            'hint': 'ComfyUI 服务不可用。请提示用户检查服务地址和开关状态。',
        }

    effective_workflow_path = str(workflow_path or '').strip()
    effective_lora = str(lora or '').strip() if lora is not None else None

    if parsed_user_id is not None:
        try:
            from src.chat.utils.database import chat_db_manager

            user_settings = await chat_db_manager.get_comfyui_user_settings(parsed_user_id)
            if not effective_workflow_path:
                effective_workflow_path = str(user_settings.get('workflow_path') or '').strip()
            if effective_lora is None:
                effective_lora = str(user_settings.get('default_lora') or '').strip()
        except Exception as error:
            log.warning(f'读取用户 ComfyUI 个性化配置失败: {error}')

    if not effective_workflow_path and comfyui_service.workflow_template is None:
        return {
            'generation_failed': True,
            'reason': 'workflow_missing',
            'hint': '未找到可用工作流。请在 Dashboard 配置默认工作流，或让用户在 /comfy 面板设置个人工作流路径。',
        }

    try:
        image_cost = max(0, int(COMFYUI_CONFIG.get('IMAGE_GENERATION_COST', 5)))
    except (TypeError, ValueError):
        image_cost = 5

    if parsed_user_id is not None and image_cost > 0:
        try:
            balance = await coin_service.get_balance(parsed_user_id)
            if balance < image_cost:
                return {
                    'generation_failed': True,
                    'reason': 'insufficient_balance',
                    'hint': f'用户余额不足（需要 {image_cost} 月光币，当前 {balance}）。请提醒用户先获取月光币。',
                }
        except Exception as error:
            log.warning(f'ComfyUI 余额检查失败: {error}')

    await add_reaction(GENERATING_EMOJI)

    suppress_preview_message = 'generate_voice' in current_turn_tool_names
    if channel and preview_message and not suppress_preview_message:
        try:
            await channel.send(replace_emojis(preview_message))
        except Exception as error:
            log.warning(f'发送 ComfyUI 预告消息失败: {error}')

    try:
        image_bytes = await comfyui_service.generate_image(
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            steps=steps,
            cfg=cfg,
            sampler=sampler,
            scheduler=scheduler,
            seed=seed,
            lora=effective_lora,
            lora_strength=lora_strength,
            workflow_path=effective_workflow_path or None,
        )

        await remove_reaction(GENERATING_EMOJI)

        if not image_bytes:
            await add_reaction(FAILED_EMOJI)
            return {
                'generation_failed': True,
                'reason': 'generation_failed',
                'hint': 'ComfyUI 生成失败。请提示用户稍后重试，或检查工作流中的占位符映射。',
            }

        new_balance = None
        if parsed_user_id is not None and image_cost > 0:
            try:
                new_balance = await coin_service.remove_coins(
                    parsed_user_id,
                    image_cost,
                    'AI ComfyUI 图片生成',
                )
            except Exception as error:
                log.warning(f'ComfyUI 扣费失败: {error}')

        await add_reaction(SUCCESS_EMOJI)

        if channel:
            try:
                from src.chat.utils.database import chat_db_manager

                embed = discord.Embed(title='AI 图片生成（ComfyUI）', color=0x2B2D31)
                _set_embed_author(embed, message, request_user)
                embed.add_field(name='提示词', value=f'```\n{prompt[:1016]}\n```', inline=False)
                if negative_prompt:
                    embed.add_field(name='负面提示词', value=f'```\n{negative_prompt[:1016]}\n```', inline=False)
                if success_message:
                    embed.add_field(name='\u200b', value=replace_emojis(success_message)[:1024], inline=False)

                footer_parts = [f'引擎: ComfyUI', f'消耗: {image_cost}']
                if new_balance is not None:
                    footer_parts.append(f'余额: {new_balance}')
                embed.set_footer(text=' | '.join(footer_parts))

                sent_message = await channel.send(
                    embed=embed,
                    file=discord.File(io.BytesIO(image_bytes), filename='generated_comfyui_image.png', spoiler=True),
                )

                if parsed_user_id is not None and sent_message:
                    await chat_db_manager.register_generated_image_message(
                        message_id=sent_message.id,
                        user_id=parsed_user_id,
                        guild_id=sent_message.guild.id if sent_message.guild else None,
                        channel_id=sent_message.channel.id,
                    )
            except Exception as error:
                log.error(f'发送 ComfyUI 图片到频道失败: {error}', exc_info=True)

        return {
            'success': True,
            'skip_ai_response': True,
            'cost': image_cost,
            'message': 'ComfyUI 图片已生成并发送，若已发送预告消息则无需再回复。',
        }
    except Exception as error:
        await remove_reaction(GENERATING_EMOJI)
        await add_reaction(FAILED_EMOJI)
        log.error(f'ComfyUI 工具执行异常: {error}', exc_info=True)
        return {
            'generation_failed': True,
            'reason': 'system_error',
            'hint': 'ComfyUI 工具执行时发生异常，请提示用户稍后重试。',
        }
