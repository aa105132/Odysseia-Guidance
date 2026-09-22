"""使用隔离 SQLite 验证红包资金守恒、并发领取、恢复退款及失败回滚。"""

import asyncio
import sqlite3

import pytest

from src.chat.features.odysseia_coin.service.red_envelope_service import (
    RedEnvelopeError,
    RedEnvelopeService,
)


@pytest.fixture
def account(tmp_path):
    path = tmp_path / "red_envelopes.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE user_coins (
                user_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE coin_transactions (
                user_id INTEGER NOT NULL, amount INTEGER NOT NULL, reason TEXT NOT NULL
            );
            INSERT INTO user_coins VALUES (1, 1000);
        """)
    now = [1_000_000.0]
    return RedEnvelopeService(str(path), clock=lambda: now[0]), now


def balances(service):
    with sqlite3.connect(service.db_path) as connection:
        return dict(connection.execute("SELECT user_id, balance FROM user_coins"))


def ledger_total(service):
    with sqlite3.connect(service.db_path) as connection:
        return connection.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM coin_transactions"
        ).fetchone()[0]


async def create(service, *, published=True, **overrides):
    arguments = dict(
        sender_id=1, guild_id=10, channel_id=20, kind="normal", total_amount=100, count=5
    )
    arguments.update(overrides)
    packet = await service.create(**arguments)
    if published:
        packet = await service.attach_message(packet["id"], 1234)
    return packet


async def claim(service, packet, user_id=2, **overrides):
    arguments = dict(
        envelope_id=packet["id"], user_id=user_id, guild_id=10, channel_id=20
    )
    arguments.update(overrides)
    return await service.claim(**arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["normal", "lucky", "password"])
async def test_all_kinds_conserve_funds_and_complete(account, kind):
    service, _ = account
    packet = await create(service, kind=kind, password="月月发财")
    assert balances(service) == {1: 900}
    amounts = []
    for user_id in range(2, 7):
        result = await claim(service, packet, user_id, password="月月发财")
        amounts.append(result["amount"])
        assert result["balance"] == result["amount"]
    assert sum(amounts) == 100 and min(amounts) >= 1
    if kind != "lucky":
        assert amounts == [20] * 5
    assert sum(balances(service).values()) == 1000
    assert ledger_total(service) == 0
    finished = await service.get(packet["id"])
    assert finished["status"] == "completed"
    assert (finished["remaining_amount"], finished["remaining_count"]) == (0, 0)
    with pytest.raises(RedEnvelopeError) as error:
        await claim(service, packet, 7)
    assert error.value.code == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("total,count", [(1, 1), (100, 100), (103, 100), (997, 7)])
async def test_lucky_distribution_keeps_every_share_positive(account, total, count):
    service, _ = account
    packet = await create(service, kind="lucky", total_amount=total, count=count)
    amounts = [
        (await claim(service, packet, user_id))["amount"]
        for user_id in range(2, count + 2)
    ]
    assert min(amounts) >= 1 and sum(amounts) == total
    assert sum(balances(service).values()) == 1000


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    {"total_amount": 0}, {"total_amount": -1}, {"total_amount": True},
    {"total_amount": 1.5}, {"total_amount": 1_000_001},
    {"count": 0}, {"count": 101}, {"count": True},
    {"total_amount": 4, "count": 5}, {"total_amount": 101},
    {"kind": "unknown"}, {"kind": "password"},
    {"kind": "password", "password": "  "},
    {"kind": "password", "password": "口" * 51},
    {"greeting": "福" * 101}, {"greeting": ""}, {"greeting": "  "},
    {"guild_id": 0},
])
async def test_invalid_requests_never_debit_balance(account, overrides):
    service, _ = account
    with pytest.raises(RedEnvelopeError):
        await create(service, **overrides)
    assert balances(service) == {1: 1000}
    assert ledger_total(service) == 0


@pytest.mark.asyncio
async def test_insufficient_balance_does_not_create_packet(account):
    service, _ = account
    with pytest.raises(RedEnvelopeError) as error:
        await create(service, total_amount=1005)
    assert error.value.code == "insufficient_balance"
    assert balances(service) == {1: 1000}
    assert ledger_total(service) == 0


@pytest.mark.asyncio
async def test_retrying_send_is_idempotent_and_conflicts_are_rejected(account):
    service, _ = account
    packets = await asyncio.gather(*(
        create(service, request_id="discord-interaction-1") for _ in range(4)
    ))
    assert len({packet["id"] for packet in packets}) == 1
    assert balances(service) == {1: 900}
    with pytest.raises(RedEnvelopeError) as error:
        await create(service, request_id="discord-interaction-1", total_amount=200)
    assert error.value.code == "request_conflict"
    assert ledger_total(service) == -100


@pytest.mark.asyncio
async def test_concurrent_sends_cannot_overdraw(account):
    service, _ = account
    results = await asyncio.gather(
        create(service, total_amount=1000), create(service, total_amount=1000),
        return_exceptions=True,
    )
    assert sum(isinstance(result, dict) for result in results) == 1
    errors = [result for result in results if isinstance(result, RedEnvelopeError)]
    assert len(errors) == 1 and errors[0].code == "insufficient_balance"
    assert balances(service) == {1: 0}
    assert ledger_total(service) == -1000


@pytest.mark.asyncio
async def test_concurrent_duplicate_claim_credits_only_once(account):
    service, _ = account
    packet = await create(service)
    results = await asyncio.gather(*(
        claim(service, packet) for _ in range(12)
    ), return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 1
    assert all(
        isinstance(result, dict) or result.code == "already_claimed" for result in results
    )
    assert balances(service) == {1: 900, 2: 20}
    assert (await service.get(packet["id"]))["remaining_count"] == 4


@pytest.mark.asyncio
async def test_concurrent_claimants_cannot_exceed_packet_count(account):
    service, _ = account
    packet = await create(service, kind="lucky")
    results = await asyncio.gather(*(
        claim(service, packet, user_id) for user_id in range(2, 22)
    ), return_exceptions=True)
    successful = [result for result in results if isinstance(result, dict)]
    assert len(successful) == 5
    assert sum(result["amount"] for result in successful) == 100
    assert sum(balances(service).values()) == 1000
    assert ledger_total(service) == 0


@pytest.mark.asyncio
async def test_password_and_channel_checks_leave_packet_untouched(account):
    service, _ = account
    packet = await create(service, kind="password", password="月月发财")
    for arguments, expected_code in [
        ({}, "wrong_password"), ({"password": "错误口令"}, "wrong_password"),
        ({"password": "月月发财", "channel_id": 21}, "wrong_channel"),
        ({"password": "月月发财", "guild_id": 11}, "wrong_channel"),
    ]:
        with pytest.raises(RedEnvelopeError) as error:
            await claim(service, packet, **arguments)
        assert error.value.code == expected_code
    assert balances(service) == {1: 900}
    assert (await service.get(packet["id"]))["remaining_count"] == 5
    assert "password_hash" not in packet
    assert "月月发财" not in str(packet)
    assert (await claim(service, packet, password="  月月发财  "))["amount"] == 20


@pytest.mark.asyncio
async def test_expiry_refunds_only_unclaimed_funds_once(account):
    service, now = account
    packet = await create(service)
    await service.attach_message(packet["id"], 1234)
    await claim(service, packet)
    now[0] += service.EXPIRES_SECONDS
    results = await asyncio.gather(service.expire_pending(), service.expire_pending())
    assert sum(map(len, results)) == 1
    assert balances(service) == {1: 980, 2: 20}
    assert ledger_total(service) == 0
    expired = await service.get(packet["id"])
    assert expired["status"] == "expired" and expired["refunded_amount"] == 80
    assert await service.expire_pending() == []


@pytest.mark.asyncio
async def test_claim_at_expiry_commits_refund_before_returning_error(account):
    service, now = account
    packet = await create(service)
    now[0] += service.EXPIRES_SECONDS
    with pytest.raises(RedEnvelopeError) as error:
        await claim(service, packet)
    assert error.value.code == "expired"
    assert balances(service) == {1: 1000}
    assert (await service.get(packet["id"]))["status"] == "expired"
    assert ledger_total(service) == 0


@pytest.mark.asyncio
async def test_restart_restores_published_packets_and_refunds_interrupted_sends(account):
    service, now = account
    published = await create(service)
    await service.attach_message(published["id"], 1234)
    interrupted = await create(service, published=False)
    now[0] += service.PUBLISH_TIMEOUT_SECONDS
    restarted = RedEnvelopeService(service.db_path, clock=lambda: now[0])
    assert [row["id"] for row in await restarted.list_active()] == [published["id"]]
    refunded = await restarted.expire_pending()
    assert [row["id"] for row in refunded] == [interrupted["id"]]
    assert balances(service) == {1: 900}
    assert (await claim(restarted, published))["amount"] == 20
    assert await restarted.expire_pending() == []


@pytest.mark.asyncio
async def test_failed_publish_can_be_cancelled_only_once(account):
    service, _ = account
    packet = await create(service, published=False)
    for _ in range(2):
        assert (await service.cancel(packet["id"]))["status"] == "cancelled"
    assert balances(service) == {1: 1000}
    assert ledger_total(service) == 0
    with pytest.raises(RedEnvelopeError) as error:
        await claim(service, packet)
    assert error.value.code == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("claimed", [True, False])
async def test_published_or_partially_claimed_packet_cannot_be_cancelled(account, claimed):
    service, _ = account
    packet = await create(service)
    if claimed:
        await claim(service, packet)
    with pytest.raises(RedEnvelopeError):
        await service.cancel(packet["id"])
    assert balances(service)[1] == 900
    assert (await service.get(packet["id"]))["status"] == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "claim", "refund"])
async def test_ledger_failure_rolls_back_all_changes_and_allows_retry(account, operation):
    service, now = account
    packet = None
    if operation != "create":
        packet = await create(service)
    if operation == "refund":
        now[0] += service.EXPIRES_SECONDS
    before = balances(service)
    before_ledger = ledger_total(service)
    with sqlite3.connect(service.db_path) as connection:
        connection.executescript("""
            CREATE TRIGGER reject_ledger BEFORE INSERT ON coin_transactions
            BEGIN SELECT RAISE(ABORT, '模拟流水写入失败'); END;
        """)

    async def perform():
        if operation == "create":
            return await create(service, request_id="retryable-send")
        if operation == "claim":
            return await claim(service, packet)
        return await service.expire_pending()

    with pytest.raises(sqlite3.IntegrityError):
        await perform()
    assert balances(service) == before
    assert ledger_total(service) == before_ledger
    if packet:
        assert await service.get(packet["id"]) == packet
    with sqlite3.connect(service.db_path) as connection:
        connection.execute("DROP TRIGGER reject_ledger")
    await perform()
    expected = {"create": {1: 900}, "claim": {1: 900, 2: 20}, "refund": {1: 1000}}
    assert balances(service) == expected[operation]


@pytest.mark.asyncio
async def test_unpublished_packet_cannot_be_claimed(account):
    service, _ = account
    packet = await create(service, published=False)
    with pytest.raises(RedEnvelopeError) as error:
        await claim(service, packet)
    assert error.value.code == "not_published"
    assert balances(service) == {1: 900}
    assert await service.get(packet["id"]) == packet
    await service.attach_message(packet["id"], 1234)
    assert (await claim(service, packet))["amount"] == 20


@pytest.mark.asyncio
@pytest.mark.parametrize("expired", [False, True])
async def test_late_publish_commits_refund_before_returning_error(account, expired):
    service, now = account
    packet = await create(service, published=False)
    now[0] += service.EXPIRES_SECONDS if expired else service.PUBLISH_TIMEOUT_SECONDS
    with pytest.raises(RedEnvelopeError) as error:
        await service.attach_message(packet["id"], 1234)
    assert error.value.code == "expired"
    assert balances(service) == {1: 1000}
    assert (await service.get(packet["id"]))["status"] == "expired"
    assert ledger_total(service) == 0
    assert await service.expire_pending() == []


@pytest.mark.asyncio
async def test_expired_unpublished_claim_refunds_before_publish_check(account):
    service, now = account
    packet = await create(service, published=False)
    now[0] += service.EXPIRES_SECONDS
    with pytest.raises(RedEnvelopeError) as error:
        await claim(service, packet)
    assert error.value.code == "expired"
    assert balances(service) == {1: 1000}
    assert ledger_total(service) == 0
