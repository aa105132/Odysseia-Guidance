# -*- coding: utf-8 -*-

import io
import json
import logging
import os
import re
from typing import Any, Dict, Optional

import discord

from src.chat.config.chat_config import COMFYUI_CONFIG
from src.chat.features.image_generation.services.comfyui_service import comfyui_service
from src.chat.features.odysseia_coin.service.coin_service import coin_service
from src.chat.features.tools.functions.image_policy_guard import (
    check_yueyue_self_nsfw_violation,
)
from src.chat.features.tools.utils.discord_image_utils import (
    extract_image_from_message_url,
    fetch_image_from_url,
)
from src.chat.utils.prompt_utils import replace_emojis

log = logging.getLogger(__name__)

GENERATING_EMOJI = '🎨'
SUCCESS_EMOJI = '✅'
FAILED_EMOJI = '❌'
NATURAL_LANGUAGE_MODEL_KEYWORDS = (
    'zimage',
    'z_image',
    'qwen',
    'zit',
    'zib',
    'moodywildmix',
    'moodypornmix',
    'lumina2',
)


def _infer_imagen_aspect_ratio(width: Optional[int], height: Optional[int]) -> str:
    if not width or not height or width <= 0 or height <= 0:
        return '3:4'

    ratio = width / height
    candidates = {
        '1:1': 1.0,
        '3:4': 3 / 4,
        '4:3': 4 / 3,
        '9:16': 9 / 16,
        '16:9': 16 / 9,
    }
    return min(candidates.keys(), key=lambda key: abs(candidates[key] - ratio))


def _infer_imagen_resolution(width: Optional[int], height: Optional[int]) -> str:
    max_side = max(width or 0, height or 0)
    if max_side >= 2048:
        return '4k'
    if max_side >= 1400:
        return '2k'
    return 'default'


def _infer_content_rating(prompt: str, negative_prompt: Optional[str] = None) -> str:
    text = f'{prompt}\n{negative_prompt or ""}'.lower()
    nsfw_keywords = [
        'nsfw',
        'nude',
        'naked',
        '性感',
        '裸',
        '乳',
        '屁股',
        '内衣',
        '泳衣',
        '比基尼',
    ]
    return 'nsfw' if any(keyword in text for keyword in nsfw_keywords) else 'sfw'


def _normalize_workflow_path_for_compare(raw_path: Optional[str]) -> str:
    normalized = str(comfyui_service._normalize_workflow_path(raw_path) or '').strip()
    if not normalized:
        return ''
    try:
        return os.path.normcase(os.path.normpath(normalized))
    except Exception:
        return normalized.lower()


def _is_default_workflow_path(workflow_path: Optional[str]) -> bool:
    current_path = _normalize_workflow_path_for_compare(workflow_path)
    if not current_path:
        return True

    default_paths = {
        _normalize_workflow_path_for_compare(COMFYUI_CONFIG.get('WORKFLOW_PATH')),
        _normalize_workflow_path_for_compare(COMFYUI_CONFIG.get('DEFAULT_REALISTIC_WORKFLOW_PATH')),
        _normalize_workflow_path_for_compare(COMFYUI_CONFIG.get('DEFAULT_ANIME_WORKFLOW_PATH')),
    }
    default_paths.discard('')
    return current_path in default_paths


class ComfyEditPromptModal(discord.ui.Modal, title='修改提示词重新生成'):
    prompt_input = discord.ui.TextInput(
        label='提示词',
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=3000,
    )
    negative_input = discord.ui.TextInput(
        label='负面提示词（可选）',
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=2000,
    )

    def __init__(self, view: 'ComfyResultView'):
        super().__init__()
        self._view = view
        self.prompt_input.default = str(view.original_params.get('prompt') or '')[:3000]
        self.negative_input.default = str(view.original_params.get('negative_prompt') or '')[:2000]

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        new_prompt = str(self.prompt_input.value or '').strip()
        new_negative = str(self.negative_input.value or '').strip() or None
        if not new_prompt:
            await interaction.followup.send('提示词不能为空。', ephemeral=True)
            return
        await self._view.regenerate_comfy(
            interaction,
            prompt=new_prompt,
            negative_prompt=new_negative,
            preview_message='正在根据新提示词重新生成（ComfyUI）...',
            success_message='已按新提示词重新生成。',
        )


