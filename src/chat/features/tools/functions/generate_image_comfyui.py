# -*- coding: utf-8 -*-

import io
import logging
import re
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


def _is_natural_language_model(model_name: Optional[str]) -> bool:
    model_text = str(model_name or '').strip().lower()
    if not model_text:
        return False
    return any(keyword in model_text for keyword in ('zimage', 'z_image', 'qwen'))


def _looks_like_sd_tag_prompt(prompt_text: Optional[str]) -> bool:
    text = str(prompt_text or '').strip()
    if not text:
        return False

    if '<lora:' in text.lower() or '<wlr:' in text.lower():
        return True

    comma_count = text.count(',') + text.count('，')
    if comma_count < 3:
        return False

    tokens = [segment.strip() for segment in re.split(r'[,，]+', text) if segment.strip()]
    if len(tokens) < 4:
        return False

    chinese_char_count = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')
    english_char_count = sum(1 for ch in text if ('a' <= ch.lower() <= 'z'))

    if english_char_count <= 0:
        return False

    return english_char_count > max(chinese_char_count * 2, 20)


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
    model_name: Optional[str] = None,
    vae_name: Optional[str] = None,
    clip_name: Optional[str] = None,
    workflow_path: Optional[str] = None,
    preview_message: Optional[str] = None,
    success_message: Optional[str] = None,
    **kwargs,
) -> dict:
    '''
    使用 ComfyUI 工作流生成图片。

    当默认绘图引擎是 comfyui 时，优先调用此工具。
    支持常见参数：步数、分辨率、CFG、采样器、调度器、seed、LoRA、底模。
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

    def _to_int(value: object) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    def _to_float(value: object) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return None

    effective_workflow_path = str(workflow_path or '').strip()
    effective_lora = str(lora or '').strip() if lora is not None else None
    effective_width = width
    effective_height = height
    effective_steps = steps
    effective_cfg = cfg
    effective_sampler = str(sampler or '').strip() if sampler is not None else None
    effective_scheduler = str(scheduler or '').strip() if scheduler is not None else None
    effective_seed = seed
    effective_model_name = str(model_name or '').strip() if model_name is not None else None
    effective_vae_name = str(vae_name or '').strip() if vae_name is not None else None
    effective_clip_name = str(clip_name or '').strip() if clip_name is not None else None
    effective_user_fixed_positive_prompt = ''
    effective_user_fixed_negative_prompt = ''

    if parsed_user_id is not None:
        try:
            from src.chat.utils.database import chat_db_manager

            user_settings = await chat_db_manager.get_comfyui_user_settings(parsed_user_id)
            if not effective_workflow_path:
                effective_workflow_path = str(user_settings.get('workflow_path') or '').strip()
            if effective_lora is None:
                effective_lora = str(user_settings.get('default_lora') or '').strip()
            if effective_width is None:
                effective_width = _to_int(user_settings.get('width'))
            if effective_height is None:
                effective_height = _to_int(user_settings.get('height'))
            if effective_steps is None:
                effective_steps = _to_int(user_settings.get('steps'))
            if effective_cfg is None:
                effective_cfg = _to_float(user_settings.get('cfg'))
            if effective_sampler is None:
                effective_sampler = str(user_settings.get('sampler') or '').strip() or None
            if effective_scheduler is None:
                effective_scheduler = str(user_settings.get('scheduler') or '').strip() or None
            if effective_seed is None:
                effective_seed = _to_int(user_settings.get('seed'))
            if effective_model_name is None:
                effective_model_name = str(user_settings.get('model_name') or '').strip() or None
            if effective_vae_name is None:
                effective_vae_name = str(user_settings.get('vae_name') or '').strip() or None
            if effective_clip_name is None:
                effective_clip_name = str(user_settings.get('clip_name') or '').strip() or None
            effective_user_fixed_positive_prompt = str(user_settings.get('fixed_positive_prompt') or '').strip()
            effective_user_fixed_negative_prompt = str(user_settings.get('fixed_negative_prompt') or '').strip()
        except Exception as error:
            log.warning(f'读取用户 ComfyUI 个性化配置失败: {error}')

    if not effective_workflow_path and comfyui_service.workflow_template is None:
        return {
            'generation_failed': True,
            'reason': 'workflow_missing',
            'hint': '未找到可用工作流。请在 Dashboard 配置默认工作流，或让用户在 /comfy 面板设置个人工作流路径。',
        }

    if _is_natural_language_model(effective_model_name) and _looks_like_sd_tag_prompt(prompt):
        return {
            'generation_failed': True,
            'reason': 'prompt_style_mismatch',
            'hint': (
                '当前底模属于真人自然语言模型（zimage/qwen），'
                '但本次 prompt 看起来是 SD tag 风格。'
                '请改为中文自然语言描述（完整句子）后再调用 generate_image_comfyui。'
            ),
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
            width=effective_width,
            height=effective_height,
            steps=effective_steps,
            cfg=effective_cfg,
            sampler=effective_sampler,
            scheduler=effective_scheduler,
            seed=effective_seed,
            lora=effective_lora,
            lora_strength=lora_strength,
            model_name=effective_model_name,
            vae_name=effective_vae_name,
            clip_name=effective_clip_name,
            workflow_path=effective_workflow_path or None,
            user_fixed_positive_prompt=effective_user_fixed_positive_prompt,
            user_fixed_negative_prompt=effective_user_fixed_negative_prompt,
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
                model_text = str(effective_model_name or '').strip()
                if model_text:
                    footer_parts.append(f'底模: {model_text}')
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
