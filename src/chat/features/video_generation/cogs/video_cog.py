# -*- coding: utf-8 -*-

"""
视频生成斜杠命令 Cog
提供 /video 命令，让用户通过 AI 生成视频
"""

import logging
import io
import discord
from discord import app_commands
from discord.ext import commands

from src.chat.config.chat_config import VIDEO_GEN_CONFIG
from src.chat.features.odysseia_coin.service.coin_service import coin_service
from src.chat.features.video_generation.services.video_service import video_service

log = logging.getLogger(__name__)


class VideoGenerationCog(commands.Cog):
    """视频生成功能模块"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.video_cost = VIDEO_GEN_CONFIG.get("VIDEO_GENERATION_COST", 10)

    @app_commands.command(name="video", description="AI 视频生成 - 根据文字描述生成视频")
    @app_commands.describe(
        prompt="视频描述（支持中文自然语言）",
        duration="视频时长（秒，默认5秒）",
    )
    async def video(
        self,
        interaction: discord.Interaction,
        prompt: str,
        duration: int = 5,
    ):
        """/video 命令的实现"""
        # 检查服务是否可用
        if not video_service.is_available():
            await interaction.response.send_message(
                "视频生成服务当前未启用，请联系管理员在 Dashboard 中配置。",
                ephemeral=True,
            )
            return

        user_id = interaction.user.id

        # 获取最新的成本配置
        cost = VIDEO_GEN_CONFIG.get("VIDEO_GENERATION_COST", 10)

        # 1. 检查余额
        balance = await coin_service.get_balance(user_id)
        if balance < cost:
            await interaction.response.send_message(
                f"你的月光币余额不足！生成视频需要 {cost} 月光币，你当前只有 {balance}。",
                ephemeral=True,
            )
            return

        # 延迟响应，因为视频生成需要较长时间
        await interaction.response.defer(thinking=True)

        try:
            log.info(
                f"用户 {user_id} 请求生成视频, "
                f"提示词: {prompt[:100]}..., 时长: {duration}s"
            )

            # 生成视频
            result = await video_service.generate_video(
                prompt=prompt,
                duration=duration,
            )

            if result is None:
                await interaction.followup.send(
                    "视频生成失败了...请稍后再试或更换描述词。"
                )
                return

            # 3. 扣除月光币
            success = await coin_service.deduct_balance(
                user_id=user_id,
                amount=cost,
                reason=f"视频生成: {prompt[:50]}",
            )
            if not success:
                await interaction.followup.send(
                    "月光币扣除失败，请稍后再试。",
                    ephemeral=True,
                )
                return

            # 获取新余额
            new_balance = await coin_service.get_balance(user_id)

            # 4. 发送视频结果
            if result.format_type == "url" and result.url:
                # URL 格式：直接发送视频链接
                embed = discord.Embed(
                    title="AI 视频生成",
                    description=f"**提示词**: {prompt[:200]}",
                    color=0x9B59B6,
                )
                embed.add_field(
                    name="视频链接",
                    value=f"[点击观看]({result.url})",
                    inline=False,
                )
                embed.set_footer(
                    text=f"消耗 {cost} 月光币 | 余额: {new_balance} | 时长: ~{duration}s"
                )

                # 尝试下载视频文件并作为附件发送
                video_file = await self._try_download_video(result.url)
                if video_file:
                    await interaction.followup.send(
                        embed=embed,
                        file=video_file,
                    )
                else:
                    # 无法下载时只发送链接
                    await interaction.followup.send(
                        embed=embed,
                    )

            elif result.format_type == "html" and result.html_content:
                # HTML 格式：将 HTML 内容作为文件发送
                embed = discord.Embed(
                    title="AI 视频生成",
                    description=f"**提示词**: {prompt[:200]}",
                    color=0x9B59B6,
                )
                embed.set_footer(
                    text=f"消耗 {cost} 月光币 | 余额: {new_balance} | 时长: ~{duration}s"
                )

                # 将 HTML 内容作为文件发送
                html_bytes = result.html_content.encode('utf-8')
                html_file = discord.File(
                    io.BytesIO(html_bytes),
                    filename="video.html",
                )

                files = [html_file]

                # 如果也有 URL，尝试下载视频
                if result.url:
                    video_file = await self._try_download_video(result.url)
                    if video_file:
                        files.append(video_file)
                    else:
                        embed.add_field(
                            name="视频链接",
                            value=f"[点击观看]({result.url})",
                            inline=False,
                        )

                await interaction.followup.send(
                    embed=embed,
                    files=files,
                )

            elif result.text_response:
                # 只有文本响应，没有视频
                embed = discord.Embed(
                    title="AI 视频生成",
                    description=f"**提示词**: {prompt[:200]}",
                    color=0xE74C3C,
                )
                embed.add_field(
                    name="API 响应",
                    value=result.text_response[:1000],
                    inline=False,
                )
                embed.set_footer(
                    text=f"消耗 {cost} 月光币 | 余额: {new_balance}"
                )
                await interaction.followup.send(embed=embed)
            else:
                await interaction.followup.send(
                    "视频生成完成但未能获取到视频内容。"
                )

        except Exception as e:
            log.error(f"视频生成命令执行失败: {e}", exc_info=True)
            await interaction.followup.send(
                f"生成视频时发生错误: {str(e)[:200]}",
            )

    async def _try_download_video(self, url: str) -> discord.File | None:
        """
        尝试下载视频文件并作为 Discord 附件返回
        Discord 文件大小限制为 25MB（普通服务器）
        """
        import aiohttp

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=60),
                    headers={"User-Agent": "Mozilla/5.0"}
                ) as response:
                    if response.status != 200:
                        log.warning(f"视频下载失败，HTTP {response.status}")
                        return None

                    # 检查大小限制 (25MB)
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > 25 * 1024 * 1024:
                        log.info(f"视频文件过大 ({content_length} bytes)，跳过下载")
                        return None

                    data = await response.read()
                    if len(data) > 25 * 1024 * 1024:
                        log.info(f"视频文件过大 ({len(data)} bytes)，跳过下载")
                        return None

                    # 推断文件扩展名
                    ext = "mp4"
                    content_type = response.headers.get("Content-Type", "")
                    if "webm" in content_type:
                        ext = "webm"
                    elif "mov" in content_type or "quicktime" in content_type:
                        ext = "mov"

                    return discord.File(
                        io.BytesIO(data),
                        filename=f"generated_video.{ext}",
                    )

        except Exception as e:
            log.warning(f"下载视频失败: {e}")
            return None


async def setup(bot: commands.Bot):
    await bot.add_cog(VideoGenerationCog(bot))