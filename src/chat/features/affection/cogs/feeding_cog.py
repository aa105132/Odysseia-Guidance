import os
import discord
import json
import io
import re
from discord import app_commands
from discord.ext import commands

from src.chat.utils.database import chat_db_manager
from src.chat.features.affection.service.affection_service import AffectionService
from src.chat.features.affection.service.feeding_service import feeding_service
from src.chat.features.odysseia_coin.service.coin_service import CoinService
from src.chat.services.gemini_service import gemini_service
from src.chat.services.prompt_service import prompt_service
from src.chat.config.chat_config import FEEDING_CONFIG, PROMPT_CONFIG
from src.chat.config import chat_config
from src.chat.utils.prompt_utils import extract_persona_prompt, replace_emojis
from src.config import DEVELOPER_USER_IDS
from src.chat.services.event_service import event_service
from src.chat.features.image_generation.services.gemini_imagen_service import gemini_imagen_service
import logging

logger = logging.getLogger(__name__)


class FeedingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.affection_service = AffectionService()
        self.coin_service = CoinService()
        self.gemini_service = gemini_service  # 使用全局实例
        self.feeding_service = feeding_service

    @app_commands.command(name="投喂", description="在吃饭?给月月来一口怎么样")
    @app_commands.describe(image="拍一下你这顿饭是什么吧!")
    async def feed(self, interaction: discord.Interaction, image: discord.Attachment):
        # --- 交互可用性检查 ---
        channel = interaction.channel
        # 0. 检查频道是否被禁言
        if channel and await chat_db_manager.is_channel_muted(channel.id):
            await interaction.response.send_message(
                "哼…这里被禁言了啦，我才不是不想理你！", ephemeral=True
            )
            return

        # 1. 检查是否在禁用的频道中
        if channel and channel.id in chat_config.DISABLED_INTERACTION_CHANNEL_IDS:
            await interaction.response.send_message(
                "啧…这个地方不让说话，换别处找我！", ephemeral=True
            )
            return

        # 2. 检查是否在置顶的帖子中
        if isinstance(channel, discord.Thread) and channel.flags.pinned:
            await interaction.response.send_message(
                "这种置顶帖很严肃的好不好，别在这里闹啦！",
                ephemeral=True,
            )
            return

        user_id = interaction.user.id

        # 检查用户是否为开发者，如果是，则绕过冷却时间检查
        if interaction.user.id not in DEVELOPER_USER_IDS:
            # 使用 FeedingService 检查是否可以投喂
            can_feed, message = await self.feeding_service.can_feed(user_id)
            if not can_feed:
                await interaction.response.send_message(message, ephemeral=False)
                return

        await interaction.response.send_message("月月正在嚼嚼嚼...", ephemeral=False)

        if not image.content_type.startswith("image/"):
            await interaction.edit_original_response(
                content="笨蛋，这又不是吃的！给我看真的食物啦！"
            )
            return

        try:
            image_bytes = await image.read()

            # 构建包含月月人设的提示词
            persona_part = extract_persona_prompt(
                prompt_service.get_prompt("SYSTEM_PROMPT")
            )
            base_prompt = PROMPT_CONFIG.get("feeding_prompt", "")
            # 注入今日穿着到 image_prompt 描述要求中
            outfit_desc = chat_config.DAILY_OUTFIT_CONFIG.get("CURRENT_OUTFIT_DESCRIPTION", "")
            if outfit_desc:
                base_prompt += f"\n\n## 重要：月月今日穿着\n{outfit_desc}\n请在 `<image_prompt>` 的图片描述中体现月月的今日穿着。"
            prompt = f"{persona_part}\n\n{base_prompt}"

            vision_model = str(FEEDING_CONFIG.get("VISION_MODEL") or "").strip()
            logger.info(
                "投喂视觉评价开始: model=%s, mime=%s, image_bytes=%s",
                vision_model or "<default>",
                image.content_type,
                len(image_bytes),
            )
            response_text = await self.gemini_service.generate_text_with_image(
                prompt=prompt,
                image_bytes=image_bytes,
                mime_type=image.content_type,
                # Discord CDN URL 可能过期；只传已经下载成功的图片数据，避免重复输入同一张图。
                image_url=None,
                model_name_override=vision_model or None,
            )

            if not response_text:
                logger.error(
                    "投喂视觉评价返回空内容: model=%s, mime=%s, image_bytes=%s",
                    vision_model or "<default>",
                    image.content_type,
                    len(image_bytes),
                )
                await interaction.edit_original_response(
                    content="呜…嚼不动了，脑子转不起来了，等会再喂嘛…"
                )
                return

            # 提取 image_prompt 标签（在解析 affection/coins 之前）
            image_prompt_match = re.search(r"<image_prompt:(.*?)>", response_text)
            image_prompt_text = image_prompt_match.group(1).strip() if image_prompt_match else None
            if image_prompt_match:
                response_text = response_text.replace(image_prompt_match.group(0), "").strip()

            pattern = re.compile(
                r"(.*?)<affection:([+-]?\d+);coins:([+-]?\d+)>", re.DOTALL
            )
            match = pattern.search(response_text)

            if not match:
                logger.error(f"解析投喂评价失败。原始文本: '{response_text}'")
                await interaction.edit_original_response(
                    content="呜…这口我没尝明白，评价格式也乱掉了，等会再喂我一次好不好？"
                )
                return
            else:
                evaluation = match.group(1).strip()
                affection_gain = int(match.group(2))
                coin_gain = int(match.group(3))

            # --- 第一步：先发送文字评价（不等绘图） ---
            await self.affection_service.add_affection_points(user_id, affection_gain)

            # 只有当 coin_gain 是正数时才增加灵石
            if coin_gain > 0:
                await self.coin_service.add_coins(user_id, coin_gain, reason="投喂奖励")

            # 替换表情并添加奖励消息
            evaluation_with_emojis = replace_emojis(evaluation)

            # 格式化系统提示，仅在获得奖励时显示
            system_message = ""
            if coin_gain > 0:
                system_message = f"> 你获得了 {coin_gain} 枚灵石！"

            # 创建 Embed（先不带生成图）
            embed_description = evaluation_with_emojis
            if system_message:
                embed_description += f"\n\n{system_message}"

            embed = discord.Embed(
                description=embed_description,
                color=discord.Color.pink(),
            )
            embed.set_author(
                name=interaction.user.display_name,
                icon_url=interaction.user.display_avatar.url,
            )

            # 用户上传的食物图片作为缩略图
            food_file = discord.File(fp=io.BytesIO(image_bytes), filename=image.filename)
            embed.set_thumbnail(url=f"attachment://{image.filename}")
            embed.set_footer(text="月月对你的投喂做出回应...")

            # 占位图：月月正在画画中
            placeholder_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..", "assets", "feeding_placeholder.png"
            )
            attachments_first = [food_file]
            if os.path.exists(placeholder_path):
                ph_file = discord.File(fp=open(placeholder_path, "rb"), filename="yueyue_drawing.png")
                attachments_first.append(ph_file)
                embed.set_image(url="attachment://yueyue_drawing.png")

            await self.feeding_service.record_feeding(user_id)

            # 立即发送文字评价 + 占位图
            await interaction.edit_original_response(
                content=None, embed=embed, attachments=attachments_first
            )
            logger.info("投喂文字回复+占位图已发送，开始生成配图...")

            # --- 第二步：后台生成图片，完成后编辑消息贴上 ---
            fallback_image_prompt_text = (
                "银白色长发扎成高马尾的可爱银狐少女月月正在认真观察用户投喂的参考图片，"
                "左眼淡绿色右眼淡蓝色异色瞳，白皙肤色，毛茸茸的白色狐耳内侧粉色，"
                "银白色蓬松大尾巴，马尾处插着银色月牙发簪，两侧戴着细微尖三角形耳坠。"
                "请以参考图内容为核心道具或画面主题：如果是食物就吃掉或评价；"
                "如果不是食物，就吐槽、研究、摆弄、举起来展示或用可爱的方式互动。"
                "Q版风格，温暖明亮，画面里要能看出参考图的主要元素。"
            )
            effective_image_prompt_text = image_prompt_text or fallback_image_prompt_text

            if (
                FEEDING_CONFIG.get("IMAGEN_ENABLED")
                and gemini_imagen_service.is_available()
            ):
                try:
                    generated_image_bytes = await gemini_imagen_service.generate_single_image(
                        prompt=effective_image_prompt_text,
                        aspect_ratio="1:1",
                        reference_image_bytes=image_bytes,
                        reference_image_mime=image.content_type,
                    )
                    if generated_image_bytes:
                        # 重新构建 embed 和附件（编辑需要重新提供所有附件）
                        embed2 = discord.Embed(
                            description=embed_description,
                            color=discord.Color.pink(),
                        )
                        embed2.set_author(
                            name=interaction.user.display_name,
                            icon_url=interaction.user.display_avatar.url,
                        )
                        food_file2 = discord.File(fp=io.BytesIO(image_bytes), filename=image.filename)
                        gen_file = discord.File(fp=io.BytesIO(generated_image_bytes), filename="yueyue_feeding.png")
                        embed2.set_thumbnail(url=f"attachment://{image.filename}")
                        embed2.set_image(url="attachment://yueyue_feeding.png")
                        embed2.set_footer(text="月月对你的投喂做出回应...")

                        await interaction.edit_original_response(
                            content=None, embed=embed2, attachments=[food_file2, gen_file]
                        )
                        logger.info("投喂配图已追加到消息")
                    else:
                        # generate_single_image 返回 None（静默失败），替换为失败占位图
                        failed_path = os.path.join(
                            os.path.dirname(os.path.abspath(__file__)),
                            "..", "assets", "feeding_failed.png"
                        )
                        if os.path.exists(failed_path):
                            try:
                                embed_fail = discord.Embed(
                                    description=embed_description,
                                    color=discord.Color.pink(),
                                )
                                embed_fail.set_author(
                                    name=interaction.user.display_name,
                                    icon_url=interaction.user.display_avatar.url,
                                )
                                food_file3 = discord.File(fp=io.BytesIO(image_bytes), filename=image.filename)
                                fail_file = discord.File(fp=open(failed_path, "rb"), filename="yueyue_failed.png")
                                embed_fail.set_thumbnail(url=f"attachment://{image.filename}")
                                embed_fail.set_image(url="attachment://yueyue_failed.png")
                                embed_fail.set_footer(text="月月对你的投喂做出回应...")
                                await interaction.edit_original_response(
                                    content=None, embed=embed_fail, attachments=[food_file3, fail_file]
                                )
                                logger.info("投喂绘图返回空，已替换为失败占位图")
                            except Exception as edit_err:
                                logger.error(f"替换失败占位图也失败了: {edit_err}")
                except Exception as img_err:
                    logger.warning(f"投喂绘图失败: {img_err}")
                    # 绘图失败：替换占位图为失败提示图
                    failed_path = os.path.join(
                        os.path.dirname(os.path.abspath(__file__)),
                        "..", "assets", "feeding_failed.png"
                    )
                    if os.path.exists(failed_path):
                        try:
                            embed_fail = discord.Embed(
                                description=embed_description,
                                color=discord.Color.pink(),
                            )
                            embed_fail.set_author(
                                name=interaction.user.display_name,
                                icon_url=interaction.user.display_avatar.url,
                            )
                            food_file3 = discord.File(fp=io.BytesIO(image_bytes), filename=image.filename)
                            fail_file = discord.File(fp=open(failed_path, "rb"), filename="yueyue_failed.png")
                            embed_fail.set_thumbnail(url=f"attachment://{image.filename}")
                            embed_fail.set_image(url="attachment://yueyue_failed.png")
                            embed_fail.set_footer(text="月月对你的投喂做出回应...")
                            await interaction.edit_original_response(
                                content=None, embed=embed_fail, attachments=[food_file3, fail_file]
                            )
                            logger.info("投喂绘图失败，已替换为失败占位图")
                        except Exception as edit_err:
                            logger.error(f"替换失败占位图也失败了: {edit_err}")

        except json.JSONDecodeError:
            logger.error(f"Failed to decode JSON response from Gemini: {response_text}")
            await interaction.edit_original_response(
                content="呜…这口味道怪怪的，尝不出来了…等下再喂好不好？"
            )
        except Exception as e:
            logger.error(f"Error processing feeding command: {e}")
            await interaction.edit_original_response(
                content="咳咳…噎、噎到了！让我缓缓…等会再吃啦！"
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(FeedingCog(bot))
