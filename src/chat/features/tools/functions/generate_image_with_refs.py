# -*- coding: utf-8 -*-

"""
角色参考图生图工具
从预设的参考图文件夹（/app/data/character_refs/）按角色名查找参考图，
把找到的参考图与用户提示词一起发给图生图模型生成新图。

适用场景：用户说"画一张紫灵跟梅凝一起喝茶的图片"时，从 character_refs
文件夹找到 紫灵.png、梅凝.png 作为角色长相参考，加上场景提示词一起生成。
"""

import logging
import os
from typing import Optional, List, Dict, Any

import discord

from src.chat.features.tools.tool_metadata import tool_metadata

from src.chat.features.tools.functions._image_compress import compress_image_for_discord

from src.chat.features.image_generation.utils.spoiler_policy import (
    should_spoiler_image,
)
from src.chat.features.tools.functions.image_policy_guard import (
    check_yueyue_self_nsfw_violation,
)
from src.chat.utils.prompt_utils import replace_emojis

log = logging.getLogger(__name__)

# 与其他画图工具一致的交互 emoji
GENERATING_EMOJI = "🎨"  # 正在生成
SUCCESS_EMOJI = "✅"      # 生成成功
FAILED_EMOJI = "❌"       # 生成失败

# 参考图根目录（容器内路径，/app/data 是宿主机 /opt/Odysseia-Guidance/data 挂载）
CHARACTER_REF_DIR = os.getenv("CHARACTER_REF_DIR", "/app/data/character_refs")

# 支持的图片扩展名
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# 单次生成最多使用的参考图数量
_MAX_REFS = 6


def list_available_characters() -> List[Dict[str, str]]:
    """扫描参考图文件夹，返回可用角色清单 [{name, filename}]。"""
    characters: List[Dict[str, str]] = []
    try:
        if not os.path.isdir(CHARACTER_REF_DIR):
            return characters
        for fn in sorted(os.listdir(CHARACTER_REF_DIR)):
            stem, ext = os.path.splitext(fn)
            if ext.lower() in _IMAGE_EXTENSIONS and not fn.startswith("."):
                characters.append({"name": stem.strip(), "filename": fn})
    except Exception as e:
        log.warning(f"扫描参考图目录失败: {e}")
    return characters


