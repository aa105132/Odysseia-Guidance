import discord
from discord import Interaction
from typing import Optional, Callable, Awaitable, Dict, Any

class ChatSettingsModal(discord.ui.Modal):
    """一个通用的模态框，用于设置聊天CD，支持回调。"""

    def __init__(
        self,
        *,
        title: str,
        current_config: Dict[str, Any],
        on_submit_callback: Callable[[Interaction, Dict[str, Any]], Awaitable[None]],
        include_enable_option: bool = True,
        entity_name: Optional[str] = None
    ):
        super().__init__(title=title)
        self.on_submit_callback = on_submit_callback

        # --- 解析当前配置 ---
        cooldown_sec_str = str(v) if (v := current_config.get("cooldown_seconds")) is not None else ""
        duration_str = str(v) if (v := current_config.get("cooldown_duration")) is not None else ""
        limit_str = str(v) if (v := current_config.get("cooldown_limit")) is not None else ""
        
        # --- 输入字段定义 ---
        if include_enable_option and entity_name:
            enabled_str = str(v).lower() if (v := current_config.get("is_chat_enabled")) is not None else ""
            drawing_str = str(v).lower() if (v := current_config.get("is_drawing_enabled")) is not None else ""
            self.enabled_input = discord.ui.TextInput(
                label=f"[{entity_name}] 是否开启聊天",
                placeholder="true / false / 留空以继承",
                default=enabled_str,
                required=False,
                row=0
            )
            self.add_item(self.enabled_input)
            
            self.drawing_input = discord.ui.TextInput(
                label=f"[{entity_name}] 是否开启绘图",
                placeholder="true / false / 留空以继承",
                default=drawing_str,
                required=False,
                row=1
            )
            self.add_item(self.drawing_input)
        else:
            self.enabled_input = None
            self.drawing_input = None

        self.cooldown_seconds_input = discord.ui.TextInput(
            label="模式一: 固定冷却",
            placeholder="每条消息冷却X秒。例如: 30",
            default=cooldown_sec_str,
            required=False,
            row=2
        )
        self.duration_input = discord.ui.TextInput(
            label="模式二: 频率限制 - 时间窗口(秒)",
            placeholder="在X秒内... (例如: 60)",
            default=duration_str,
            required=False,
            row=3
        )
        self.limit_input = discord.ui.TextInput(
            label="模式二: 频率限制 - 次数上限",
            placeholder="...最多允许发送Y条消息。例如: 2",
            default=limit_str,
            required=False,
            row=3
        )

        self.add_item(self.cooldown_seconds_input)
        self.add_item(self.duration_input)
        self.add_item(self.limit_input)

    async def on_submit(self, interaction: Interaction):
        settings = {}
        try:
            # 解析布尔值
            if self.enabled_input:
                enabled_val = self.enabled_input.value.strip().lower()
                if enabled_val in ['true', '1', 'yes']:
                    settings['is_chat_enabled'] = True
                elif enabled_val in ['false', '0', 'no']:
                    settings['is_chat_enabled'] = False
                elif enabled_val == '':
                    settings['is_chat_enabled'] = None
                else:
                    raise ValueError("无效的布尔值")
            if self.drawing_input:
                drawing_val = self.drawing_input.value.strip().lower()
                if drawing_val in ['true', '1', 'yes']:
                    settings['is_drawing_enabled'] = True
                elif drawing_val in ['false', '0', 'no']:
                    settings['is_drawing_enabled'] = False
                elif drawing_val == '':
                    settings['is_drawing_enabled'] = None
                else:
                    raise ValueError("无效的布尔值")
            
            # 解析整数值
            settings['cooldown_seconds'] = int(v) if (v := self.cooldown_seconds_input.value.strip()) else None
            settings['cooldown_duration'] = int(v) if (v := self.duration_input.value.strip()) else None
            settings['cooldown_limit'] = int(v) if (v := self.limit_input.value.strip()) else None

            # 调用回调函数
            await self.on_submit_callback(interaction, settings)

        except (ValueError, TypeError):
            await interaction.response.send_message("输入的值无效，请确保CD值为纯数字。", ephemeral=True)
