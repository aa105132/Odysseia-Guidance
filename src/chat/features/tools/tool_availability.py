from __future__ import annotations

from datetime import date, datetime
import logging
from typing import Callable, Iterable, List, Optional

from src.chat.config.chat_config import SPRING_FESTIVAL_CONFIG
from src.chat.utils.time_utils import BEIJING_TZ

log = logging.getLogger(__name__)

SPRING_FESTIVAL_TOOL_NAME = "spring_festival_red_envelope"
_SPRING_FESTIVAL_FALLBACK_START_MMDD = (1, 15)
_SPRING_FESTIVAL_FALLBACK_END_MMDD = (3, 1)


def _get_beijing_today() -> date:
    return datetime.now(BEIJING_TZ).date()


def _parse_config_date(raw_value: str) -> Optional[date]:
    text = str(raw_value or "").strip()
    if not text:
        return None

    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        log.warning("春节时间配置格式无效：%s，预期格式为 YYYY-MM-DD", text)
        return None


def is_spring_festival_in_date_window(today: Optional[date] = None) -> bool:
    """判断当前是否处于春节红包工具可用时间窗。"""
    current_day = today or _get_beijing_today()
    start_date = _parse_config_date(SPRING_FESTIVAL_CONFIG.get("start_date", ""))
    end_date = _parse_config_date(SPRING_FESTIVAL_CONFIG.get("end_date", ""))

    if start_date and end_date:
        if start_date > end_date:
            log.warning(
                "春节时间配置无效：start_date %s 晚于 end_date %s",
                start_date.isoformat(),
                end_date.isoformat(),
            )
            return False
        return start_date <= current_day <= end_date

    current_mmdd = (current_day.month, current_day.day)
    return (
        _SPRING_FESTIVAL_FALLBACK_START_MMDD
        <= current_mmdd
        <= _SPRING_FESTIVAL_FALLBACK_END_MMDD
    )


def is_spring_festival_tool_visible(today: Optional[date] = None) -> bool:
    """判断春节红包工具当前是否应暴露给模型。"""
    enabled = bool(SPRING_FESTIVAL_CONFIG.get("enabled", True))
    return enabled and is_spring_festival_in_date_window(today=today)


def filter_tool_declarations(tool_declarations: Iterable[Callable]) -> List[Callable]:
    """按当前活动状态过滤不应暴露给模型的工具。"""
    filtered_tools: List[Callable] = []
    for tool in tool_declarations:
        tool_name = getattr(tool, "__name__", "")
        if (
            tool_name == SPRING_FESTIVAL_TOOL_NAME
            and not is_spring_festival_tool_visible()
        ):
            continue
        filtered_tools.append(tool)
    return filtered_tools
