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
                "呜…我现在不能在这里说话啦…", ephemeral=True
            )
            return

        # 1. 检查是否在禁用的频道中
        if channel and channel.id in chat_config.DISABLED_INTERACTION_CHANNEL_IDS:
            await interaction.response.send_message(
                "嘘... 在这里我需要保持安静，我们去别的地方聊吧？", ephemeral=True
            )
            return

        # 2. 检查是否在置顶的帖子中
        if isinstance(channel, discord.Thread) and channel.flags.pinned:
            await interaction.response.send_message(
                "唔... 这个帖子被置顶了，一定是很重要的内容。我们不要在这里聊天，以免打扰到大家哦。",
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
                content="欸？这个不能吃啦，给我看看真正的食物图片嘛！"
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

            response_text = await self.gemini_service.generate_text_with_image(
                prompt=prompt, image_bytes=image_bytes, mime_type=image.content_type
            )

            if not response_text:
                await interaction.edit_original_response(
                    content="抱歉，我有点累了，暂时无法评价呢。"
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
                evaluation = response_text
                affection_gain = 1
                coin_gain = 10
            else:
                evaluation = match.group(1).strip()
                affection_gain = int(match.group(2))
                coin_gain = int(match.group(3))

            # AI 绘图：月月与食物互动的画面
            generated_image_bytes = None
            if (
                FEEDING_CONFIG.get("IMAGEN_ENABLED")
                and image_prompt_text
                and gemini_imagen_service.is_available()
            ):
                try:
                    generated_image_bytes = await gemini_imagen_service.generate_single_image(
                        prompt=image_prompt_text,
                        aspect_ratio="1:1",
                        reference_image_bytes=image_bytes,
                        reference_image_mime=image.content_type,
                    )
                except Exception as img_err:
                    logger.warning(f"投喂绘图失败，回退到静态贴纸: {img_err}")

            await self.affection_service.add_affection_points(user_id, affection_gain)

            # 只有当 coin_gain 是正数时才增加月光币
            if coin_gain > 0:
                await self.coin_service.add_coins(user_id, coin_gain, reason="投喂奖励")

            # 替换表情并添加奖励消息
            evaluation_with_emojis = replace_emojis(evaluation)

            # 格式化系统提示，仅在获得奖励时显示
            system_message = ""
            if coin_gain > 0:
                system_message = f"> 你获得了 {coin_gain} 枚月光币！"

            # 创建 Embed
            embed_description = evaluation_with_emojis
            if system_message:
                embed_description += f"\n\n{system_message}"

            embed = discord.Embed(
                description=embed_description,
                color=discord.Color.pink(),  # 你可以自定义颜色
            )

            # 设置作者信息
            embed.set_author(
                name=interaction.user.display_name,
                icon_url=interaction.user.display_avatar.url,
            )

            # 从配置中获取图片 URL
            # --- 动态获取图片 ---

            # 构建附件列表
            attachments_to_send = []

            # 用户上传的食物图片作为缩略图
            food_file = discord.File(fp=io.BytesIO(image_bytes), filename=image.filename)
            attachments_to_send.append(food_file)
            embed.set_thumbnail(url=f"attachment://{image.filename}")

            if generated_image_bytes:
                gen_file = discord.File(fp=io.BytesIO(generated_image_bytes), filename="yueyue_feeding.png")
                attachments_to_send.append(gen_file)
                embed.set_image(url="attachment://yueyue_feeding.png")
            else:
                sticker_url = FEEDING_CONFIG.get("RESPONSE_IMAGE_URL")
                if sticker_url:
                    embed.set_image(url=sticker_url)

            embed.set_footer(text="月月对你的投喂做出回应...")

            await self.feeding_service.record_feeding(user_id)

            logger.info(f"准备发送投喂回复: attachments={len(attachments_to_send)}, 有生成图={generated_image_bytes is not None}")
            await interaction.edit_original_response(
                content=None, embed=embed, attachments=attachments_to_send
            )
            logger.info("投喂回复已发送到频道")

        except json.JSONDecodeError:
            logger.error(f"Failed to decode JSON response from Gemini: {response_text}")
            await interaction.edit_original_response(
                content="呜... 我、我有点尝不出来味道... 你能等一下再喂我吗？"
            )
        except Exception as e:
            logger.error(f"Error processing feeding command: {e}")
            await interaction.edit_original_response(
                content="啊呀，不小心噎着了！等、等我一下，稍后再试试看！"
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(FeedingCog(bot))
