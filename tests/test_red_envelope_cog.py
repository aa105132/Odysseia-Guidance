"""验证红包交互的权限、发布失败补偿和持久化入口。"""

from types import SimpleNamespace
import sqlite3
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

from src.chat.features.odysseia_coin.cogs.red_envelope_cog import (
    EnvelopeView, PasswordModal, RedEnvelopeCog, envelope_embed,
)


@pytest.fixture
def envelope():
    return dict(id="123", sender_id=1, guild_id=2, channel_id=3,
                message_id=None, kind="normal", total_amount=100, count=10,
                remaining_amount=100, remaining_count=10, greeting="恭喜发财！",
                status="active", expires_at=100000)


@pytest.fixture
def interaction():
    event = MagicMock()
    event.id = 123
    event.guild_id = 2
    event.channel_id = 3
    event.user.id = 1
    event.user.bot = False
    event.permissions = discord.Permissions.all()
    event.app_permissions = discord.Permissions.all()
    event.response.defer = AsyncMock()
    event.response.is_done.return_value = True
    event.response.send_modal = AsyncMock()
    event.followup.send = AsyncMock()
    event.channel.send = AsyncMock(return_value=SimpleNamespace(id=4, edit=AsyncMock()))
    return event


@pytest.fixture
def cog(envelope):
    instance = RedEnvelopeCog(MagicMock())
    instance.service = AsyncMock()
    instance.service.create.return_value = envelope
    instance.service.cancel.return_value = {**envelope, 'status': 'cancelled', 'remaining_amount': 0}
    instance.refresh = AsyncMock()
    return instance


async def send(cog, interaction):
    await RedEnvelopeCog.send_envelope.callback(
        cog, interaction, app_commands.Choice(name="普通红包", value="normal"), 100, 10,
    )


@pytest.mark.asyncio
async def test_send_activates_only_after_public_message(cog, interaction):
    await send(cog, interaction)
    cog.service.create.assert_awaited_once_with(1, 2, 3, "normal", 100, 10, None, "恭喜发财，大吉大利！", request_id="123")
    interaction.channel.send.assert_awaited_once()
    cog.service.attach_message.assert_awaited_once_with("123", 4)
    cog.service.cancel.assert_not_awaited()
    assert interaction.channel.send.call_args.kwargs["allowed_mentions"].everyone is False


@pytest.mark.asyncio
async def test_publish_failure_refunds(cog, interaction):
    interaction.channel.send.side_effect = RuntimeError("模拟网络失败")
    await send(cog, interaction)
    cog.service.cancel.assert_awaited_once_with("123")
    cog.service.attach_message.assert_not_awaited()
    assert "已退回" in interaction.followup.send.call_args.args[0]


@pytest.mark.asyncio
async def test_activation_failure_refunds(cog, interaction):
    cog.service.attach_message.side_effect = RuntimeError("模拟绑定失败")
    await send(cog, interaction)
    cog.service.cancel.assert_awaited_once_with("123")


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["permissions", "app_permissions"])
async def test_no_send_permission_never_debits(cog, interaction, target):
    setattr(interaction, target, discord.Permissions.none())
    await send(cog, interaction)
    cog.service.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_private_channel_never_debits(cog, interaction):
    interaction.guild_id = None
    await send(cog, interaction)
    cog.service.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_request_is_not_republished(cog, interaction, envelope):
    envelope['message_id'] = 4
    await send(cog, interaction)
    interaction.channel.send.assert_not_awaited()
    cog.service.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_password_button_requires_modal(cog, interaction, envelope):
    envelope['kind'] = 'password'
    cog.service.get_by_message.return_value = envelope
    view = EnvelopeView(cog)
    await view.claim_button.callback(interaction)
    modal = interaction.response.send_modal.call_args.args[0]
    assert isinstance(modal, PasswordModal)
    assert modal.envelope_id == '123'
    cog.service.claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_uses_current_member_and_scope(cog, interaction, envelope):
    cog.service.claim.return_value = {"amount": 10, "balance": 20, "envelope": envelope}
    await cog.claim(interaction, '123', '好运')
    cog.service.claim.assert_awaited_once_with('123', 1, 2, 3, password='好运')
    assert '10' in interaction.followup.send.call_args.args[0]


@pytest.mark.asyncio
async def test_persistent_buttons_and_embed_do_not_expose_password(cog, envelope):
    assert EnvelopeView(cog).is_persistent()
    assert EnvelopeView(cog, closed=True).claim_button.disabled
    envelope.update(kind='password', password_hash='secret-hash')
    assert 'secret-hash' not in str(envelope_embed(envelope).to_dict())


@pytest.mark.asyncio
async def test_expiry_task_recovers_after_temporary_failure(cog, envelope):
    cog.service.expire_pending.side_effect = [RuntimeError('暂时锁定'), [envelope]]
    await cog.expire_envelopes()
    await cog.expire_envelopes()
    cog.refresh.assert_awaited_once_with('123')


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['normal', 'lucky', 'password'])
async def test_command_to_claim_with_real_service(cog, interaction, tmp_path, kind):
    from src.chat.features.odysseia_coin.service.red_envelope_service import RedEnvelopeService

    path = tmp_path / 'integration.sqlite'
    with sqlite3.connect(path) as connection:
        connection.executescript('''
            CREATE TABLE user_coins (user_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL);
            CREATE TABLE coin_transactions (user_id INTEGER, amount INTEGER, reason TEXT);
            INSERT INTO user_coins VALUES (1, 100);
        ''')
    cog.service = RedEnvelopeService(str(path))
    await cog.service.initialize()
    await RedEnvelopeCog.send_envelope.callback(
        cog, interaction, app_commands.Choice(name=kind, value=kind),
        100, 10, password='好运' if kind == 'password' else None,
    )
    packet = await cog.service.get_by_message(2, 3, 4)
    assert packet['kind'] == kind and packet['message_id'] == 4
    interaction.user.id = 9
    await cog.claim(interaction, packet['id'], '好运' if kind == 'password' else None)
    claims = await cog.service.list_claims(packet['id'])
    assert len(claims) == 1 and claims[0]['user_id'] == 9
    assert claims[0]['amount'] >= 1
    with sqlite3.connect(path) as connection:
        balance = connection.execute('SELECT balance FROM user_coins WHERE user_id=9').fetchone()[0]
        assert balance == claims[0]['amount']
    assert (await cog.service.get(packet['id']))['remaining_count'] == 9
