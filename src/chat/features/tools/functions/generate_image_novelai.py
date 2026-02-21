# -*- coding: utf-8 -*-

"""
NovelAI 图片生成工具
让 LLM 可以在对话中自动调用 NovelAI 生成图片。
对话场景采用“双串策略”：主 AI 先提供一版 Danbooru 草稿串，工具内提示词 AI 再生成一版优化串。
若提示词 AI 未返回合格 Danbooru，则回退使用主 AI 草稿串继续生图。

遵循 NAI 预设规则:
- Tag 必须是 Danbooru 格式的英文单词/词语，逗号分隔
- 单图 Tag 数量 ≤ 90 个（建议 75~90）
- 使用权重语法: n::Tag:: (n>1 增强, n<1 减弱)
- 支持角色 DNA 系统确保角色一致性
- 同人角色强制使用 `character_name (work_name)` 英文身份标签（如 `raiden shogun (genshin impact)`）
- 支持 Character Prompt + Character UC 分离
- 支持场景模板库关键词匹配
"""

import logging
import io
import random
import re
import discord
from typing import Optional, List, Dict

from src.chat.utils.prompt_utils import replace_emojis
from src.chat.config.chat_config import NOVELAI_CONFIG
from src.chat.features.novelai_generation.tag_rules import (
    NOVELAI_TAG_RULES,
    TAG_LIBRARY_COMPACT,
    clamp_danbooru_tags,
    get_rewrite_prompt,
    get_rewrite_messages,
    get_tag_generation_messages,
)

log = logging.getLogger(__name__)

NOVELAI_PROMPT_MAX_OUTPUT_TOKENS = 8196


def _detect_novelai_prompt_api_format(prompt_api_url: Optional[str]) -> Optional[str]:
    """根据 NovelAI 提示词专用 URL 自动推断 API 格式。"""
    if not prompt_api_url:
        return None

    lowered = prompt_api_url.lower()
    if (
        "generativelanguage.googleapis.com" in lowered
        or "aiplatform.googleapis.com" in lowered
        or "/v1beta" in lowered
    ):
        return "gemini"

    # 非 Gemini 风格端点默认按 OpenAI 兼容处理（如 /v1/chat/completions）
    return "openai"


def _get_novelai_prompt_llm_overrides() -> Dict[str, Optional[str]]:
    """获取 NovelAI 提示词专用 LLM 覆盖配置（模型 / URL / KEY / API 格式）。"""
    prompt_model = str(NOVELAI_CONFIG.get("PROMPT_MODEL", "") or "").strip() or None
    prompt_api_url = str(NOVELAI_CONFIG.get("PROMPT_API_URL", "") or "").strip() or None
    prompt_api_key = str(NOVELAI_CONFIG.get("PROMPT_API_KEY", "") or "").strip() or None
    prompt_api_format = _detect_novelai_prompt_api_format(prompt_api_url)
    return {
        "model_name": prompt_model,
        "api_url": prompt_api_url,
        "api_key": prompt_api_key,
        "api_format": prompt_api_format,
    }


def _is_probably_tag_prompt(prompt: str) -> bool:
    """粗略判断是否为 Danbooru 标签串（用于切换到 Imagen 时转自然语言）。"""
    if not prompt:
        return False
    tokens = [seg.strip() for seg in prompt.split(",") if seg.strip()]
    if len(tokens) < 16:
        return False
    ascii_like = sum(
        1
        for seg in tokens
        if re.fullmatch(r"[a-zA-Z0-9_:\-\.#()/'\s]+", seg) is not None
    )
    return (ascii_like / max(1, len(tokens))) >= 0.75


async def _convert_tag_prompt_to_imagen_prompt(prompt: str, force_rewrite: bool = False) -> str:
    """将 Danbooru Tag 串转换为适配 Imagen 的中文自然语言提示词。"""
    if not force_rewrite and not _is_probably_tag_prompt(prompt):
        return prompt

    from src.chat.services.gemini_service import gemini_service

    llm_overrides = _get_novelai_prompt_llm_overrides()
    conversion_messages = [
        {
            "role": "user",
            "content": (
                "你是图像提示词转换助手。请把下面的 Danbooru 标签串转换为适配 Gemini Imagen 的简体中文自然语言提示词。"
                "要求：\n"
                "1) 只输出最终中文提示词，不要解释。\n"
                "2) 保留关键主体、服饰、场景、光影、构图、氛围。\n"
                "3) 不要输出英文标签列表，不要输出多段。\n\n"
                f"标签串：{prompt}"
            ),
        }
    ]

    try:
        converted = await gemini_service.generate_simple_response(
            prompt="",
            generation_config={
                "temperature": 0.5,
                "max_output_tokens": 1200,
            },
            messages=conversion_messages,
            model_name=llm_overrides["model_name"],
            api_url=llm_overrides["api_url"],
            api_key=llm_overrides["api_key"],
            api_format=llm_overrides["api_format"],
        )
        normalized = (converted or "").strip().strip('"').strip("'")
        if normalized:
            log.info("已将 Danbooru 标签转换为 Imagen 中文自然语言提示词")
            return normalized
    except Exception as e:
        log.warning(f"Tag->Imagen 自然语言转换失败，回退原提示词: {e}")

    return prompt


async def _convert_imagen_prompt_to_novelai_prompt(prompt: str, force_rewrite: bool = False) -> str:
    """将输入提示词转换/优化为 NovelAI Danbooru Tag。"""
    if not force_rewrite and _is_probably_tag_prompt(prompt):
        return prompt

    from src.chat.services.gemini_service import gemini_service

    llm_overrides = _get_novelai_prompt_llm_overrides()
    normalized_input = str(prompt or "").strip()
    is_tag_input = _is_probably_tag_prompt(normalized_input)

    if is_tag_input:
        request_messages = get_rewrite_messages(
            prompt=normalized_input,
            description=(
                "请在保留核心主体、场景、动作与构图意图的前提下，"
                "将这串 Danbooru 标签优化为更高质量、更完整、结构更清晰的 NovelAI 标签串。"
            ),
        )
        request_type = "Tag->Tag 优化"
    else:
        request_messages = get_tag_generation_messages(description=normalized_input)
        request_type = "描述->Tag 转换"

    try:
        converted = await gemini_service.generate_simple_response(
            prompt="",
            generation_config={
                "temperature": 0.7,
                "max_output_tokens": NOVELAI_PROMPT_MAX_OUTPUT_TOKENS,
            },
            messages=request_messages,
            model_name=llm_overrides["model_name"],
            api_url=llm_overrides["api_url"],
            api_key=llm_overrides["api_key"],
            api_format=llm_overrides["api_format"],
        )
        normalized = clamp_danbooru_tags(converted, max_tags=90)
        if normalized:
            log.info(f"已完成 NovelAI 提示词{request_type}（标签数已限制为≤90）")
            return normalized
    except Exception as e:
        log.warning(f"NovelAI 提示词{request_type}失败，回退原提示词: {e}")

    return prompt


