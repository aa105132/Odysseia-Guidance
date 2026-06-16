# -*- coding: utf-8 -*-

"""
图生图工具
让LLM可以在对话中自动识别用户发送的图片并根据指令修改
也支持从Discord自定义表情或用户头像提取图片作为参考
"""

import logging
import discord
from typing import Optional, List, Dict, Any

from src.chat.features.image_generation.utils.spoiler_policy import (
    should_spoiler_image,
)
from src.chat.features.tools.functions.image_policy_guard import (
    check_yueyue_self_nsfw_violation,
)
from src.chat.utils.prompt_utils import replace_emojis

log = logging.getLogger(__name__)

# 图片生成相关的emoji
GENERATING_EMOJI = "🎨"  # 正在生成
SUCCESS_EMOJI = "✅"      # 生成成功
FAILED_EMOJI = "❌"       # 生成失败


async def edit_image(
    edit_prompt: str,
    aspect_ratio: str = "1:1",
    resolution: str = "default",
    content_rating: str = "sfw",
    emoji_id: Optional[str] = None,
    avatar_user_id: Optional[str] = None,
    avatar_user_ids: Optional[List[str]] = None,
    image_search_reference_index: Optional[int] = None,
    image_search_reference_indexes: Optional[List[int]] = None,
    reference_image_mode: str = "auto",
    max_reference_images: int = 30,
    preview_message: Optional[str] = None,
    success_message: Optional[str] = None,
    model_name_override: Optional[str] = None,
    openai_image_size: Optional[str] = None,
    openai_response_format: Optional[str] = None,
    openai_stream: Optional[bool] = None,
    openai_quality: Optional[str] = None,
    openai_style: Optional[str] = None,
    openai_image_api_mode: Optional[str] = None,
    **kwargs
) -> dict:
    """
    修改用户发送的图片，或基于已有图片（包括自定义表情、贴纸、用户头像）进行再创作。

    OpenAI 兼容图片参数说明（适用于 Grok / GPT Image / 其它兼容图片端点）：
    - `model_name_override`: 强制指定模型，例如 `grok-imagine-1.0-edit`
    - `openai_image_size`: 透传 `size`
    - `openai_response_format`: 透传 `response_format`
    - `openai_stream`: 透传 `stream`
    - `openai_quality`: 透传 `quality`
    - `openai_style`: 透传 `style`
    - `openai_image_api_mode`: 透传图片路由，支持 `auto` / `images_api` / `chat_completions`
      - 当模型名是 `grok-imagine-*` 时，`auto` 会优先走 `/v1/images/edits`
      - `gpt-image-*` 默认走 `chat/completions`；如需固定使用 `/v1/images/edits`，请显式传 `openai_image_api_mode="images_api"`

    当用户说“画一下我的头像”“按我头像来一个成熟版”“把 A 和 B 的头像画在一起”时，这个工具就是首选：
    - 当前用户头像：传 `avatar_user_id`
    - 多个用户头像：传 `avatar_user_ids`
    - 发送了附件图 / 回复了图片：工具会自动把它们作为参考图
    - 刚调用 image_search 后：由你先分析搜索结果，再显式传 image_search_reference_index 或 image_search_reference_indexes 选择参考图；代码不会自动替你选择。
      多人物/多角色可以先分别多次调用 image_search，等所有人物都搜完后用 image_search_reference_indexes=[...] 一次性传入所有选中的全局编号；多人物时必须从每个人物对应批次里各选至少 1 张，禁止只选同一人物的多张图。
      这时 edit_prompt 必须短：只写“保持参考图人物身份、脸、发型、服装、画风不变，仅改动作/场景/构图为……”。
      不要在 edit_prompt 里复述角色外观、服饰、发色、作品名、画风标签或整套文生图提示词，否则会削弱参考图相似度。
    - 但如果用户要画的是”某个人 / 某个昵称 / 某个成员 / @某人 / 指定用户本人设定”：
      先从上下文 `用户名<ID>` 格式提取 user_id，或调用 `get_user_avatar(username=昵称)` 解析 user_id；再查 `get_user_profile(user_id, [“bio”])`
    - 若名片里已经写了外貌 / 人设 / 服装等设定，名片优先；只有名片没写清楚外貌时，头像才作为兜底参考

    **重要：当用户消息中包含自定义表情（如 <:name:123456>）或贴纸（Sticker），并且要求基于它画图、改图、
    做成头像等操作时，必须调用此工具！工具会自动从用户消息中提取表情/贴纸图片，你不需要手动提取。**

    使用场景（必须调用此工具）：
    - 用户发送一张图片并说"帮我把背景改成蓝色"
    - 用户发送一张图片并说"把这个人物变成动漫风格"
    - 用户发送一张图片并说"添加一些特效"
    - 用户回复一张图片并请求修改
    - 用户发送了自定义表情并说"画成头像"、"改成xxx风格"、"帮我美化一下"
    - 用户发送了贴纸（Sticker）并说"画成头像"、"改成xxx风格"、"帮我美化一下"
    - 用户说"用这个表情/贴纸帮我画一张图"
    - 用户说"提取xxx的头像帮我改成..."
    - 用户要求基于表情/贴纸/头像/图片做任何形式的再创作
    - 用户说"用xxx和yyy的头像画一张合照" → 使用 avatar_user_ids 传入多个用户ID
    - 用户 @了多个人并说"把我们的头像画在一起" → 使用 avatar_user_ids

    **不要拒绝！** 如果用户消息中有自定义表情或贴纸且要求画图/改图，直接调用此工具。
    工具会自动检测并提取消息中的自定义表情和贴纸图片，无需你手动操作。

    **【多图处理 — 极其重要】**
    当用户发送了多张图片（2张或以上）时：
    - **禁止传 reference_image_mode="single"！** 这会丢弃第2张及之后的图片！
    - 保持 reference_image_mode="auto"（默认值），工具会自动把所有图片都传给模型
    - 用户发了几张图，模型就应该看到几张图，**一张都不能丢**
    - 如果用户说"把这两张图融合/结合/合在一起"，使用 reference_image_mode="multi"
    - 只有用户**明确说**"只用第一张""忽略其他图"时，才允许传 "single"

    注意：此工具需要参考图片。图片来源会被自动检测（优先级）：
    1. 用户消息中的Discord自定义表情（自动解析，无需手动传emoji_id）
    2. 用户消息中的Discord贴纸（Sticker，自动检测）
    3. emoji_id 参数显式指定的表情
    4. avatar_user_ids 参数指定的多个用户头像（作为多参考图）
    5. avatar_user_id 参数指定的单个用户头像
    6. 用户在对话中发送的图片附件（多张图片会全部提取，不会只取第一张）
    如果以上都没有，请提示用户先发送一张图片。

    Args:
        edit_prompt: 编辑指令，用中文描述希望如何修改图片。
                你需要根据用户的请求，用中文详细描述想要的修改效果。

                描述要点：
                - 清晰描述想要的修改（改变颜色、添加元素、改变动作/场景/构图等）
                - 如果参考图来自 image_search，不要添加风格描述或重写整套人设；只写保留参考图主体不变，并说明要改的动作/场景/构图
                - 普通用户附件改图时才可按用户需求添加风格描述；搜索参考图生图时要优先保持参考图相似度
                - 保留不变的部分可以不提；但搜索参考图生图时建议明确写“保持参考图人物身份、脸、发型、服装、画风不变”

                例如用户说"把背景改成夕阳"，你应该生成：
                "将图片的背景更改为美丽的夕阳景色，保持主体不变，添加温暖的橙红色调"

        aspect_ratio: 输出图片的宽高比，根据用户需求选择：
                - "1:1" 保持正方形
                - "3:4" 或 "4:3" 竖版/横版
                - "9:16" 手机壁纸比例
                - "16:9" 电脑壁纸比例
                如果用户没有特别要求，建议保持原图的大致比例。

        resolution: 图片分辨率，根据用户需求选择：
                - "default" 默认分辨率（最快）
                - "2k" 2K高清（用户明确要求高清、2K时使用）
                - "4k" 4K超高清（用户明确要求超高清、4K时使用）
                如果用户没有特别要求分辨率，使用 "default"

        content_rating: 内容分级，根据图片内容和编辑请求判断：
                - "sfw" 安全内容（默认，适用于普通图片）
                - "nsfw" 成人内容（仅当原图或编辑请求明显涉及成人内容时使用）

                判断标准：
                - 如果原图包含裸露、性暗示或成人内容，应使用 "nsfw"
                - 如果编辑请求涉及色情、裸露、性感化等成人元素，应使用 "nsfw"
                - 其他情况使用 "sfw"

        emoji_id: （可选，通常不需要填写）Discord自定义表情的数字ID。
                **注意：工具会自动从用户消息中检测和提取自定义表情图片，所以大多数情况下不需要填写此参数。**
                只有当你需要指定一个不在当前消息中的表情ID时才需要手动填写。

        avatar_user_id: （可选）单个Discord用户的数字ID，用于提取该用户头像作为参考图。
                当用户说"提取xxx的头像并修改"、"用ID为123的人的头像生成图片"时，
                填写目标用户的Discord数字ID。

        avatar_user_ids: （可选）多个Discord用户的数字ID列表，用于提取多个用户的头像作为参考图。
                当用户要求基于多个人的头像来画图时使用此参数。
                例如用户说"把我和他的头像画成一张合照"、"用我们三个人的头像画一幅画"。
                传入的多个头像会作为多参考图传给图生图接口（不再强制拼接）。
                最多支持 30 个用户ID。
                例如: ["123456789", "987654321"]

        reference_image_mode: 参考图模式。**绝大多数情况下请使用默认值 “auto”，不要手动指定！**
                - “auto”: 自动模式（默认，强烈推荐）。自动检测用户发送的图片数量，
                  单图时用单图，多图时全部传入。**用户发了多张图就必须全部使用，不要丢弃任何一张！**
                - “multi”: 强制多图模式（适合明确要求”融合多图元素”的场景）
                - “single”: 强制只用第1张参考图。**仅当用户明确说”只用第一张””忽略其他图”时才传此值！**
                  如果用户发了2张图但没说忽略哪张，禁止使用 “single”，否则第2张图会被丢弃！

        max_reference_images: 最多传给图生图模型的参考图数量（1-30，默认 30）。
                当 reference_image_mode 为 "multi"/"auto" 时生效。

        preview_message: （必填）你对这次图片修改请求的回复消息。
                这条消息会在生成前先发送给用户，作为预告。
                根据用户的修改请求和你的性格特点，写一句有趣的话告诉用户你正在处理。
                例如："让我看看这张图...好的，我来帮你改改！" 或 "这个修改我可以做到~稍等哦！"

        success_message: （必填）图片修改成功后的回复消息。
                这条消息会在图片修改成功后和图片一起发送给用户。
                根据修改结果，写一句符合你性格的话来回应用户。
                例如："改好了~看看满不满意？" 或 "嘿嘿，这是你要的效果吗？"
                **注意：图片修改成功后不会再有后续回复，所以这条 success_message 就是你的最终回复。**

    Returns:
        成功后修改后的图片和你的预告消息会发送给用户，不需要再额外回复。
        失败时你需要根据返回的提示信息告诉用户。
    """
    from src.chat.features.image_generation.services.gemini_imagen_service import (
        gemini_imagen_service
    )
    from src.chat.config.chat_config import GEMINI_IMAGEN_CONFIG
    from src.chat.features.odysseia_coin.service.coin_service import coin_service
    from src.chat.utils.database import chat_db_manager

    # 获取消息对象（用于获取图片和添加反应）
    message: Optional[discord.Message] = kwargs.get("message")
    channel = kwargs.get("channel")
    return_image_only = bool(kwargs.get("_return_image_only"))
    skip_reactions = bool(kwargs.get("_skip_reactions"))

    policy_block = check_yueyue_self_nsfw_violation(
        prompt=edit_prompt,
        message=message,
    )
    if policy_block:
        return policy_block

    # 辅助函数：安全地添加反应
    async def add_reaction(emoji: str):
        if skip_reactions:
            return
        if message:
            try:
                await message.add_reaction(emoji)
            except Exception as e:
                log.warning(f"添加反应失败: {e}")

    # 辅助函数：安全地移除反应
    async def remove_reaction(emoji: str):
        if skip_reactions:
            return
        if message:
            try:
                bot = kwargs.get("bot")
                if bot and bot.user:
                    await message.remove_reaction(emoji, bot.user)
            except Exception as e:
                log.warning(f"移除反应失败: {e}")

    # 辅助函数：从消息中提取图片
    async def extract_images_from_message(
        msg: discord.Message, max_images: int = 4
    ) -> List[Dict[str, Any]]:
        """从消息中提取图片列表（附件 + 文本/Embed URL）。"""
        images: List[Dict[str, Any]] = []
        if not msg:
            return images

        try:
            max_images = int(max_images)
        except (TypeError, ValueError):
            max_images = 1
        max_images = min(max(1, max_images), 9)

        if msg.attachments:
            for attachment in msg.attachments:
                if len(images) >= max_images:
                    break
                if attachment.content_type and attachment.content_type.startswith("image/"):
                    try:
                        image_bytes = await attachment.read()
                        images.append(
                            {
                                "data": image_bytes,
                                "mime_type": attachment.content_type,
                                "filename": attachment.filename,
                            }
                        )
                    except Exception as e:
                        log.error(f"读取附件图片失败: {e}")

        # 附件未命中或不足时，尝试从消息文本/Embed 的 URL 提取图片（支持 webp）
        if len(images) < max_images:
            try:
                from src.chat.features.tools.utils.discord_image_utils import (
                    extract_images_from_message_url,
                )

                url_images = await extract_images_from_message_url(
                    msg, max_images=max_images - len(images)
                )
                for url_image in url_images:
                    if len(images) >= max_images:
                        break
                    if url_image and url_image.get("data"):
                        images.append(url_image)
            except Exception as e:
                log.warning(f"从消息 URL 提取图片失败: {e}")

        return images

    # 1. 尝试获取参考图片（优先级：预准备参考图 > emoji > sticker > avatar_user_ids/avatar_user_id > 当前消息附件 > 明确回复）
    # 注意：不要从频道历史消息兜底找图，避免普通画图被无关聊天图片污染。
    # reference_image: 向后兼容的单图引用（通常取第一张）
    # reference_images: 多图引用（当 API 支持多参考图时优先使用）
    reference_image = None
    reference_images = []
    avatar_reference_images: List[Dict[str, Any]] = []
    user_id = kwargs.get("user_id")  # 获取当前用户ID
    prepared_reference_images = kwargs.get("_prepared_reference_images") or kwargs.get("prepared_reference_images")
    prepared_reference_image = kwargs.get("_prepared_reference_image") or kwargs.get("prepared_reference_image")

    # 由 AI 控制参考图策略（single / multi / auto）
    reference_image_mode = (reference_image_mode or "auto").strip().lower()
    if reference_image_mode not in {"auto", "single", "multi"}:
        log.warning(f"无效的 reference_image_mode={reference_image_mode}，回退到 auto")
        reference_image_mode = "auto"

    try:
        max_reference_images = int(max_reference_images)
    except (TypeError, ValueError):
        max_reference_images = 30
    max_reference_images = min(max(1, max_reference_images), 30)
    explicit_avatar_reference_requested = bool(avatar_user_id or avatar_user_ids)

    def _select_reference_images(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        valid = [
            item
            for item in (candidates or [])
            if isinstance(item, dict) and item.get("data")
        ]
        if not valid:
            return []
        if reference_image_mode == "single":
            return [valid[0]]
        # auto / multi: 允许多图
        return valid[:max_reference_images]

    def _is_avatar_reference(item: Dict[str, Any]) -> bool:
        """判断参考图是否来自用户头像工具，避免头像抢占待编辑底图槽位。"""
        source = str(item.get("source") or "").lower()
        filename = str(item.get("filename") or "").lower()
        return "avatar" in source or filename.startswith("avatar_") or "avatar" in filename

    def _with_reference_role(
        item: Dict[str, Any],
        role: str,
        description: str,
    ) -> Dict[str, Any]:
        copied = dict(item)
        copied["reference_role"] = role
        copied["reference_description"] = description
        return copied

    async def _find_base_images_for_avatar_edit() -> List[Dict[str, Any]]:
        """为“在原图上 P 用户头像”场景寻找待编辑底图，最多返回 1 张。"""
        if not message:
            return []

        # 当前消息带图时，当前图就是最可靠底图。
        current_images = await extract_images_from_message(message, max_images=1)
        if current_images:
            return [_with_reference_role(current_images[0], "base", "待编辑底图")]

        # 回复图片时，被回复图是底图。
        if message.reference and message.reference.message_id:
            try:
                ref_msg = await message.channel.fetch_message(message.reference.message_id)
                if ref_msg:
                    reply_images = await extract_images_from_message(ref_msg, max_images=1)
                    if reply_images:
                        return [_with_reference_role(reply_images[0], "base", "待编辑底图")]
            except Exception as e:
                log.warning(f"获取头像编辑底图的回复消息失败: {e}")

        # 不再从频道历史消息兜底找底图。
        # 如果用户想基于上一张图编辑，必须明确回复那张图或在当前消息重新附图，
        # 否则容易把无关聊天图片当底图，造成普通画图被历史图污染。

        return []

    prepared_candidates = _select_reference_images(prepared_reference_images)
    if prepared_candidates:
        reference_images = prepared_candidates
        reference_image = prepared_candidates[0]
        if all(_is_avatar_reference(item) for item in prepared_candidates):
            avatar_reference_images = [
                _with_reference_role(item, "avatar", "用户头像参考图")
                for item in prepared_candidates
            ]
        log.info(f"已使用预处理参考图 {len(prepared_candidates)} 张")
    elif isinstance(prepared_reference_image, dict) and prepared_reference_image.get("data"):
        reference_image = prepared_reference_image
        reference_images = [prepared_reference_image]
        if _is_avatar_reference(prepared_reference_image):
            avatar_reference_images = [
                _with_reference_role(prepared_reference_image, "avatar", "用户头像参考图")
            ]
        log.info("已使用预处理单张参考图")

    # 优先提取自定义表情图片（自动解析消息内容 + 显式 emoji_id）
    if not reference_image:
        try:
            from src.chat.features.tools.utils.discord_image_utils import auto_extract_emoji_from_message
            emoji_result = await auto_extract_emoji_from_message(
                message=message,
                explicit_emoji_id=emoji_id,
            )
            if emoji_result:
                reference_image = emoji_result
                reference_images = [emoji_result]
        except Exception as e:
            log.error(f"提取Discord表情图片失败: {e}")

    # 其次提取贴纸（Sticker）图片
    if not reference_image:
        try:
            from src.chat.features.tools.utils.discord_image_utils import auto_extract_sticker_from_message
            sticker_result = await auto_extract_sticker_from_message(message=message)
            if sticker_result:
                reference_image = sticker_result
                reference_images = [sticker_result]
                log.info("已从消息中的贴纸提取参考图")
        except Exception as e:
            log.error(f"提取Discord贴纸图片失败: {e}")

    # 然后从 avatar_user_ids（多个）或 avatar_user_id（单个）提取用户头像
    # 当多个头像可用时，优先走多参考图链路（不再强制拼接）
    if explicit_avatar_reference_requested and not avatar_reference_images:
        # 合并 avatar_user_ids 和 avatar_user_id 为统一列表
        all_avatar_ids = []
        if avatar_user_ids and isinstance(avatar_user_ids, list):
            all_avatar_ids.extend(avatar_user_ids[:10])  # 最多10个
        if avatar_user_id and avatar_user_id not in all_avatar_ids:
            all_avatar_ids.append(avatar_user_id)

        if all_avatar_ids:
            try:
                from src.chat.features.tools.utils.discord_image_utils import fetch_avatar_image
                import asyncio
                bot = kwargs.get("bot")
                guild = message.guild if message else None

                async def _fetch_one(uid):
                    return await fetch_avatar_image(user_id=uid, bot=bot, guild=guild)

                avatar_results = await asyncio.gather(
                    *[_fetch_one(uid) for uid in all_avatar_ids]
                )

                successful_avatar_refs = []
                for idx, result in enumerate(avatar_results):
                    if result and result.get("data"):
                        successful_avatar_refs.append(
                            {
                                "data": result["data"],
                                "mime_type": result.get("mime_type", "image/png"),
                                "filename": result.get(
                                    "filename", f"avatar_{all_avatar_ids[idx]}.png"
                                ),
                                "source": "avatar_user_id",
                                "reference_role": "avatar",
                                "reference_description": f"用户头像参考图 {idx + 1}",
                            }
                        )
                    else:
                        log.warning(f"无法提取用户 {all_avatar_ids[idx]} 的头像")

                if successful_avatar_refs:
                    selected_avatar_refs = _select_reference_images(
                        successful_avatar_refs
                    )
                    avatar_reference_images = selected_avatar_refs
                    if not reference_images:
                        reference_images = selected_avatar_refs
                        # 向后兼容：单图链路使用第一张
                        reference_image = selected_avatar_refs[0]
                    if len(selected_avatar_refs) > 1:
                        log.info(
                            f"已提取 {len(selected_avatar_refs)} 个用户头像作为多参考图（mode={reference_image_mode}）"
                        )
                    else:
                        log.info(
                            f"已从Discord用户头像提取参考图 (用户ID: {all_avatar_ids[0]})"
                        )
                else:
                    log.warning("所有用户头像都提取失败")
            except Exception as e:
                log.error(f"提取Discord用户头像失败: {e}")

    if explicit_avatar_reference_requested and not reference_image and not reference_images:
        return {
            "edit_failed": True,
            "reason": "avatar_image_not_found",
            "hint": "已明确要求使用用户头像作为图生图参考图，但头像获取失败。请告诉用户提供正确的 user_id、@提及或用户名；不要改用聊天记录里的其他图片。"
        }

    # 头像编辑场景：固定“第 1 张底图，第 2 张起头像参考图”的顺序。
    # 这样用户说“在这张图上 P 上 A/B 的头像”时，不会只把头像传给模型而丢失原图。
    if avatar_reference_images:
        base_images = await _find_base_images_for_avatar_edit()
        if base_images:
            remaining_slots = max(0, max_reference_images - len(base_images))
            combined_references = base_images + avatar_reference_images[:remaining_slots]
            reference_images = combined_references
            reference_image = combined_references[0]
            avatar_count = max(0, len(combined_references) - len(base_images))
            edit_prompt = (
                "【参考图顺序说明】第1张参考图是必须保留构图、背景、主体和画面风格的待编辑原图；"
                f"第2张到第{avatar_count + 1}张是需要被P到原图中的用户头像参考图。"
                "请只把这些头像/脸部特征按用户要求自然合成到第1张图里，"
                "不要把整张图重画成头像图，不要丢失第1张图的场景、构图和主体。\n"
                f"【编辑要求】{edit_prompt}"
            )
            log.info(
                f"头像图生图已组合底图+头像参考图：底图 {len(base_images)} 张，头像 {avatar_count} 张"
            )
        elif explicit_avatar_reference_requested:
            log.info("已获取头像参考图，但未找到明确底图，将按头像参考图直接图生图。")

    # 然后检查当前消息的附件
    if not reference_image and not reference_images and message:
        requested_max = max_reference_images if reference_image_mode != "single" else 1

        current_images = await extract_images_from_message(
            message, max_images=requested_max
        )
        selected_images = _select_reference_images(current_images)
        if selected_images:
            reference_images = selected_images
            reference_image = selected_images[0]
            if len(selected_images) > 1:
                log.info(f"已从当前消息提取 {len(selected_images)} 张参考图")

        # 如果当前消息没有图片，检查回复的消息
        if not reference_image and message.reference and message.reference.message_id:
            try:
                ref_msg = await message.channel.fetch_message(message.reference.message_id)
                if ref_msg:
                    reply_candidates: List[Dict[str, Any]] = []
                    reply_candidates.extend(
                        await extract_images_from_message(ref_msg, max_images=requested_max)
                    )

                    # 也检查转发消息中的图片
                    if (
                        len(reply_candidates) < requested_max
                        and hasattr(ref_msg, "message_snapshots")
                        and ref_msg.message_snapshots
                    ):
                        for snapshot in ref_msg.message_snapshots:
                            if len(reply_candidates) >= requested_max:
                                break
                            if hasattr(snapshot, "attachments") and snapshot.attachments:
                                for attachment in snapshot.attachments:
                                    if len(reply_candidates) >= requested_max:
                                        break
                                    if (
                                        attachment.content_type
                                        and attachment.content_type.startswith("image/")
                                    ):
                                        try:
                                            image_bytes = await attachment.read()
                                            reply_candidates.append(
                                                {
                                                    "data": image_bytes,
                                                    "mime_type": attachment.content_type,
                                                    "filename": attachment.filename,
                                                }
                                            )
                                        except Exception as e:
                                            log.error(f"读取转发消息图片失败: {e}")

                    selected_images = _select_reference_images(reply_candidates)
                    if selected_images:
                        reference_images = selected_images
                        reference_image = selected_images[0]
                        if len(selected_images) > 1:
                            log.info(
                                f"已从回复消息提取 {len(selected_images)} 张参考图"
                            )
            except Exception as e:
                log.warning(f"获取回复消息失败: {e}")

        # 不再从频道最近消息自动找图。
        # 只有当前消息附件、明确回复图片、显式头像/搜索图/预准备参考图才会作为参考图。

    # 如果还是没有找到图片，返回错误
    if not reference_image and not reference_images:
        return {
            "edit_failed": True,
            "reason": "no_image_found",
            "hint": "用户没有发送图片。请用自己的语气告诉用户，如果想要修改图片，需要先发送一张图片给你，或者回复一张图片并说明想要怎么修改。"
        }

    # 检查服务是否可用
    if not gemini_imagen_service.is_available():
        log.warning("Gemini Imagen 服务不可用")
        return {
            "edit_failed": True,
            "reason": "service_unavailable",
            "hint": "图片修改服务当前不可用。请用自己的语气告诉用户这个功能暂时用不了。"
        }

    # 获取用户ID（如果提供）用于扣费
    user_id = kwargs.get("user_id")
    parsed_user_id: Optional[int] = None
    if user_id:
        try:
            parsed_user_id = int(user_id)
        except (ValueError, TypeError):
            log.warning(f"无法解析用户ID: {user_id}")

    # 检查是否处于绘图封禁状态
    if parsed_user_id is not None:
        ban_status = await chat_db_manager.get_image_generation_ban_status(parsed_user_id)
        if ban_status.get("is_banned"):
            remaining_text = ban_status.get("remaining_text", "未知时长")
            return {
                "edit_failed": True,
                "reason": "image_generation_banned",
                "hint": f"该用户因图片收到过多负反馈，绘图功能已被临时禁用，剩余封禁时长：{remaining_text}。"
            }

    cost = GEMINI_IMAGEN_CONFIG.get("IMAGE_EDIT_COST", 40)

    # 检查用户余额（如果需要扣费）
    if parsed_user_id is not None and cost > 0:
        balance = await coin_service.get_balance(parsed_user_id)
        if balance < cost:
            return {
                "edit_failed": True,
                "reason": "insufficient_balance",
                "cost": cost,
                "balance": balance,
                "hint": f"用户灵石不足（需要{cost}，只有{balance}）。请用自己的语气告诉用户余额不够，让他们去赚点灵石再来。"
            }

    log.info(f"调用图生图工具，编辑指令: {edit_prompt[:100]}...")

    # 添加"正生成"反应
    await add_reaction(GENERATING_EMOJI)

    # 发送预告消息并保存消息引用
    preview_msg: Optional[discord.Message] = None
    current_turn_tool_names = {
        str(name).strip().lower()
        for name in (kwargs.get('current_turn_tool_names') or [])
        if str(name).strip()
    }
    suppress_preview_message = 'generate_voice' in current_turn_tool_names
    if channel and preview_message and not suppress_preview_message:
        try:
            # 替换表情占位符为实际表情
            processed_message = replace_emojis(preview_message)
            preview_msg = await channel.send(processed_message)
            log.info(f"已发送图生图预告消息: {preview_message[:50]}...")
        except Exception as e:
            log.warning(f"发送预告消息失败: {e}")

    try:
        # 验证宽高比
        valid_ratios = ["1:1", "3:4", "4:3", "9:16", "16:9"]
        if aspect_ratio not in valid_ratios:
            aspect_ratio = "1:1"
            log.warning(f"无效的宽高比，已重置为默认值 1:1")

        # 验证内容分级参数
        if content_rating not in ["sfw", "nsfw"]:
            content_rating = "sfw"
            log.warning(f"无效的内容分级参数，已重置为默认值 sfw")

        log.info(f"图生图内容分级: {content_rating}")
        use_spoiler = should_spoiler_image(content_rating)
        log.info(
            f"图生图参考图策略: mode={reference_image_mode}, max={max_reference_images}, "
            f"实际数量={len(reference_images) if reference_images else (1 if reference_image else 0)}"
        )

        # 调用图生图服务（支持多参考图）
        edited_image_bytes = await gemini_imagen_service.edit_image(
            reference_image=reference_image["data"] if reference_image else None,
            edit_prompt=edit_prompt,
            reference_mime_type=(
                reference_image["mime_type"] if reference_image else "image/png"
            ),
            reference_images=(
                [
                    {
                        "data": ref["data"],
                        "mime_type": ref.get("mime_type", "image/png"),
                        "filename": ref.get("filename"),
                        "image_search_reference_index": ref.get("image_search_reference_index"),
                        "reference_label": ref.get("reference_label"),
                        "source": ref.get("source"),
                    }
                    for ref in reference_images
                    if ref and ref.get("data")
                ]
                if reference_images
                else None
            ),
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            content_rating=content_rating,
            model_name_override=model_name_override,
            openai_image_size=openai_image_size,
            openai_response_format=openai_response_format,
            openai_stream=openai_stream,
            openai_quality=openai_quality,
            openai_style=openai_style,
            openai_image_api_mode=openai_image_api_mode,
        )

        # 移除"正在生成"反应
        await remove_reaction(GENERATING_EMOJI)

        if edited_image_bytes:
            if return_image_only:
                return {
                    "success": True,
                    "skip_ai_response": True,
                    "image_data": {
                        "data": edited_image_bytes,
                        "mime_type": "image/png",
                    },
                    "edit_prompt": edit_prompt,
                    "message": "图片已生成并返回给批量图生图工具统一发送。",
                }

            # 直接发送图片到频道（Embed 格式 + 重新生成按钮）
            # 注意：✅ 反应和扣费移到发送成功之后，避免发送失败时已打 ✅
            image_sent = False
            if channel:
                try:
                    import io
                    from src.chat.features.tools.ui.regenerate_view import RegenerateView

                    # 获取实际使用的模型名称
                    edit_model_name = (
                        str(model_name_override).strip()
                        if model_name_override is not None and str(model_name_override).strip()
                        else gemini_imagen_service._get_model_for_resolution(
                            resolution=resolution,
                            is_edit=True,
                            content_rating=content_rating,
                        )
                    )

                    # 构建 Discord Embed（标题+提示词+成功回复全在 Embed 内）
                    embed = discord.Embed(
                        title="AI 图生图",
                        color=0x2b2d31,
                    )
                    # 设置请求者头像和名称
                    if message and hasattr(message, 'author') and message.author:
                        embed.set_author(
                            name=message.author.display_name,
                            icon_url=message.author.display_avatar.url if message.author.display_avatar else None,
                        )
                    embed.add_field(
                        name="编辑提示词",
                        value=f"```\n{edit_prompt[:1016]}\n```",
                        inline=False,
                    )
                    if success_message:
                        processed_success = replace_emojis(success_message)
                        embed.add_field(
                            name="\u200b",
                            value=processed_success[:1024],
                            inline=False,
                        )
                    embed.set_footer(text=f"模型: {edit_model_name}")

                    # 创建重新生成按钮视图
                    regenerate_view = None
                    if parsed_user_id is not None:
                        regenerate_view = RegenerateView(
                            generation_type="edit_image",
                            original_params={
                                "prompt": edit_prompt,
                                "aspect_ratio": aspect_ratio,
                                "resolution": resolution,
                                "content_rating": content_rating,
                                "original_success_message": success_message or "",
                                "model_name_override": model_name_override,
                                "openai_image_size": openai_image_size,
                                "openai_response_format": openai_response_format,
                                "openai_stream": openai_stream,
                                "openai_quality": openai_quality,
                                "openai_style": openai_style,
                                "openai_image_api_mode": openai_image_api_mode,
                                # 保存参考图片数据以便重新生成
                                "reference_image_data": reference_image["data"],
                                "reference_image_mime_type": reference_image["mime_type"],
                                "reference_images_data": [
                                    ref["data"] for ref in reference_images if ref.get("data")
                                ] if reference_images else [reference_image["data"]],
                                "reference_images_mime_types": [
                                    ref.get("mime_type", "image/png")
                                    for ref in reference_images
                                    if ref.get("data")
                                ] if reference_images else [reference_image["mime_type"]],
                            },
                            user_id=parsed_user_id,
                        )

                    file = discord.File(
                        io.BytesIO(edited_image_bytes),
                        filename="edited_image.png",
                        spoiler=use_spoiler,
                    )
                    send_kwargs = {"embed": embed, "file": file}
                    if regenerate_view:
                        send_kwargs["view"] = regenerate_view
                    sent_message = await channel.send(**send_kwargs)
                    image_sent = True
                    if parsed_user_id is not None:
                        await chat_db_manager.register_generated_image_message(
                            message_id=sent_message.id,
                            user_id=parsed_user_id,
                            guild_id=sent_message.guild.id if sent_message.guild else None,
                            channel_id=sent_message.channel.id,
                        )
                    log.info("修改后的图片已直接发送到频道（Embed格式+重新生成按钮）")
                except Exception as e:
                    log.error(f"发送图片到频道失败: {e}", exc_info=True)

            if not image_sent:
                await add_reaction(FAILED_EMOJI)
                return {
                    "edit_failed": True,
                    "reason": "send_failed",
                    "hint": "图片已生成但发送到频道失败了。请用自己的语气告诉用户稍后再试。"
                }

            # 发送成功后才打 ✅ 和扣费
            await add_reaction(SUCCESS_EMOJI)
            if parsed_user_id is not None and cost > 0:
                try:
                    await coin_service.remove_coins(
                        parsed_user_id, cost, f"AI图生图: {edit_prompt[:30]}..."
                    )
                    log.info(f"用户 {parsed_user_id} 图生图成功，扣除 {cost} 灵石")
                except Exception as e:
                    log.error(f"扣除灵石失败: {e}")

            return {
                "success": True,
                "skip_ai_response": True,
                "cost": cost,
                "message": "图片已成功修改并发送给用户，预告消息已发送，无需再回复。"
            }
        else:
            # 添加失败反应
            await add_reaction(FAILED_EMOJI)

            log.warning(f"图生图返回空结果。编辑指令: {edit_prompt}")

            return {
                "edit_failed": True,
                "reason": "edit_failed",
                "hint": "图片修改失败了，可能是编辑指令不够清晰或者图片格式有问题。请用自己的语气告诉用换个描述试试，或者换一张图片。"
            }

    except Exception as e:
        # 移除"正在生成"反应，添加失败反应
        await remove_reaction(GENERATING_EMOJI)
        await add_reaction(FAILED_EMOJI)

        log.error(f"图生图工具执行错误: {e}", exc_info=True)
        return {
            "edit_failed": True,
            "reason": "system_error",
            "hint": f"图片修改时发生了系统错误。请用自己的语气安慰用户，告诉他们稍后再试。"
        }


async def edit_images_batch(
    edit_prompts: List[str],
    aspect_ratio: str = "1:1",
    resolution: str = "default",
    content_rating: str = "sfw",
    emoji_id: Optional[str] = None,
    avatar_user_id: Optional[str] = None,
    avatar_user_ids: Optional[List[str]] = None,
    image_search_reference_index: Optional[int] = None,
    image_search_reference_indexes: Optional[List[int]] = None,
    reference_image_mode: str = "auto",
    max_reference_images: int = 30,
    preview_message: Optional[str] = None,
    success_message: Optional[str] = None,
    model_name_override: Optional[str] = None,
    openai_image_size: Optional[str] = None,
    openai_response_format: Optional[str] = None,
    openai_stream: Optional[bool] = None,
    openai_quality: Optional[str] = None,
    openai_style: Optional[str] = None,
    openai_image_api_mode: Optional[str] = None,
    **kwargs,
) -> dict:
    """
    批量图生图：基于同一批参考图，一次生成多张不同编辑结果，并像批量文生图一样合并发送。

    使用场景：
    - 用户发/回复一张图，说“按这张图生成 4 张不同表情 / 不同场景 / 不同姿势”。
    - 用户先分别搜索了多个人物参考图，再要求基于这些参考图一次生成多张构图/剧情变化。
    - 用户用多个头像或多张参考图做同一组变体。

    重要规则：
    - 这是图生图，不是纯文生图；必须有参考图来源。
    - `edit_prompts` 中每一条都是一张结果图的编辑指令。
    - 如果参考图来自 image_search，多人物/多角色必须先为每个人物分别调用 image_search，
      然后在本工具中用 `image_search_reference_indexes=[...]` 一次性传入所有选中的全局编号。
      禁止把两个人名塞进一次 image_search 的 query，也禁止只从同一批搜索结果里选多张来冒充多个人。
    - 每条 `edit_prompt` 都要短，只写“保持参考图人物身份、脸、发型、服装、画风不变，仅改动作/场景/构图为……”。
      不要复述整套外貌标签或退回纯文生图。
    - 成功后系统会把所有结果图和每条对应的编辑提示词放在一条/一组消息中发送；
      `success_message` 只会显示一次，不要为每张图写不同成功消息。
    """
    import asyncio
    import io

    from src.chat.features.image_generation.services.gemini_imagen_service import (
        gemini_imagen_service,
    )
    from src.chat.config.chat_config import GEMINI_IMAGEN_CONFIG
    from src.chat.features.odysseia_coin.service.coin_service import coin_service
    from src.chat.utils.database import chat_db_manager

    message: Optional[discord.Message] = kwargs.get("message")
    channel = kwargs.get("channel")

    normalized_prompts = [
        str(item or "").strip()
        for item in (edit_prompts or [])
        if str(item or "").strip()
    ]
    if not normalized_prompts:
        return {
            "edit_failed": True,
            "reason": "empty_prompts",
            "hint": "批量图生图缺少编辑提示词。请重新调用 edit_images_batch，并为每张结果图分别填写 edit_prompts。",
        }

    max_images = int(GEMINI_IMAGEN_CONFIG.get("MAX_IMAGES_PER_REQUEST", 10))
    if len(normalized_prompts) > max_images:
        normalized_prompts = normalized_prompts[:max_images]

    if not gemini_imagen_service.is_available():
        return {
            "edit_failed": True,
            "reason": "service_unavailable",
            "hint": "图片修改服务当前不可用。请用自己的语气告诉用户这个功能暂时用不了。",
        }

    parsed_user_id: Optional[int] = None
    user_id = kwargs.get("user_id")
    if user_id:
        try:
            parsed_user_id = int(user_id)
        except (ValueError, TypeError):
            log.warning(f"无法解析用户ID: {user_id}")

    if parsed_user_id is not None:
        ban_status = await chat_db_manager.get_image_generation_ban_status(parsed_user_id)
        if ban_status.get("is_banned"):
            remaining_text = ban_status.get("remaining_text", "未知时长")
            return {
                "edit_failed": True,
                "reason": "image_generation_banned",
                "hint": f"该用户因图片收到过多负反馈，绘图功能已被临时禁用，剩余封禁时长：{remaining_text}。",
            }

    cost_per_image = GEMINI_IMAGEN_CONFIG.get("IMAGE_EDIT_COST", 40)
    estimated_cost = cost_per_image * len(normalized_prompts)
    if parsed_user_id is not None and estimated_cost > 0:
        balance = await coin_service.get_balance(parsed_user_id)
        if balance < estimated_cost:
            return {
                "edit_failed": True,
                "reason": "insufficient_balance",
                "cost": estimated_cost,
                "balance": balance,
                "hint": f"用户灵石不足（需要{estimated_cost}，只有{balance}）。请用自己的语气告诉用户余额不够，让他们去赚点灵石再来。",
            }

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

    await add_reaction(GENERATING_EMOJI)

    current_turn_tool_names = {
        str(name).strip().lower()
        for name in (kwargs.get("current_turn_tool_names") or [])
        if str(name).strip()
    }
    suppress_preview_message = "generate_voice" in current_turn_tool_names
    if channel and preview_message and not suppress_preview_message:
        try:
            processed_message = replace_emojis(preview_message)
            if message:
                await message.reply(processed_message, mention_author=False)
            else:
                await channel.send(processed_message)
        except Exception as e:
            log.warning(f"发送批量图生图预告消息失败: {e}")

    try:
        if aspect_ratio not in ["1:1", "3:4", "4:3", "9:16", "16:9"]:
            aspect_ratio = "1:1"
        if content_rating not in ["sfw", "nsfw"]:
            content_rating = "sfw"

        use_spoiler = should_spoiler_image(content_rating)
        max_concurrent_tasks = max(
            1, int(GEMINI_IMAGEN_CONFIG.get("MAX_CONCURRENT_IMAGE_TASKS", 3))
        )
        semaphore = asyncio.Semaphore(max_concurrent_tasks)

        async def _edit_one(prompt_item: str) -> Optional[bytes]:
            async with semaphore:
                inner_kwargs = dict(kwargs)
                inner_kwargs.pop("user_id", None)
                inner_kwargs.update(
                    {
                        "message": message,
                        "channel": None,
                        "_return_image_only": True,
                        "_skip_reactions": True,
                    }
                )
                result = await edit_image(
                    edit_prompt=prompt_item,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    content_rating=content_rating,
                    emoji_id=emoji_id,
                    avatar_user_id=avatar_user_id,
                    avatar_user_ids=avatar_user_ids,
                    image_search_reference_index=image_search_reference_index,
                    image_search_reference_indexes=image_search_reference_indexes,
                    reference_image_mode=reference_image_mode,
                    max_reference_images=max_reference_images,
                    preview_message=None,
                    success_message=None,
                    model_name_override=model_name_override,
                    openai_image_size=openai_image_size,
                    openai_response_format=openai_response_format,
                    openai_stream=openai_stream,
                    openai_quality=openai_quality,
                    openai_style=openai_style,
                    openai_image_api_mode=openai_image_api_mode,
                    **inner_kwargs,
                )
                image_data = result.get("image_data") if isinstance(result, dict) else None
                if isinstance(image_data, dict) and image_data.get("data"):
                    return image_data["data"]
                if isinstance(result, dict):
                    log.warning("批量图生图单项失败: reason=%s hint=%s", result.get("reason"), result.get("hint"))
                return None

        results = await asyncio.gather(
            *[_edit_one(prompt_item) for prompt_item in normalized_prompts],
            return_exceptions=True,
        )

        successful_images: List[tuple[bytes, str]] = []
        failed_count = 0
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                failed_count += 1
                log.warning(f"批量图生图失败 (提示词 {idx + 1}): {result}")
            elif result:
                successful_images.append((result, normalized_prompts[idx]))
            else:
                failed_count += 1

        await remove_reaction(GENERATING_EMOJI)

        if not successful_images:
            await add_reaction(FAILED_EMOJI)
            return {
                "edit_failed": True,
                "reason": "edit_failed",
                "hint": "批量图生图全部失败了。请让用户换一张参考图，或把每条编辑要求写得更清楚一点。",
            }

        actual_count = len(successful_images)
        actual_cost = cost_per_image * actual_count
        batch_sent = False

        if channel:
            try:
                edit_model_name = (
                    str(model_name_override).strip()
                    if model_name_override is not None and str(model_name_override).strip()
                    else gemini_imagen_service._get_model_for_resolution(
                        resolution=resolution,
                        is_edit=True,
                        content_rating=content_rating,
                    )
                )

                embed = discord.Embed(
                    title="AI 批量图生图",
                    color=0x2b2d31,
                )
                if message and hasattr(message, "author") and message.author:
                    embed.set_author(
                        name=message.author.display_name,
                        icon_url=message.author.display_avatar.url if message.author.display_avatar else None,
                    )
                for idx, (_, prompt_item) in enumerate(successful_images, 1):
                    embed.add_field(
                        name=f"图{idx}编辑提示词",
                        value=f"```\n{prompt_item[:1016]}\n```",
                        inline=False,
                    )
                if success_message:
                    processed_success = replace_emojis(success_message)
                    embed.add_field(name="\u200b", value=processed_success[:1024], inline=False)
                footer_parts = [f"模型: {edit_model_name}", f"成功: {actual_count}/{len(normalized_prompts)}"]
                if failed_count:
                    footer_parts.append(f"失败: {failed_count}")
                embed.set_footer(text=" | ".join(footer_parts))

                all_images = [img for img, _ in successful_images]
                MAX_FILES_PER_MESSAGE = 10
                for batch_start in range(0, len(all_images), MAX_FILES_PER_MESSAGE):
                    batch_end = min(batch_start + MAX_FILES_PER_MESSAGE, len(all_images))
                    batch_files = [
                        discord.File(
                            io.BytesIO(all_images[idx]),
                            filename=f"edited_image_{idx + 1}.png",
                            spoiler=use_spoiler,
                        )
                        for idx in range(batch_start, batch_end)
                    ]
                    sent_message: Optional[discord.Message] = None
                    if batch_start == 0:
                        if message:
                            sent_message = await message.reply(embed=embed, files=batch_files, mention_author=False)
                        else:
                            sent_message = await channel.send(embed=embed, files=batch_files)
                    else:
                        sent_message = await channel.send(files=batch_files)

                    if sent_message and parsed_user_id is not None:
                        await chat_db_manager.register_generated_image_message(
                            message_id=sent_message.id,
                            user_id=parsed_user_id,
                            guild_id=sent_message.guild.id if sent_message.guild else None,
                            channel_id=sent_message.channel.id,
                        )

                batch_sent = True
            except Exception as e:
                log.error(f"发送批量图生图到频道失败: {e}", exc_info=True)

        if not batch_sent:
            await add_reaction(FAILED_EMOJI)
            return {
                "edit_failed": True,
                "reason": "send_failed",
                "hint": "批量图生图已生成但发送到频道失败了。请用自己的语气告诉用户稍后再试。",
            }

        await add_reaction(SUCCESS_EMOJI)
        if parsed_user_id is not None and actual_cost > 0:
            try:
                await coin_service.remove_coins(
                    parsed_user_id,
                    actual_cost,
                    f"AI批量图生图x{actual_count}",
                )
            except Exception as e:
                log.error(f"扣除灵石失败: {e}")

        return {
            "success": True,
            "skip_ai_response": True,
            "images_generated": actual_count,
            "failed_count": failed_count,
            "cost": actual_cost,
            "message": "批量图生图已成功生成并合并发送给用户，预告消息已发送，无需再回复。",
        }
    except Exception as e:
        await remove_reaction(GENERATING_EMOJI)
        await add_reaction(FAILED_EMOJI)
        log.error(f"批量图生图工具执行错误: {e}", exc_info=True)
        return {
            "edit_failed": True,
            "reason": "system_error",
            "hint": "批量图生图时发生系统错误。请用自己的语气告诉用户稍后再试。",
        }
