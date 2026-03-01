# -*- coding: utf-8 -*-
import random
from src.chat.config.chat_config import WARMUP_MESSAGES


def get_random_praise_prompt():
    """从列表中随机选择一个新帖评价提示词"""
    prompts = WARMUP_MESSAGES.get("new_thread_comment_prompts")
    if not prompts:
        prompts = WARMUP_MESSAGES["consent_prompts"]
    return random.choice(prompts)


def get_random_auto_chat_prompt(is_idle_call: bool = False):
    """根据场景随机获取自动发言提示词。"""
    key = "idle_call_prompts" if is_idle_call else "auto_chat_prompts"
    prompts = WARMUP_MESSAGES.get(key) or WARMUP_MESSAGES["consent_prompts"]
    return random.choice(prompts)