def _compress_ref_image(data: bytes, max_bytes: int = 950 * 1024) -> tuple:
    """把参考图压到 max_bytes 以下：先降质量转 JPEG，再缩尺寸。

    Returns:
        (compressed_bytes, mime_type) — 压缩产物是 JPEG，mime 一并返回。
    """
    try:
        from PIL import Image
        import io as _io

        img = Image.open(_io.BytesIO(data))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        # 第一轮：降 JPEG 质量
        for quality in (88, 80, 70, 60, 50):
            buf = _io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            out = buf.getvalue()
            if len(out) <= max_bytes:
                log.info(
                    f"[参考图压缩] {len(data)/1024:.0f}KB -> {len(out)/1024:.0f}KB (q={quality})"
                )
                return out, "image/jpeg"

        # 第二轮：缩尺寸 + q=60
        for scale in (0.75, 0.5, 0.4, 0.3):
            resized = img.resize(
                (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                Image.LANCZOS,
            )
            buf = _io.BytesIO()
            resized.save(buf, format="JPEG", quality=60, optimize=True)
            out = buf.getvalue()
            if len(out) <= max_bytes:
                log.info(
                    f"[参考图压缩] {len(data)/1024:.0f}KB -> {len(out)/1024:.0f}KB "
                    f"(缩放{scale*100:.0f}%)"
                )
                return out, "image/jpeg"

        log.warning("[参考图压缩] 压缩后仍超限，返回最后一次结果")
        return out, "image/jpeg"
    except Exception as e:
        log.warning(f"[参考图压缩] 压缩失败，返回原图: {e}")
        return data, ""


def find_character_images(character_names: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    按角色名模糊匹配参考图文件。

    匹配优先级：精确同名 > 文件名包含角色名（如 "紫灵_正面.png"）> 角色名包含文件名。

    Returns:
        {角色名: {"path": str, "data": bytes, "mime_type": str}} 只含命中的角色。
    """
    available = list_available_characters()
    found: Dict[str, Dict[str, Any]] = {}

    for raw_name in character_names:
        name = str(raw_name).strip()
        if not name:
            continue
        hit = None
        # 1) 精确同名
        for item in available:
            if item["name"] == name:
                hit = item
                break
        # 2) 文件名包含角色名（取最短的那个，通常是主图）
        if hit is None:
            candidates = [it for it in available if name in it["name"]]
            if candidates:
                hit = min(candidates, key=lambda it: len(it["name"]))
        # 3) 角色名包含文件名（文件名是简称）
        if hit is None:
            candidates = [it for it in available if it["name"] and it["name"] in name]
            if candidates:
                hit = max(candidates, key=lambda it: len(it["name"]))
        if hit is None:
            continue

        path = os.path.join(CHARACTER_REF_DIR, hit["filename"])
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception as e:
            log.warning(f"读取参考图失败 {path}: {e}")
            continue

        ext = os.path.splitext(hit["filename"])[1].lower()
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }.get(ext, "image/png")

        # 防御性压缩：上游 edits 接口本身无 1MB 限制（实测 1.77MB PNG 直传 OK），
        # 但 10MB 级异常大图仍会拖慢请求/撑爆 multipart，超 9MB 才压
        if len(data) > 9 * 1024 * 1024:
            data, compressed_mime = _compress_ref_image(data, max_bytes=9 * 1024 * 1024)
            if compressed_mime:
                mime = compressed_mime

        found[name] = {"path": path, "data": data, "mime_type": mime}

    return found


@tool_metadata(
    name="角色参考图生图",
    description="从预设角色图库里找参考图来画指定角色的新图。当用户请求画某个有参考图的角色做某事（如'画紫灵和梅凝喝茶'）时调用。传 character_names（角色名列表）和 prompt（场景描述）。",
    emoji="🎭",
    category="图片生成",
)
async def generate_image_with_refs(
    character_names: List[str],
    prompt: str,
    aspect_ratio: str = "1:1",
    resolution: str = "default",
    content_rating: str = "sfw",
    preview_message: Optional[str] = None,
    success_message: Optional[str] = None,
    **kwargs,
) -> dict:
    """
    用预设角色参考图生成新图。

    **【工具路由 — 最高优先】**
    - 凡是要画角色图库里的预设角色（凡人修仙传角色：紫灵、梅凝、南宫婉、韩立、银月、元瑶、
      菡云芝、董萱儿、陈巧倩、慕沛灵 等，以及月月），**必须用本工具**，不要用 edit_image、
      不要用 image_search、不要用普通画图工具——图库参考图的人物身份远比文字描述准确。
    - **多角色同框也用本工具**：character_names 必须传入用户提到的**全部**出场角色，
      一个都不能落下、不能用文字描述代替参考图。常见错误：只传 ["紫灵"] 然后在 prompt
      里文字描述梅凝——这是错的，梅凝的图库参考图必须一起传 ["紫灵","梅凝"]。

    使用场景（必须调用此工具）：
    - 用户说"画一张紫灵跟梅凝一起喝茶的图片"→ character_names=["紫灵","梅凝"], prompt="紫灵和梅凝坐在一起喝茶，温馨的午后，茶香袅袅"
    - 用户提到要画"某某和某某"做某事，且这些角色在角色图库里时

    工作流程：工具按角色名从角色图库找参考图 → 找到的图与你的 prompt 一起发给图生图模型。
    如果有角色没找到参考图，返回值会告诉你哪些角色没找到——此时改用普通画图工具并放弃参考图。

    Args:
        character_names: 要出场的角色名列表，按用户提到的名字填写。
            例如用户说"画紫灵跟梅凝喝茶"就传 ["紫灵", "梅凝"]。
            最多 6 个角色，超出部分忽略。
        prompt: （必填）画面描述，只写场景/动作/构图/氛围。
            例如："两位少女坐在庭院茶桌旁喝茶，午后阳光，樱花飘落，温馨氛围"。
            **不要**在 prompt 里复述角色的外貌/服装/发色——参考图会提供角色长相，
            模型会保持参考图人物身份。重复描述反而会稀释参考图作用。
        aspect_ratio: 宽高比，可选 "1:1" "3:4" "4:3" "9:16" "16:9"，默认 "1:1"。
        resolution: 分辨率 "default"/"2k"/"4k"，默认 "default"。
        content_rating: 内容分级 "sfw"/"nsfw"，默认 "sfw"。
        preview_message: （可选）生成前发给用户的预告消息，符合月月口吻的一句话。
        success_message: （必填）生成成功后随图片一起发送的回复，符合月月口吻。
            图片发送后不会再有后续回复，这条就是最终回复。

    Returns:
        成功后图片和 success_message 发送给用户，不需要再额外回复。
        失败时根据返回的提示信息告诉用户。
    """
    from src.chat.features.image_generation.services.gemini_imagen_service import (
        gemini_imagen_service,
    )
    from src.chat.config.chat_config import GEMINI_IMAGEN_CONFIG
    from src.chat.features.odysseia_coin.service.coin_service import coin_service
    from src.chat.utils.database import chat_db_manager

    message: Optional[discord.Message] = kwargs.get("message")
    channel = kwargs.get("channel")

    policy_block = check_yueyue_self_nsfw_violation(
        prompt=prompt,
        message=message,
    )
    if policy_block:
        return policy_block

    # --- 参数校验 ---
    prompt = str(prompt or "").strip()
    if not prompt:
        return {
            "error": True,
            "reason": "empty_prompt",
            "hint": "画面描述不能为空。请补上场景/动作描述再调用。",
        }

    names_raw = character_names or []
    if isinstance(names_raw, str):
        names_raw = [names_raw]
    names = [str(n).strip() for n in names_raw if str(n).strip()][:_MAX_REFS]
    if not names:
        return {
            "error": True,
            "reason": "no_character",
            "hint": "没有提供角色名。请从用户消息里提取角色名列表再调用。",
        }

    async def add_reaction(emoji: str):
        if message:
            try:
                await message.add_reaction(emoji)
            except Exception as e:
                log.warning(f"添加反应失败: {e}")

    async def remove_reaction(emoji: str):
        if message:
            bot = kwargs.get("bot")
            if bot and bot.user:
                try:
                    await message.remove_reaction(emoji, bot.user)
                except Exception as e:
                    log.warning(f"移除反应失败: {e}")

    # --- 查找参考图 ---
    found = find_character_images(names)
    missing = [n for n in names if n not in found]

    if not found:
        available_names = [it["name"] for it in list_available_characters()]
        return {
            "refs_not_found": True,
            "reason": "no_reference_images",
            "requested_characters": names,
            "available_characters": available_names[:30],
            "hint": (
                "角色图库里没有这些角色的参考图。"
                f"图库里现有：{'、'.join(available_names[:20]) if available_names else '（空）'}。"
                "请告诉用户没有这些角色的参考图，建议改用普通画图工具。"
            ),
        }

    # --- 扣费准备（与 edit_image 同价：图生图） ---
    user_id = kwargs.get("user_id")
    parsed_user_id: Optional[int] = None
    if user_id:
        try:
            parsed_user_id = int(user_id)
        except (ValueError, TypeError):
            log.warning(f"无法解析用户ID: {user_id}")

    from src.chat.config.chat_config import GEMINI_IMAGEN_CONFIG

    cost = GEMINI_IMAGEN_CONFIG.get("IMAGE_EDIT_COST", 40)
    if parsed_user_id is not None and cost > 0:
        balance = await coin_service.get_balance(parsed_user_id)
        if balance < cost:
            return {
                "error": True,
                "reason": "insufficient_balance",
                "cost": cost,
                "balance": balance,
                "hint": f"用户灵石不足（需要{cost}，只有{balance}）。请用自己的语气告诉用户余额不够。",
            }

    # --- 绘图封禁检查 ---
    if parsed_user_id is not None:
        ban_status = await chat_db_manager.get_image_generation_ban_status(parsed_user_id)
        if ban_status.get("is_banned"):
            remaining_text = ban_status.get("remaining_text", "未知时长")
            return {
                "error": True,
                "reason": "image_generation_banned",
                "hint": f"该用户绘图功能已被临时禁用，剩余时长：{remaining_text}。",
            }

    await add_reaction(GENERATING_EMOJI)

    # --- 预告消息 ---
    if channel and preview_message:
        try:
            await channel.send(replace_emojis(preview_message))
        except Exception as e:
            log.warning(f"发送预告消息失败: {e}")

    try:
        # 组装参考图列表（带标签，方便模型区分角色）
        reference_images = []
        used_chars = []
        for name, item in found.items():
            reference_images.append({
                "data": item["data"],
                "mime_type": item["mime_type"],
                "filename": os.path.basename(item["path"]),
                "reference_label": name,
                "source": "character_ref",
            })
            used_chars.append(name)

        # 编辑指令：参考图身份保持 + 用户场景描述
        ref_names_text = "、".join(used_chars)
        edit_prompt = (
            f"参考图角色：{ref_names_text}。"
            f"保持每个参考图角色的人物身份、脸型、发型、瞳色、服装特征与画风完全不变（参考图顺序即上面提到的角色顺序）。"
            f"画面内容：{prompt}"
        )

        if content_rating not in ("sfw", "nsfw"):
            content_rating = "sfw"
        valid_ratios = ["1:1", "3:4", "4:3", "9:16", "16:9"]
        if aspect_ratio not in valid_ratios:
            aspect_ratio = "1:1"

        log.info(
            f"[角色参考图生图] 角色: {ref_names_text} | 缺失: {missing or '无'} | prompt: {prompt[:60]}..."
        )

        edited_image_bytes = await gemini_imagen_service.edit_image(
            reference_image=reference_images[0]["data"],
            edit_prompt=edit_prompt,
            reference_mime_type=reference_images[0]["mime_type"],
            reference_images=reference_images,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            content_rating=content_rating,
        )

        await remove_reaction(GENERATING_EMOJI)

        if not edited_image_bytes:
            await add_reaction(FAILED_EMOJI)
            return {
                "error": True,
                "reason": "generation_failed",
                "hint": "图片生成失败了。请用自己的语气告诉用户稍后再试或换个描述。",
            }

        # --- 发送图片 ---
        image_sent = False
        sent_message: Optional[discord.Message] = None
        if channel:
            try:
                actual_model = str(gemini_imagen_service.last_actual_model or "").strip()
                embed = discord.Embed(title="角色参考图生图", color=0x2b2d31)
                if message and getattr(message, "author", None):
                    embed.set_author(
                        name=message.author.display_name,
                        icon_url=message.author.display_avatar.url if message.author.display_avatar else None,
                    )
                refs_desc = f"{ref_names_text}"
                if missing:
                    refs_desc += f"（未找到：{'、'.join(missing)}）"
                embed.add_field(name="参考角色", value=refs_desc[:1024], inline=False)
                embed.add_field(name="画面描述", value=f"```\n{prompt[:1016]}\n```", inline=False)
                if success_message:
                    embed.add_field(name="\u200b", value=replace_emojis(success_message)[:1024], inline=False)
                embed.set_footer(text=f"模型: {actual_model or '角色参考图生图'}")

                _img_data, _img_ext = compress_image_for_discord(edited_image_bytes)
                file = discord.File(
                    io_bytes := __import__("io").BytesIO(_img_data),
                    filename=f"character_ref_image.{_img_ext}",
                    spoiler=should_spoiler_image(content_rating),
                )
                sent_message = await channel.send(embed=embed, file=file)
                image_sent = True
                if parsed_user_id is not None:
                    await chat_db_manager.register_generated_image_message(
                        message_id=sent_message.id,
                        user_id=parsed_user_id,
                        guild_id=sent_message.guild.id if sent_message.guild else None,
                        channel_id=sent_message.channel.id,
                    )
            except Exception as e:
                log.error(f"发送图片到频道失败: {e}", exc_info=True)

        if not image_sent:
            await add_reaction(FAILED_EMOJI)
            return {
                "error": True,
                "reason": "send_failed",
                "hint": "图片已生成但发送失败。请用自己的语气告诉用户稍后再试。",
            }

        # --- 扣费（发送成功后） ---
        if parsed_user_id is not None and cost > 0:
            try:
                await coin_service.remove_coins(
                    parsed_user_id, cost, f"角色参考图生图: {prompt[:30]}..."
                )
                log.info(f"角色参考图生图成功，扣除 {cost} 灵石")
                try:
                    _bal_after = await coin_service.get_balance(parsed_user_id)
                    if sent_message and sent_message.embeds:
                        _emb = sent_message.embeds[0]
                        _old_footer = _emb.footer.text or ""
                        _emb.set_footer(
                            text=f"{_old_footer} · 灵石 -{cost}（余 {_bal_after}）"
                            if _old_footer
                            else f"灵石 -{cost}（余 {_bal_after}）"
                        )
                        await sent_message.edit(embed=_emb)
                except Exception as _e:
                    log.warning(f"更新Embed灵石信息失败: {_e}")
            except Exception as e:
                log.error(f"扣除灵石失败: {e}")

        return {
            "success": True,
            "skip_ai_response": True,
            "cost": cost,
            "used_characters": used_chars,
            "missing_characters": missing,
            "message": "图片已成功生成并发送给用户，无需再回复。",
        }

    except Exception as e:
        await remove_reaction(GENERATING_EMOJI)
        await add_reaction(FAILED_EMOJI)
        log.error(f"角色参考图生图工具执行错误: {e}", exc_info=True)
        return {
            "error": True,
            "reason": "system_error",
            "hint": "生成时发生了系统错误。请用自己的语气安慰用户，告诉他们稍后再试。",
        }
