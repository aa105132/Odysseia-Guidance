import logging
import random
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, List, Dict, Any

from src.chat.utils.database import chat_db_manager
from src.chat.config.chat_config import COIN_CONFIG
from ...affection.service.affection_service import affection_service

log = logging.getLogger(__name__)

# --- 特殊商品效果ID ---
PERSONAL_MEMORY_ITEM_EFFECT_ID = "unlock_personal_memory"
WORLD_BOOK_CONTRIBUTION_ITEM_EFFECT_ID = "contribute_to_world_book"
COMMUNITY_MEMBER_UPLOAD_EFFECT_ID = "upload_community_member"
DISABLE_THREAD_COMMENTOR_EFFECT_ID = "disable_thread_commentor"
BLOCK_THREAD_REPLIES_EFFECT_ID = "block_thread_replies"
ENABLE_THREAD_COMMENTOR_EFFECT_ID = "enable_thread_commentor"
ENABLE_THREAD_REPLIES_EFFECT_ID = "enable_thread_replies"
SELL_BODY_EVENT_SUBMISSION_EFFECT_ID = "submit_sell_body_event"
CLEAR_PERSONAL_MEMORY_ITEM_EFFECT_ID = "clear_personal_memory"
VIEW_PERSONAL_MEMORY_ITEM_EFFECT_ID = "view_personal_memory"


