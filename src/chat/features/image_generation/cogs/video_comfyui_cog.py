# -*- coding: utf-8 -*-
"""
/comfy视频 — ComfyUI 视频生成命令 (Discord Cog)
独立于 /comfy 画图命令，不影响现有画图功能。
使用 Wan 2.2 Bernini-R + LightX2V 4-step LoRA 工作流。
"""

import logging
import os
import random
import tempfile
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from src.chat.config.chat_config import COMFYUI_VIDEO_CONFIG
from src.chat.features.image_generation.services.video_comfyui_service import video_comfyui_service
from src.chat.features.odysseia_coin.service.coin_service import coin_service

log = logging.getLogger(__name__)

# 视频生成消耗金币（比画图贵，因为 GPU 时间更长）
VIDEO_GENERATION_COST = 10

# 预设分辨率
RESOLUTION_PRESETS = [
    ("832x480", 832, 480, "832x480 (推荐, 适合大多数场景)"),
    ("1024x576", 1024, 576, "1024x576 (高清)"),
    ("1280x720", 1280, 720, "1280x720 (720p, 需要更多显存)"),
    ("640x480", 640, 480, "640x480 (省显存)"),
    ("832x624", 832, 624, "832x624 (竖屏)"),
]

# 帧数预设
LENGTH_PRESETS = [
    ("81", 81, "81帧 ≈ 2.5秒 (最快)"),
    ("121", 121, "121帧 ≈ 3.8秒"),
    ("161", 161, "161帧 ≈ 5秒"),
    ("241", 241, "241帧 ≈ 7.5秒 (需要更多显存)"),
]


