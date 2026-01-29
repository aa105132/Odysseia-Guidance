# -*- coding: utf-8 -*-
"""
服务注册表
用于在Bot进程内共享服务实例给Dashboard
"""

import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.chat.services.gemini_service import GeminiService
    from discord.ext.commands import Bot

log = logging.getLogger(__name__)


class ServiceRegistry:
    """
    服务注册表单例，用于在Bot和Dashboard之间共享服务实例。
    当Dashboard和Bot在同一进程中运行时，可以直接访问这些服务。
    """
    
    _instance: Optional["ServiceRegistry"] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._gemini_service = None
            cls._instance._bot = None
            cls._instance._initialized = False
        return cls._instance
    
    @property
    def gemini_service(self) -> Optional["GeminiService"]:
        """获取 GeminiService 实例"""
        return self._gemini_service
    
    @gemini_service.setter
    def gemini_service(self, service: "GeminiService"):
        """注册 GeminiService 实例"""
        self._gemini_service = service
        log.info("✅ GeminiService 已注册到 ServiceRegistry")
    
    @property
    def bot(self) -> Optional["Bot"]:
        """获取 Discord Bot 实例"""
        return self._bot
    
    @bot.setter
    def bot(self, bot_instance: "Bot"):
        """注册 Discord Bot 实例"""
        self._bot = bot_instance
        log.info("✅ Discord Bot 已注册到 ServiceRegistry")
    
    @property
    def is_initialized(self) -> bool:
        """检查服务是否已初始化"""
        return self._gemini_service is not None
    
    def get_bot_status(self) -> dict:
        """获取Bot状态信息"""
        if self._bot is None:
            return {"status": "not_initialized", "user": None, "guilds": 0}
        
        return {
            "status": "running" if self._bot.is_ready() else "starting",
            "user": str(self._bot.user) if self._bot.user else None,
            "user_id": self._bot.user.id if self._bot.user else None,
            "guilds": len(self._bot.guilds) if self._bot.is_ready() else 0,
            "latency_ms": round(self._bot.latency * 1000, 2) if self._bot.latency else None,
        }


# 全局单例实例
service_registry = ServiceRegistry()