def _prompt_already_contains_artist(prompt: str, artist_string: str) -> bool:
    """
    检查 prompt 中是否已经包含了画师串的内容，防止重复拼接。

    策略：提取画师串中的 artist:xxx tag，检查 prompt 是否已包含其中的大部分。
    如果画师串中超过一半的 artist tag 已存在于 prompt 中，认为已包含。
    """
    import re
    # 提取画师串中的 artist: tag（忽略权重语法）
    artist_tags = re.findall(r'artist[:\s]+[\w\-_/()]+', artist_string.lower())
    if not artist_tags:
        # 画师串中没有明确的 artist: tag，用前几个 tag 做简单比对
        first_tags = [t.strip().lower() for t in artist_string.split(",")[:3] if t.strip()]
        if not first_tags:
            return False
        prompt_lower = prompt.lower()
        match_count = sum(1 for t in first_tags if t in prompt_lower)
        return match_count >= max(1, len(first_tags) // 2)

    prompt_lower = prompt.lower()
    match_count = sum(1 for tag in artist_tags if tag in prompt_lower)
    return match_count > len(artist_tags) // 2


def _normalize_tag_for_match(tag: str) -> str:
    """标准化标签文本，用于去权重后的等价匹配。"""
    normalized = str(tag or "").strip().lower()
    normalized = normalized.replace("（", "(").replace("）", ")")
    normalized = re.sub(r"^\s*\d+(?:\.\d+)?::\s*", "", normalized)
    normalized = re.sub(r"\s*::\s*$", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _prompt_contains_tag(prompt: str, tag: str) -> bool:
    """判断 prompt（逗号分隔）中是否已包含目标标签（忽略权重语法）。"""
    target = _normalize_tag_for_match(tag)
    if not target:
        return False

    for token in str(prompt or "").split(","):
        if _normalize_tag_for_match(token) == target:
            return True
    return False


def _split_prompt_tokens(prompt: str) -> List[str]:
    """按逗号切分并清洗 Tag。"""
    return [seg.strip() for seg in str(prompt or "").split(",") if seg and seg.strip()]


def _strip_artist_tokens(prompt: str, artist_strings: List[Optional[str]]) -> str:
    """
    从 prompt 中剥离画师串里的标签（不限于前缀，进行全量剥离）。
    用于“切换画师串重生”时，避免旧画师串残留。
    """
    tokens = _split_prompt_tokens(prompt)
    if not tokens:
        return str(prompt or "").strip()

    artist_token_set = set()
    for artist in artist_strings:
        for artist_token in _split_prompt_tokens(str(artist or "")):
            normalized_artist_token = _normalize_tag_for_match(artist_token)
            if normalized_artist_token:
                artist_token_set.add(normalized_artist_token)

    if not artist_token_set:
        return ", ".join(tokens)

    filtered_tokens: List[str] = []
    for token in tokens:
        normalized_token = _normalize_tag_for_match(token)
        if normalized_token and normalized_token in artist_token_set:
            continue
        filtered_tokens.append(token)

    return ", ".join(filtered_tokens)


def _compose_prompt_with_artist(prompt: str, artist_string: Optional[str]) -> str:
    """将画师串与正面提示词进行组合，并避免重复拼接。"""
    base_prompt = str(prompt or "").strip()
    normalized_artist = str(artist_string or "").strip()

    if not normalized_artist:
        return base_prompt
    if not base_prompt:
        return normalized_artist

    if _prompt_already_contains_artist(base_prompt, normalized_artist):
        return base_prompt
    return f"{normalized_artist}, {base_prompt}"


def _build_prompt_preview_pages(
    artist_string: Optional[str],
    positive_prompt: Optional[str],
    negative_prompt: Optional[str],
) -> List[str]:
    """构建“画师串/正面/负面”三段代码块展示内容，超长时自动拆分多条消息。"""
    default_negative_prompt = str(NOVELAI_CONFIG.get("DEFAULT_NEGATIVE_PROMPT", "") or "").strip()
    artist_text = str(artist_string or "").strip() or "（无画师串）"
    positive_text = str(positive_prompt or "").strip() or "（无）"
    negative_text = str(negative_prompt or "").strip() or default_negative_prompt or "（无默认负面提示词）"

    def _build_section_blocks(title: str, value: str) -> List[str]:
        chunk_size = 1400
        chunks = [value[i:i + chunk_size] for i in range(0, len(value), chunk_size)] or [value]
        blocks: List[str] = []
        for idx, chunk in enumerate(chunks):
            suffix = "（续）" if idx > 0 else ""
            blocks.append(f"**{title}{suffix}：**\n```\n{chunk}\n```")
        return blocks

    all_blocks: List[str] = []
    all_blocks.extend(_build_section_blocks("画师串", artist_text))
    all_blocks.extend(_build_section_blocks("正面提示词", positive_text))
    all_blocks.extend(_build_section_blocks("负面提示词", negative_text))

    pages: List[str] = []
    current_page = ""
    for block in all_blocks:
        candidate = f"{current_page}\n\n{block}" if current_page else block
        if len(candidate) <= 2000:
            current_page = candidate
            continue

        if current_page:
            pages.append(current_page)
            current_page = block
            continue

        # 极端兜底：单个区块仍超长时硬切
        hard_chunks = [block[i:i + 1900] for i in range(0, len(block), 1900)] or [block]
        pages.extend(hard_chunks[:-1])
        current_page = hard_chunks[-1]

    if current_page:
        pages.append(current_page)

    return pages or ["（无可展示内容）"]


def _build_prompt_summary_for_embed(
    prompt: Optional[str],
    negative_prompt: Optional[str],
    artist_string: Optional[str],
) -> str:
    """构建主体身份/外貌优先的摘要，便于上下文生图保持角色一致。"""
    normalized_prompt = str(prompt or "").strip()
    normalized_artist = str(artist_string or "").strip()
    if normalized_artist and normalized_prompt:
        stripped_prompt = _strip_artist_tokens(normalized_prompt, [normalized_artist])
        normalized_prompt = stripped_prompt or normalized_prompt

    def _truncate(text: str, limit: int) -> str:
        compact = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(compact) <= limit:
            return compact
        return compact[: max(0, limit - 3)] + "..."

    def _is_identity_token(tag: str) -> bool:
        identity_exact = {
            "original",
            "solo",
            "girl",
            "girls",
            "boy",
            "boys",
            "woman",
            "man",
            "female",
            "male",
        }
        if tag in identity_exact:
            return True
        if re.fullmatch(r"\d+\s*(girl|girls|boy|boys)", tag):
            return True
        # 同人身份标签，例如：raiden shogun (genshin impact)
        if "(" in tag and ")" in tag:
            return True
        return False

    appearance_keywords = [
        "hair", "bangs", "ponytail", "twintail", "braid", "bun", "ahoge",
        "eye", "eyes", "eyelash", "eyebrow", "pupil", "heterochromia",
        "face", "lips", "lipstick", "nose", "fang", "teeth", "smile",
        "skin", "pale", "tan", "dark skin", "freckles", "mole",
        "ears", "fox ears", "animal ears", "tail", "horn", "halo", "wings",
        "petite", "curvy", "slim", "thick", "waist", "hips", "thigh",
        "legs", "body", "breasts", "chest",
        "silver hair", "white hair", "black hair", "blonde hair", "blue hair",
        "red hair", "pink hair", "purple hair", "green hair", "brown hair",
        "blue eyes", "red eyes", "green eyes", "golden eyes", "grey eyes",
    ]
    skip_keywords = [
        "masterpiece", "best quality", "amazing quality", "very aesthetic", "absurdres",
        "highres", "4k", "8k", "score_", "year",
        "nsfw", "sfw",
        "background", "scenery", "landscape", "city", "street", "forest", "sky", "cloud",
        "sunset", "night", "day", "indoors", "outdoors", "room", "bedroom",
        "lighting", "shadow", "cinematic", "depth of field", "dof", "bokeh",
        "close-up", "cowboy shot", "wide shot", "from above", "from below", "from behind",
        "pose", "sitting", "standing", "lying", "kneeling", "all fours",
        "walking", "running", "jumping", "dancing", "holding", "grabbing",
        "sex", "fellatio", "masturbation",
    ]

    selected_tokens: List[str] = []
    seen_normalized = set()

    for raw_token in _split_prompt_tokens(normalized_prompt):
        normalized_token = _normalize_tag_for_match(raw_token)
        if not normalized_token or normalized_token in seen_normalized:
            continue
        if any(skip in normalized_token for skip in skip_keywords):
            continue

        if _is_identity_token(normalized_token) or any(
            kw in normalized_token for kw in appearance_keywords
        ):
            selected_tokens.append(raw_token.strip())
            seen_normalized.add(normalized_token)
            if len(selected_tokens) >= 14:
                break

    # 兜底：若未提取到明显外貌标签，保留前若干个非场景动作类标签
    if not selected_tokens:
        for raw_token in _split_prompt_tokens(normalized_prompt):
            normalized_token = _normalize_tag_for_match(raw_token)
            if not normalized_token or normalized_token in seen_normalized:
                continue
            if any(skip in normalized_token for skip in skip_keywords):
                continue
            selected_tokens.append(raw_token.strip())
            seen_normalized.add(normalized_token)
            if len(selected_tokens) >= 10:
                break

    appearance_text = ", ".join(selected_tokens) if selected_tokens else "（未提取到明确外貌标签）"
    appearance_preview = _truncate(appearance_text, 420)
    return f"主体外貌: {appearance_preview}"


def _build_video_prompt_from_image(image_prompt: str, user_idea: str) -> str:
    """基于图片提示词和用户补充描述构建视频提示词。"""
    base_prompt = (image_prompt or "").strip()
    idea = (user_idea or "").strip()
    common_requirements = (
        "保持原图画风，保持主体与场景一致，画风一致，整体风格自然流畅，镜头具有电影感。"
    )

    if idea:
        return (
            "请基于这张图片生成一段短视频。"
            f"通用要求：{common_requirements}"
            f"\n原图提示词：{base_prompt}"
            f"\n补充要求：{idea}"
        )

    return (
        "请基于这张图片生成一段短视频。"
        f"通用要求：{common_requirements}"
        f"\n原图提示词：{base_prompt}"
    )


async def _generate_video_from_image_interaction(
    interaction: discord.Interaction,
    image_prompt: str,
    user_idea: str,
) -> None:
    """根据当前图片消息生成视频并反馈执行结果。"""
    if interaction.channel is None:
        await interaction.followup.send("当前频道不可用，无法生成视频。", ephemeral=True)
        return

    from src.chat.features.tools.functions.generate_video import generate_video

    video_prompt = _build_video_prompt_from_image(
        image_prompt=image_prompt,
        user_idea=user_idea,
    )
    result = await generate_video(
        prompt=video_prompt,
        duration=5,
        use_reference_image=True,
        preview_message="正在根据这张图片生成视频，请稍候...",
        success_message="视频已生成并发送到频道。",
        message=interaction.message if isinstance(interaction.message, discord.Message) else None,
        channel=interaction.channel,
        user_id=str(interaction.user.id),
        bot=interaction.client,
        request_user=interaction.user,
    )

    if isinstance(result, dict) and result.get("success"):
        await interaction.followup.send("视频已生成，结果消息已发送到当前频道。", ephemeral=True)
        return

    hint = ""
    if isinstance(result, dict):
        hint = str(result.get("hint") or "").strip()

    await interaction.followup.send(
        f"生成视频失败：{hint or '服务暂时不可用或本次请求未成功，请稍后重试。'}",
        ephemeral=True,
    )


async def generate_image_novelai(
    prompt: str,
    negative_prompt: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    steps: Optional[int] = None,
    scale: Optional[float] = None,
    sampler: Optional[str] = None,
    model: Optional[str] = None,
    seed: Optional[int] = None,
    preset_name: Optional[str] = None,
    artist_string: Optional[str] = None,
    skip_artist_prefix: bool = False,
    preview_message: Optional[str] = None,
    success_message: Optional[str] = None,
    character_name: Optional[str] = None,
    work_name: Optional[str] = None,
    **kwargs
) -> dict:
    """
    使用 NovelAI 引擎生成图片。当默认绘图引擎为 "novelai" 时，所有画图请求都必须使用此工具，不要使用 generate_image。

    **重要：对话主 AI 需要先写一版 Danbooru 草稿串，工具会再调用提示词 AI 生成一版优化串。**
    **若提示词 AI 没返回合格 Danbooru，会自动回退使用主 AI 草稿串。**

    ## 画师串自动拼接说明（重要）
    系统会自动在你的 prompt 前面拼接画师串前缀，优先级如下：
    1) 显式传入 `artist_string`：优先使用该画师串（用于你自行编写画师串时）。
    2) 未传 `artist_string` 且传入 `preset_name`：先匹配用户同名预设，再匹配管理员同名预设。
    3) 未传 `artist_string` 且未传 `preset_name`：优先读取用户持久化画师串模式（默认/预设/无画师串）。
    4) 若未命中持久化模式：回退全局默认画师串（`DEFAULT_ARTIST_STRING`，若已配置）。
    5) 传 `skip_artist_prefix=True`：不拼接任何画师串。
    - 你应尽量将画师串放在 `artist_string` 参数中单独传递，不要混在 `prompt` 里。
    - 你只需要专注于生成场景、角色、动作、表情等内容 Tag。
    - 如果用户明确说"不要画师串"、"不用预设"、"裸 prompt"等，请传 `skip_artist_prefix=True`。

    ## 对话主 AI 输入规则（必须严格遵守）：
    ### 1. 你传入 prompt 时的要求
    - prompt 优先写成 Danbooru 草稿串（英文标签、逗号分隔，可不完美）
    - 工具会基于你的草稿，再调用提示词 AI 生成一版优化 Danbooru
    - 若提示词 AI 没返回合格 Danbooru，系统会回退使用你传入的草稿串
    - 若用户只给自然语言，你需先细化并转成一版 Danbooru 草稿再传入
    - 你负责补全用户意图里的关键信息：主体、场景、动作、构图、光影、氛围、服饰、表情
    - 定格画面：描述应聚焦单一静态瞬间，避免连续动作过程
    - 单图最多 4 个角色，最多 2 个女性角色
    - **不要在 prompt 中写画师串（artist:xxx），系统会自动拼接或由 artist_string/preset_name 控制**

    ### 2. Tag 构成顺序（工具内提示词 AI 执行，主 AI 仅需理解）
    系统会按以下顺序自动构建 Tag：
    1. **质量 Tag**: masterpiece, best quality, amazing quality, very aesthetic, absurdres
    2. **场景构成 (5~10%)**: nsfw/sfw, 角色数量(1girl, solo), 角色关系(hetero, harem, yuri)
    3. **背景 (10~20%)**: 年代(modern, medieval, fantasy), 环境(bedroom, park, outdoor, onsen, train interior), 时间(night, sunset, golden hour), 氛围(mystical atmosphere), 光影(backlighting, rim lighting, sidelighting, dramatic shadows, moonlight, neon light, spotlight, tyndall effect)
    4. **构图 (10~20%)**: 区域(full body, upper body, cowboy shot), 远近(close-up, mid shot, wide shot), 透视(wide-angle, foreshortening, fisheye), 视角(front view, from behind, from above, from below, from side, pov, male pov), 焦点(face focus, ass focus, breast focus, foot focus, crotch focus), 角度(cinematic angle, dynamic angle, dutch angle), 效果(depth of field, bokeh, motion blur)
    5. **角色 DNA - 身份**: 性别(girl, boy), 姓名标签(同人角色必须使用 `character_name (work_name)` 英文格式，如 `raiden shogun (genshin impact)`；原创角色必须使用 `original`), 身份(bishoujo, maid, loli, milf, office lady)
    6. **角色 DNA - 外貌**: 发长/发型/发色/瞳色/罩杯(flat chest=A, small breasts=B, medium breasts=C, large breasts=D, huge breasts=E, gigantic breasts=F+)/肤色/修饰(makeup, scar, tan lines, bangs, petite, curvy, narrow waist, wide hips)
    7. **角色 DNA - 服饰**: 核心服饰(上装/下装/内衣/袜子/鞋类/配饰, 含风格/品类/颜色), 材质(plaid, latex, satin, velvet, sheer fabric, lace), 状态(wet clothes, torn clothes, see-through), 穿着状态(nude, open shirt, strap slip, no panties, clothes lift, skirt lift), 裸露部位(pussy, ass, nipples, navel)
    8. **当前动作**: 基础姿势(sitting, standing, lying, kneeling, all fours, squatting), 肢体动作(heart hands, head down, leg lift, v, arms up, peace sign), 核心交互(walking, masturbation, hug, kiss, sex, fellatio), 物理反馈(bouncing breasts, ass ripple, skin indentation, motion lines), 交互接触点(明确动作主体+做什么+放在哪, 如: grabbing own breasts, grabbing another's ass, holding phone)
    9. **当前表情**: 视线(looking at viewer, looking down, looking back, looking away), 眼(tears, wide-eyed, dilated pupils, half-closed eyes, empty eyes), 嘴(smile, open mouth, tongue out, clenched teeth, :3, pout), 感官(blush, ahegao, excited, embarrassed, flustered)
    10. **表现力/微细节**: 环境氛围(falling leaves, fireworks, steam, floating sakura, light particles), 生理反应(full-face blush, dilated pupils, drooling, heavy breathing, sweat, steaming body, shiny skin, wet body), 动态互动(speed lines, motion lines, bouncing breasts, splashing fluids, motion blur), 特效粒子(magical, ripple, glowing, light particles, sound effects)

    ### 3. 碎片化转译（具象化，拒绝模糊）
    将复合概念拆解为多个具体 Tag：
    - 月下 -> moonlit, night, starry sky
    - 战斗 -> battle, standing, holding sword
    - 害羞 -> shy, blushing, looking down, fidgeting
    - '好热' -> sweating, fanning self, loosening clothes
    - 色情浴室 -> bathroom, steam, wet body, shiny skin, nude
    - 上课无聊 -> classroom, sitting, chin rest, looking away, yawning

    ### 4. 权重调整（重要）
    - 增强核心元素: `1.2::Tag::` 或 `1.3::Tag::`
    - 减弱次要元素: `0.8::Tag::` 或 `0.7::Tag::`
    - 增强 3~8 次，减弱 2~4 次
    - 增强优先级: 同人角色姓名(含作品名) > 核心动作 > 服饰 > 特效 > 表情

    ### 5. 画面范围（只保留视觉可见 Tag）
    - 构图导致不可见: 下身特写->排除上身元素
    - 衣物覆盖导致不可见: 穿戴整齐->排除内衣Tag
    - 遮盖导致不可见: 抱胸->hands, breast hold, covered nipples

    ### 6. POV 建议（1女+1男且男=user时）
    - 无互动: 1girl, solo
    - 对视/对话: 1girl, solo, looking at viewer
    - 物理接触: 1girl, 1boy, male pov, pov hands
    - 性行为: 1girl, male pov, pov hands, penis

    ### 7. 多角色互动前缀
    - source#: 动作发起方 (如 source#grabbing another's breasts)
    - target#: 动作接收方 (如 target#being grabbed)
    - mutual#: 双方互动 (如 mutual#kissing)

    ### 8. 男性配角默认 Tag
    - 大几把路人男: 1boy, faceless male, muscular male, large penis, hetero
    - 黑人男: dark-skinned male, dark skin, muscular male
    - 正太: shota, age difference, size difference
    - 胖男: fat man, faceless male, ugly man

    ### 9. 画月月（自己）时的 Tag
    如果用户要求画"你"、"月月"、"自己"：
    - 1girl, solo, silver hair, high ponytail, crescent hair ornament, blue grey eyes
    - fox ears, white fox ears, pink inner ear, fox tail, silver white tail, fluffy tail
    - small triangular watermelon earrings（小巧的三角形西瓜耳坠）
    - white off-shoulder top, fur trim, detached sleeves, white high waist skirt, pink bow belt, silver necklace, jewelry

    ### 10. 参考标签库
    === 表情 ===
    grin, smile, smug, seductive smile, naughty face, glaring, pout, crying, sobbing, tears, surprised, flustered, blush, embarrassed, parted lips, open mouth, tongue out, ahegao, heart eyes, heart-shaped pupils, fucked silly, rolling eyes, empty eyes, half-closed eyes, wavy mouth, clenched teeth, :3, expressionless, evil smile, shaded face
    表情组合: ahegao+drooling+tears+rolling eyes(高潮), open mouth+heavy breathing+blush(插入), smile+blush+looking at viewer(温柔), clenched teeth+tears+blush(忍耐), tongue out+saliva+half-closed eyes(口交), empty eyes+expressionless(精神崩坏)

    === 姿势 ===
    standing, sitting, lying, kneeling, all fours, squatting, bent over, crawling, walking, running, jumping, contrapposto, seiza, wariza, leaning forward, leaning back, on stomach, on back, on side, top-down bottom-up, dogeza

    === 肢体 ===
    手臂: arm support, arms behind head, arms behind back, arms up, crossed arms, victory pose, outstretched arm, waving, beckoning
    腿部: crossed legs, spread legs, leg up, legs up, tiptoes, m legs, knees apart, legs together, standing split, leg lock
    手部: thumbs up, peace sign, double peace sign, heart hands, finger gun, v, double v, finger to mouth, shushing
    视线: looking at viewer, looking down, looking up, looking back, looking away, sideways glance, eye contact, upturned eyes

    === 衣物 ===
    状态: nude, completely nude, topless, bottomless, open shirt, no bra, no panties, strap slip, see-through, wet clothes, torn clothes, clothed female nude male, naked shirt, naked apron, clothes lift, skirt lift, shirt lift, panty pull
    开口: off-shoulder, bare shoulders, cleavage cutout, underboob cutout, center opening, navel cutout, back cutout, sideless dress, crotchless, nipple cutout
    类型: school uniform, sailor uniform, maid outfit, bikini, swimsuit, wedding dress, kimono, yukata, hanfu, gothic lolita, bunny girl, santa costume, leotard, bodysuit, latex, lingerie, babydoll, gym uniform, pajamas, naked apron, cheerleader
    配饰: choker, collar, leash, thigh strap, garter straps, thighhighs, pantyhose, high heels, boots, glasses, earrings, necklace, blindfold, ball gag

    === 发型/发色/瞳色 ===
    发型: long hair, short hair, ponytail, high ponytail, twintails, braid, bob cut, ahoge, bangs, sidelocks, wavy hair, curly hair, drill hair, messy hair, wet hair
    发色: black hair, blonde hair, brown hair, silver hair, white hair, red hair, blue hair, pink hair, purple hair, green hair, gradient hair
    瞳色: blue eyes, red eyes, green eyes, brown eyes, purple eyes, yellow eyes, heterochromia, heart-shaped pupils, slit pupils

    === 光影/氛围 ===
    光影: backlighting, rim lighting, sidelighting, dramatic shadows, moonlight, sunlight, neon light, golden hour, spotlight, tyndall effect, volumetric light, dimly lit, dark theme
    氛围: falling leaves, fireworks, steam, floating sakura, light particles, glowing, starry sky, rain, snow, petals, lens flare
    生理: sweat, heavy breathing, steaming body, trembling, drooling, tears, blush, shiny skin, oiled skin, wet body, twitching, flying sweatdrops
    动态: speed lines, motion lines, motion blur, bouncing breasts, ass ripple, sound effects

    === 性爱 ===
    体位: doggystyle, missionary, cowgirl position, reverse cowgirl, mating press, 69, girl on top, sex from behind, piledriver, spitroast, suspended congress, standing sex, prone bone, leg lock
    行为: sex, vaginal, anal, oral, fellatio, deepthroat, handjob, footjob, paizuri, thigh sex, buttjob, fingering, masturbation, deep penetration, cunnilingus, double penetration
    射精: cum, excessive cum, bukkake, facial, ejaculation, cumdrip, cum on body, internal cumshot, cum in mouth, cum in pussy, cum on breasts, cum string, cum overflow, after ejaculation, used condom
    BDSM: bondage, rope bondage, shibari, handcuffs, chains, blindfold, ball gag, collar, leash, pet play, spanking, slap mark, breast bondage, suspension
    双人: holding hands, eye contact, cuddling, princess carry, hug, hug from behind, breast press, grabbing another's breasts, lifting person, headpat, sitting on lap, face to face
    玩具: vibrator, dildo, egg vibrator, remote control vibrator, vibrator under panties, anal beads, butt plug, dildo riding, object insertion
    特殊: x-ray, cross section, stomach bulge, livestream, fake screenshot, exhibitionism, public indecency

    === 场景 ===
    室内: bedroom, bathroom, kitchen, classroom, library, office, hotel room, love hotel, bar, elevator, train interior, car interior, dungeon, church
    室外: beach, forest, park, alley, rooftop, garden, pool, shrine, street, ruins

    === 场景模板参考 ===
    骑乘: cowgirl position, girl on top, straddling, spread legs, sex, penis, motion lines
    后入: doggystyle, sex from behind, all fours, ass, from behind, grabbing hips
    传教士: missionary, on back, lying, spread legs, on bed, from above
    口交: fellatio, oral, kneeling, penis, pov, looking up, tongue out, saliva
    洗澡: bathroom, steam, completely nude, wet, wet hair, shiny skin, standing
    温泉: onsen, partially submerged, steam, wet body, nude, outdoors, rocks
    做饭: kitchen, cooking, apron, holding spatula, stove, steam
    自慰: female masturbation, fingering, spread legs, blush, heavy breathing, solo
    绳缚: bondage, rope bondage, shibari, bound wrists, nude, breast bondage, tears
    触手: tentacles, tentacle sex, restrained, spread legs, suspended, nude
    露出: exhibitionism, public indecency, outdoors, crowd, embarrassed, blush
    钢管舞: pole dancing, stripper pole, holding pole, armpits, spotlight, sweat
    酒吧: bar (place), cocktail, dim lighting, neon light, looking at viewer
    直播: livestream, chat log, fake screenshot, sitting, gaming chair, webcam

    ### 11. 示例（以下是提示词 AI 的输出示例，不是主 AI 手写内容）
    用户说"画一个银发少女在月光下"，提示词 AI 会生成：
    ```
    masterpiece, best quality, amazing quality, very aesthetic, absurdres, sfw, 1girl, solo, outdoors, night, 1.2::moonlight::, starry sky, rim lighting, backlighting, full body, front view, cinematic angle, depth of field, girl, bishoujo, 1.3::silver hair::, long hair, flowing hair, blue eyes, medium breasts, white skin, dress, white dress, long dress, elegant, standing, wind, hair flowing, looking at viewer, gentle smile, serene, falling leaves, light particles
    ```

    用户说"画月月在温泉里"，提示词 AI 会生成：
    ```
    masterpiece, best quality, amazing quality, very aesthetic, absurdres, nsfw, 1girl, solo, outdoors, night, starry sky, 1.2::moonlight::, rim lighting, onsen, steam, rocks, hot spring, cowboy shot, from above, depth of field, girl, bishoujo, 1.3::silver hair::, high ponytail, crescent hair ornament, blue grey eyes, fox ears, white fox ears, fox tail, silver white tail, fluffy tail, medium breasts, white skin, nude, completely nude, partially submerged, wet body, wet hair, 1.2::shiny skin::, small triangular watermelon earrings, bathing, relaxing, arms on edge, looking at viewer, gentle smile, blush, nose blush, steam, water droplets, light particles, 0.8::falling leaves::
    ```

    Args:
        prompt: 建议传主 AI 写的 Danbooru 草稿串（英文标签、逗号分隔，可不完美）。
                工具会再调用提示词 AI，将你的草稿优化为更高质量 Danbooru Tag 后生图。
                若提示词 AI 未返回合格 Danbooru，会回退使用你传入的草稿串。
                当用户只给自然语言时，你应先细化需求并转成一版 Danbooru 草稿再传入。
                草稿需尽量覆盖：主体、场景、动作、构图、光影、氛围、服饰、表情与风格倾向。

        negative_prompt: 负面提示词（可选），使用英文 Tag。
                留空则使用系统默认负面提示词。
                可用于排除不需要的元素，如: "background characters, fused bodies, bad anatomy"

        width: 图片宽度。未填写时优先读取用户已保存配置，否则回退全局默认。常用尺寸:
                - 竖图(人物): 832x1216
                - 横图(风景): 1216x832
                - 正方形: 1024x1024

        height: 图片高度。未填写时优先读取用户已保存配置，否则回退全局默认。

        steps: 采样步数(1-28)。未填写时优先读取用户已保存配置，否则回退全局默认。

        scale: 引导强度 CFG Scale(1.0-10.0)。未填写时优先读取用户已保存配置，否则回退全局默认。
                越高越贴近提示词但可能过饱和。

        sampler: 采样器。未填写时优先读取用户已保存配置，否则回退全局默认。可选:
                - "k_euler_ancestral" (推荐，效果最好)
                - "k_euler"
                - "k_dpmpp_2s_ancestral"
                - "k_dpmpp_2m"
                - "k_dpmpp_sde"
                - "ddim"

        seed: 随机种子（可选），留空则随机。指定种子可复现相同图片。

        preset_name: 画师串预设名称（可选，但命中点名时应强制传）。
                当用户原话明确点名预设（如“用XX串”“切到XX预设”“按XX风格画”）时，必须传 preset_name。
                preset_name 只能使用系统提供的可用预设名原文，不要编造。
                命中管理员预设时，优先传 `管理员/预设名` 以避免同名冲突。
                若用户未点名且你无法明确判断，才可不传；此时会优先读取用户持久化画师串模式，
                若模式不可用再回退到全局默认画师串（若已配置）。

        artist_string: 显式画师串（可选，优先级高于 preset_name）。
                当你想自行编写画师串时，应通过该参数单独传递；
                不要再把画师串混写到 prompt 中。
                传入后会直接作为画师串前缀参与拼接。

        skip_artist_prefix: 是否跳过画师串拼接，默认 False。
                设为 True 时不会在 prompt 前拼接任何画师串预设或全局默认画师串。
                仅当用户明确要求不使用画师串时才设为 True。

        preview_message: （必填）在图片生成前先发送给用户的预告消息。
                告诉用户你正在画图，例如："稍等一下，我来画~" 或 "让我想想怎么画..."

        success_message: （必填）图片生成成功后随图片一起发送的回复消息。
                用你自己的语气写一句简短的话，例如："画好了哦~" 或 "来看看这张怎么样！"
                **绝对不要在这里写提示词/Tag内容！** 提示词用户可以通过按钮自己查看。

        character_name: 同人角色英文名（可选，但同人角色场景建议必传）。
                例如: "raiden shogun"。
                若传入该参数，建议同时传 `work_name`，系统会自动补充/强化
                `character_name (work_name)` 身份标签，提升角色还原度。

        work_name: 角色所属作品英文名（可选，但同人角色场景建议与 `character_name` 同时传）。
                例如: "genshin impact"。
                与 `character_name` 同时存在时，系统会自动确保提示词中包含
                `1.3::character_name (work_name)::` 身份标签（若原 prompt 缺失）。

    Returns:
        成功后图片和你的回复会一起发送给用户。
        **你不需要也不应该再发送任何额外消息（包括提示词）。**
        用户想看提示词可以点「查看提示词」按钮。
        失败时你需要根据返回的提示信息告诉用户。
    """
    from src.chat.features.novelai_generation.services.novelai_service import novelai_service
    from src.chat.config.chat_config import NOVELAI_CONFIG
    from src.chat.features.odysseia_coin.service.coin_service import coin_service
    from src.chat.utils.database import chat_db_manager

    # 成本实时读取数据库配置（避免旧消息按钮使用到历史成本）
    try:
        db_generation_cost = await chat_db_manager.get_global_setting("novelai_generation_cost")
        if db_generation_cost is not None:
            cost = int(db_generation_cost)
    except Exception as e:
        log.warning(f"重新生成读取实时成本配置失败，回退到当前值 {cost}: {e}")

    # 获取消息对象
    message: Optional[discord.Message] = kwargs.get("message")

    # 辅助函数：安全地添加/移除反应
    async def add_reaction(emoji: str):
        if message:
            try:
                await message.add_reaction(emoji)
            except Exception as e:
                log.warning(f"添加反应失败: {e}")

    async def remove_reaction(emoji: str):
        if message:
            try:
                bot = kwargs.get("bot")
                if bot and bot.user:
                    await message.remove_reaction(emoji, bot.user)
            except Exception as e:
                log.warning(f"移除反应失败: {e}")

    # 检查服务是否可用
    if not novelai_service.is_available():
        log.warning("NovelAI 服务不可用")
        return {
            "generation_failed": True,
            "reason": "service_unavailable",
            "hint": "NovelAI 图片生成服务当前不可用。请用自己的语气告诉用户这个功能暂时用不了，可以让管理员在 Dashboard 中配置。"
        }

    # 获取用户ID
    user_id = kwargs.get("user_id")
    parsed_user_id: Optional[int] = None
    if user_id:
        try:
            parsed_user_id = int(user_id)
        except (ValueError, TypeError):
            log.warning(f"无法解析用户ID: {user_id}")

    # 兜底：若工具参数里的 user_id 异常，尝试从上下文对象恢复
    if parsed_user_id is None:
        request_user = kwargs.get("request_user")
        if request_user is not None and hasattr(request_user, "id"):
            try:
                parsed_user_id = int(request_user.id)
            except (ValueError, TypeError):
                parsed_user_id = None

    if parsed_user_id is None and message is not None and hasattr(message, "author") and getattr(message, "author", None):
        try:
            parsed_user_id = int(message.author.id)
        except (ValueError, TypeError):
            parsed_user_id = None

    if parsed_user_id is None:
        author_id = kwargs.get("author_id")
        if author_id is not None:
            try:
                parsed_user_id = int(author_id)
            except (ValueError, TypeError):
                parsed_user_id = None

    # 读取数据库中的实时全局配置（Dashboard），作为兜底默认值
    default_width = int(NOVELAI_CONFIG.get("DEFAULT_WIDTH", 832))
    default_height = int(NOVELAI_CONFIG.get("DEFAULT_HEIGHT", 1216))
    default_steps = int(NOVELAI_CONFIG.get("DEFAULT_STEPS", 28))
    default_scale = float(NOVELAI_CONFIG.get("DEFAULT_SCALE", 5.0))
    default_sampler = str(NOVELAI_CONFIG.get("DEFAULT_SAMPLER", "k_euler_ancestral"))
    default_model = str(NOVELAI_CONFIG.get("MODEL", "nai-diffusion-4-5-full"))
    generation_cost = int(NOVELAI_CONFIG.get("IMAGE_GENERATION_COST", 5))

    try:
        db_default_width = await chat_db_manager.get_global_setting("novelai_default_width")
        db_default_height = await chat_db_manager.get_global_setting("novelai_default_height")
        db_default_steps = await chat_db_manager.get_global_setting("novelai_default_steps")
        db_default_scale = await chat_db_manager.get_global_setting("novelai_default_scale")
        db_default_sampler = await chat_db_manager.get_global_setting("novelai_default_sampler")
        db_model = await chat_db_manager.get_global_setting("novelai_model")
        db_generation_cost = await chat_db_manager.get_global_setting("novelai_generation_cost")

        if db_default_width is not None:
            default_width = int(db_default_width)
        if db_default_height is not None:
            default_height = int(db_default_height)
        if db_default_steps is not None:
            default_steps = int(db_default_steps)
        if db_default_scale is not None:
            default_scale = float(db_default_scale)
        if db_default_sampler is not None:
            default_sampler = str(db_default_sampler)
        if db_model is not None:
            default_model = str(db_model)
        if db_generation_cost is not None:
            generation_cost = int(db_generation_cost)
    except Exception as e:
        log.warning(f"读取 NovelAI 实时全局配置失败，将回退到进程内配置: {e}")

    # 用户持久化参数优先；仅在用户无持久化时回退 Dashboard 全局参数
    user_generation_settings: Dict[str, object] = {}
    has_user_generation_settings = False
    if parsed_user_id is not None:
        try:
            user_generation_settings = await chat_db_manager.get_novelai_generation_settings(parsed_user_id)
            has_user_generation_settings = bool(user_generation_settings.get("_from_user"))
        except Exception as e:
            log.warning(f"读取用户 {parsed_user_id} 的 NovelAI 持久化参数失败: {e}")

    user_width = int(user_generation_settings.get("width", default_width))
    user_height = int(user_generation_settings.get("height", default_height))
    user_steps = int(user_generation_settings.get("steps", default_steps))
    user_scale = float(user_generation_settings.get("scale", default_scale))
    user_sampler = str(user_generation_settings.get("sampler", default_sampler))
    user_model = str(user_generation_settings.get("model", default_model))

    def _resolve_int_param(incoming_value, user_value: int, global_default: int) -> int:
        if incoming_value is None:
            return user_value if has_user_generation_settings else global_default
        try:
            normalized = int(incoming_value)
        except (TypeError, ValueError):
            normalized = global_default

        if has_user_generation_settings and normalized == global_default and user_value != global_default:
            return user_value
        return normalized

    def _resolve_float_param(incoming_value, user_value: float, global_default: float) -> float:
        if incoming_value is None:
            return user_value if has_user_generation_settings else global_default
        try:
            normalized = float(incoming_value)
        except (TypeError, ValueError):
            normalized = global_default

        if has_user_generation_settings and normalized == global_default and user_value != global_default:
            return user_value
        return normalized

    def _resolve_str_param(incoming_value, user_value: str, global_default: str) -> str:
        normalized = str(incoming_value).strip() if incoming_value is not None else ""
        if not normalized:
            return user_value if has_user_generation_settings else global_default

        if has_user_generation_settings and normalized == global_default and user_value != global_default:
            return user_value
        return normalized

    width = _resolve_int_param(width, user_width, default_width)
    height = _resolve_int_param(height, user_height, default_height)
    steps = _resolve_int_param(steps, user_steps, default_steps)
    scale = _resolve_float_param(scale, user_scale, default_scale)
    sampler = _resolve_str_param(sampler, user_sampler, default_sampler)
    model = _resolve_str_param(model, user_model, default_model)

    # 检查是否处于绘图封禁状态
    if parsed_user_id is not None:
        ban_status = await chat_db_manager.get_image_generation_ban_status(parsed_user_id)
        if ban_status.get("is_banned"):
            remaining_text = ban_status.get("remaining_text", "未知时长")
            return {
                "generation_failed": True,
                "reason": "image_generation_banned",
                "hint": f"该用户因图片收到过多负反馈，绘图功能已被临时禁用，剩余封禁时长：{remaining_text}。"
            }

    cost = generation_cost

    # 检查用户余额
    if parsed_user_id is not None and cost > 0:
        balance = await coin_service.get_balance(parsed_user_id)
        if balance < cost:
            return {
                "generation_failed": True,
                "reason": "insufficient_balance",
                "cost": cost,
                "balance": balance,
                "hint": f"用户月光币不足（需要{cost}，只有{balance}）。请用自己的语气告诉用户余额不够。"
            }

    # 对话场景：双串策略（主 AI 草稿串 + 提示词 AI 优化串）
    main_prompt_draft = str(prompt or "").strip()
    if not main_prompt_draft:
        return {
            "generation_failed": True,
            "reason": "empty_description",
            "hint": "你没有提供可用的绘图草稿。请先补充 Danbooru 草稿标签串。"
        }

    rewritten_prompt = await _convert_imagen_prompt_to_novelai_prompt(
        main_prompt_draft,
        force_rewrite=True,
    )
    normalized_rewritten_prompt = str(rewritten_prompt or "").strip().strip('"').strip("'")
    normalized_main_prompt = main_prompt_draft.strip().strip('"').strip("'")

    if normalized_rewritten_prompt and _is_probably_tag_prompt(normalized_rewritten_prompt):
        # 优先使用提示词 AI 的优化串
        prompt = normalized_rewritten_prompt
        log.info("双串策略：已采用提示词 AI 生成的优化 Danbooru 标签串。")
    else:
        # 回退：提示词 AI 未产出合格 Danbooru 时，使用主 AI 草稿串
        if normalized_rewritten_prompt:
            log.warning("提示词 AI 返回内容疑似不是 Danbooru 标签，已回退使用主 AI 草稿串。")
        else:
            log.warning("提示词 AI 返回空内容，已回退使用主 AI 草稿串。")

        if not normalized_main_prompt:
            return {
                "generation_failed": True,
                "reason": "prompt_rewrite_empty",
                "hint": "提示词 AI 与主提示词都不可用，请稍后重试。"
            }

        prompt = normalized_main_prompt
        if _is_probably_tag_prompt(prompt):
            log.info("双串策略回退：使用主 AI 草稿 Danbooru 标签串继续生成。")
        else:
            log.warning("回退草稿串疑似不是 Danbooru 标签串，已继续尝试生成。建议主 AI 输出 Danbooru 草稿。")

    # 画师串应用策略：
    # skip_artist_prefix=True 时跳过所有画师串拼接
    # 1) 显式 artist_string 优先（AI 自行编写画师串时应使用该参数）
    # 2) 指定 preset_name 时，先匹配用户预设，再匹配管理员预设（均支持大小写不敏感）
    # 3) 未指定 preset_name 时，优先读取用户持久化画师串模式（default/preset/none）
    # 4) 若仍无可用预设，则回退全局默认画师串
    base_prompt = prompt
    final_prompt = base_prompt
    applied_artist = False
    effective_preset_name: Optional[str] = None
    effective_artist_string: Optional[str] = None

    normalized_artist_string: Optional[str] = None
    if isinstance(artist_string, str):
        normalized_artist = artist_string.strip()
        if normalized_artist:
            normalized_artist_string = normalized_artist
    elif artist_string is not None:
        normalized_artist = str(artist_string).strip()
        if normalized_artist:
            normalized_artist_string = normalized_artist

    normalized_preset_name: Optional[str] = None
    if isinstance(preset_name, str):
        normalized_name = preset_name.strip()
        if normalized_name:
            normalized_preset_name = normalized_name
    elif preset_name is not None:
        normalized_name = str(preset_name).strip()
        if normalized_name:
            normalized_preset_name = normalized_name

    # 未显式指定 preset_name 时，读取用户持久化画师串模式
    if (not normalized_artist_string) and (not normalized_preset_name) and (not skip_artist_prefix) and parsed_user_id is not None:
        try:
            active_state = await chat_db_manager.get_novelai_active_preset_state(parsed_user_id)
            active_mode = active_state.get("active_mode", "default")
            active_preset_name = active_state.get("active_preset_name")

            if active_mode == "none":
                skip_artist_prefix = True
                log.info(f"使用用户 {parsed_user_id} 的持久化画师串模式: none")
            elif active_mode == "preset" and active_preset_name:
                normalized_preset_name = str(active_preset_name).strip()
                log.info(f"使用用户 {parsed_user_id} 的持久化画师串预设: {normalized_preset_name}")
        except Exception as e:
            log.warning(f"读取用户持久化画师串模式失败: {e}")

    # 容错：支持通过 preset_name 传控制语义
    # - none / 无画师串 => 等价于 skip_artist_prefix=True
    # - default / 默认 => 回退全局默认画师串（不匹配任何预设）
    if normalized_preset_name:
        lower_name = normalized_preset_name.lower()
        no_artist_aliases = {
            "none", "null", "no_artist", "no-artist", "noartist",
            "无", "无画师串", "不用画师串", "裸prompt", "裸 prompt", "裸",
        }
        default_aliases = {
            "default", "global_default", "system_default",
            "默认", "系统默认", "全局默认",
        }

        if lower_name in no_artist_aliases:
            skip_artist_prefix = True
            normalized_preset_name = None
            log.info("收到 preset_name=无画师串 控制语义，已切换为 skip_artist_prefix=True")
        elif lower_name in default_aliases:
            normalized_preset_name = None
            log.info("收到 preset_name=默认 控制语义，已回退为全局默认画师串模式")

    if skip_artist_prefix:
        log.info("skip_artist_prefix=True, 跳过画师串拼接")
    elif normalized_artist_string:
        if normalized_preset_name:
            log.info("同时收到 artist_string 与 preset_name，优先使用 artist_string")
        final_prompt = _compose_prompt_with_artist(base_prompt, normalized_artist_string)
        effective_artist_string = normalized_artist_string
        applied_artist = True
        effective_preset_name = "自定义画师串"
        log.info("应用显式 artist_string 参数作为画师串")
    else:
        user_presets = []
        admin_presets = []

        if parsed_user_id is not None:
            try:
                user_presets = await chat_db_manager.get_novelai_presets(parsed_user_id)
            except Exception as e:
                log.warning(f"读取用户画师串预设失败: {e}")

        try:
            admin_presets = await chat_db_manager.get_novelai_admin_presets()
        except Exception as e:
            log.warning(f"读取管理员画师串预设失败: {e}")

        selected_preset = None
        selected_scope: Optional[str] = None

        if normalized_preset_name:
            search_name = normalized_preset_name
            force_admin_only = False
            if str(search_name).startswith("管理员/"):
                stripped_name = str(search_name).split("/", 1)[1].strip()
                if stripped_name:
                    search_name = stripped_name
                    force_admin_only = True

            # 先匹配用户预设
            if not force_admin_only:
                selected_preset = next(
                    (p for p in user_presets if p.get("name") == search_name),
                    None,
                )
                if selected_preset:
                    selected_scope = "user"

            if not selected_preset and (not force_admin_only):
                requested_name_lower = str(search_name).lower()
                selected_preset = next(
                    (
                        p
                        for p in user_presets
                        if str(p.get("name", "")).lower() == requested_name_lower
                    ),
                    None,
                )
                if selected_preset:
                    selected_scope = "user"
                    log.info(
                        f"预设名大小写不一致，已匹配预设: {search_name} -> {selected_preset.get('name')}"
                    )

            # 用户未命中时，继续匹配管理员预设
            if not selected_preset:
                selected_preset = next(
                    (p for p in admin_presets if p.get("name") == search_name),
                    None,
                )
                if selected_preset:
                    selected_scope = "admin"

            if not selected_preset:
                requested_name_lower = str(search_name).lower()
                selected_preset = next(
                    (
                        p
                        for p in admin_presets
                        if str(p.get("name", "")).lower() == requested_name_lower
                    ),
                    None,
                )
                if selected_preset:
                    selected_scope = "admin"
                    log.info(
                        f"管理员预设名大小写不一致，已匹配预设: {search_name} -> {selected_preset.get('name')}"
                    )

            if not selected_preset:
                log.warning(
                    f"未找到用户或管理员预设 '{normalized_preset_name}'，将继续使用全局默认画师串（或无前缀）"
                )
        else:
            # 未指定预设名：不自动套“最近预设/场景匹配预设”，仅使用用户持久化模式或全局默认
            selected_preset = None
            selected_scope = None

        if selected_preset and selected_preset.get("artist_string"):
            artist_str = str(selected_preset.get("artist_string") or "").strip()
            final_prompt = _compose_prompt_with_artist(base_prompt, artist_str)
            effective_artist_string = artist_str or None
            applied_artist = bool(effective_artist_string)

            preset_scope_text = "管理员" if selected_scope == "admin" else "用户"
            log.info(f"应用{preset_scope_text}画师串预设: {selected_preset.get('name', 'unknown')}")

            selected_name = selected_preset.get("name")
            if selected_name:
                if selected_scope == "admin":
                    effective_preset_name = f"管理员/{selected_name}"
                else:
                    effective_preset_name = selected_name

            # 仅用户预设支持负面提示词
            if (
                selected_scope == "user"
                and not negative_prompt
                and selected_preset.get("negative_prompt")
            ):
                negative_prompt = selected_preset["negative_prompt"]

        # 如果没有通过用户/管理员预设应用画师串，则使用全局默认画师串
        if not applied_artist:
            default_artist = str(NOVELAI_CONFIG.get("DEFAULT_ARTIST_STRING", "") or "").strip()
            if default_artist:
                final_prompt = _compose_prompt_with_artist(base_prompt, default_artist)
                effective_artist_string = default_artist
                applied_artist = True
                log.info(f"应用全局默认画师串: {default_artist[:60]}...")

    # 角色身份标签自动补全（同人角色）
    normalized_character_name = str(character_name or "").strip()
    normalized_work_name = str(work_name or "").strip()

    # 兼容把 "character (work)" 误填到 character_name 的情况
    parsed_identity = re.fullmatch(
        r"\s*([^()]+?)\s*[（(]\s*([^()]+?)\s*[)）]\s*",
        normalized_character_name,
    )
    if parsed_identity and not normalized_work_name:
        normalized_character_name = parsed_identity.group(1).strip()
        normalized_work_name = parsed_identity.group(2).strip()

    if normalized_character_name and normalized_work_name:
        identity_tag = f"{normalized_character_name} ({normalized_work_name})"
        weighted_identity_tag = f"1.3::{identity_tag}::"
        if not _prompt_contains_tag(final_prompt, identity_tag):
            final_prompt = f"{weighted_identity_tag}, {final_prompt}"
            log.info(f"自动补充角色身份标签: {identity_tag}")
    elif normalized_character_name and not normalized_work_name:
        log.warning("收到 character_name 但缺少 work_name，已跳过身份标签自动补充")

    log.info(f"调用 NovelAI 图片生成工具，Tag: {final_prompt[:100]}..., 尺寸: {width}x{height}")

    # 添加"正在生成"反应
    await add_reaction("🎨")

    # 发送预告消息
    channel = kwargs.get("channel")
    if channel and preview_message:
        try:
            processed_message = replace_emojis(preview_message)
            await channel.send(processed_message)
            log.info(f"已发送 NovelAI 图片生成预告消息")
        except Exception as e:
            log.warning(f"发送预告消息失败: {e}")

    try:
        # 验证尺寸参数
        try:
            width = int(width)
        except (TypeError, ValueError):
            width = default_width
        try:
            height = int(height)
        except (TypeError, ValueError):
            height = default_height
        try:
            steps = int(steps)
        except (TypeError, ValueError):
            steps = default_steps
        try:
            scale = float(scale)
        except (TypeError, ValueError):
            scale = default_scale

        width = max(512, min(2048, width))
        height = max(512, min(2048, height))
        steps = max(1, min(50, steps))
        scale = max(1.0, min(10.0, scale))

        # 验证采样器
        valid_samplers = ["k_euler", "k_euler_ancestral", "k_dpmpp_2s_ancestral",
                          "k_dpmpp_2m", "k_dpmpp_sde", "ddim"]
        sampler = str(sampler or "").strip()
        if sampler not in valid_samplers:
            sampler = default_sampler

        model = str(model or "").strip() or default_model

        # 调用 NovelAI 服务
        result = await novelai_service.generate_image(
            prompt=final_prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            steps=steps,
            scale=scale,
            sampler=sampler,
            model=model,
            seed=seed,
        )

        # 移除"正在生成"反应
        await remove_reaction("🎨")

        if result is not None:
            # 添加成功反应
            await add_reaction("✅")

            # 扣除月光币
            if parsed_user_id is not None and cost > 0:
                try:
                    await coin_service.remove_coins(
                        parsed_user_id, cost, f"NovelAI生图: {final_prompt[:25]}..."
                    )
                    log.info(f"用户 {parsed_user_id} NovelAI 生图成功，扣除 {cost} 月光币")
                except Exception as e:
                    log.error(f"扣除月光币失败: {e}")

            # 发送图片到频道
            if channel:
                try:
                    # 构建 Embed
                    embed = discord.Embed(
                        title="NovelAI 图片生成",
                        color=0x9B59B6,
                    )
                    # 设置请求者信息
                    request_user = kwargs.get("request_user")
                    author_user = request_user
                    if not author_user and message and hasattr(message, "author"):
                        author_user = message.author
                    if author_user:
                        author_name = getattr(author_user, "display_name", None) or getattr(author_user, "name", None)
                        author_avatar = getattr(author_user, "display_avatar", None)
                        author_icon_url = getattr(author_avatar, "url", None) if author_avatar else None
                        if author_name:
                            embed.set_author(name=author_name, icon_url=author_icon_url)

                    if success_message:
                        processed_success = replace_emojis(success_message)
                        embed.description = processed_success[:2048]

                    # 生成信息（紧凑排列）
                    model_name = result.model or NOVELAI_CONFIG.get("MODEL", "unknown")
                    if effective_preset_name:
                        embed.add_field(name="预设", value=effective_preset_name, inline=True)
                    embed.add_field(name="种子", value=str(result.seed), inline=True)
                    embed.add_field(
                        name="参数",
                        value=f"{result.width}x{result.height} | {steps}步 | CFG {scale}",
                        inline=True,
                    )
                    embed.add_field(
                        name="人物外貌摘要",
                        value=_build_prompt_summary_for_embed(
                            prompt=final_prompt,
                            negative_prompt=negative_prompt,
                            artist_string=effective_artist_string,
                        ),
                        inline=False,
                    )
                    embed.set_footer(
                        text=f"消耗 {cost} 月光币 | {sampler} | {model_name}"
                    )

                    image_file = discord.File(
                        io.BytesIO(result.image_data),
                        filename="novelai_generated.png",
                        spoiler=True,
                    )
                    # 不使用 embed.set_image()，让 spoiler 遮罩正常生效

                    # 创建交互按钮 View
                    prompt_without_artist = _strip_artist_tokens(final_prompt, [effective_artist_string])
                    if not prompt_without_artist:
                        prompt_without_artist = base_prompt or final_prompt

                    interaction_view = NovelAIResultView(
                        prompt=prompt_without_artist,
                        artist_string=effective_artist_string,
                        negative_prompt=negative_prompt,
                        width=width,
                        height=height,
                        steps=steps,
                        scale=scale,
                        sampler=sampler,
                        preset_name=effective_preset_name,
                        user_id=user_id,
                        cost=cost,
                    )

                    sent_message = await channel.send(
                        embed=embed, file=image_file, view=interaction_view
                    )
                    if parsed_user_id is not None:
                        await chat_db_manager.register_generated_image_message(
                            message_id=sent_message.id,
                            user_id=parsed_user_id,
                            guild_id=sent_message.guild.id if sent_message.guild else None,
                            channel_id=sent_message.channel.id,
                        )
                    log.info("已发送 NovelAI 生成图片到频道（含交互按钮）")

                except Exception as e:
                    log.error(f"发送 NovelAI 图片到频道失败: {e}")

            return {
                "success": True,
                "skip_ai_response": True,
                "images_generated": 1,
                "cost": cost,
                "message": "NovelAI 图片已成功生成并发送给用户。不要再发送任何消息，不要发送提示词，不要回复。"
            }
        else:
            # 生成失败
            await add_reaction("❌")
            log.warning(f"NovelAI 图片生成返回空结果。Tag: {final_prompt[:100]}")
            return {
                "generation_failed": True,
                "reason": "generation_failed",
                "hint": "NovelAI 图片生成失败了。请用自己的语气告诉用户稍后再试或换个描述试试。可能是 API Token 无效、Anlas 不足、或请求冲突。"
            }

    except Exception as e:
        await remove_reaction("🎨")
        await add_reaction("❌")
        log.error(f"NovelAI 图片生成异常: {e}", exc_info=True)
        return {
            "generation_failed": True,
            "reason": "exception",
            "hint": f"NovelAI 图片生成时出现错误: {str(e)[:100]}。请用自己的语气告诉用户出了问题。"
        }


# ==================== 交互按钮组件 ====================


class EditPromptModal(discord.ui.Modal, title="修改提示词"):
    """弹出 Modal 让用户编辑提示词后重新生成"""

    prompt_input = discord.ui.TextInput(
        label="正面提示词 (Danbooru Tag)",
        style=discord.TextStyle.paragraph,
        placeholder="masterpiece, best quality, 1girl, ...",
        required=True,
        max_length=4000,
    )
    negative_input = discord.ui.TextInput(
        label="负面提示词（可选）",
        style=discord.TextStyle.paragraph,
        placeholder="lowres, bad anatomy, ...",
        required=False,
        max_length=2000,
    )

    def __init__(
        self,
        current_prompt: str,
        current_negative: Optional[str],
        width: int,
        height: int,
        steps: int,
        scale: float,
        sampler: str,
        preset_name: Optional[str],
        user_id: Optional[str],
        cost: int,
        current_artist_string: Optional[str] = None,
    ):
        super().__init__()
        self.prompt_input.default = current_prompt[:4000]
        if current_negative:
            self.negative_input.default = current_negative[:2000]
        self._width = width
        self._height = height
        self._steps = steps
        self._scale = scale
        self._sampler = sampler
        self._preset_name = preset_name
        self._user_id = user_id
        self._cost = cost
        normalized_artist = str(current_artist_string or "").strip()
        self._artist_string = normalized_artist or None

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            new_prompt = self.prompt_input.value.strip()
            new_negative = self.negative_input.value.strip() if self.negative_input.value else None

            if not new_prompt:
                await interaction.followup.send("提示词不能为空！", ephemeral=True)
                return

            await _regenerate_novelai(
                interaction=interaction,
                prompt=new_prompt,
                negative_prompt=new_negative,
                width=self._width,
                height=self._height,
                steps=self._steps,
                scale=self._scale,
                sampler=self._sampler,
                preset_name=self._preset_name,
                user_id=self._user_id,
                cost=self._cost,
                artist_string=self._artist_string,
            )
        except Exception as e:
            log.error(f"修改提示词重新生成失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"生成失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass


class ToolAIRewriteModal(discord.ui.Modal, title="AI 重写提示词"):
    """对话工具生成结果 - AI 重写提示词弹窗"""

    description_input = discord.ui.TextInput(
        label="描述你想要的变化",
        style=discord.TextStyle.paragraph,
        placeholder="例如：换成夜晚的场景，加上星空和月亮\n或：改为更动感的姿势，添加战斗元素\n留空则自动优化当前提示词",
        required=False,
        max_length=1000,
    )

    def __init__(
        self,
        current_prompt: str,
        negative_prompt: Optional[str],
        width: int,
        height: int,
        steps: int,
        scale: float,
        sampler: str,
        preset_name: Optional[str],
        user_id: Optional[str],
        cost: int,
        artist_string: Optional[str] = None,
    ):
        super().__init__()
        self._current_prompt = current_prompt
        self._negative_prompt = negative_prompt
        self._width = width
        self._height = height
        self._steps = steps
        self._scale = scale
        self._sampler = sampler
        self._preset_name = preset_name
        self._user_id = user_id
        self._cost = cost
        normalized_artist = str(artist_string or "").strip()
        self._artist_string = normalized_artist or None

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            description = self.description_input.value.strip() if self.description_input.value else "自动优化和增强提示词，使画面更精美细腻"

            # 调用 AI 重写 prompt
            from src.chat.services.gemini_service import gemini_service

            rewrite_messages = get_rewrite_messages(
                prompt=self._current_prompt,
                description=description,
            )
            llm_overrides = _get_novelai_prompt_llm_overrides()
            new_tags = await gemini_service.generate_simple_response(
                prompt="",  # messages 模式下 prompt 被忽略
                generation_config={
                    "temperature": 0.8,
                    "max_output_tokens": NOVELAI_PROMPT_MAX_OUTPUT_TOKENS,
                },
                messages=rewrite_messages,
                model_name=llm_overrides["model_name"],
                api_url=llm_overrides["api_url"],
                api_key=llm_overrides["api_key"],
                api_format=llm_overrides["api_format"],
            )

            if not new_tags or not new_tags.strip():
                await interaction.followup.send("AI 重写失败，请稍后重试。", ephemeral=True)
                return

            new_prompt = clamp_danbooru_tags(new_tags, max_tags=90)
            log.info(f"对话工具 AI 重写 prompt 成功（标签数≤90）: {new_prompt[:100]}...")

            await _regenerate_novelai(
                interaction=interaction,
                prompt=new_prompt,
                negative_prompt=self._negative_prompt,
                width=self._width,
                height=self._height,
                steps=self._steps,
                scale=self._scale,
                sampler=self._sampler,
                preset_name=self._preset_name,
                user_id=self._user_id,
                cost=self._cost,
                artist_string=self._artist_string,
                title_suffix="（AI 重写）",
            )
        except Exception as e:
            log.error(f"对话工具 AI 重写 prompt 失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"AI 重写失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass


class GenerateVideoModal(discord.ui.Modal, title="生成视频"):
    """从当前生图结果生成视频的描述输入框。"""

    description_input = discord.ui.TextInput(
        label="描述你的想法（可选）",
        style=discord.TextStyle.paragraph,
        placeholder="例如：镜头缓慢推进，角色转头微笑；留空则 AI 自由发挥",
        required=False,
        max_length=1000,
    )

    def __init__(self, image_prompt: str):
        super().__init__()
        self._image_prompt = image_prompt

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            user_idea = self.description_input.value.strip() if self.description_input.value else ""
            await _generate_video_from_image_interaction(
                interaction=interaction,
                image_prompt=self._image_prompt,
                user_idea=user_idea,
            )
        except Exception as e:
            log.error(f"从生图结果生成视频失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"生成视频失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass


def _build_imagen_model_options(current_resolution: str = "default", current_rating: str = "nsfw") -> List[discord.SelectOption]:
    """构建 Imagen 分辨率×内容分级选项列表。"""
    options: List[discord.SelectOption] = []
    combinations = [
        ("default", "sfw", "标准 | SFW"),
        ("default", "nsfw", "标准 | NSFW"),
        ("2k", "sfw", "2K 高清 | SFW"),
        ("2k", "nsfw", "2K 高清 | NSFW"),
        ("4k", "sfw", "4K 超清 | SFW"),
        ("4k", "nsfw", "4K 超清 | NSFW"),
    ]
    for resolution, rating, label in combinations:
        value = f"{resolution}|{rating}"
        is_default = (resolution == current_resolution and rating == current_rating)
        options.append(
            discord.SelectOption(
                label=label,
                value=value,
                default=is_default,
                description=f"分辨率: {resolution.upper()}, 内容: {rating.upper()}",
            )
        )
    return options


class ToolImagenEditPromptModal(discord.ui.Modal, title="修改提示词重新生成"):
    """从 Imagen 切换结果修改提示词的模态框。"""

    prompt_input = discord.ui.TextInput(
        label="提示词",
        style=discord.TextStyle.paragraph,
        placeholder="输入新的中文自然语言提示词...",
        max_length=2000,
        required=True,
    )

    def __init__(
        self,
        current_prompt: str,
        user_id: Optional[str],
        negative_prompt: Optional[str],
        width: int,
        height: int,
        steps: int,
        scale: float,
        sampler: str,
        preset_name: Optional[str],
        novelai_cost: int,
        resolution: str = "default",
        content_rating: str = "nsfw",
    ):
        super().__init__()
        self.prompt_input.default = current_prompt
        self._user_id = user_id
        self._negative_prompt = negative_prompt
        self._width = width
        self._height = height
        self._steps = steps
        self._scale = scale
        self._sampler = sampler
        self._preset_name = preset_name
        self._novelai_cost = novelai_cost
        self._resolution = resolution
        self._content_rating = content_rating

    async def on_submit(self, interaction: discord.Interaction):
        new_prompt = self.prompt_input.value.strip()
        if not new_prompt:
            await interaction.response.send_message("提示词不能为空哦！", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        try:
            await _regenerate_with_imagen(
                interaction=interaction,
                prompt=new_prompt,
                user_id=self._user_id,
                negative_prompt=self._negative_prompt,
                width=self._width,
                height=self._height,
                steps=self._steps,
                scale=self._scale,
                sampler=self._sampler,
                preset_name=self._preset_name,
                novelai_cost=self._novelai_cost,
                resolution=self._resolution,
                content_rating=self._content_rating,
                title_suffix="（修改提示词）",
            )
        except Exception as e:
            log.error(f"修改提示词 Imagen 重新生成失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"重新生成失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass


class ToolImagenAIRewriteModal(discord.ui.Modal, title="AI 重写提示词"):
    """对 Imagen 中文自然语言提示词进行 AI 重写。"""

    description_input = discord.ui.TextInput(
        label="描述你想要的变化",
        style=discord.TextStyle.paragraph,
        placeholder="例如：改成黄昏海边、电影感光影、人物更有动态",
        required=False,
        max_length=1000,
    )

    def __init__(
        self,
        current_prompt: str,
        user_id: Optional[str],
        negative_prompt: Optional[str],
        width: int,
        height: int,
        steps: int,
        scale: float,
        sampler: str,
        preset_name: Optional[str],
        novelai_cost: int,
        resolution: str = "default",
        content_rating: str = "nsfw",
    ):
        super().__init__()
        self._current_prompt = current_prompt
        self._user_id = user_id
        self._negative_prompt = negative_prompt
        self._width = width
        self._height = height
        self._steps = steps
        self._scale = scale
        self._sampler = sampler
        self._preset_name = preset_name
        self._novelai_cost = novelai_cost
        self._resolution = resolution
        self._content_rating = content_rating

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            from src.chat.services.gemini_service import gemini_service

            description = (
                self.description_input.value.strip()
                if self.description_input.value
                else "自动优化该提示词，增强画面层次、光影和细节表现"
            )
            llm_overrides = _get_novelai_prompt_llm_overrides()
            rewrite_messages = [
                {
                    "role": "user",
                    "content": (
                        "你是图像提示词优化助手。请将当前提示词按用户要求重写为一段简体中文自然语言提示词，"
                        "用于 Gemini Imagen 生图。\n"
                        "要求：\n"
                        "1) 只输出最终提示词，不要解释。\n"
                        "2) 保持核心主体不变，按要求修改场景/动作/光影/氛围。\n"
                        "3) 不要输出标签列表。\n\n"
                        f"当前提示词：{self._current_prompt}\n"
                        f"用户要求：{description}"
                    ),
                }
            ]
            rewritten = await gemini_service.generate_simple_response(
                prompt="",
                generation_config={
                    "temperature": 0.75,
                    "max_output_tokens": 1800,
                },
                messages=rewrite_messages,
                model_name=llm_overrides["model_name"],
                api_url=llm_overrides["api_url"],
                api_key=llm_overrides["api_key"],
                api_format=llm_overrides["api_format"],
            )
            new_prompt = (rewritten or "").strip().strip('"').strip("'") or self._current_prompt

            await _regenerate_with_imagen(
                interaction=interaction,
                prompt=new_prompt,
                user_id=self._user_id,
                negative_prompt=self._negative_prompt,
                width=self._width,
                height=self._height,
                steps=self._steps,
                scale=self._scale,
                sampler=self._sampler,
                preset_name=self._preset_name,
                novelai_cost=self._novelai_cost,
                resolution=self._resolution,
                content_rating=self._content_rating,
                title_suffix="（AI 重写）",
            )
        except Exception as e:
            log.error(f"Imagen AI 重写失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"AI 重写失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass


class ToolImagenResultView(discord.ui.View):
    """从 NovelAI 切换到 Imagen 后的结果交互按钮。"""

    def __init__(
        self,
        prompt: str,
        user_id: Optional[str],
        cost: int,
        negative_prompt: Optional[str],
        width: int,
        height: int,
        steps: int,
        scale: float,
        sampler: str,
        preset_name: Optional[str],
        novelai_cost: int,
        resolution: str = "default",
        content_rating: str = "nsfw",
        timeout: float = 600,
    ):
        super().__init__(timeout=timeout)
        self._prompt = prompt
        self._user_id = user_id
        self._cost = cost
        self._negative_prompt = negative_prompt
        self._width = width
        self._height = height
        self._steps = steps
        self._scale = scale
        self._sampler = sampler
        self._preset_name = preset_name
        self._novelai_cost = novelai_cost
        self._resolution = resolution
        self._content_rating = content_rating

        model_options = _build_imagen_model_options(resolution, content_rating)
        if model_options:
            self._model_select = discord.ui.Select(
                placeholder="更换模型重新生成",
                options=model_options,
                min_values=1,
                max_values=1,
                row=1,
            )
            self._model_select.callback = self._on_model_select
            self.add_item(self._model_select)

    def _is_allowed(self, interaction: discord.Interaction) -> bool:
        if self._user_id and str(interaction.user.id) != str(self._user_id):
            if not interaction.user.guild_permissions.administrator:
                return False
        return True

    @discord.ui.button(label="重新生成", style=discord.ButtonStyle.primary, row=0)
    async def regenerate_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._is_allowed(interaction):
            await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        try:
            await _regenerate_with_imagen(
                interaction=interaction,
                prompt=self._prompt,
                user_id=self._user_id,
                negative_prompt=self._negative_prompt,
                width=self._width,
                height=self._height,
                steps=self._steps,
                scale=self._scale,
                sampler=self._sampler,
                preset_name=self._preset_name,
                novelai_cost=self._novelai_cost,
                resolution=self._resolution,
                content_rating=self._content_rating,
            )
        except Exception as e:
            log.error(f"Imagen 重新生成失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"重新生成失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="修改提示词", style=discord.ButtonStyle.secondary, row=0)
    async def edit_prompt_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._is_allowed(interaction):
            await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
            return
        modal = ToolImagenEditPromptModal(
            current_prompt=self._prompt,
            user_id=self._user_id,
            negative_prompt=self._negative_prompt,
            width=self._width,
            height=self._height,
            steps=self._steps,
            scale=self._scale,
            sampler=self._sampler,
            preset_name=self._preset_name,
            novelai_cost=self._novelai_cost,
            resolution=self._resolution,
            content_rating=self._content_rating,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="AI 重写提示词", style=discord.ButtonStyle.secondary, row=0)
    async def ai_rewrite_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._is_allowed(interaction):
            await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
            return
        modal = ToolImagenAIRewriteModal(
            current_prompt=self._prompt,
            user_id=self._user_id,
            negative_prompt=self._negative_prompt,
            width=self._width,
            height=self._height,
            steps=self._steps,
            scale=self._scale,
            sampler=self._sampler,
            preset_name=self._preset_name,
            novelai_cost=self._novelai_cost,
            resolution=self._resolution,
            content_rating=self._content_rating,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="切换到 NovelAI", style=discord.ButtonStyle.success, row=0)
    async def switch_to_novelai_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._is_allowed(interaction):
            await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        try:
            novelai_prompt = await _convert_imagen_prompt_to_novelai_prompt(
                self._prompt,
                force_rewrite=True,
            )
            await _regenerate_novelai(
                interaction=interaction,
                prompt=novelai_prompt,
                negative_prompt=self._negative_prompt,
                width=self._width,
                height=self._height,
                steps=self._steps,
                scale=self._scale,
                sampler=self._sampler,
                preset_name=self._preset_name,
                user_id=self._user_id,
                cost=self._novelai_cost,
                title_suffix="（从 Imagen 切换）",
            )
        except Exception as e:
            log.error(f"切换到 NovelAI 失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"切换失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="生成视频", style=discord.ButtonStyle.secondary, row=0)
    async def generate_video_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._is_allowed(interaction):
            await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
            return
        modal = GenerateVideoModal(image_prompt=self._prompt)
        await interaction.response.send_modal(modal)

    async def _on_model_select(self, interaction: discord.Interaction):
        if not self._is_allowed(interaction):
            await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        selected_value = self._model_select.values[0]
        resolution, rating = selected_value.split("|")

        try:
            await _regenerate_with_imagen(
                interaction=interaction,
                prompt=self._prompt,
                user_id=self._user_id,
                negative_prompt=self._negative_prompt,
                width=self._width,
                height=self._height,
                steps=self._steps,
                scale=self._scale,
                sampler=self._sampler,
                preset_name=self._preset_name,
                novelai_cost=self._novelai_cost,
                resolution=resolution,
                content_rating=rating,
            )
        except Exception as e:
            log.error(f"更换模型 Imagen 重新生成失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"重新生成失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass

    async def on_timeout(self):
        """超时后禁用所有按钮。"""
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True
        try:
            if self.message:
                await self.message.edit(view=self)
        except Exception:
            pass


class ArtistPresetSwitchView(discord.ui.View):
    """切换画师串并快捷重新生成。"""

    def __init__(
        self,
        prompt: str,
        negative_prompt: Optional[str],
        width: int,
        height: int,
        steps: int,
        scale: float,
        sampler: str,
        current_preset_name: Optional[str],
        user_id: Optional[str],
        cost: int,
        user_presets: List[dict],
        admin_presets: List[dict],
        current_artist_string: Optional[str] = None,
    ):
        super().__init__(timeout=120)
        self._prompt = str(prompt or "").strip()
        self._negative_prompt = negative_prompt
        self._width = width
        self._height = height
        self._steps = steps
        self._scale = scale
        self._sampler = sampler
        self._current_preset_name = current_preset_name
        self._user_id = user_id
        self._cost = cost
        normalized_current_artist = str(current_artist_string or "").strip()
        self._current_artist_string = normalized_current_artist or None

        self._user_presets = user_presets
        self._admin_presets = admin_presets
        self._preset_lookup: Dict[str, dict] = {}

        options = [
            discord.SelectOption(
                label="清除画师串并重生",
                value="none",
                description="不拼接任何画师串前缀",
                emoji="🚫",
            ),
            discord.SelectOption(
                label="使用默认画师串并重生",
                value="default",
                description="使用系统默认画师串前缀",
                emoji="⚙️",
            ),
        ]

        for preset in user_presets:
            if len(options) >= 25:
                break
            preset_id = preset.get("id")
            value = f"user:{preset_id}"
            artist_preview = str(preset.get("artist_string", ""))
            description = artist_preview[:80] + ("..." if len(artist_preview) > 80 else "")
            options.append(
                discord.SelectOption(
                    label=f"用户/{preset.get('name', '未命名预设')}"[:100],
                    value=value[:100],
                    description=(description or "用户预设")[:100],
                    emoji="👤",
                )
            )
            self._preset_lookup[value] = {"scope": "user", **preset}

        if len(options) < 25:
            for preset in admin_presets:
                if len(options) >= 25:
                    break
                preset_id = preset.get("id")
                value = f"admin:{preset_id}"
                artist_preview = str(preset.get("artist_string", ""))
                description = artist_preview[:80] + ("..." if len(artist_preview) > 80 else "")
                options.append(
                    discord.SelectOption(
                        label=f"管理员/{preset.get('name', '未命名预设')}"[:100],
                        value=value[:100],
                        description=(description or "管理员预设")[:100],
                        emoji="🛡️",
                    )
                )
                self._preset_lookup[value] = {"scope": "admin", **preset}

        select = discord.ui.Select(
            placeholder="选择画师串并重新生成",
            options=options,
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)

        selected_value = interaction.data["values"][0]
        default_artist = str(NOVELAI_CONFIG.get("DEFAULT_ARTIST_STRING", "") or "").strip()

        known_artist_strings: List[Optional[str]] = [self._current_artist_string, default_artist]
        for preset in self._user_presets:
            known_artist_strings.append(str(preset.get("artist_string") or "").strip() or None)
        for preset in self._admin_presets:
            known_artist_strings.append(str(preset.get("artist_string") or "").strip() or None)

        base_prompt = _strip_artist_tokens(self._prompt, known_artist_strings)
        if not base_prompt:
            base_prompt = self._prompt

        target_preset_name: Optional[str] = None
        target_negative_prompt = self._negative_prompt
        target_artist_string: Optional[str] = None

        if selected_value == "none":
            target_preset_name = "无画师串"
        elif selected_value == "default":
            target_preset_name = "默认"
            target_artist_string = default_artist or None
        else:
            preset = self._preset_lookup.get(selected_value)
            if not preset:
                await interaction.followup.send("预设不存在或已失效，请重试。", ephemeral=True)
                return

            target_artist_string = str(preset.get("artist_string") or "").strip() or None
            preset_name = str(preset.get("name") or "未命名预设")
            if preset.get("scope") == "admin":
                target_preset_name = f"管理员/{preset_name}"
            else:
                target_preset_name = preset_name
                preset_negative = str(preset.get("negative_prompt") or "").strip()
                if (not target_negative_prompt) and preset_negative:
                    target_negative_prompt = preset_negative

        await _regenerate_novelai(
            interaction=interaction,
            prompt=base_prompt,
            negative_prompt=target_negative_prompt,
            width=self._width,
            height=self._height,
            steps=self._steps,
            scale=self._scale,
            sampler=self._sampler,
            preset_name=target_preset_name,
            user_id=self._user_id,
            cost=self._cost,
            artist_string=target_artist_string,
            title_suffix="（切换画师串）",
        )


class NovelAIResultView(discord.ui.View):
    """NovelAI 生成结果的交互按钮"""

    def __init__(
        self,
        prompt: str,
        negative_prompt: Optional[str],
        width: int,
        height: int,
        steps: int,
        scale: float,
        sampler: str,
        preset_name: Optional[str],
        user_id: Optional[str],
        cost: int,
        artist_string: Optional[str] = None,
    ):
        super().__init__(timeout=600)  # 10 分钟超时
        normalized_artist = str(artist_string or "").strip()
        self._artist_string = normalized_artist or None

        normalized_prompt = str(prompt or "").strip()
        if self._artist_string:
            stripped_prompt = _strip_artist_tokens(normalized_prompt, [self._artist_string])
            self._prompt = stripped_prompt or normalized_prompt
        else:
            self._prompt = normalized_prompt

        self._negative_prompt = negative_prompt
        self._width = width
        self._height = height
        self._steps = steps
        self._scale = scale
        self._sampler = sampler
        self._preset_name = preset_name
        self._user_id = user_id
        self._cost = cost

    @discord.ui.button(label="重新生成", style=discord.ButtonStyle.primary, row=0)
    async def regenerate_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """用相同 prompt 但新种子重新生成"""
        # 权限检查：只有原始请求者或管理员可以操作
        if self._user_id and str(interaction.user.id) != str(self._user_id):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
                return

        await interaction.response.defer(thinking=True)
        try:
            await _regenerate_novelai(
                interaction=interaction,
                prompt=self._prompt,
                negative_prompt=self._negative_prompt,
                width=self._width,
                height=self._height,
                steps=self._steps,
                scale=self._scale,
                sampler=self._sampler,
                preset_name=self._preset_name,
                user_id=self._user_id,
                cost=self._cost,
                artist_string=self._artist_string,
            )
        except Exception as e:
            log.error(f"重新生成失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"重新生成失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="修改提示词", style=discord.ButtonStyle.secondary, row=0)
    async def edit_prompt_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """弹出 Modal 让用户编辑提示词"""
        if self._user_id and str(interaction.user.id) != str(self._user_id):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
                return

        modal = EditPromptModal(
            current_prompt=self._prompt,
            current_negative=self._negative_prompt,
            width=self._width,
            height=self._height,
            steps=self._steps,
            scale=self._scale,
            sampler=self._sampler,
            preset_name=self._preset_name,
            user_id=self._user_id,
            cost=self._cost,
            current_artist_string=self._artist_string,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="切换到 Imagen", style=discord.ButtonStyle.success, row=0)
    async def switch_to_imagen_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """使用 Gemini Imagen 重新生成"""
        if self._user_id and str(interaction.user.id) != str(self._user_id):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
                return

        await interaction.response.defer(thinking=True)
        try:
            prompt_for_imagen = _compose_prompt_with_artist(self._prompt, self._artist_string)
            await _regenerate_with_imagen(
                interaction=interaction,
                prompt=prompt_for_imagen,
                user_id=self._user_id,
                negative_prompt=self._negative_prompt,
                width=self._width,
                height=self._height,
                steps=self._steps,
                scale=self._scale,
                sampler=self._sampler,
                preset_name=self._preset_name,
                novelai_cost=self._cost,
                force_prompt_rewrite=True,
            )
        except Exception as e:
            log.error(f"切换到 Imagen 失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"切换失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="切换画师串重生", style=discord.ButtonStyle.secondary, emoji="🎨", row=1)
    async def switch_artist_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """快捷切换用户/管理员画师串并重新生成。"""
        if self._user_id and str(interaction.user.id) != str(self._user_id):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
                return

        from src.chat.utils.database import chat_db_manager

        parsed_user_id: Optional[int] = None
        if self._user_id:
            try:
                parsed_user_id = int(self._user_id)
            except (ValueError, TypeError):
                parsed_user_id = None

        user_presets: List[dict] = []
        admin_presets: List[dict] = []

        if parsed_user_id is not None:
            try:
                user_presets = await chat_db_manager.get_novelai_presets(parsed_user_id)
            except Exception as e:
                log.warning(f"读取用户画师串预设失败: {e}")

        try:
            admin_presets = await chat_db_manager.get_novelai_admin_presets()
        except Exception as e:
            log.warning(f"读取管理员画师串预设失败: {e}")

        default_artist = str(NOVELAI_CONFIG.get("DEFAULT_ARTIST_STRING", "") or "").strip()
        if not user_presets and not admin_presets and not default_artist:
            await interaction.response.send_message("当前没有可切换的画师串（用户/管理员/默认均为空）。", ephemeral=True)
            return

        view = ArtistPresetSwitchView(
            prompt=self._prompt,
            negative_prompt=self._negative_prompt,
            width=self._width,
            height=self._height,
            steps=self._steps,
            scale=self._scale,
            sampler=self._sampler,
            current_preset_name=self._preset_name,
            user_id=self._user_id,
            cost=self._cost,
            user_presets=user_presets,
            admin_presets=admin_presets,
            current_artist_string=self._artist_string,
        )
        await interaction.response.send_message("请选择要切换的画师串：", view=view, ephemeral=True)

    @discord.ui.button(label="查看提示词", style=discord.ButtonStyle.secondary, emoji="📋", row=1)
    async def view_prompt_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """以 ephemeral 消息展示完整提示词"""
        pages = _build_prompt_preview_pages(
            artist_string=self._artist_string,
            positive_prompt=self._prompt,
            negative_prompt=self._negative_prompt,
        )
        await interaction.response.send_message(pages[0], ephemeral=True)
        for page in pages[1:]:
            await interaction.followup.send(page, ephemeral=True)

    @discord.ui.button(label="AI 重写", style=discord.ButtonStyle.secondary, row=1)
    async def ai_rewrite_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """让 AI 根据用户描述重新生成 prompt"""
        if self._user_id and str(interaction.user.id) != str(self._user_id):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
                return

        modal = ToolAIRewriteModal(
            current_prompt=self._prompt,
            negative_prompt=self._negative_prompt,
            width=self._width,
            height=self._height,
            steps=self._steps,
            scale=self._scale,
            sampler=self._sampler,
            preset_name=self._preset_name,
            user_id=self._user_id,
            cost=self._cost,
            artist_string=self._artist_string,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="生成视频", style=discord.ButtonStyle.secondary, row=1)
    async def generate_video_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """基于当前结果图生成视频。"""
        if self._user_id and str(interaction.user.id) != str(self._user_id):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
                return
        prompt_for_video = _compose_prompt_with_artist(self._prompt, self._artist_string)
        modal = GenerateVideoModal(image_prompt=prompt_for_video)
        await interaction.response.send_modal(modal)

    async def on_timeout(self):
        """超时后禁用所有按钮"""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        # 尝试编辑原消息来禁用按钮
        try:
            if self.message:
                await self.message.edit(view=self)
        except Exception:
            pass


async def _regenerate_novelai(
    interaction: discord.Interaction,
    prompt: str,
    negative_prompt: Optional[str],
    width: int,
    height: int,
    steps: int,
    scale: float,
    sampler: str,
    preset_name: Optional[str],
    user_id: Optional[str],
    cost: int,
    artist_string: Optional[str] = None,
    title_suffix: str = "（重新生成）",
):
    """内部函数：使用 NovelAI 重新生成图片"""
    from src.chat.features.novelai_generation.services.novelai_service import novelai_service
    from src.chat.config.chat_config import NOVELAI_CONFIG
    from src.chat.features.odysseia_coin.service.coin_service import coin_service
    from src.chat.utils.database import chat_db_manager

    base_prompt = str(prompt or "").strip()
    normalized_artist = str(artist_string or "").strip()
    effective_artist_string = normalized_artist or None
    final_prompt = _compose_prompt_with_artist(base_prompt, effective_artist_string)

    if not final_prompt:
        await interaction.followup.send("提示词不能为空！", ephemeral=True)
        return

    # 按钮实际操作者付费（管理员代点时扣管理员）
    billing_user_id = int(interaction.user.id)

    # 检查封禁与余额
    ban_status = await chat_db_manager.get_image_generation_ban_status(billing_user_id)
    if ban_status.get("is_banned"):
        remaining_text = ban_status.get("remaining_text", "未知时长")
        await interaction.followup.send(
            f"你的绘图功能当前已被临时禁用，剩余封禁时长：{remaining_text}",
            ephemeral=True,
        )
        return

    if cost > 0:
        balance = await coin_service.get_balance(billing_user_id)
        if balance < cost:
            await interaction.followup.send(
                f"月光币不足（需要 {cost}，当前 {balance}）",
                ephemeral=True,
            )
            return

    # 生成图片（新种子）
    result = await novelai_service.generate_image(
        prompt=final_prompt,
        negative_prompt=negative_prompt,
        width=width,
        height=height,
        steps=steps,
        scale=scale,
        sampler=sampler,
        seed=None,  # 新种子
    )

    if result is None:
        await interaction.followup.send("NovelAI 图片生成失败，请稍后重试。", ephemeral=True)
        return

    # 扣费
    if cost > 0:
        try:
            await coin_service.remove_coins(
                billing_user_id, cost, f"NovelAI重新生图: {final_prompt[:25]}..."
            )
        except Exception as e:
            log.error(f"扣除月光币失败: {e}")

    # 构建 Embed
    embed = discord.Embed(
        title=f"NovelAI 图片生成{title_suffix}",
        color=0x9B59B6,
    )
    embed.set_author(
        name=interaction.user.display_name,
        icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
    )
    # 生成信息（紧凑排列，提示词通过按钮查看）
    model_name = result.model or NOVELAI_CONFIG.get("MODEL", "unknown")
    if preset_name:
        embed.add_field(name="预设", value=preset_name, inline=True)
    embed.add_field(name="种子", value=str(result.seed), inline=True)
    embed.add_field(
        name="参数",
        value=f"{result.width}x{result.height} | {steps}步 | CFG {scale}",
        inline=True,
    )
    embed.add_field(
        name="人物外貌摘要",
        value=_build_prompt_summary_for_embed(
            prompt=final_prompt,
            negative_prompt=negative_prompt,
            artist_string=effective_artist_string,
        ),
        inline=False,
    )
    embed.set_footer(
        text=f"消耗 {cost} 月光币 | {sampler} | {model_name}"
    )

    image_file = discord.File(
        io.BytesIO(result.image_data),
        filename="novelai_generated.png",
        spoiler=True,
    )
    # 不使用 embed.set_image()，让 spoiler 遮罩正常生效

    prompt_without_artist = _strip_artist_tokens(final_prompt, [effective_artist_string])
    if not prompt_without_artist:
        prompt_without_artist = base_prompt or final_prompt

    # 创建新的交互按钮
    new_view = NovelAIResultView(
        prompt=prompt_without_artist,
        negative_prompt=negative_prompt,
        width=width,
        height=height,
        steps=steps,
        scale=scale,
        sampler=sampler,
        preset_name=preset_name,
        user_id=user_id,
        cost=cost,
        artist_string=effective_artist_string,
    )

    sent_message = await interaction.followup.send(
        embed=embed, file=image_file, view=new_view, wait=True
    )
    if sent_message:
        await chat_db_manager.register_generated_image_message(
            message_id=sent_message.id,
            user_id=billing_user_id,
            guild_id=sent_message.guild.id if sent_message.guild else None,
            channel_id=sent_message.channel.id,
        )
    log.info(f"NovelAI 重新生成成功, 种子: {result.seed}")


async def _regenerate_with_imagen(
    interaction: discord.Interaction,
    prompt: str,
    user_id: Optional[str],
    negative_prompt: Optional[str] = None,
    width: int = 832,
    height: int = 1216,
    steps: int = 28,
    scale: float = 5.0,
    sampler: str = "k_euler_ancestral",
    preset_name: Optional[str] = None,
    novelai_cost: Optional[int] = None,
    resolution: str = "default",
    content_rating: str = "nsfw",
    title_suffix: str = "（从 NovelAI 切换）",
    force_prompt_rewrite: bool = False,
):
    """内部函数：使用 Gemini Imagen 重新生成图片。"""
    from src.chat.features.image_generation.services.gemini_imagen_service import gemini_imagen_service
    from src.chat.config.chat_config import GEMINI_IMAGEN_CONFIG
    from src.chat.features.odysseia_coin.service.coin_service import coin_service
    from src.chat.utils.database import chat_db_manager

    cost_per_image = int(GEMINI_IMAGEN_CONFIG.get("IMAGE_GENERATION_COST", 1))
    effective_novelai_cost = (
        int(novelai_cost) if novelai_cost is not None else int(NOVELAI_CONFIG.get("IMAGE_GENERATION_COST", 5))
    )

    if not gemini_imagen_service.is_available():
        await interaction.followup.send("Gemini Imagen 服务当前不可用。", ephemeral=True)
        return

    # 按钮实际操作者付费（管理员代点时扣管理员）
    billing_user_id = int(interaction.user.id)

    ban_status = await chat_db_manager.get_image_generation_ban_status(billing_user_id)
    if ban_status.get("is_banned"):
        remaining_text = ban_status.get("remaining_text", "未知时长")
        await interaction.followup.send(
            f"你的绘图功能当前已被临时禁用，剩余封禁时长：{remaining_text}",
            ephemeral=True,
        )
        return

    if cost_per_image > 0:
        balance = await coin_service.get_balance(billing_user_id)
        if balance < cost_per_image:
            await interaction.followup.send(
                f"月光币不足（需要 {cost_per_image}，当前 {balance}）",
                ephemeral=True,
            )
            return

    imagen_prompt = await _convert_tag_prompt_to_imagen_prompt(
        prompt,
        force_rewrite=force_prompt_rewrite,
    )

    result = await gemini_imagen_service.generate_single_image(
        prompt=imagen_prompt,
        negative_prompt=None,
        aspect_ratio="3:4",
        resolution=resolution,
        content_rating=content_rating,
    )

    if not result:
        await interaction.followup.send("Gemini Imagen 图片生成失败，请稍后重试。", ephemeral=True)
        return

    if cost_per_image > 0:
        try:
            await coin_service.remove_coins(
                billing_user_id, cost_per_image, f"Imagen生图(切换): {imagen_prompt[:25]}..."
            )
        except Exception as e:
            log.error(f"扣除月光币失败: {e}")

    imagen_model_name = gemini_imagen_service._get_model_for_resolution(
        resolution=resolution, is_edit=False, content_rating=content_rating
    )
    resolution_text = "标准分辨率" if resolution == "default" else f"{resolution.upper()} 分辨率"

    embed = discord.Embed(
        title=f"Gemini Imagen 图片生成{title_suffix}",
        color=0x4285F4,
    )
    embed.set_author(
        name=interaction.user.display_name,
        icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
    )
    embed.set_footer(
        text=(
            f"消耗 {cost_per_image} 月光币 | 模型: {imagen_model_name} | "
            f"{resolution_text} | {content_rating.upper()} | 引擎: Gemini Imagen"
        )
    )

    image_file = discord.File(
        io.BytesIO(result),
        filename="imagen_generated.png",
        spoiler=True,
    )

    view = ToolImagenResultView(
        prompt=imagen_prompt,
        user_id=user_id,
        cost=cost_per_image,
        negative_prompt=negative_prompt,
        width=width,
        height=height,
        steps=steps,
        scale=scale,
        sampler=sampler,
        preset_name=preset_name,
        novelai_cost=effective_novelai_cost,
        resolution=resolution,
        content_rating=content_rating,
    )

    sent_message = await interaction.followup.send(embed=embed, file=image_file, view=view, wait=True)
    if sent_message:
        view.message = sent_message
        await chat_db_manager.register_generated_image_message(
            message_id=sent_message.id,
            user_id=billing_user_id,
            guild_id=sent_message.guild.id if sent_message.guild else None,
            channel_id=sent_message.channel.id,
        )
    log.info(
        f"已切换到 Imagen 生成图片, resolution={resolution}, rating={content_rating}, model={imagen_model_name}"
    )