class CoinService:
    """处理与月光币相关的所有业务逻辑"""

    def __init__(self):
        pass

    async def get_balance(self, user_id: int) -> int:
        """获取用户的月光币余额"""
        query = "SELECT balance FROM user_coins WHERE user_id = ?"
        result = await chat_db_manager._execute(
            chat_db_manager._db_transaction, query, (user_id,), fetch="one"
        )
        return result["balance"] if result else 0

    async def add_coins(self, user_id: int, amount: int, reason: str) -> int:
        """
        为用户增加月光币并记录交易。
        返回新的余额。
        """
        if amount <= 0:
            raise ValueError("增加的金额必须为正数")

        # 插入或更新用户余额
        upsert_query = """
            INSERT INTO user_coins (user_id, balance) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET balance = balance + excluded.balance;
        """
        await chat_db_manager._execute(
            chat_db_manager._db_transaction,
            upsert_query,
            (user_id, amount),
            commit=True,
        )

        # 记录交易
        transaction_query = """
            INSERT INTO coin_transactions (user_id, amount, reason)
            VALUES (?, ?, ?);
        """
        await chat_db_manager._execute(
            chat_db_manager._db_transaction,
            transaction_query,
            (user_id, amount, reason),
            commit=True,
        )

        # 获取新余额
        new_balance = await self.get_balance(user_id)
        log.info(
            f"用户 {user_id} 获得 {amount} 月光币，原因: {reason}。新余额: {new_balance}"
        )
        return new_balance

    async def remove_coins(
        self, user_id: int, amount: int, reason: str
    ) -> Optional[int]:
        """
        扣除用户的月光币并记录交易。
        如果余额不足，则返回 None，否则返回新的余额。
        """
        if amount <= 0:
            raise ValueError("扣除的金额必须为正数")

        current_balance = await self.get_balance(user_id)
        if current_balance < amount:
            log.warning(
                f"用户 {user_id} 扣款失败，余额不足。需要 {amount}，拥有 {current_balance}"
            )
            return None

        # 更新余额
        update_query = "UPDATE user_coins SET balance = balance - ? WHERE user_id = ?"
        await chat_db_manager._execute(
            chat_db_manager._db_transaction,
            update_query,
            (amount, user_id),
            commit=True,
        )

        # 记录交易
        transaction_query = """
            INSERT INTO coin_transactions (user_id, amount, reason)
            VALUES (?, ?, ?);
        """
        await chat_db_manager._execute(
            chat_db_manager._db_transaction,
            transaction_query,
            (user_id, -amount, reason),
            commit=True,
        )

        # 获取新余额
        new_balance = await self.get_balance(user_id)
        log.info(
            f"用户 {user_id} 消费 {amount} 月光币，原因: {reason}。新余额: {new_balance}"
        )
        return new_balance

    async def grant_daily_message_reward(self, user_id: int) -> bool:
        """
        检查并授予每日首次发言奖励。
        如果成功授予奖励，返回 True，否则返回 False。
        """
        from datetime import timedelta

        # 使用北京时间 (UTC+8)
        beijing_tz = timezone(timedelta(hours=8))
        today_beijing = datetime.now(beijing_tz).date()

        # 检查上次领取日期
        query_last_date = (
            "SELECT last_daily_message_date FROM user_coins WHERE user_id = ?"
        )
        result = await chat_db_manager._execute(
            chat_db_manager._db_transaction, query_last_date, (user_id,), fetch="one"
        )

        if result and result["last_daily_message_date"]:
            last_daily_date = datetime.fromisoformat(
                result["last_daily_message_date"]
            ).date()
            if last_daily_date >= today_beijing:
                return False  # 今天已经发过了

        # 更新最后发言日期并增加金币
        reward_amount = COIN_CONFIG["DAILY_FIRST_CHAT_REWARD"]
        update_query = """
            INSERT INTO user_coins (user_id, balance, last_daily_message_date)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                balance = balance + ?,
                last_daily_message_date = excluded.last_daily_message_date;
        """
        await chat_db_manager._execute(
            chat_db_manager._db_transaction,
            update_query,
            (user_id, reward_amount, today_beijing.isoformat(), reward_amount),
            commit=True,
        )

        # 记录交易
        transaction_query = """
            INSERT INTO coin_transactions (user_id, amount, reason)
            VALUES (?, ?, ?);
        """
        await chat_db_manager._execute(
            chat_db_manager._db_transaction,
            transaction_query,
            (user_id, reward_amount, "每日首次与AI对话奖励"),
            commit=True,
        )

        log.info(f"用户 {user_id} 获得每日首次与AI对话奖励 ({reward_amount} 月光币)。")
        return True

    async def add_item_to_shop(
        self,
        name: str,
        description: str,
        price: int,
        category: str,
        target: str = "self",
        effect_id: Optional[str] = None,
    ):
        """向商店添加或更新一件商品"""
        query = """
            INSERT INTO shop_items (name, description, price, category, target, effect_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                description = excluded.description,
                price = excluded.price,
                category = excluded.category,
                target = excluded.target,
                effect_id = excluded.effect_id,
                is_available = 1;
        """
        await chat_db_manager._execute(
            chat_db_manager._db_transaction,
            query,
            (name, description, price, category, target, effect_id),
            commit=True,
        )
        log.info(f"已添加或更新商品: {name} ({category})")

    async def get_items_by_category(self, category: str) -> list:
        """根据类别获取所有可用的商品"""
        query = "SELECT * FROM shop_items WHERE category = ? AND is_available = 1 ORDER BY price ASC"
        return await chat_db_manager._execute(
            chat_db_manager._db_transaction, query, (category,), fetch="all"
        )

    async def get_all_items(self) -> list:
        """获取所有可用的商品"""
        query = "SELECT * FROM shop_items WHERE is_available = 1 ORDER BY category, price ASC"
        return await chat_db_manager._execute(
            chat_db_manager._db_transaction, query, (), fetch="all"
        )

    async def get_item_by_id(self, item_id: int):
        """通过ID获取商品信息"""
        query = "SELECT * FROM shop_items WHERE item_id = ?"
        return await chat_db_manager._execute(
            chat_db_manager._db_transaction, query, (item_id,), fetch="one"
        )

    async def purchase_item(
        self, user_id: int, guild_id: int, item_id: int, quantity: int = 1
    ) -> tuple[bool, str, Optional[int], bool, bool, Optional[dict]]:
        """
        处理用户购买商品的逻辑。
        返回一个元组 (success: bool, message: str, new_balance: Optional[int], should_show_modal: bool, should_generate_gift_response: bool, embed_data: Optional[dict])。
        """
        item = await self.get_item_by_id(item_id)
        if not item:
            return False, "找不到该商品。", None, False, False, None

        total_cost = item["price"] * quantity
        current_balance = await self.get_balance(user_id)

        if current_balance < total_cost:
            return (
                False,
                f"你的余额不足！需要 {total_cost} 月光币，但你只有 {current_balance}。",
                None,
                False,
                False,
                None,
            )

        # 扣款并记录（仅当费用大于0时）
        new_balance = current_balance
        if total_cost > 0:
            reason = f"购买 {quantity}x {item['name']}"
            new_balance = await self.remove_coins(user_id, total_cost, reason)
            if new_balance is None:
                return False, "购买失败，无法扣除月光币。", None, False, False, None

        # 根据物品目标执行不同操作
        item_target = item["target"]
        item_effect = item["effect_id"]

        if item_target == "ai":
            # --- 送给月月的物品 ---
            points_to_add = max(1, item["price"] // 10)
            (
                gift_success,
                gift_message,
            ) = await affection_service.increase_affection_for_gift(
                user_id, points_to_add
            )

            if gift_success:
                # 购买成功，返回空消息，并标记需要生成AI回应
                return True, "", new_balance, False, True, None
            else:
                # 送礼失败，回滚交易
                await self.add_coins(
                    user_id, total_cost, f"送礼失败返还: {item['name']}"
                )
                log.warning(
                    f"用户 {user_id} 送礼失败，已返还 {total_cost} 月光币。原因: {gift_message}"
                )
                return False, gift_message, current_balance, False, False, None

        elif item_target == "self" and item_effect:
            # --- 给自己用且有立即效果的物品 ---
            if item_effect == CLEAR_PERSONAL_MEMORY_ITEM_EFFECT_ID:
                # 清除用户的个人记忆
                from src.chat.features.personal_memory.services.personal_memory_service import (
                    personal_memory_service,
                )

                await personal_memory_service.clear_personal_memory(user_id)
                return (
                    True,
                    f"一道耀眼的闪光后，月月关于 **{item['name']}** 的记忆...呃，不对，是月月关于你的记忆被清除了。你们可以重新开始了。",
                    new_balance,
                    False,
                    False,
                    None,
                )
            elif item_effect == VIEW_PERSONAL_MEMORY_ITEM_EFFECT_ID:
                # 查看用户的个人记忆
                from src.chat.features.personal_memory.services.personal_memory_service import (
                    personal_memory_service,
                )

                summary = await personal_memory_service.get_memory_summary(user_id)
                embed_data = {
                    "title": "午后闲谈",
                    "description": f"经过一次愉快的闲谈，你得知了在她心中，你的印象是这样的：\n\n>>> {summary}",
                }
                return (
                    True,
                    "你与月月进行了一次成功的“午后闲谈”。",
                    new_balance,
                    False,
                    False,
                    embed_data,
                )
            elif item_effect == PERSONAL_MEMORY_ITEM_EFFECT_ID:
                # 检查用户是否已经拥有个人记忆功能
                user_profile = await chat_db_manager.get_user_profile(user_id)
                has_personal_memory = (
                    user_profile and user_profile["has_personal_memory"]
                )

                if has_personal_memory:
                    # 用户已经拥有该功能，扣除10个月光币作为更新费用
                    # 用户已经拥有该功能，同样需要弹出模态框让他们编辑
                    return (
                        True,
                        f"你花费了 {total_cost} 月光币来更新你的个人档案。",
                        new_balance,
                        True,
                        False,
                        None,
                    )
                else:
                    return (
                        True,
                        f"你已成功解锁 **{item['name']}**！现在月月将开始为你记录个人记忆。",
                        new_balance,
                        True,
                        False,
                        None,
                    )
            elif item_effect == WORLD_BOOK_CONTRIBUTION_ITEM_EFFECT_ID:
                # 购买"知识纸条"商品，需要弹出模态窗口
                return (
                    True,
                    f"你花费了 {total_cost} 月光币购买了 {quantity}x **{item['name']}**。",
                    new_balance,
                    True,
                    False,
                    None,
                )
            elif item_effect == COMMUNITY_MEMBER_UPLOAD_EFFECT_ID:
                # 购买"社区成员档案上传"商品，需要弹出模态窗口
                return (
                    True,
                    f"你花费了 {total_cost} 月光币购买了 {quantity}x **{item['name']}**。",
                    new_balance,
                    True,
                    False,
                    None,
                )
            elif item_effect == SELL_BODY_EVENT_SUBMISSION_EFFECT_ID:
                # 购买“拉皮条”商品，需要弹出模态窗口
                return (
                    True,
                    f"你花费了 {total_cost} 月光币购买了 {quantity}x **{item['name']}**。",
                    new_balance,
                    True,
                    False,
                    None,
                )
            elif item_effect == DISABLE_THREAD_COMMENTOR_EFFECT_ID:
                # 购买“枯萎向日葵”，禁用暖贴功能
                await self.set_warmup_preference(user_id, wants_warmup=False)
                return (
                    True,
                    f"你“购买”了 **{item['name']}**。从此，月月将不再暖你的贴。",
                    new_balance,
                    False,
                    False,
                    None,
                )
            elif item_effect == BLOCK_THREAD_REPLIES_EFFECT_ID:
                query = """
                    INSERT INTO user_coins (user_id, blocks_thread_replies) VALUES (?, 1)
                    ON CONFLICT(user_id) DO UPDATE SET blocks_thread_replies = 1;
                """
                await chat_db_manager._execute(
                    chat_db_manager._db_transaction, query, (user_id,), commit=True
                )
                log.info(f"用户 {user_id} 购买了告示牌，已禁用帖子回复功能。")
                return (
                    True,
                    f"你举起了 **{item['name']}**，上面写着“禁止通行”。从此，月月将不再进入你的帖子。",
                    new_balance,
                    False,
                    False,
                    None,
                )
            elif item_effect == ENABLE_THREAD_COMMENTOR_EFFECT_ID:
                # 购买“魔法向日葵”，重新启用暖贴功能
                await self.set_warmup_preference(user_id, wants_warmup=True)
                return (
                    True,
                    f"你使用了 **{item['name']}**，枯萎的向日葵恢复了生机。月月现在会重新暖你的贴了。",
                    new_balance,
                    False,
                    False,
                    None,
                )
            elif item_effect == ENABLE_THREAD_REPLIES_EFFECT_ID:
                # 购买“通行许可”，重新启用帖子回复并设置默认CD
                default_limit = 2
                default_duration = 60
                query = """
                    INSERT INTO user_coins (user_id, blocks_thread_replies, thread_cooldown_limit, thread_cooldown_duration, thread_cooldown_seconds)
                    VALUES (?, 0, ?, ?, NULL)
                    ON CONFLICT(user_id) DO UPDATE SET
                        blocks_thread_replies = 0,
                        thread_cooldown_limit = excluded.thread_cooldown_limit,
                        thread_cooldown_duration = excluded.thread_cooldown_duration,
                        thread_cooldown_seconds = NULL;
                """
                await chat_db_manager._execute(
                    chat_db_manager._db_transaction,
                    query,
                    (user_id, default_limit, default_duration),
                    commit=True,
                )
                log.info(
                    f"用户 {user_id} 购买了通行许可，已重新启用帖子回复功能，并设置默认冷却 (limit={default_limit}, duration={default_duration})。"
                )
                return (
                    True,
                    f"你使用了 **{item['name']}**，花费了 {total_cost} 月光币。现在你创建的所有帖子将默认拥有 **60秒2次** 的发言许可，你也可以随时通过弹出的窗口自定义规则。",
                    new_balance,
                    True,
                    False,
                    None,
                )
            else:
                # 其他未知效果，暂时先放入背包
                await self._add_item_to_inventory(user_id, item_id, quantity)
                return (
                    True,
                    f"购买成功！你花费了 {total_cost} 月光币购买了 {quantity}x **{item['name']}**，已放入你的背包。",
                    new_balance,
                    False,
                    False,
                    None,
                )
        else:
            # --- 普通物品，放入背包 ---
            await self._add_item_to_inventory(user_id, item_id, quantity)
            return (
                True,
                f"购买成功！你花费了 {total_cost} 月光币购买了 {quantity}x **{item['name']}**，已放入你的背包。",
                new_balance,
                False,
                False,
                None,
            )

    async def purchase_event_item(
        self, user_id: int, item_name: str, price: int
    ) -> tuple[bool, str, Optional[int]]:
        """
        处理用户购买活动商品的逻辑。
        这是一个简化版的购买流程，只处理扣款。
        返回 (success: bool, message: str, new_balance: Optional[int])
        """
        if price < 0:
            return False, "商品价格不能为负数。", None

        current_balance = await self.get_balance(user_id)
        if current_balance < price:
            return (
                False,
                f"你的余额不足！需要 {price} 月光币，但你只有 {current_balance}。",
                None,
            )

        # 仅当费用大于0时才扣款
        new_balance = current_balance
        if price > 0:
            reason = f"购买活动商品: {item_name}"
            new_balance = await self.remove_coins(user_id, price, reason)
            if new_balance is None:
                return False, "购买失败，无法扣除月光币。", None

        return True, f"成功购买 {item_name}！", new_balance

    async def _add_item_to_inventory(self, user_id: int, item_id: int, quantity: int):
        """将物品添加到用户背包的内部方法"""
        query = """
            INSERT INTO user_inventory (user_id, item_id, quantity) VALUES (?, ?, ?)
            ON CONFLICT(user_id, item_id) DO UPDATE SET quantity = quantity + excluded.quantity;
        """
        await chat_db_manager._execute(
            chat_db_manager._db_transaction,
            query,
            (user_id, item_id, quantity),
            commit=True,
        )

    async def has_withered_sunflower(self, user_id: int) -> bool:
        """检查用户是否拥有枯萎向日葵（即是否禁用了暖贴功能）"""
        query = "SELECT has_withered_sunflower FROM user_coins WHERE user_id = ?"
        result = await chat_db_manager._execute(
            chat_db_manager._db_transaction, query, (user_id,), fetch="one"
        )
        return (
            result["has_withered_sunflower"]
            if result and result["has_withered_sunflower"]
            else False
        )

    async def blocks_thread_replies(self, user_id: int) -> bool:
        """检查用户是否拥有告示牌（即是否禁用了帖子回复功能）"""
        query = "SELECT blocks_thread_replies FROM user_coins WHERE user_id = ?"
        result = await chat_db_manager._execute(
            chat_db_manager._db_transaction, query, (user_id,), fetch="one"
        )
        return (
            result["blocks_thread_replies"]
            if result and result["blocks_thread_replies"]
            else False
        )

    async def has_made_warmup_choice(self, user_id: int) -> bool:
        """检查用户是否已经对暖贴功能做出过选择（同意或拒绝）"""
        query = "SELECT has_withered_sunflower FROM user_coins WHERE user_id = ?"
        result = await chat_db_manager._execute(
            chat_db_manager._db_transaction, query, (user_id,), fetch="one"
        )
        # 如果记录存在且 has_withered_sunflower 不是 NULL，则用户已做出选择
        return result is not None and result["has_withered_sunflower"] is not None

    async def set_warmup_preference(self, user_id: int, wants_warmup: bool):
        """
        直接设置用户的暖贴偏好。
        wants_warmup = True  -> 允许暖贴 (has_withered_sunflower = 0)
        wants_warmup = False -> 禁止暖贴 (has_withered_sunflower = 1)
        """
        has_withered_sunflower = 0 if wants_warmup else 1
        query = """
            INSERT INTO user_coins (user_id, has_withered_sunflower)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                has_withered_sunflower = excluded.has_withered_sunflower;
        """
        await chat_db_manager._execute(
            chat_db_manager._db_transaction,
            query,
            (user_id, has_withered_sunflower),
            commit=True,
        )
        log.info(f"用户 {user_id} 的暖贴偏好已设置为: {wants_warmup}")

    # async def transfer_coins(
    #     self, sender_id: int, receiver_id: int, amount: int
    # ) -> tuple[bool, str, Optional[int]]:
    #     """
    #     处理用户之间的转账。
    #     返回 (success, message, new_balance)。
    #     """
    #     if sender_id == receiver_id:
    #         return False, "❌ 你不能给自己转账。", None
    #
    #     if amount <= 0:
    #         return False, "❌ 转账金额必须是正数。", None
    #
    #     tax = int(amount * COIN_CONFIG["TRANSFER_TAX_RATE"])
    #     total_deduction = amount + tax
    #
    #     sender_balance = await self.get_balance(sender_id)
    #     if sender_balance < total_deduction:
    #         return (
    #             False,
    #             f"❌ 你的余额不足以完成转账。需要 {total_deduction} (包含 {tax} 税费)，你只有 {sender_balance}。",
    #             None,
    #         )
    #
    #     try:
    #         # 扣除发送者余额
    #         sender_new_balance = await self.remove_coins(
    #             sender_id, total_deduction, f"转账给用户 {receiver_id} (含税)"
    #         )
    #         if sender_new_balance is None:
    #             # 这理论上不应该发生，因为我们已经检查过余额了
    #             return False, "❌ 转账失败：扣款时发生错误。", None
    #
    #         # 增加接收者余额
    #         await self.add_coins(
    #             receiver_id, amount, f"收到来自用户 {sender_id} 的转账"
    #         )
    #
    #         log.info(
    #             f"用户 {sender_id} 成功转账 {amount} 月光币给用户 {receiver_id}，税费 {tax}。"
    #         )
    #         return (
    #             True,
    #             f"✅ 转账成功！你向 <@{receiver_id}> 转账了 **{amount}** 月光币，并支付了 **{tax}** 的税费。",
    #             sender_new_balance,
    #         )
    #     except Exception as e:
    #         log.error(
    #             f"转账失败: 从 {sender_id} 到 {receiver_id}，金额 {amount}。错误: {e}"
    #         )
    #         # 在一个更健壮的系统中，这里需要处理分布式事务回滚
    #         # 但对于当前场景，我们假设如果扣款成功，收款大概率也会成功
    #         return False, f"❌ 转账时发生未知错误: {e}", None

    async def get_active_loan(self, user_id: int) -> Optional[dict]:
        """获取用户当前未还清的贷款"""
        query = "SELECT * FROM coin_loans WHERE user_id = ? AND status = 'active'"
        result = await chat_db_manager._execute(
            chat_db_manager._db_transaction, query, (user_id,), fetch="one"
        )
        return dict(result) if result else None

    async def borrow_coins(self, user_id: int, amount: int) -> tuple[bool, str]:
        """处理用户借款"""
        if amount <= 0:
            return False, "❌ 借款金额必须是正数。"

        max_loan = COIN_CONFIG["MAX_LOAN_AMOUNT"]
        if amount > max_loan:
            return False, f"❌ 单次最多只能借 {max_loan} 月光币。"

        active_loan = await self.get_active_loan(user_id)
        if active_loan:
            return (
                False,
                f"❌ 你还有一笔 **{active_loan['amount']}** 月光币的借款尚未还清，请先还款。",
            )

        try:
            await self.add_coins(user_id, amount, "从系统借款")

            query = "INSERT INTO coin_loans (user_id, amount) VALUES (?, ?)"
            await chat_db_manager._execute(
                chat_db_manager._db_transaction, query, (user_id, amount), commit=True
            )

            log.info(f"用户 {user_id} 成功借款 {amount} 月光币。")
            return True, f"✅ 成功借款 **{amount}** 月光币！"
        except Exception as e:
            log.error(f"用户 {user_id} 借款失败: {e}")
            return False, f"❌ 借款时发生未知错误: {e}"

    async def repay_loan(self, user_id: int) -> tuple[bool, str]:
        """处理用户还款"""
        active_loan = await self.get_active_loan(user_id)
        if not active_loan:
            return False, "❌ 你当前没有需要偿还的贷款。"

        loan_amount = active_loan["amount"]
        current_balance = await self.get_balance(user_id)

        if current_balance < loan_amount:
            return (
                False,
                f"❌ 你的余额不足以偿还贷款。需要 **{loan_amount}**，你只有 **{current_balance}**。",
            )

        try:
            new_balance = await self.remove_coins(user_id, loan_amount, "偿还系统贷款")
            if new_balance is None:
                return False, "❌ 还款失败，无法扣除月光币。"

            query = "UPDATE coin_loans SET status = 'paid', paid_at = CURRENT_TIMESTAMP WHERE loan_id = ?"
            await chat_db_manager._execute(
                chat_db_manager._db_transaction,
                query,
                (active_loan["loan_id"],),
                commit=True,
            )

            log.info(f"用户 {user_id} 成功偿还 {loan_amount} 月光币的贷款。")
            return True, f"✅ 成功偿还 **{loan_amount}** 月光币的贷款！"
        except Exception as e:
            log.error(f"用户 {user_id} 还款失败: {e}")
            return False, f"❌ 还款时发生未知错误: {e}"

    async def get_transaction_history(
        self, user_id: int, limit: int = 10, offset: int = 0
    ) -> list[dict]:
        """获取用户最近的月光币交易记录"""
        query = """
            SELECT timestamp, amount, reason
            FROM coin_transactions
            WHERE user_id = ?
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?;
        """
        transactions = await chat_db_manager._execute(
            chat_db_manager._db_transaction,
            query,
            (user_id, limit, offset),
            fetch="all",
        )
        return [dict(row) for row in transactions] if transactions else []

    async def get_transaction_count(self, user_id: int) -> int:
        """获取用户的总交易记录数"""
        query = "SELECT COUNT(*) as count FROM coin_transactions WHERE user_id = ?;"
        result = await chat_db_manager._execute(
            chat_db_manager._db_transaction, query, (user_id,), fetch="one"
        )
        return result["count"] if result else 0

    async def daily_checkin(self, user_id: int) -> Tuple[bool, str, int, int]:
        """
        处理每日签到。
        返回: (success, message, reward_amount, current_streak)
        """
        # 使用北京时间
        beijing_tz = timezone(timedelta(hours=8))
        today = datetime.now(beijing_tz).date()
        yesterday = today - timedelta(days=1)
        
        # 获取用户签到信息
        query = """
            SELECT last_checkin_date, checkin_streak
            FROM user_coins WHERE user_id = ?
        """
        result = await chat_db_manager._execute(
            chat_db_manager._db_transaction, query, (user_id,), fetch="one"
        )
        
        last_checkin = None
        current_streak = 0
        
        if result and result["last_checkin_date"]:
            last_checkin = datetime.fromisoformat(result["last_checkin_date"]).date()
            current_streak = result["checkin_streak"] or 0
            
            # 检查是否今天已签到
            if last_checkin >= today:
                return False, "你今天已经签到过了，明天再来吧！", 0, current_streak
            
            # 检查是否连续签到
            if last_checkin == yesterday:
                current_streak += 1
            else:
                # 断签，重置连续天数
                current_streak = 1
        else:
            current_streak = 1
        
        # 计算奖励
        base_reward = random.randint(
            COIN_CONFIG["DAILY_CHECKIN_REWARD_MIN"],
            COIN_CONFIG["DAILY_CHECKIN_REWARD_MAX"]
        )
        
        # 连续签到奖励（每天+10，最多+50）
        streak_bonus = min(
            (current_streak - 1) * COIN_CONFIG["DAILY_CHECKIN_STREAK_BONUS"],
            COIN_CONFIG["DAILY_CHECKIN_MAX_STREAK_BONUS"]
        )
        
        total_reward = base_reward + streak_bonus
        
        # 更新数据库
        update_query = """
            INSERT INTO user_coins (user_id, balance, last_checkin_date, checkin_streak)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                balance = balance + ?,
                last_checkin_date = excluded.last_checkin_date,
                checkin_streak = excluded.checkin_streak;
        """
        await chat_db_manager._execute(
            chat_db_manager._db_transaction,
            update_query,
            (user_id, total_reward, today.isoformat(), current_streak, total_reward),
            commit=True,
        )
        
        # 记录交易
        transaction_query = """
            INSERT INTO coin_transactions (user_id, amount, reason)
            VALUES (?, ?, ?);
        """
        reason = f"每日签到 (连续{current_streak}天)"
        if streak_bonus > 0:
            reason += f" +{streak_bonus}连签奖励"
        await chat_db_manager._execute(
            chat_db_manager._db_transaction,
            transaction_query,
            (user_id, total_reward, reason),
            commit=True,
        )
        
        log.info(f"用户 {user_id} 签到成功，获得 {total_reward} 月光币，连续签到 {current_streak} 天")
        
        message = f"✨ 签到成功！获得 **{total_reward}** 月光币"
        if streak_bonus > 0:
            message += f"\n🔥 连续签到 **{current_streak}** 天，额外获得 **{streak_bonus}** 月光币！"
        else:
            message += f"\n📅 已连续签到 **{current_streak}** 天"
        
        return True, message, total_reward, current_streak

    async def claim_bankruptcy_subsidy(self, user_id: int) -> Tuple[bool, str, int]:
        """
        领取破产补贴。
        返回: (success, message, new_balance)
        """
        beijing_tz = timezone(timedelta(hours=8))
        now = datetime.now(beijing_tz)
        
        balance = await self.get_balance(user_id)
        
        # 检查余额是否低于阈值
        if balance >= COIN_CONFIG["BANKRUPTCY_THRESHOLD"]:
            return (
                False,
                f"你还没有破产呢！余额必须低于 **{COIN_CONFIG['BANKRUPTCY_THRESHOLD']}** 月光币才能领取补贴。",
                balance
            )
        
        # 检查冷却时间
        query = "SELECT last_bankruptcy_claim FROM user_coins WHERE user_id = ?"
        result = await chat_db_manager._execute(
            chat_db_manager._db_transaction, query, (user_id,), fetch="one"
        )
        
        if result and result["last_bankruptcy_claim"]:
            last_claim = datetime.fromisoformat(result["last_bankruptcy_claim"])
            if last_claim.tzinfo is None:
                last_claim = last_claim.replace(tzinfo=beijing_tz)
            
            cooldown_end = last_claim + timedelta(hours=COIN_CONFIG["BANKRUPTCY_COOLDOWN_HOURS"])
            if now < cooldown_end:
                remaining = cooldown_end - now
                hours = int(remaining.total_seconds() // 3600)
                minutes = int((remaining.total_seconds() % 3600) // 60)
                return (
                    False,
                    f"破产补贴还在冷却中！请在 **{hours}小时{minutes}分钟** 后再来。",
                    balance
                )
        
        # 发放补贴
        subsidy = COIN_CONFIG["BANKRUPTCY_SUBSIDY"]
        
        update_query = """
            INSERT INTO user_coins (user_id, balance, last_bankruptcy_claim)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                balance = balance + ?,
                last_bankruptcy_claim = excluded.last_bankruptcy_claim;
        """
        await chat_db_manager._execute(
            chat_db_manager._db_transaction,
            update_query,
            (user_id, subsidy, now.isoformat(), subsidy),
            commit=True,
        )
        
        # 记录交易
        transaction_query = """
            INSERT INTO coin_transactions (user_id, amount, reason)
            VALUES (?, ?, ?);
        """
        await chat_db_manager._execute(
            chat_db_manager._db_transaction,
            transaction_query,
            (user_id, subsidy, "破产补贴"),
            commit=True,
        )
        
        new_balance = await self.get_balance(user_id)
        log.info(f"用户 {user_id} 领取破产补贴 {subsidy} 月光币")
        
        return (
            True,
            f"💸 月月看你太可怜了，给你 **{subsidy}** 月光币救急！\n别再乱花钱了好吗...",
            new_balance
        )

    async def get_leaderboard(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取月光币排行榜"""
        query = """
            SELECT user_id, balance
            FROM user_coins
            WHERE balance > 0
            ORDER BY balance DESC
            LIMIT ?
        """
        results = await chat_db_manager._execute(
            chat_db_manager._db_transaction, query, (limit,), fetch="all"
        )
        return [dict(row) for row in results] if results else []

    async def get_user_rank(self, user_id: int) -> Optional[int]:
        """获取用户在排行榜中的排名"""
        query = """
            SELECT COUNT(*) + 1 as rank
            FROM user_coins
            WHERE balance > (
                SELECT COALESCE(balance, 0) FROM user_coins WHERE user_id = ?
            )
        """
        result = await chat_db_manager._execute(
            chat_db_manager._db_transaction, query, (user_id,), fetch="one"
        )
        return result["rank"] if result else None

    async def get_checkin_info(self, user_id: int) -> Tuple[Optional[str], int]:
        """
        获取用户签到信息。
        返回: (last_checkin_date, checkin_streak)
        """
        query = "SELECT last_checkin_date, checkin_streak FROM user_coins WHERE user_id = ?"
        result = await chat_db_manager._execute(
            chat_db_manager._db_transaction, query, (user_id,), fetch="one"
        )
        if result:
            return result["last_checkin_date"], result["checkin_streak"] or 0
        return None, 0


async def _setup_initial_items():
    """设置商店的初始商品（覆盖逻辑）"""
    log.info("正在设置商店初始商品...")

    # --- 新增：先删除所有现有商品以确保覆盖 ---
    delete_query = "DELETE FROM shop_items"
    await chat_db_manager._execute(
        chat_db_manager._db_transaction, delete_query, commit=True
    )
    log.info("已删除所有旧的商店商品。")
    # --- 结束 ---

    # 从配置文件导入商品列表
    from src.chat.config.shop_config import SHOP_ITEMS

    for name, desc, price, cat, target, effect in SHOP_ITEMS:
        await coin_service.add_item_to_shop(name, desc, price, cat, target, effect)
    log.info("商店初始商品设置完毕。")


# 单例实例
coin_service = CoinService()

# 在服务实例化后，这个函数需要由主程序在启动时调用