class ComfyAIRewriteModal(discord.ui.Modal, title='AI 重写提示词'):
    description_input = discord.ui.TextInput(
        label='描述你想要的变化',
        style=discord.TextStyle.paragraph,
        placeholder='例如：改成夜景、增加逆光、人物更靠近镜头；留空则自动优化当前提示词',
        required=False,
        max_length=1000,
    )

    def __init__(self, view: 'ComfyResultView'):
        super().__init__()
        self._view = view

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        description = str(self.description_input.value or '').strip()
        await self._view.ai_rewrite_and_regenerate(interaction, description)


class ComfyResultView(discord.ui.View):
    def __init__(
        self,
        original_params: Dict[str, Any],
        user_id: Optional[int],
        timeout: float = 600,
    ):
        super().__init__(timeout=timeout)
        self.original_params = original_params
        self.user_id = user_id
        self.message: Optional[discord.Message] = None

    def _is_allowed(self, interaction: discord.Interaction) -> bool:
        if self.user_id is None:
            return True
        if int(interaction.user.id) == int(self.user_id):
            return True
        return bool(interaction.user.guild_permissions.administrator)

    async def _check_permission(self, interaction: discord.Interaction) -> bool:
        if self._is_allowed(interaction):
            return True
        await interaction.response.send_message('只有原始请求者（或管理员）才能操作。', ephemeral=True)
        return False

    async def regenerate_comfy(
        self,
        interaction: discord.Interaction,
        prompt: str,
        negative_prompt: Optional[str],
        preview_message: str,
        success_message: str,
    ) -> None:
        params = dict(self.original_params)
        params['prompt'] = prompt
        params['negative_prompt'] = negative_prompt
        params['preview_message'] = preview_message
        params['success_message'] = success_message
        params['channel'] = interaction.channel
        params['user_id'] = str(interaction.user.id)
        params['request_user'] = interaction.user
        params['message'] = None
        params['current_turn_tool_names'] = []

        result = await generate_image_comfyui(**params)
        if isinstance(result, dict) and result.get('generation_failed'):
            await interaction.followup.send(str(result.get('hint') or '重新生成失败。'), ephemeral=True)

    async def switch_to_novelai(
        self,
        interaction: discord.Interaction,
        prompt: str,
        negative_prompt: Optional[str],
    ) -> None:
        from src.chat.features.tools.functions.generate_image_novelai import generate_image_novelai

        result = await generate_image_novelai(
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=self.original_params.get('width'),
            height=self.original_params.get('height'),
            steps=self.original_params.get('steps'),
            scale=self.original_params.get('cfg'),
            sampler=None,
            preview_message='正在切换到 NovelAI 重新生成...',
            success_message='已切换到 NovelAI 重新生成。',
            channel=interaction.channel,
            user_id=str(interaction.user.id),
            request_user=interaction.user,
            message=None,
            current_turn_tool_names=[],
        )
        if isinstance(result, dict) and result.get('generation_failed'):
            await interaction.followup.send(str(result.get('hint') or '切换到 NovelAI 失败。'), ephemeral=True)

    async def switch_to_imagen(
        self,
        interaction: discord.Interaction,
        prompt: str,
        negative_prompt: Optional[str],
    ) -> None:
        from src.chat.features.tools.functions.generate_image import generate_image

        width = self.original_params.get('width')
        height = self.original_params.get('height')

        result = await generate_image(
            prompt=prompt,
            negative_prompt=negative_prompt,
            aspect_ratio=_infer_imagen_aspect_ratio(width, height),
            number_of_images=1,
            resolution=_infer_imagen_resolution(width, height),
            content_rating=_infer_content_rating(prompt, negative_prompt),
            preview_message='正在切换到 Imagen 重新生成...',
            success_message='已切换到 Imagen 重新生成。',
            channel=interaction.channel,
            user_id=str(interaction.user.id),
            request_user=interaction.user,
            message=None,
            current_turn_tool_names=[],
        )
        if isinstance(result, dict) and result.get('generation_failed'):
            await interaction.followup.send(str(result.get('hint') or '切换到 Imagen 失败。'), ephemeral=True)

    async def ai_rewrite_and_regenerate(self, interaction: discord.Interaction, description: str) -> None:
        from src.chat.services.gemini_service import gemini_service

        base_prompt = str(self.original_params.get('prompt') or '').strip()
        if not base_prompt:
            await interaction.followup.send('当前提示词为空，无法 AI 重写。', ephemeral=True)
            return

        change_text = description or '在不改变用户核心意图前提下优化细节、构图与光影。'
        model_name = str(self.original_params.get('model_name') or '').strip()

        if _is_natural_language_model(model_name):
            rewrite_instruction = (
                '你是 Visual Logic Compiler v10 的中文提示词编译器。请将当前提示词按用户要求改写为中文自然语言提示词。\n'
                '要求：\n'
                '1) 只输出最终正面提示词正文，不要解释，不要 Markdown，不要 SD tag 串。\n'
                '2) 严格按以下九段结构组织内容：风格、画面关系、外貌、发型、服装、姿势、神情、光影、背景。\n'
                '3) 句法使用主谓宾，禁止成语，禁止被动语态（被/遭到/受到），避免空泛形容词。\n'
                '4) 人物面色必须写“面色自然”或“肤色正常”，禁止“脸红/微醺”等异常面色。\n'
                '5) 禁止“清秀/英俊/丰满”等抽象外貌词，改为可见几何或物理特征。\n'
                '6) 动作必须符合人体工学，单手单物，避免不可能姿态。\n'
                '7) 场景需包含前景/中景/背景层次，明确主光方向、辅光、色温、阴影和轮廓光。\n'
                '8) 保留用户核心意图，不得私自改主体身份与关键设定。\n'
                '9) 若涉及私密暴露内容，在 prompt 开头添加 nsfw 前缀。\n'
                f'当前提示词：{base_prompt}\n'
                f'用户要求：{change_text}'
            )
        else:
            rewrite_instruction = (
                '你是 Danbooru 提示词优化助手。请将当前提示词按用户要求改写为英文逗号分隔标签。\n'
                '要求：\n'
                '1) 只输出最终标签串，不要解释。\n'
                '2) 保留用户核心意图与主体设定。\n'
                '3) 标签顺序建议：质量词 -> 主体 -> 外观 -> 服装 -> 动作 -> 场景 -> 光影。\n'
                f'当前提示词：{base_prompt}\n'
                f'用户要求：{change_text}'
            )

        rewritten_prompt = await gemini_service.generate_simple_response(
            prompt=' ',
            generation_config={
                'temperature': 0.45,
                'max_output_tokens': 1800,
            },
            messages=[
                {
                    'role': 'user',
                    'content': rewrite_instruction,
                }
            ],
        )

        new_prompt = str(rewritten_prompt or '').strip().strip('"').strip("'")
        if not new_prompt:
            await interaction.followup.send('AI 重写失败，请稍后再试。', ephemeral=True)
            return

        await self.regenerate_comfy(
            interaction,
            prompt=new_prompt,
            negative_prompt=str(self.original_params.get('negative_prompt') or '').strip() or None,
            preview_message='正在使用 AI 重写提示词重新生成（ComfyUI）...',
            success_message='已根据 AI 重写提示词重新生成。',
        )

    @discord.ui.button(label='重新生成', style=discord.ButtonStyle.primary, emoji='🔄', row=0)
    async def btn_regenerate(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_permission(interaction):
            return
        await interaction.response.defer(thinking=True)
        await self.regenerate_comfy(
            interaction,
            prompt=str(self.original_params.get('prompt') or '').strip(),
            negative_prompt=str(self.original_params.get('negative_prompt') or '').strip() or None,
            preview_message='正在重新生成（ComfyUI）...',
            success_message='已重新生成。',
        )

    @discord.ui.button(label='修改提示词', style=discord.ButtonStyle.secondary, emoji='✏️', row=0)
    async def btn_edit_prompt(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_permission(interaction):
            return
        await interaction.response.send_modal(ComfyEditPromptModal(self))

    @discord.ui.button(label='切换到 NovelAI', style=discord.ButtonStyle.success, row=0)
    async def btn_switch_novelai(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_permission(interaction):
            return
        await interaction.response.defer(thinking=True)
        await self.switch_to_novelai(
            interaction,
            prompt=str(self.original_params.get('prompt') or '').strip(),
            negative_prompt=str(self.original_params.get('negative_prompt') or '').strip() or None,
        )

    @discord.ui.button(label='切换到 Imagen', style=discord.ButtonStyle.success, row=0)
    async def btn_switch_imagen(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_permission(interaction):
            return
        await interaction.response.defer(thinking=True)
        await self.switch_to_imagen(
            interaction,
            prompt=str(self.original_params.get('prompt') or '').strip(),
            negative_prompt=str(self.original_params.get('negative_prompt') or '').strip() or None,
        )

    @discord.ui.button(label='AI 重写', style=discord.ButtonStyle.secondary, emoji='🤖', row=0)
    async def btn_ai_rewrite(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_permission(interaction):
            return
        await interaction.response.send_modal(ComfyAIRewriteModal(self))

    async def on_timeout(self):
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True
        try:
            if self.message:
                await self.message.edit(view=self)
        except Exception:
            pass


def _is_natural_language_model(model_name: Optional[str]) -> bool:
    model_text = str(model_name or '').strip().lower()
    if not model_text:
        return False
    return any(keyword in model_text for keyword in NATURAL_LANGUAGE_MODEL_KEYWORDS)


def _is_placeholder_token_text(value_text: str) -> bool:
    text = str(value_text or '').strip()
    if not text:
        return False
    if re.fullmatch(r'%[^%]+%', text):
        return True
    if re.fullmatch(r'\{\{[^{}]+\}\}', text):
        return True
    return False


def _extract_model_names_from_workflow_template(workflow_template: Optional[Dict[str, Any]]) -> list[str]:
    if not isinstance(workflow_template, dict):
        return []

    model_field_names = {'unet_name', 'ckpt_name', 'model_name'}
    candidates: list[str] = []
    seen = set()

    for node_data in workflow_template.values():
        if not isinstance(node_data, dict):
            continue
        inputs = node_data.get('inputs')
        if not isinstance(inputs, dict):
            continue
        for field_name in model_field_names:
            value = inputs.get(field_name)
            value_text = str(value or '').strip()
            if not value_text or _is_placeholder_token_text(value_text):
                continue
            key = value_text.lower()
            if key in seen:
                continue
            seen.add(key)
            candidates.append(value_text)

    return candidates


def _extract_natural_model_hints_from_workflow(
    workflow_template: Optional[Dict[str, Any]],
) -> list[str]:
    if not isinstance(workflow_template, dict):
        return []

    try:
        workflow_text = json.dumps(workflow_template, ensure_ascii=False).lower()
    except Exception:
        return []

    hints: list[str] = []
    for keyword in NATURAL_LANGUAGE_MODEL_KEYWORDS:
        if keyword in workflow_text:
            hints.append(keyword)
    return hints


def _resolve_model_name_for_style_check(
    effective_model_name: Optional[str],
    effective_workflow_path: Optional[str],
    prompt: Optional[str],
) -> str:
    explicit_model_name = str(effective_model_name or '').strip()
    if explicit_model_name:
        return explicit_model_name

    default_model_name = str(
        comfyui_service.resolve_default_model_name(
            prompt=prompt,
            positive_prompt=prompt,
        )
        or ''
    ).strip()

    workflow_template_for_check: Optional[Dict[str, Any]] = None
    normalized_workflow_path = str(effective_workflow_path or '').strip()

    if normalized_workflow_path:
        workflow_template_for_check = comfyui_service._load_workflow_template_from_path(
            normalized_workflow_path
        )
    else:
        style_workflow_path = str(
            comfyui_service.resolve_default_workflow_path(
                prompt=prompt,
                positive_prompt=prompt,
            )
            or ''
        ).strip()
        if style_workflow_path:
            workflow_template_for_check = comfyui_service._load_workflow_template_from_path(
                style_workflow_path
            )
        elif isinstance(comfyui_service.workflow_template, dict):
            workflow_template_for_check = comfyui_service.workflow_template

    model_candidates = _extract_model_names_from_workflow_template(workflow_template_for_check)
    model_candidates.extend(_extract_natural_model_hints_from_workflow(workflow_template_for_check))
    if not model_candidates and default_model_name:
        return default_model_name

    if default_model_name:
        model_candidates.append(default_model_name)

    return ' '.join(model_candidates).strip()


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


def _count_sentence_segments(text: str) -> int:
    segments = [
        segment.strip()
        for segment in re.split(r'[。！？!?；;\n]+', str(text or '').strip())
        if segment.strip()
    ]
    return len(segments)


def _is_brief_natural_language_prompt(prompt_text: Optional[str]) -> bool:
    text = str(prompt_text or '').strip()
    if not text:
        return True

    text_length = len(text)
    sentence_count = _count_sentence_segments(text)
    detail_keyword_hits = sum(
        1
        for keyword in (
            '前景',
            '中景',
            '背景',
            '光影',
            '色温',
            '镜头',
            '构图',
            '材质',
            '纹理',
            '动作',
            '姿态',
            '服装',
            '发型',
            '五官',
            '景深',
        )
        if keyword in text
    )

    if text_length < 220:
        return True
    if sentence_count < 6:
        return True
    if detail_keyword_hits < 4:
        return True
    return False


def _expand_brief_natural_prompt(prompt_text: Optional[str]) -> str:
    base_text = str(prompt_text or '').strip().strip('。')
    if not base_text:
        return ''

    return (
        f'风格：写实摄影风格。画面采用数字摄影，画面强调高清晰度与细腻纹理，整体色调呈现自然统一的视觉倾向。{base_text}。'
        '一张基于现代场景的画面，包含一到两位人物。主体位于画面视觉中心，人物关系明确，画面呈现稳定且连贯的叙事氛围。'
        '外貌：主体是东亚人种，眼部、鼻部、唇部等五官采用可见物理特征描述，面部保持肤色正常或面色自然，皮肤保留真实质感。'
        '发型：主体发色和发型明确，发丝走向与层次清晰，刘海与发尾状态符合重力与动作逻辑，避免不合理漂浮。'
        '服装：主体服装与场景时代一致，上装与下装材质、颜色、纹理清楚可辨，配饰或道具结构完整并具有可见细节。'
        '姿势：主体姿态符合人体工学，身体角度、双手动作和重心关系明确，单手单物，避免关节反折、肢体穿模和物理冲突。'
        '神情：主体表情自然舒展，视线方向明确，情绪表达克制且清晰，不使用夸张或冲突的面部描述。'
        '光影：主光源方向、辅光补偿、色温和阴影软硬过渡明确，轮廓光与反射光用于增强体积感与层次感。'
        '背景：背景包含前景、中景、背景三层空间，环境物体与氛围元素服务主体叙事，画面景深和虚化关系自然，避免背景重复和透视错误。'
    )


def _default_natural_negative_prompt() -> str:
    return (
        '请排除低清晰度、噪点、压缩伪影、过曝死白、欠曝死黑、涂抹糊化与过度锐化；'
        '请排除解剖结构错误、手指数量异常、关节反折、肢体穿模、单手多物和不合理受力；'
        '请排除构图失衡、主体截断、视线引导混乱、背景重复、透视错误与空间层级混乱；'
        '请排除皮肤塑料感、纹理拉伸、材质错误、衣物结构错误和光影方向冲突；'
        '请排除成语化描述导致的抽象画面、夸张表情和不符合场景时代的道具元素。'
    )


def _normalize_natural_negative_prompt(negative_prompt: Optional[str]) -> str:
    text = str(negative_prompt or '').strip()
    default_text = _default_natural_negative_prompt()
    if not text:
        return default_text
    if _looks_like_sd_tag_prompt(text):
        return default_text
    if len(text) < 80:
        return f'{text}；{default_text}'
    return text


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


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {'1', 'true', 'yes', 'y', 'on'}:
        return True
    if text in {'0', 'false', 'no', 'n', 'off'}:
        return False
    return default


def _is_image_attachment(attachment: discord.Attachment) -> bool:
    content_type = str(getattr(attachment, 'content_type', '') or '').lower()
    filename = str(getattr(attachment, 'filename', '') or '').lower()
    if content_type.startswith('image/'):
        return True
    return filename.endswith(('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.avif'))


async def _extract_image_from_message(message: Optional[discord.Message]) -> Optional[Dict[str, Any]]:
    if not message:
        return None

    for attachment in (message.attachments or []):
        if not _is_image_attachment(attachment):
            continue
        try:
            image_bytes = await attachment.read()
        except Exception:
            continue
        if not image_bytes:
            continue

        content_type = str(getattr(attachment, 'content_type', '') or '').strip() or 'image/png'
        filename = str(getattr(attachment, 'filename', '') or '').strip() or 'reference_image.png'
        return {
            'data': image_bytes,
            'mime_type': content_type,
            'filename': filename,
        }

    try:
        url_image = await extract_image_from_message_url(message)
        if url_image:
            return url_image
    except Exception:
        pass

    return None


async def _resolve_reference_image(
    message: Optional[discord.Message],
    channel: Optional[discord.abc.Messageable],
    reference_image_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    normalized_url = str(reference_image_url or '').strip()
    if normalized_url:
        try:
            image_from_url = await fetch_image_from_url(normalized_url)
            if image_from_url:
                return image_from_url
        except Exception:
            pass

    direct_image = await _extract_image_from_message(message)
    if direct_image:
        return direct_image

    if message and message.reference and message.reference.message_id and message.channel:
        try:
            referenced_message = await message.channel.fetch_message(message.reference.message_id)
            referenced_image = await _extract_image_from_message(referenced_message)
            if referenced_image:
                return referenced_image
        except Exception:
            pass

    if channel and hasattr(channel, 'history'):
        try:
            async for history_message in channel.history(limit=6):
                if message and history_message.id == message.id:
                    continue
                history_image = await _extract_image_from_message(history_message)
                if history_image:
                    return history_image
        except Exception:
            pass

    return None


def _normalize_generation_mode(raw_mode: Optional[str]) -> str:
    mode = str(raw_mode or '').strip().lower()
    if mode in {'image_to_video', 'image2video', 'img2video', 'i2v', 'video'}:
        return 'image_to_video'
    return 'text_to_image'


def _guess_filename_by_mime(mime_type: str, fallback_name: str = 'generated_comfyui_output') -> str:
    mime_text = str(mime_type or '').strip().lower()
    if mime_text == 'video/mp4':
        return f'{fallback_name}.mp4'
    if mime_text == 'video/webm':
        return f'{fallback_name}.webm'
    if mime_text == 'image/gif':
        return f'{fallback_name}.gif'
    if mime_text == 'image/jpeg':
        return f'{fallback_name}.jpg'
    if mime_text == 'image/webp':
        return f'{fallback_name}.webp'
    if mime_text == 'image/avif':
        return f'{fallback_name}.avif'
    return f'{fallback_name}.png'


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
    generation_mode: Optional[str] = None,
    use_reference_image: Optional[bool] = None,
    reference_image_url: Optional[str] = None,
    workflow_path: Optional[str] = None,
    preview_message: Optional[str] = None,
    success_message: Optional[str] = None,
    **kwargs,
) -> dict:
    '''
    使用 ComfyUI 工作流生成图片。

    当默认绘图引擎是 comfyui 时，优先调用此工具。
    支持常见参数：步数、分辨率、CFG、采样器、调度器、seed、LoRA、底模；
    支持图生视频：可传 generation_mode=image_to_video，并提供参考图（URL 或对话附件/回复）。
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

    reserved_context_keys = {
        'bot',
        'channel',
        'guild',
        'guild_id',
        'thread_id',
        'message',
        'request_user',
        'user_id',
        'author_id',
        'current_turn_tool_names',
        'log_detailed',
    }
    passthrough_runtime_kwargs = {}
    for key, value in kwargs.items():
        key_text = str(key or '').strip()
        if not key_text or key_text in reserved_context_keys:
            continue
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool, list, dict)):
            passthrough_runtime_kwargs[key_text] = value

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
    effective_generation_mode = _normalize_generation_mode(
        generation_mode or kwargs.get('generation_mode')
    )
    effective_use_reference_image = (
        _parse_bool(use_reference_image, default=False)
        if use_reference_image is not None
        else _parse_bool(kwargs.get('use_reference_image'), default=(effective_generation_mode == 'image_to_video'))
    )
    effective_reference_image_url = str(reference_image_url or kwargs.get('reference_image_url') or '').strip()
    passthrough_runtime_kwargs.setdefault('generation_mode', effective_generation_mode)

    if parsed_user_id is not None:
        try:
            from src.chat.utils.database import chat_db_manager

            user_settings = await chat_db_manager.get_comfyui_user_settings(parsed_user_id)
            user_workflow_path = str(user_settings.get('workflow_path') or '').strip()
            if not effective_workflow_path:
                effective_workflow_path = user_workflow_path

            should_apply_user_persisted_settings = not _is_default_workflow_path(
                effective_workflow_path
            )
            if should_apply_user_persisted_settings:
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
                effective_user_fixed_positive_prompt = str(
                    user_settings.get('fixed_positive_prompt') or ''
                ).strip()
                effective_user_fixed_negative_prompt = str(
                    user_settings.get('fixed_negative_prompt') or ''
                ).strip()
        except Exception as error:
            log.warning(f'读取用户 ComfyUI 个性化配置失败: {error}')

    if not effective_workflow_path and comfyui_service.workflow_template is None:
        return {
            'generation_failed': True,
            'reason': 'workflow_missing',
            'hint': '未找到可用工作流。请在 Dashboard 配置默认工作流，或让用户在 /comfy 面板设置个人工作流路径。',
        }

    model_name_for_style_check = _resolve_model_name_for_style_check(
        effective_model_name=effective_model_name,
        effective_workflow_path=effective_workflow_path,
        prompt=prompt,
    )

    if _is_natural_language_model(model_name_for_style_check) and _looks_like_sd_tag_prompt(prompt):
        return {
            'generation_failed': True,
            'reason': 'prompt_style_mismatch',
            'hint': (
                '当前底模属于真人自然语言模型（zimage/qwen/zit/zib），'
                '但本次 prompt 看起来是 SD tag 风格。'
                '请改为高细节中文自然语言描述（包含风格、主体细节、动作关系、前景中景背景、光影参数）后再调用 generate_image_comfyui。'
            ),
        }

    effective_prompt = str(prompt or '').strip()
    effective_negative_prompt = str(negative_prompt or '').strip() or None
    if _is_natural_language_model(model_name_for_style_check):
        if _is_brief_natural_language_prompt(effective_prompt):
            effective_prompt = _expand_brief_natural_prompt(effective_prompt)
        effective_negative_prompt = _normalize_natural_negative_prompt(effective_negative_prompt)

    policy_block = check_yueyue_self_nsfw_violation(
        prompt=effective_prompt,
        negative_prompt=effective_negative_prompt,
        message=message,
    )
    if policy_block:
        return policy_block

    uploaded_reference_name = ''
    if effective_use_reference_image or effective_generation_mode == 'image_to_video':
        reference_image = await _resolve_reference_image(
            message=message,
            channel=channel,
            reference_image_url=effective_reference_image_url,
        )
        if not reference_image:
            return {
                'generation_failed': True,
                'reason': 'missing_reference_image',
                'hint': '图生视频需要参考图。请让用户上传图片、回复图片，或传 reference_image_url 后重试。',
            }

        uploaded_reference_name = str(
            await comfyui_service.upload_input_image(
                image_bytes=reference_image.get('data') or b'',
                filename=str(reference_image.get('filename') or 'reference_image.png'),
            )
            or ''
        ).strip()
        if not uploaded_reference_name:
            return {
                'generation_failed': True,
                'reason': 'upload_reference_failed',
                'hint': '参考图上传到 ComfyUI 失败，请检查 ComfyUI /upload/image 接口后重试。',
            }

        for alias_key in ('input_image', 'reference_image', 'init_image', 'image'):
            if alias_key not in passthrough_runtime_kwargs:
                passthrough_runtime_kwargs[alias_key] = uploaded_reference_name

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
                    'hint': f'用户余额不足（需要 {image_cost} 灵石，当前 {balance}）。请提醒用户先获取灵石。',
                }
        except Exception as error:
            log.warning(f'ComfyUI 余额检查失败: {error}')

    await add_reaction(GENERATING_EMOJI)

    suppress_preview_message = 'generate_voice' in current_turn_tool_names
    if channel and preview_message and not suppress_preview_message:
        try:
            processed_preview = replace_emojis(preview_message)
            if message:
                await message.reply(processed_preview, mention_author=False)
            else:
                await channel.send(processed_preview)
        except Exception as error:
            log.warning(f'发送 ComfyUI 预告消息失败: {error}')

    try:
        media_result = await comfyui_service.generate_media(
            prompt=effective_prompt,
            negative_prompt=effective_negative_prompt,
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
            **passthrough_runtime_kwargs,
        )

        await remove_reaction(GENERATING_EMOJI)

        if not media_result:
            await add_reaction(FAILED_EMOJI)
            return {
                'generation_failed': True,
                'reason': 'generation_failed',
                'hint': 'ComfyUI 生成失败。请提示用户稍后重试，或检查工作流占位符/节点映射。',
            }

        media_bytes = media_result.get('bytes')
        if not isinstance(media_bytes, bytes) or not media_bytes:
            await add_reaction(FAILED_EMOJI)
            return {
                'generation_failed': True,
                'reason': 'empty_media_result',
                'hint': 'ComfyUI 返回了空媒体结果，请检查输出节点（images/videos/gifs）配置。',
            }

        media_kind = str(media_result.get('media_kind') or 'image').strip().lower()
        mime_type = str(media_result.get('mime_type') or '').strip().lower()
        generated_filename = str(media_result.get('filename') or '').strip()
        if generated_filename:
            generated_filename = generated_filename.split('/')[-1].split('\\')[-1]
        if not generated_filename:
            generated_filename = _guess_filename_by_mime(mime_type, fallback_name='generated_comfyui_output')

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

                embed_title = 'AI 视频生成（ComfyUI）' if media_kind == 'video' else 'AI 图片生成（ComfyUI）'
                embed = discord.Embed(title=embed_title, color=0x2B2D31)
                _set_embed_author(embed, message, request_user)
                embed.add_field(name='提示词', value=f'```\n{effective_prompt[:1016]}\n```', inline=False)
                if effective_negative_prompt:
                    embed.add_field(name='负面提示词', value=f'```\n{effective_negative_prompt[:1016]}\n```', inline=False)
                if success_message:
                    embed.add_field(name='\u200b', value=replace_emojis(success_message)[:1024], inline=False)

                footer_parts = [f'引擎: ComfyUI', f'消耗: {image_cost}']
                model_text = str(effective_model_name or '').strip()
                if model_text:
                    footer_parts.append(f'底模: {model_text}')
                if effective_width and effective_height:
                    footer_parts.append(f'分辨率: {effective_width}x{effective_height}')
                if effective_steps is not None:
                    footer_parts.append(f'steps: {effective_steps}')
                if effective_cfg is not None:
                    footer_parts.append(f'cfg: {effective_cfg}')
                if effective_sampler:
                    footer_parts.append(f'sampler: {effective_sampler}')
                if effective_scheduler:
                    footer_parts.append(f'scheduler: {effective_scheduler}')
                if effective_seed is not None:
                    footer_parts.append(f'seed: {effective_seed}')
                if effective_vae_name:
                    footer_parts.append(f'vae: {effective_vae_name}')
                if effective_clip_name:
                    footer_parts.append(f'clip: {effective_clip_name}')
                if effective_generation_mode == 'image_to_video':
                    footer_parts.append('模式: 图生视频')
                if uploaded_reference_name:
                    footer_parts.append(f'参考图: {uploaded_reference_name}')
                if mime_type:
                    footer_parts.append(f'mime: {mime_type}')
                if new_balance is not None:
                    footer_parts.append(f'余额: {new_balance}')
                embed.set_footer(text=' | '.join(footer_parts))

                result_view: Optional[ComfyResultView] = None
                if parsed_user_id is not None:
                    result_view = ComfyResultView(
                        original_params={
                            'prompt': effective_prompt,
                            'negative_prompt': effective_negative_prompt,
                            'width': effective_width,
                            'height': effective_height,
                            'steps': effective_steps,
                            'cfg': effective_cfg,
                            'sampler': effective_sampler,
                            'scheduler': effective_scheduler,
                            'seed': effective_seed,
                            'lora': effective_lora,
                            'lora_strength': lora_strength,
                            'model_name': effective_model_name,
                            'vae_name': effective_vae_name,
                            'clip_name': effective_clip_name,
                            'generation_mode': effective_generation_mode,
                            'use_reference_image': effective_use_reference_image,
                            'reference_image_url': effective_reference_image_url or None,
                            'workflow_path': effective_workflow_path or None,
                            'success_message': success_message,
                            **passthrough_runtime_kwargs,
                        },
                        user_id=parsed_user_id,
                    )

                send_kwargs = {
                    'embed': embed,
                    'file': discord.File(io.BytesIO(media_bytes), filename=generated_filename, spoiler=(media_kind != 'video')),
                    'view': result_view,
                }
                if message:
                    sent_message = await message.reply(**send_kwargs, mention_author=False)
                else:
                    sent_message = await channel.send(**send_kwargs)
                if result_view is not None:
                    result_view.message = sent_message

                if parsed_user_id is not None and sent_message and media_kind == 'image':
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
            'message': 'ComfyUI 媒体已生成并发送，若已发送预告消息则无需再回复。',
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
