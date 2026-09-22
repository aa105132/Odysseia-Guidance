"""灵石红包指令、持久化领取按钮与过期退款。"""

import asyncio
import logging

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

from src.chat.features.odysseia_coin.service.red_envelope_service import (
    RedEnvelopeError,
    red_envelope_service,
)

log = logging.getLogger(__name__)
KIND_NAMES = {"normal": "普通红包", "lucky": "拼手气红包", "password": "口令红包"}
STATUS_NAMES = {
    "pending": "正在发送", "active": "等待领取", "completed": "已领完", "expired": "已过期，余款已退回", "cancelled": "已取消，余款已退回",
}


def envelope_embed(envelope: dict) -> discord.Embed:
    embed = discord.Embed(
        title=f"🧧 {KIND_NAMES[envelope['kind']]}",
        description=(f"<@{envelope['sender_id']}> 发来红包\n\n"
                     f"{discord.utils.escape_markdown(envelope['greeting'])}"),
        color=discord.Color.red(),
    )
    embed.add_field(name="红包总额", value=f"{envelope['total_amount']} 灵石")
    embed.add_field(name="剩余", value=f"{envelope['remaining_count']} / {envelope['count']} 份")
    embed.add_field(name="状态", value=("正在发送" if envelope["status"] == "active" and not envelope["message_id"] else STATUS_NAMES.get(envelope["status"], "已结束")))
    if envelope['kind'] != 'lucky':
        embed.add_field(name="每份金额", value=f"{envelope['total_amount'] // envelope['count']} 灵石")
    if envelope['kind'] == 'password':
        embed.add_field(name="领取方式", value="点击领取按钮，输入发送者提供的口令。", inline=False)
    embed.add_field(name="到期时间", value=f"<t:{int(envelope['expires_at'])}:R>")
    embed.set_footer(text="每人限领一次 · 24 小时有效 · 未领取金额自动退回")
    return embed


class PasswordModal(ui.Modal, title="输入红包口令"):
    password = ui.TextInput(label="口令", min_length=1, max_length=50)

    def __init__(self, cog, envelope_id: str):
        super().__init__(timeout=300)
        self.cog = cog
        self.envelope_id = envelope_id

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.claim(interaction, self.envelope_id, self.password.value)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        log.error("红包口令弹窗异常", exc_info=error)
        await self.cog.respond(interaction, "处理口令时发生错误，请稍后重试。")


class EnvelopeView(ui.View):
    """固定 custom_id，通过消息 ID 查找红包，重启后仍可处理旧按钮。"""

    def __init__(self, cog, *, closed: bool = False):
        super().__init__(timeout=None)
        self.cog = cog
        self.claim_button.disabled = closed

    @ui.button(label="领取红包 / 输入口令", emoji="🧧", style=discord.ButtonStyle.danger,
               custom_id="coin_red_envelope:claim")
    async def claim_button(self, interaction: discord.Interaction, button: ui.Button):
        envelope = await self.cog.from_interaction(interaction)
        if not envelope:
            return
        if envelope['kind'] == 'password':
            await interaction.response.send_modal(PasswordModal(self.cog, envelope['id']))
        else:
            await self.cog.claim(interaction, envelope['id'])

    @ui.button(label="领取记录", style=discord.ButtonStyle.secondary,
               custom_id="coin_red_envelope:records")
    async def records_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        envelope = await self.cog.from_interaction(interaction)
        if not envelope:
            return
        records = await self.cog.service.list_claims(envelope['id'])
        # 分块展示，最多 100 份也不超过 Discord 的单字段和消息限制。
        embed = envelope_embed(envelope)
        for start in range(0, len(records), 20):
            lines = [f"<@{row['user_id']}>：{row['amount']} 灵石" for row in records[start:start + 20]]
            embed.add_field(name=f"领取记录 {start + 1}—{start + len(lines)}", value="\n".join(lines), inline=False)
        if not records:
            embed.add_field(name="领取记录", value="暂时还没有人领取。", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: ui.Item):
        log.error("红包按钮处理异常", exc_info=error)
        await self.cog.respond(interaction, "处理红包时发生错误，请稍后重试；已入账的领取不会重复发放。")


class RedEnvelopeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.service = red_envelope_service
        self.view = EnvelopeView(self)
        self._refresh_lock = asyncio.Lock()

    async def cog_load(self):
        await self.service.initialize()
        self.bot.add_view(self.view)
        self.expire_envelopes.start()

    def cog_unload(self):
        self.expire_envelopes.cancel()
        self.view.stop()

    @staticmethod
    async def respond(interaction: discord.Interaction, message: str):
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message(message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    async def from_interaction(self, interaction: discord.Interaction):
        if not interaction.guild_id or not interaction.channel_id or not interaction.message:
            await self.respond(interaction, "请在红包原来的服务器频道中操作。")
            return None
        envelope = await self.service.get_by_message(
            interaction.guild_id, interaction.channel_id, interaction.message.id,
        )
        if not envelope:
            await self.respond(interaction, "红包尚未发送完成或不存在，请稍后重试。")
        return envelope

    async def refresh(self, envelope_id: str):
        # 查询和编辑一并串行，避免并发领取把卡片刷新成旧的剩余份数。
        async with self._refresh_lock:
            envelope = await self.service.get(envelope_id)
            if not envelope or not envelope['message_id']:
                return
            try:
                channel = self.bot.get_channel(envelope['channel_id'])
                if channel is None:
                    channel = await self.bot.fetch_channel(envelope['channel_id'])
                message = channel.get_partial_message(envelope['message_id'])
                await message.edit(
                    embed=envelope_embed(envelope),
                    view=EnvelopeView(self, closed=envelope['status'] != 'active'),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                log.warning("红包卡片刷新失败，资金记录已保存：%s", envelope_id, exc_info=True)

    async def claim(self, interaction: discord.Interaction, envelope_id: str, password=None):
        await interaction.response.defer(ephemeral=True)
        if interaction.user.bot or not interaction.guild_id or not interaction.channel_id:
            await self.respond(interaction, "请由服务器成员在红包原频道领取。")
            return
        try:
            result = await self.service.claim(
                envelope_id, interaction.user.id, interaction.guild_id,
                interaction.channel_id, password=password,
            )
        except RedEnvelopeError as error:
            await self.respond(interaction, str(error))
        else:
            await self.respond(interaction, f"🧧 你领到了 **{result['amount']} 灵石**！当前余额：{result['balance']} 灵石。")
        await self.refresh(envelope_id)

    @app_commands.command(name="发红包", description="用自己的灵石发送普通、拼手气或口令红包")
    @app_commands.guild_only()
    @app_commands.rename(kind="类型", total_amount="总金额", count="份数", password="口令", greeting="祝福语")
    @app_commands.describe(kind="选择红包类型", total_amount="扣除的灵石总额，普通和口令红包须能被份数整除",
                           count="红包份数，每人限领一次", password="口令红包必填，由你告知领取者",
                           greeting="红包上的祝福语")
    @app_commands.choices(kind=[app_commands.Choice(name=name, value=key) for key, name in KIND_NAMES.items()])
    async def send_envelope(
        self, interaction: discord.Interaction, kind: app_commands.Choice[str],
        total_amount: app_commands.Range[int, 1, 1_000_000],
        count: app_commands.Range[int, 1, 100] = 1,
        password: str = None, greeting: str = "恭喜发财，大吉大利！",
    ):
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild_id or not interaction.channel_id or interaction.channel is None:
            await self.respond(interaction, "请在服务器文字频道中发红包。")
            return
        permissions = interaction.permissions
        bot_permissions = interaction.app_permissions
        in_thread = isinstance(interaction.channel, discord.Thread)
        can_send = permissions.send_messages_in_threads if in_thread else permissions.send_messages
        bot_can_send = bot_permissions.send_messages_in_threads if in_thread else bot_permissions.send_messages
        if not can_send or not bot_can_send or not bot_permissions.embed_links:
            await self.respond(interaction, "你和月月都需要当前频道的发消息权限，月月还需要嵌入链接权限。")
            return

        # 同一个交互 ID 是唯一的扣款凭据，重试不会再次扣费。
        envelope_id = str(interaction.id)
        try:
            envelope = await self.service.create(
                interaction.user.id, interaction.guild_id, interaction.channel_id,
                kind.value, total_amount, count, password, greeting, request_id=envelope_id,
            )
        except RedEnvelopeError as error:
            await self.respond(interaction, str(error))
            return
        if envelope['status'] != 'active' or envelope['message_id']:
            await self.respond(interaction, "这个红包请求已经处理过，请查看频道中的红包。")
            return
        envelope_id = envelope['id']
        message = None
        try:
            message = await interaction.channel.send(
                embed=envelope_embed(envelope), view=EnvelopeView(self),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await self.service.attach_message(envelope_id, message.id)
        except Exception:
            log.exception("红包发布失败：%s", envelope_id)
            cancelled = await self.service.cancel(envelope_id)
            if message is not None:
                try:
                    await message.edit(
                        embed=envelope_embed(cancelled), view=EnvelopeView(self, closed=True),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    log.warning("发送失败的红包卡片无法更新：%s", envelope_id)
            await self.respond(interaction, "红包发送失败，灵石已退回，请检查频道权限后重试。")
            return
        await self.respond(interaction, f"🧧 已发送{KIND_NAMES[kind.value]}：{total_amount} 灵石，共 {count} 份。24 小时后未领取的金额会自动退回。")
        await self.refresh(envelope_id)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        log.error("红包指令执行异常", exc_info=error)
        await self.respond(interaction, "红包操作暂时失败，请稍后重试；可通过灵石中心查看余额和流水。")

    @tasks.loop(seconds=60)
    async def expire_envelopes(self):
        try:
            expired = await self.service.expire_pending()
            for envelope in expired:
                await self.refresh(envelope['id'])
        except Exception:
            log.exception("红包过期退款任务失败，将在下一轮重试")

    @expire_envelopes.before_loop
    async def before_expire(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(RedEnvelopeCog(bot))
