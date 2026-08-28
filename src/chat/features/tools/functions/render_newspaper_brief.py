# -*- coding: utf-8 -*-

import logging
import os
import io
from datetime import datetime
from typing import Optional

import discord

from src.chat.features.tools.tool_metadata import tool_metadata
from src.chat.features.image_generation.services.gemini_imagen_service import gemini_imagen_service
from src.chat.features.tools.functions.summarize_channel import (
    text_to_newspaper_brief_image,
)

log = logging.getLogger(__name__)

# 投喂功能同款占位图，暂用作简报占位图
_PLACEHOLDER_FILENAME = "feeding_placeholder.png"
_FAILED_FILENAME = "feeding_failed.png"


def _affection_asset_path(filename: str) -> str:
    """返回 affection assets 目录下指定文件的绝对路径。"""
    base = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(
        base,
        "..", "..", "affection", "assets", filename,
    )
    return os.path.normpath(path)


def _build_image_prompt(final_title: str, body: str) -> str:
    """根据标题和正文片段构造画图提示词。"""
    snippet = body[:120].replace("\n", " ").strip()
    prompt = (
        f"可爱的银狐少女月月风格的插画，主题是：{final_title}。"
        f"温暖明亮的色彩，Q版风格，跟以下总结内容相关联的场景：{snippet}。"
        f"银白色长发，高马尾，异色瞳（左眼淡绿右眼淡蓝），"
        f"白皙肤色，毛茸茸的白色狐耳内侧粉色，银白色蓬松大尾巴，"
        f"马尾处插着银色月牙发簪，两侧戴着细微尖三角形耳坠。"
    )
    return prompt


@tool_metadata(
    name="报纸摘要",
    description="当搜索总结或频道总结较长、适合用报纸风摘要图展示时使用。图片里不要放链接。",
    emoji="📰",
    category="总结",
)
async def render_newspaper_brief(
    body: str,
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    section_name: Optional[str] = None,
    issue_date: Optional[str] = None,
    dek: Optional[str] = None,
    **kwargs,
) -> dict:
    """将整理好的摘要正文先以 Embed+占位图发到频道，后台画配图，成功替换、失败用报纸模板。"""
    try:
        if not str(body or "").strip():
            return {"error": "报纸摘要正文不能为空。"}

        message = kwargs.get("message")
        channel = kwargs.get("channel") or (message.channel if message else None)
        if not channel:
            return {"error": "无法找到频道上下文"}

        final_title = (
            str(title or "").strip()
            or str(subtitle or "").strip()
            or str(section_name or "").strip()
            or "月月简报"
        )
        final_issue_date = str(issue_date or "").strip() or datetime.now().strftime(
            "%Y-%m-%d"
        )
        final_section = str(section_name or "").strip() or "月月简报"
        body_text = str(body).strip()

        # ---------- 第一步：先发 Embed + 文字总结 + 占位图 ----------
        embed_color = discord.Color(0x9B59B6)  # 紫色

        def _make_embed() -> discord.Embed:
            e = discord.Embed(
                title=f"📰 {final_title}",
                description=body_text[:2000],
                color=embed_color,
            )
            e.set_footer(text=f"月月简报 · {final_issue_date}")
            return e

        embed = _make_embed()

        attachments_first: list[discord.File] = []
        placeholder_path = _affection_asset_path(_PLACEHOLDER_FILENAME)
        if os.path.exists(placeholder_path):
            ph_file = discord.File(
                fp=open(placeholder_path, "rb"), filename="summary_placeholder.png"
            )
            attachments_first.append(ph_file)
            embed.set_image(url="attachment://summary_placeholder.png")

        if message:
            sent_msg = await message.reply(
                embed=embed, files=attachments_first, mention_author=False
            )
        else:
            sent_msg = await channel.send(embed=embed, files=attachments_first)

        log.info("报纸摘要占位消息已发送，开始生成配图...")

        # ---------- 辅助：编辑消息为报纸模板 fallback ----------
        async def _fallback_to_newspaper() -> None:
            try:
                image_bytes = text_to_newspaper_brief_image(
                    body=body_text,
                    title=final_title,
                    subtitle=str(subtitle or "").strip() or None,
                    section_name=final_section,
                    issue_date=final_issue_date,
                    dek=str(dek or "").strip() or None,
                )
                if image_bytes:
                    np_embed = _make_embed()
                    np_file = discord.File(
                        fp=io.BytesIO(image_bytes), filename="newspaper.png"
                    )
                    np_embed.set_image(url="attachment://newspaper.png")
                    await sent_msg.edit(embed=np_embed, attachments=[np_file])
                    log.info("报纸摘要已回退为报纸模板图")
                else:
                    # 报纸模板也失败，用失败占位图
                    failed_path = _affection_asset_path(_FAILED_FILENAME)
                    if os.path.exists(failed_path):
                        fb_embed = _make_embed()
                        fb_file = discord.File(
                            fp=open(failed_path, "rb"), filename="summary_failed.png"
                        )
                        fb_embed.set_image(url="attachment://summary_failed.png")
                        await sent_msg.edit(embed=fb_embed, attachments=[fb_file])
                        log.warning("报纸模板生成失败，已替换为失败占位图")
            except Exception as edit_err:
                log.error(f"回退报纸模板时出错: {edit_err}", exc_info=True)

        # ---------- 第二步：后台生成配图 ----------
        image_prompt = _build_image_prompt(final_title, body_text)

        try:
            if not gemini_imagen_service.is_available():
                log.warning("Imagen 服务不可用，直接使用报纸模板")
                await _fallback_to_newspaper()
            else:
                generated_bytes = await gemini_imagen_service.generate_single_image(
                    prompt=image_prompt,
                    aspect_ratio="1:1",
                )
                if generated_bytes:
                    # 成功：编辑消息替换占位图
                    gen_embed = _make_embed()
                    gen_file = discord.File(
                        fp=io.BytesIO(generated_bytes), filename="summary_art.png"
                    )
                    gen_embed.set_image(url="attachment://summary_art.png")
                    await sent_msg.edit(embed=gen_embed, attachments=[gen_file])
                    log.info("报纸摘要配图已替换成功")
                else:
                    # 静默失败：用报纸模板 fallback
                    log.warning("Imagen 返回空结果，回退到报纸模板")
                    await _fallback_to_newspaper()
        except Exception as img_err:
            log.warning(f"报纸摘要配图生成失败: {img_err}", exc_info=True)
            await _fallback_to_newspaper()

        return {
            "success": True,
            "skip_ai_response": True,
            "message": "总结已发送到频道。",
        }
    except Exception as e:
        log.error(f"生成报纸摘要工具失败: {e}", exc_info=True)
        return {"error": f"生成报纸摘要失败: {e}"}
