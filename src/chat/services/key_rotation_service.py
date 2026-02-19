import asyncio
import time
import logging
import random
from dataclasses import dataclass
from enum import Enum, auto
from typing import List, Dict

# 配置日志
log = logging.getLogger(__name__)

class KeyStatus(Enum):
    AVAILABLE = auto()
    IN_USE = auto()
    COOLING_DOWN = auto()
    DISABLED = auto()


@dataclass
class ApiKey:
    key: str
    status: KeyStatus = KeyStatus.AVAILABLE
    last_used: float = 0.0
    cooldown_until: float = 0.0
    reputation: int = 100  # 信誉评分，100为满分
    consecutive_successes: int = 0  # 连续成功次数
    consecutive_failures: int = 0  # 新增：连续失败次数


class NoAvailableKeyError(Exception):
    """当没有可用Key时抛出此异常"""

    pass


class KeyRotationService:
    """
    管理和轮换API Key的智能服务。
    """

    def __init__(self, api_keys: List[str]):
        if not api_keys:
            raise ValueError("API密钥列表不能为空。")

        self.keys: Dict[str, ApiKey] = {key: ApiKey(key=key) for key in api_keys}
        self.lock = asyncio.Lock()
        log.info(f"密钥轮换服务已初始化，共加载 {len(self.keys)} 个密钥。")

    def _load_reputations(self):
        """兼容保留：信誉系统已禁用，不执行任何操作。"""
        return

    def _save_reputations_sync(self):
        """兼容保留：信誉系统已禁用，不执行任何操作。"""
        return

    async def acquire_key(self) -> ApiKey:
        """
        获取一个可用的API Key。

        会一直等待直到有可用的Key为止。
        """
        while True:
            async with self.lock:
                now = time.time()

                # 步骤 1: 检查冷却时间结束的Key并更新其状态
                for key_obj in self.keys.values():
                    if (
                        key_obj.status == KeyStatus.COOLING_DOWN
                        and now >= key_obj.cooldown_until
                    ):
                        key_obj.status = KeyStatus.AVAILABLE
                        key_obj.cooldown_until = 0.0
                        log.info(f"密钥 ...{key_obj.key[-4:]} 冷却结束，现已可用。")

                # 步骤 2: 寻找一个可用的Key
                available_keys = [
                    k for k in self.keys.values() if k.status == KeyStatus.AVAILABLE
                ]

                if available_keys:
                    # 找到最久未使用的Key
                    # 从可用密钥中随机选择一个
                    best_key = random.choice(available_keys)
                    best_key.status = KeyStatus.IN_USE
                    best_key.last_used = now
                    log.info(f"获取到密钥: ...{best_key.key[-4:]}")
                    return best_key

            # 步骤 3: 如果没有可用的Key，等待后重试
            log.debug("当前无可用密钥，等待中...")
            await asyncio.sleep(1)

    async def release_key(
        self,
        key: str,
        success: bool = True,
        failure_penalty: int = 25,
        safety_penalty: int = 0,
    ):
        """
        释放一个API Key。

        当前已移除信誉与惩罚机制：
        - 不再扣分
        - 不再进入冷却
        - 始终恢复为 AVAILABLE（除非该 key 已被禁用）

        Args:
            key (str): 要释放的API Key。
            success (bool): 兼容参数，当前不影响行为。
            failure_penalty (int): 兼容参数，当前不影响行为。
            safety_penalty (int): 兼容参数，当前不影响行为。
        """
        async with self.lock:
            key_obj = self.keys.get(key)
            if not key_obj:
                log.warning(f"尝试释放一个不存在的密钥: {key}")
                return

            if key_obj.status == KeyStatus.DISABLED:
                return

            key_obj.status = KeyStatus.AVAILABLE
            key_obj.cooldown_until = 0.0
            key_obj.consecutive_successes = 0
            key_obj.consecutive_failures = 0
            log.info(f"密钥 ...{key_obj.key[-4:]} 已释放并恢复可用。")

    def _calculate_cooldown(self, reputation: int) -> float:
        """
        兼容保留：信誉系统已禁用，始终返回 0。
        """
        return 0.0

    async def disable_key(self, key: str, reason: str):
        """
        永久禁用一个Key（例如，因无效或被吊销）。
        """
        async with self.lock:
            key_obj = self.keys.get(key)
            if key_obj:
                key_obj.status = KeyStatus.DISABLED
                log.error(f"密钥 ...{key_obj.key[-4:]} 已被永久禁用。原因: {reason}")
            else:
                log.warning(f"尝试禁用一个不存在的密钥: {key}")
