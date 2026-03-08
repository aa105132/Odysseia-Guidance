# -*- coding: utf-8 -*-

import logging
import math
from typing import Optional
from pydantic import BaseModel, Field
import discord
from datetime import datetime
import io
import os
import re

# Pillow is used for image generation. Make sure it's installed.
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    # This will prevent the bot from starting if Pillow is not installed, which is good.
    raise ImportError(
        "Pillow is not installed. Please install it with 'pip install Pillow'"
    )

from src.chat.features.tools.tool_metadata import tool_metadata

log = logging.getLogger(__name__)


class SummarizeChannelParams(BaseModel):
    limit: int = Field(200, description="要获取的消息数量。")
    start_date: Optional[str] = Field(None, description="开始日期 (格式: YYYY-MM-DD)。")
    end_date: Optional[str] = Field(None, description="结束日期 (格式: YYYY-MM-DD)。")


@tool_metadata(
    name="总结",
    description="总结一下最近的聊天内容～可以指定消息数量和时间范围哦！",
    emoji="📝",
    category="总结",
)
async def summarize_channel(
    params: SummarizeChannelParams,
    **kwargs,
) -> str:
    """
    1. 获取当前频道的最近消息并返回一个准备好用于总结的字符串。
    2. **仅在用户明确表示想要"总结"、"概括"或回顾"最近的对话"时使用此工具。**
    3. 用户可以指定消息数量、开始日期或结束日期。
    4. 当指定了时间范围(start_date/end_date)时，会自动获取该范围内的所有消息（上限2000条），无需手动指定limit。

    [使用示例]
    - 用户说: "总结一下最近的对话"
      - 调用参数: `limit=200`
    - 用户说: "总结一下从昨天开始的对话"
      - 调用参数: `start_date="YYYY-MM-DD"` （不需要设置limit，系统会自动获取该时间范围内所有消息）
    - 用户说: "总结一下0点到现在的对话"
      - 调用参数: `start_date="YYYY-MM-DD"`（使用今天的日期）

    [返回格式与要求]
    - 函数返回一个包含消息历史的字符串，每条消息的格式为：`'作者(时间): 内容'`。
    - 你在收到内容后，需要将其内容总结成一段通顺的文字。
    - **重要：禁止使用任何 Markdown 格式，直接输出纯文本。**
    """
    channel = kwargs.get("channel")
    if not channel or not isinstance(channel, discord.abc.Messageable):
        return "错误：无法在当前上下文中找到有效的频道。"

    # 健壮性处理：如果传入的是字典，先用它创建 Pydantic 模型实例
    if not isinstance(params, SummarizeChannelParams):
        try:
            # 清理从模型收到的参数键，以防出现 '\"key\"' 等错误格式
            clean_dict = {k.strip().strip('"'): v for k, v in params.items()}
            params = SummarizeChannelParams(**clean_dict)
        except Exception as e:
            log.error(f"从字典 {params} 创建 SummarizeChannelParams 时出错: {e}")
            return f"错误：提供的参数格式不正确。详情: {e}"

    # 解析时间范围
    after = None
    if params.start_date:
        try:
            after = datetime.strptime(params.start_date, "%Y-%m-%d")
        except ValueError:
            return "错误: `start_date` 格式不正确，请使用 YYYY-MM-DD 格式。"

    before = None
    if params.end_date:
        try:
            before = datetime.strptime(params.end_date, "%Y-%m-%d")
        except ValueError:
            return "错误: `end_date` 格式不正确，请使用 YYYY-MM-DD 格式。"

    # 当指定了时间范围时，不限制消息数量（获取该范围内所有消息）
    # 否则使用用户指定的 limit，硬性上限 2000 条
    HARD_LIMIT = 2000
    if after or before:
        # 有时间范围时，获取该范围内所有消息（上限 HARD_LIMIT）
        limit = HARD_LIMIT
    else:
        limit = min(params.limit, HARD_LIMIT)

    channel_id = getattr(channel, "id", "未知")
    log.info(
        f"工具 'summarize_channel' 被调用，在频道 {channel_id} 中获取最多 {limit} 条消息"
        f"{'（时间范围: ' + str(after) + ' ~ ' + str(before) + '）' if after or before else ''}"
    )

    try:
        messages = []
        async for message in channel.history(limit=limit, before=before, after=after):
            if message.author.bot or not message.content:
                continue
            local_time = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
            messages.append(
                f"{message.author.display_name}({local_time}): {message.content}"
            )

        messages.reverse()

        if not messages:
            return "在指定范围内没有找到消息。"

        return "\n".join(messages)

    except discord.Forbidden:
        log.error(f"机器人缺少访问频道 {channel_id} 历史记录的权限。")
        return "错误：我没有权限查看这个频道的历史记录。"
    except Exception as e:
        log.error(f"处理频道 {channel_id} 的消息时发生未知错误: {e}")
        return f"错误：处理消息时发生未知错误: {e}"