class VideoComfyUICog(commands.Cog):
    """ComfyUI 视频生成命令"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="comfy视频",
        description="使用 ComfyUI (Wan 2.2 Bernini-R) 生成视频"
    )
    @app_commands.describe(
        prompt="视频描述提示词，用自然语言描述你想要的视频内容",
        task_type="生成模式: t2v=文生视频(默认), i2v=图生视频(1张图), r2v=参考图生视频(2张图)",
        image0="参考图1 (i2v/r2v 模式必填)",
        image1="参考图2 (r2v 模式必填)",
        width="视频宽度",
        height="视频高度",
        length="视频帧数 (81帧≈2.5秒, 161帧≈5秒)",
        seed="随机种子 (相同种子=相同结果)",
        negative_prompt="负面提示词，描述不想要的内容",
        use_rife="是否使用 RIFE 帧插值提升流畅度 (默认开启)",
        use_rtx_upscale="是否使用 RTX 视频超分 (实验性, 共享GPU可能不支持)",
        frame_rate="输出帧率 (默认32)",
    )
    @app_commands.choices(
        task_type=[
            app_commands.Choice(name="t2v - 文生视频", value="t2v"),
            app_commands.Choice(name="i2v - 图生视频 (1张图)", value="i2v"),
            app_commands.Choice(name="r2v - 参考图生视频 (2张图)", value="r2v"),
        ],
        width=[app_commands.Choice(name=f"{w}px", value=w) for _, w, _, _ in RESOLUTION_PRESETS],
        height=[app_commands.Choice(name=f"{h}px", value=h) for _, _, h, _ in RESOLUTION_PRESETS],
        length=[app_commands.Choice(name=f"{l}帧", value=l) for _, l, _ in LENGTH_PRESETS],
    )
    async def comfy_video(
        self,
        interaction: discord.Interaction,
        prompt: str,
        task_type: str = "t2v",
        image0: Optional[discord.Attachment] = None,
        image1: Optional[discord.Attachment] = None,
        width: int = 832,
        height: int = 480,
        length: int = 81,
        seed: Optional[int] = None,
        negative_prompt: str = "低质量视频, 模糊, 变形",
        use_rife: bool = True,
        use_rtx_upscale: bool = False,
        frame_rate: int = 32,
    ):
        """ComfyUI 视频生成命令"""

        # 检查是否启用
        if not video_comfyui_service.is_enabled():
            await interaction.response.send_message(
                "❌ ComfyUI 视频生成功能未启用。请在 `.env` 中设置 `COMFYUI_VIDEO_ENABLED=True`",
                ephemeral=True,
            )
            return

        # 检查金币
        user_id = interaction.user.id
        balance = await coin_service.get_balance(user_id)
        if balance < VIDEO_GENERATION_COST:
            await interaction.response.send_message(
                f"❌ 灵石不足！生成视频需要 {VIDEO_GENERATION_COST} 灵石，你当前有 {balance} 灵石。",
                ephemeral=True,
            )
            return

        # 生成随机种子
        if seed is None:
            seed = random.randint(0, 2**32 - 1)

        # 收集参考图 (i2v/r2v 模式)
        reference_images = []
        mode_label = {"t2v": "文生视频", "i2v": "图生视频", "r2v": "参考图生视频"}.get(task_type, task_type)

        if task_type in ("i2v", "r2v"):
            if image0:
                img_bytes = await image0.read()
                reference_images.append(img_bytes)
            if task_type == "r2v" and image1:
                img_bytes = await image1.read()
                reference_images.append(img_bytes)

            # 验证图片数量
            needed = 1 if task_type == "i2v" else 2
            if len(reference_images) < needed:
                await interaction.response.send_message(
                    f"❌ {mode_label}模式需要 {needed} 张参考图，请通过 image{'0' if needed == 1 else '0和image1'} 参数上传。",
                    ephemeral=True,
                )
                return

        # 先回复，告知任务已接收
        img_info = f" | 🖼️ 参考{'图' if len(reference_images) == 1 else '图片'}: {len(reference_images)}张" if reference_images else ""
        await interaction.response.send_message(
            f"🎬 **ComfyUI {mode_label}**\n"
            f"📝 提示词: {prompt[:100]}{'...' if len(prompt) > 100 else ''}\n"
            f"📐 分辨率: {width}x{height} | 帧数: {length} | 种子: {seed}{img_info}\n"
            f"⏳ 正在准备生成环境..."
        )

        # 设置通知回调
        async def notify(msg: str):
            try:
                await interaction.followup.send(msg)
            except Exception:
                pass

        # 调用服务生成视频
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
            notify_callback=notify,
            reference_images=reference_images if reference_images else None,
        )

        if not result:
            await interaction.followup.send(
                "❌ 视频生成失败。可能是 GPU 显存不足或服务未就绪，请稍后重试。"
            )
            return

        # 扣金币
        new_balance = await coin_service.remove_coins(user_id, VIDEO_GENERATION_COST, "ComfyUI视频生成")

        # 保存视频到临时文件
        video_bytes = result["video_bytes"]
        filename = result.get("filename", "output.mp4")
        if not filename.endswith(".mp4"):
            filename = filename.rsplit(".", 1)[0] + ".mp4"

        tmp_path = os.path.join(tempfile.gettempdir(), f"comfy_video_{seed}_{filename}")
        with open(tmp_path, "wb") as f:
            f.write(video_bytes)

        # 发送视频文件
        duration_sec = length / frame_rate
        file_size_mb = len(video_bytes) / 1024 / 1024

        await interaction.followup.send(
            content=(
                f"✅ **视频生成完成！**\n"
                f"📐 {width}x{height} | 🎬 {length}帧 ({duration_sec:.1f}秒) | "
                f"🎲 seed={seed} | 💰 消耗 {VIDEO_GENERATION_COST} 灵石 (余额 {new_balance})\n"
                f"📦 文件大小: {file_size_mb:.1f}MB"
            ),
            file=discord.File(tmp_path, filename=filename),
        )

        # 清理临时文件
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(VideoComfyUICog(bot))
    log.info("VideoComfyUICog 已加载 — /comfy视频 命令可用")
