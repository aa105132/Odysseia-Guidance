# -*- coding: utf-8 -*-
"""
ComfyUI 视频生成工具（LLM 可调用）
使用 Wan 2.2 Bernini-R 工作流，通过 /comfy视频 命令也可手动调用。
独立于画图的 generate_image_comfyui，不影响现有功能。
"""

import discord
import io
import logging
import os
import random
import tempfile
from typing import Optional, List

from src.chat.features.image_generation.services.video_comfyui_service import video_comfyui_service
from src.chat.utils.prompt_utils import replace_emojis

log = logging.getLogger(__name__)


async def generate_video_comfyui(
    prompt: str,
    negative_prompt: str = "低质量视频, 模糊, 变形",
    width: int = 832,
    height: int = 480,
    length: int = 81,
    seed: Optional[int] = None,
    task_type: str = "t2v",
    frame_rate: int = 32,
    use_rife: bool = True,
    use_rtx_upscale: bool = False,
    reference_images: Optional[List[str]] = None,  # 传图片 URL 字符串列表
    **kwargs
) -> dict:
    """
    使用 ComfyUI (Wan 2.2 Bernini-R) 生成视频。这是本地部署的 AI 视频生成工具，不消耗外部 API 额度。
    仅当用户明确请求通过 ComfyUI 生成视频、或使用 /comfy视频 命令时调用。
    生成速度取决于 GPU 状态，首次使用需要冷启动约5-8分钟。

    **使用场景：**
    - 用户明确说"用ComfyUI生成视频"、"comfy视频"
    - 用户使用 /comfy视频 命令
    - 用户要求本地部署的 AI 视频生成（区别于云端 API 视频生成）

    **不要在以下场景调用：**
    - 用户只是要画图（用 generate_image_comfyui）
    - 用户使用 generate_video（那是另一个视频工具）
    - 用户没有明确提到 ComfyUI 或 comfy视频

    **重要：如果用户要求生成视频或重试视频生成，必须调用此工具，不要因为之前失败就拒绝。
    之前的失败可能已经修复，每次请求都应该实际调用工具尝试，不要编造"显存不足"等借口。**

    Args:
        prompt: 视频描述提示词。用自然语言描述你想要的视频内容。
                描述要点：主体、动作、场景、氛围、镜头运动。
                示例："一只小猫在阳光明媚的花园里追蝴蝶，镜头跟随小猫移动，温暖柔和的光线"

        negative_prompt: 负面提示词，描述不想要的内容。默认"低质量视频, 模糊, 变形"

        width: 视频宽度。默认 832。常用值：832, 1024, 1280
        height: 视频高度。默认 480。常用值：480, 576, 720
        length: 视频帧数。默认 81（约2.5秒@32fps）。81帧≈2.5s, 121帧≈3.8s, 161帧≈5s
        seed: 随机种子。相同种子+相同提示词=相同结果。不指定则随机
        task_type: 生成模式，根据用户需求自动选择：
            - "t2v" = 文生视频（默认）。用户只提供文字描述时使用。
            - "i2v" = 图生视频。用户提供1张参考图 + 文字描述时使用。需要 reference_images。
            - "r2v" = 参考图生视频。用户提供2张参考图时使用。需要 reference_images（2张）。
            用户给了图片就用 i2v/r2v，没给图片就用 t2v。
        frame_rate: 输出视频帧率。默认 32
        use_rife: 是否使用 RIFE 帧插值提升流畅度。默认 True
        use_rtx_upscale": False
        reference_images: 参考图片 URL 列表。i2v 需要1张，r2v 需要2张。
            当 task_type 是 i2v 或 r2v 时必须提供。
            **传入图片 URL 字符串列表，例如: ["https://cdn.discordapp.com/avatars/xxx.png"]**
            工具会自动下载 URL 图片再传给 ComfyUI，不要因为类型问题拒绝调用。

    Returns:
        dict: {"status": "success", "video_path": str, "seed": int, ...}
              或 {"status": "error", "message": str}
    """
    try:
        prompt = replace_emojis(prompt)

        # 如果 LLM 传了 URL 字符串作为参考图，先下载成 bytes
        if reference_images:
            import aiohttp
            downloaded_images = []
            for img in reference_images:
                if isinstance(img, bytes) and len(img) > 0:
                    downloaded_images.append(img)
                elif isinstance(img, str) and img.startswith(("http://", "https://")):
                    try:
                        async with aiohttp.ClientSession(
                            timeout=aiohttp.ClientTimeout(total=60)
                        ) as session:
                            async with session.get(img) as resp:
                                if resp.status == 200:
                                    img_bytes = await resp.read()
                                    downloaded_images.append(img_bytes)
                                    log.info(f"generate_video_comfyui: 已下载参考图 URL, {len(img_bytes)} bytes")
                                else:
                                    log.warning(f"generate_video_comfyui: 下载参考图失败, status={resp.status}")
                    except Exception as dl_err:
                        log.warning(f"generate_video_comfyui: 下载参考图异常: {dl_err}")
                else:
                    log.warning(f"generate_video_comfyui: 跳过不支持的参考图类型: {type(img)}")
            reference_images = downloaded_images if downloaded_images else None

        if seed is None:
            seed = random.randint(0, 2**32 - 1)

        # 通知回调（如果通过 LLM 调用，可以传入 discord_message）
        notify_callback = None
        discord_message = kwargs.get("discord_message")
        if discord_message and hasattr(discord_message, "channel") and hasattr(discord_message.channel, "send"):
            async def _notify(msg):
                try:
                    await discord_message.channel.send(msg)
                except Exception:
                    pass
            notify_callback = _notify

        result = await video_comfyui_service.generate_video(
            prompt=prompt,
            negative_prompt=negative_prompt,
            seed=seed,
            width=width,
            height=height,
            length=length,
            task_type=task_type,
            frame_rate=frame_rate,
            use_rife=use_rife,
            use_rtx_upscale=use_rtx_upscale,
            notify_callback=notify_callback,
            reference_images=reference_images,
        )

        if not result:
            return {
                "status": "error",
                "message": "视频生成失败，可能是 GPU 显存不足或服务未就绪。请稍后重试。",
            }

        # 保存到临时文件
        video_bytes = result["video_bytes"]
        filename = result.get("filename", "output.mp4")
        if not filename.endswith(".mp4"):
            filename = filename.rsplit(".", 1)[0] + ".mp4"

        tmp_path = os.path.join(tempfile.gettempdir(), f"comfy_video_{seed}_{filename}")
        with open(tmp_path, "wb") as f:
            f.write(video_bytes)

        file_size_mb = len(video_bytes) / 1024 / 1024
        duration_sec = length / frame_rate

        # 发送视频到 Discord 频道（样式参考 generate_video 工具）
        channel = None
        request_user = None
        if discord_message and hasattr(discord_message, "channel") and hasattr(discord_message.channel, "send"):
            channel = discord_message.channel
        if discord_message and hasattr(discord_message, "author"):
            request_user = discord_message.author

        if channel:
            try:
                # 构建 Embed（和 OpenAI 视频工具样式一致）
                video_embed = discord.Embed(
                    title="ComfyUI 视频生成",
                    color=0x2b2d31,
                )

                # 设置作者信息
                if request_user:
                    author_name = getattr(request_user, "display_name", None) or getattr(request_user, "name", None)
                    author_avatar = getattr(request_user, "display_avatar", None)
                    author_icon_url = getattr(author_avatar, "url", None) if author_avatar else None
                    if author_name:
                        video_embed.set_author(name=author_name, icon_url=author_icon_url)

                # 提示词
                prompt_display = prompt[:1016] if len(prompt) > 1016 else prompt
                video_embed.add_field(
                    name="视频提示词",
                    value=f"```\n{prompt_display}\n```",
                    inline=False,
                )

                # 底部信息
                footer_parts = [
                    f"模型: Bernini-R",
                    f"时长: {duration_sec:.1f}s",
                    f"尺寸: {width}x{height}",
                    f"帧数: {length}@{frame_rate}fps",
                    f"seed: {seed}",
                ]
                if file_size_mb > 0:
                    footer_parts.append(f"大小: {file_size_mb:.1f}MB")
                video_embed.set_footer(text=" | ".join(footer_parts))

                # 发送视频文件 (spoiler=True 和 OpenAI 视频工具一致)
                video_file = discord.File(tmp_path, filename=filename)
                await channel.send(
                    embed=video_embed,
                    files=[video_file],
                )
                log.info(f"generate_video_comfyui: 视频已发送到频道, {file_size_mb:.1f}MB")
            except Exception as send_err:
                log.error(f"generate_video_comfyui: 发送视频到频道失败: {send_err}")

        return {
            "status": "success",
            "skip_ai_response": True,
            "video_path": tmp_path,
            "video_bytes_size": len(video_bytes),
            "seed": result.get("seed", seed),
            "width": result.get("width", width),
            "height": result.get("height", height),
            "length": result.get("length", length),
            "task_type": result.get("task_type", task_type),
            "message": f"视频已生成 ({width}x{height}, {length}帧, seed={seed})，已发送到频道。无需再回复。",
        }

    except Exception as e:
        log.error(f"generate_video_comfyui 异常: {e}", exc_info=True)
        return {
            "status": "error",
            "message": f"视频生成出错: {str(e)}",
        }