def _load_summary_image_assets(
    title_font_size: int,
    body_font_size: int,
    logo_max_size: tuple[int, int] = (250, 250),
):
    """加载总结图片共用资源。"""
    logo_path = "src/chat/assets/logo.png"
    font_path = "src/chat/assets/font.TTF"

    try:
        title_font = ImageFont.truetype(font_path, size=title_font_size)
        body_font = ImageFont.truetype(font_path, size=body_font_size)
    except IOError:
        log.error(f"字体文件在 '{font_path}' 未找到！无法生成图片。")
        return None

    logo_img = None
    if os.path.exists(logo_path):
        logo_img = Image.open(logo_path).convert("RGBA")
        logo_img.thumbnail(logo_max_size, Image.Resampling.LANCZOS)
    else:
        log.warning(f"Logo 文件未找到: {logo_path}")

    return title_font, body_font, logo_img


def _strip_custom_emojis(text: str) -> str:
    emoji_pattern = r"<a?:.+?:\d+>"
    return re.sub(emoji_pattern, "", text or "").strip()


def text_to_summary_image(
    text: str, title: str = "月月的总结时间到!"
) -> Optional[bytes]:
    """
    将文本转换为一张自适应高度的长图，能正确处理换行和避让右上角的Logo。
    """
    # --- 1. 配置 ---
    LOGO_PATH = "src/chat/assets/logo.png"
    FONT_PATH = "src/chat/assets/font.TTF"
    IMG_WIDTH = 1200
    MARGIN = 60
    LINE_SPACING = 15
    TITLE_FONT_SIZE = 48
    BODY_FONT_SIZE = 32
    BG_COLOR = (43, 45, 49, 255)  # 接近 Discord 的深色背景
    TEXT_COLOR = (220, 221, 222, 255)  # 接近 Discord 的文字颜色
    LOGO_MAX_SIZE = (250, 250)

    try:
        # --- 2. 资源加载和预处理 ---
        loaded_assets = _load_summary_image_assets(
            title_font_size=TITLE_FONT_SIZE,
            body_font_size=BODY_FONT_SIZE,
            logo_max_size=LOGO_MAX_SIZE,
        )
        if not loaded_assets:
            return None
        title_font, body_font, logo_img = loaded_assets
        logo_w, logo_h = logo_img.size if logo_img else (0, 0)

        clean_text = _strip_custom_emojis(text)

        if not clean_text:
            return None

        # --- 3. 精确排版与高度计算 ---
        lines = []
        current_y = float(MARGIN)

        # --- 排版标题 ---
        title_bbox = title_font.getbbox(title)
        title_height = title_bbox[3] - title_bbox[1]
        lines.append(
            {"text": title, "y": current_y, "font": title_font, "color": TEXT_COLOR}
        )
        current_y += title_height + 30  # 标题和正文间距

        # --- 排版正文 (Character-by-character wrapping) ---
        body_bbox = body_font.getbbox("A")
        line_height = (body_bbox[3] - body_bbox[1]) + LINE_SPACING

        full_width = IMG_WIDTH - 2 * MARGIN
        short_width = IMG_WIDTH - 2 * MARGIN - logo_w - int(MARGIN / 2)
        logo_area_y_end = MARGIN + logo_h

        paragraphs = clean_text.split("\n")
        for para in paragraphs:
            if not para.strip():  # 处理空行
                current_y += line_height
                continue

            current_line = ""
            for char in para:
                max_width_for_line = (
                    short_width
                    if current_y < logo_area_y_end and logo_img
                    else full_width
                )

                line_if_added = f"{current_line}{char}"
                if body_font.getlength(line_if_added) <= max_width_for_line:
                    current_line = line_if_added
                else:
                    lines.append(
                        {
                            "text": current_line,
                            "y": current_y,
                            "font": body_font,
                            "color": TEXT_COLOR,
                        }
                    )
                    current_y += line_height
                    current_line = char

            if current_line:
                lines.append(
                    {
                        "text": current_line,
                        "y": current_y,
                        "font": body_font,
                        "color": TEXT_COLOR,
                    }
                )
                current_y += line_height

        # 确保底部有足够的边距
        total_height = int(current_y - line_height + body_bbox[3] + MARGIN)

        # --- 4. 图像绘制 ---
        image = Image.new("RGBA", (IMG_WIDTH, total_height), BG_COLOR)
        draw = ImageDraw.Draw(image)

        if logo_img:
            logo_x = IMG_WIDTH - logo_w - MARGIN
            logo_y = MARGIN
            image.paste(logo_img, (logo_x, logo_y), logo_img)

        for line_info in lines:
            draw.text(
                (MARGIN, line_info["y"]),
                line_info["text"],
                font=line_info["font"],
                fill=line_info["color"],
            )

        # --- 5. 返回图片数据 ---
        output_buffer = io.BytesIO()
        image.save(output_buffer, format="PNG")
        image_bytes = output_buffer.getvalue()

        log.info(
            f"成功创建长图，尺寸: {IMG_WIDTH}x{total_height}，大小: {len(image_bytes) / 1024:.2f} KB"
        )
        return image_bytes

    except Exception as e:
        log.error(f"创建文本转图片时发生严重错误: {e}", exc_info=True)
        return None


def _clean_newspaper_text(text: str) -> str:
    if not text:
        return ""

    cleaned = str(text)
    cleaned = re.sub(r"<a?:[^:]+:\d+>", "", cleaned)
    cleaned = re.sub(r"\[\s*citation\s*:\s*\d+\s*\]", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"(?<![A-Za-z])citation\s*:\s*\d+(?![A-Za-z])",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\[\^?\d+\]", "", cleaned)
    cleaned = re.sub(r"^\s*\[\^?\d+\]:.*$", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"<https?://[^>]+>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"```(?:[\w+-]+)?\s*\n?(.*?)```",
        r"\1",
        cleaned,
        flags=re.DOTALL,
    )
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    cleaned = re.sub(r"^\s*>\s?", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s*#{1,6}\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s*[-*+]\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s*\d+\.\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__(.*?)__", r"\1", cleaned)
    cleaned = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", cleaned)
    cleaned = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def text_to_newspaper_brief_image(
    body: str,
    title: str,
    subtitle: Optional[str] = None,
    section_name: str = "月月简报",
    issue_date: Optional[str] = None,
    dek: Optional[str] = None,
) -> Optional[bytes]:
    """将摘要正文渲染为报纸风排版图片。"""
    IMG_WIDTH = 1400
    MARGIN = 64
    MASTHEAD_H = 110
    META_H = 44
    SECTION_LABEL_H = 36
    FOOTER_H = 44
    TOP_PAD = 28
    COL_GUTTER = 52
    THICK_DIV = 3
    THIN_DIV = 1
    FONT_PATH = "src/chat/assets/font.TTF"
    LOGO_PATH = "src/chat/assets/logo.png"

    BG = (245, 243, 237)
    MASTHEAD_BG = (25, 22, 18)
    MASTHEAD_FG = (255, 255, 255)
    META_BG = (50, 44, 34)
    META_FG = (189, 178, 155)
    LABEL_BG = (25, 22, 18)
    LABEL_FG = (255, 255, 255)
    HEADLINE_C = (22, 20, 16)
    BODY_C = (58, 52, 40)
    DEK_C = (80, 70, 52)
    SECTION_H_C = (22, 20, 16)
    DIV_C = (158, 146, 122)

    PUB_FS = 54
    META_FS = 21
    LABEL_FS = 17
    HEADLINE_FS = 50
    DEK_FS = 27
    BODY_FS = 25
    SHEAD_FS = 23
    SBODY_FS = 21
    FOOTER_FS = 17

    def _lf(size: int):
        try:
            return ImageFont.truetype(FONT_PATH, size=size)
        except IOError:
            return ImageFont.load_default()

    try:
        c_title = _clean_newspaper_text(title) or "月月简报"
        c_section = _clean_newspaper_text(section_name) or "月月简报"
        c_date = _clean_newspaper_text(issue_date or "") or datetime.now().strftime(
            "%Y-%m-%d"
        )
        c_subtitle = _clean_newspaper_text(subtitle or "")
        c_dek = _clean_newspaper_text(dek or "")
        c_body = _clean_newspaper_text(body)

        if not c_body:
            return None

        fp = _lf(PUB_FS)
        fm = _lf(META_FS)
        fl = _lf(LABEL_FS)
        fh = _lf(HEADLINE_FS)
        fd = _lf(DEK_FS)
        fb = _lf(BODY_FS)
        fsh = _lf(SHEAD_FS)
        fsb = _lf(SBODY_FS)
        ff = _lf(FOOTER_FS)

        content_w = IMG_WIDTH - MARGIN * 2

        def _tw(text: str, font) -> float:
            try:
                return font.getbbox(text)[2]
            except Exception:
                return len(text) * font.size * 0.66

        def _lh(font, sp: int = 10) -> int:
            try:
                bb = font.getbbox("测A")
                return bb[3] - bb[1] + sp
            except Exception:
                return font.size + sp

        def _wrap(text: str, font, max_w: int) -> list:
            lines = []
            for para in text.split("\n"):
                para = para.strip()
                if not para:
                    lines.append("")
                    continue
                cur = ""
                for ch in para:
                    if _tw(cur + ch, font) <= max_w:
                        cur += ch
                    else:
                        if cur:
                            lines.append(cur)
                        cur = ch
                if cur:
                    lines.append(cur)
            return lines

        def _bh(lines: list, font, sp: int = 10) -> int:
            return max(1, len(lines)) * _lh(font, sp)

        def _parse_sections(text: str) -> list:
            sections = []
            cur_head = ""
            cur_body = []
            for line in text.split("\n"):
                raw = line.strip()
                m = re.match(r"^#{1,3}\s+(.+)", raw)
                if m:
                    if cur_body or cur_head:
                        sections.append((cur_head, "\n".join(cur_body).strip()))
                    cur_head = m.group(1).strip()
                    cur_body = []
                else:
                    cur_body.append(raw)
            if cur_body or cur_head:
                sections.append((cur_head, "\n".join(cur_body).strip()))
            return sections

        sections = _parse_sections(c_body)
        has_sections = len(sections) > 1 or (len(sections) == 1 and sections[0][0])

        if has_sections:
            main_head_raw, main_body_raw = sections[0]
            sub_sections = sections[1:]
        else:
            main_head_raw = ""
            main_body_raw = c_body
            sub_sections = []

        headline_text = c_subtitle or main_head_raw or c_title
        dek_text = c_dek
        main_body_text = main_body_raw

        pub_lines = _wrap(c_title, fp, content_w - 210)
        pub_h = _bh(pub_lines, fp, 8)

        hl_lines = _wrap(headline_text, fh, content_w) if headline_text else []
        hl_h = _bh(hl_lines, fh, 8) if hl_lines else 0
        headline_section_h = (SECTION_LABEL_H + 14 + hl_h + 8) if hl_lines else 0

        dek_lines = _wrap(dek_text, fd, content_w) if dek_text else []
        dek_h = (_bh(dek_lines, fd, 8) + 14) if dek_lines else 0

        col_w = (content_w - COL_GUTTER) // 2
        body_lines = _wrap(main_body_text, fb, col_w) if main_body_text else []
        half = math.ceil(len(body_lines) / 2)
        left_lines = body_lines[:half]
        right_lines = body_lines[half:]
        body_h = max(_bh(left_lines, fb), _bh(right_lines, fb)) if body_lines else 0
        body_section_h = (THICK_DIV + 16 + body_h + 24) if body_h else 0

        grid_h = 0
        if sub_sections:
            gc_w = (content_w - COL_GUTTER) // 2
            col_hts = [0, 0]
            for i, (sh, sb) in enumerate(sub_sections):
                ci = i % 2
                sh_lines = _wrap(sh, fsh, gc_w - 8) if sh else []
                sb_lines = _wrap(sb, fsb, gc_w - 8) if sb else []
                sh_h = _bh(sh_lines, fsh, 6) if sh_lines else 0
                sb_h = _bh(sb_lines, fsb, 5) if sb_lines else 0
                col_hts[ci] += sh_h + sb_h + 24
            grid_h = THICK_DIV + 20 + max(col_hts) + 20

        total_h = (
            MASTHEAD_H + META_H + THICK_DIV + TOP_PAD
            + headline_section_h + dek_h
            + body_section_h + grid_h
            + FOOTER_H + 20
        )
        total_h = max(total_h, 700)

        img = Image.new("RGB", (IMG_WIDTH, total_h), BG)
        draw = ImageDraw.Draw(img)

        logo_img = None
        if os.path.exists(LOGO_PATH):
            try:
                logo_img = Image.open(LOGO_PATH).convert("RGBA")
                logo_img.thumbnail((86, 86), Image.Resampling.LANCZOS)
            except Exception:
                logo_img = None

        # 1. 报头
        draw.rectangle([0, 0, IMG_WIDTH, MASTHEAD_H], fill=MASTHEAD_BG)
        py = max(14, (MASTHEAD_H - pub_h) // 2)
        for line in pub_lines:
            draw.text((MARGIN, py), line, font=fp, fill=MASTHEAD_FG)
            py += _lh(fp, 8)
        if logo_img:
            lw, lhv = logo_img.size
            img.paste(logo_img, (IMG_WIDTH - MARGIN - lw, (MASTHEAD_H - lhv) // 2), logo_img)

        # 2. 元数据栏
        draw.rectangle([0, MASTHEAD_H, IMG_WIDTH, MASTHEAD_H + META_H], fill=META_BG)
        my = MASTHEAD_H + (META_H - _lh(fm, 0)) // 2
        draw.text((MARGIN, my), c_section, font=fm, fill=META_FG)
        rw = _tw(c_date, fm)
        draw.text((IMG_WIDTH - MARGIN - rw, my), c_date, font=fm, fill=META_FG)

        # 3. 分割线
        dv_y = MASTHEAD_H + META_H
        draw.rectangle([0, dv_y, IMG_WIDTH, dv_y + THICK_DIV], fill=HEADLINE_C)
        cur_y = dv_y + THICK_DIV + TOP_PAD

        # 4. 大标题
        if hl_lines:
            lx2 = MARGIN + 124
            draw.rectangle([MARGIN, cur_y, lx2, cur_y + SECTION_LABEL_H], fill=LABEL_BG)
            lby = cur_y + (SECTION_LABEL_H - _lh(fl, 0)) // 2
            draw.text((MARGIN + 10, lby), "TOP STORY", font=fl, fill=LABEL_FG)
            cur_y += SECTION_LABEL_H + 12
            for line in hl_lines:
                draw.text((MARGIN, cur_y), line, font=fh, fill=HEADLINE_C)
                cur_y += _lh(fh, 8)
            cur_y += 8

        # 5. Dek
        if dek_lines:
            for line in dek_lines:
                draw.text((MARGIN, cur_y), line, font=fd, fill=DEK_C)
                cur_y += _lh(fd, 8)
            cur_y += 12

        # 6. 正文双栏
        if body_lines:
            draw.line([(MARGIN, cur_y), (IMG_WIDTH - MARGIN, cur_y)], fill=DIV_C, width=THICK_DIV)
            cur_y += THICK_DIV + 16
            ly = cur_y
            ry = cur_y
            rx = MARGIN + col_w + COL_GUTTER
            for line in left_lines:
                if line:
                    draw.text((MARGIN, ly), line, font=fb, fill=BODY_C)
                ly += _lh(fb)
            mid_x = MARGIN + col_w + COL_GUTTER // 2
            bottom_col = max(ly, ry + _bh(right_lines, fb))
            draw.line([(mid_x, cur_y), (mid_x, bottom_col)], fill=DIV_C, width=THIN_DIV)
            for line in right_lines:
                if line:
                    draw.text((rx, ry), line, font=fb, fill=BODY_C)
                ry += _lh(fb)
            cur_y = max(ly, ry) + 24

        # 7. 子分节网格
        if sub_sections:
            draw.line([(MARGIN, cur_y), (IMG_WIDTH - MARGIN, cur_y)], fill=HEADLINE_C, width=2)
            cur_y += 20
            gc_w = (content_w - COL_GUTTER) // 2
            col_xs = [MARGIN, MARGIN + gc_w + COL_GUTTER]
            col_ys = [cur_y, cur_y]
            for i, (sh, sb) in enumerate(sub_sections):
                ci = i % 2
                gx = col_xs[ci]
                gy = col_ys[ci]
                if sh:
                    draw.line([(gx, gy), (gx + 3, gy)], fill=HEADLINE_C, width=4)
                    for sl in _wrap(sh, fsh, gc_w - 10):
                        draw.text((gx + 6, gy), sl, font=fsh, fill=SECTION_H_C)
                        gy += _lh(fsh, 6)
                if sb:
                    for sl in _wrap(sb, fsb, gc_w - 10):
                        draw.text((gx + 6, gy), sl, font=fsb, fill=BODY_C)
                        gy += _lh(fsb, 5)
                col_ys[ci] = gy + 20
            mid_gx = MARGIN + gc_w + COL_GUTTER // 2
            draw.line([(mid_gx, cur_y), (mid_gx, max(col_ys))], fill=DIV_C, width=THIN_DIV)
            cur_y = max(col_ys) + 16

        # 8. 底栏
        ft_y_start = total_h - FOOTER_H
        draw.rectangle([0, ft_y_start, IMG_WIDTH, total_h], fill=MASTHEAD_BG)
        ft_text = f"月月简报  ·  {c_date}"
        ftw = _tw(ft_text, ff)
        fty = ft_y_start + (FOOTER_H - _lh(ff, 0)) // 2
        draw.text(((IMG_WIDTH - ftw) // 2, fty), ft_text, font=ff, fill=META_FG)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        image_bytes = buf.getvalue()
        log.info(f"成功创建报纸摘要图，尺寸: {IMG_WIDTH}x{total_h}，大小: {len(image_bytes)/1024:.1f} KB")
        return image_bytes

    except Exception as e:
        log.error(f"创建报纸摘要图时发生严重错误: {e}", exc_info=True)
        return None


__all__ = [
    "SummarizeChannelParams",
    "summarize_channel",
    "text_to_summary_image",
    "text_to_newspaper_brief_image",
]
