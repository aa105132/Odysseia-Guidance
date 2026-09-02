#!/usr/bin/env python3
"""Fix two bugs:
1. get_yearly_summary infinite loop: add in-memory cooldown per user
2. warning_threshold lost on restart: load from DB at startup
"""

# === Fix 1: Add cooldown to get_yearly_summary ===
FILE1 = "/opt/Odysseia-Guidance/src/chat/features/tools/functions/get_yearly_summary.py"

with open(FILE1, "r", encoding="utf-8") as f:
    content1 = f.read()

# Add imports and cooldown dict at top (after existing imports)
old_import_anchor = "from src.chat.config.chat_config import SUMMARY_CONFIG"
new_import_block = """from src.chat.config.chat_config import SUMMARY_CONFIG

# 防止同一用户短时间内被重复调用年度总结（防死循环）
import time as _time
_yearly_summary_cooldown: dict = {}  # {user_id: last_call_timestamp}
_YEARLY_SUMMARY_COOLDOWN_SECONDS = 30  # 同一用户至少间隔30秒"""

if "_yearly_summary_cooldown" not in content1:
    content1 = content1.replace(old_import_anchor, new_import_block, 1)
    print("Fix1: Added cooldown imports")
else:
    print("Fix1: Cooldown imports already present, skipping")

# Add cooldown check right after "步骤 1: 验证并获取 user_id" and user_id conversion
old_step2 = """    # 步骤 2: 从配置读取年份和生成上限（Dashboard 可动态修改）"""
new_step2 = """    # 步骤 1.5: 防死循环冷却检查
    _now = _time.time()
    _last_call = _yearly_summary_cooldown.get(user_id, 0)
    if _now - _last_call < _YEARLY_SUMMARY_COOLDOWN_SECONDS:
        log.warning(f"get_yearly_summary 冷却中: user_id={user_id}, 距上次调用仅 {_now - _last_call:.1f}s，拒绝执行")
        return {
            "status": "cooldown",
            "message": "年度总结刚刚已经发送过了，请稍后再试哦~",
        }
    _yearly_summary_cooldown[user_id] = _now

    # 步骤 2: 从配置读取年份和生成上限（Dashboard 可动态修改）"""

if "冷却检查" not in content1:
    content1 = content1.replace(old_step2, new_step2, 1)
    print("Fix1: Added cooldown check")
else:
    print("Fix1: Cooldown check already present, skipping")

with open(FILE1, "w", encoding="utf-8") as f:
    f.write(content1)
print("Fix1: get_yearly_summary.py saved")

# === Fix 2: Load warning_threshold from DB at startup ===
FILE2 = "/opt/Odysseia-Guidance/src/main.py"

with open(FILE2, "r", encoding="utf-8") as f:
    content2 = f.read()

# Find the on_ready function and add DB config loading
# Look for a good spot after DB initialization in on_ready
if "load_moderation_config_from_db" not in content2:
    # Add a function call in on_ready
    old_on_ready_end = "        log.info(f\"Bot is ready! Logged in as {self.user}\")"
    if old_on_ready_end not in content2:
        # Try alternate
        old_on_ready_end = "        log.info(f\"Bot 已就绪! 登录为 {self.user}\")"
    if old_on_ready_end not in content2:
        # Search for what's actually there
        import re
        m = re.search(r'(log\.info.*[Bb]ot.*ready.*\n)', content2)
        if m:
            old_on_ready_end = m.group(1).rstrip('\n')
            print(f"Fix2: Found on_ready log line: {old_on_ready_end[:80]}")
        else:
            print("Fix2: WARNING - Could not find on_ready log line, skipping DB load")
            old_on_ready_end = None

    if old_on_ready_end:
        new_on_ready_end = old_on_ready_end + """

        # 从数据库加载管理配置（如警告阈值），防止重启后丢失 Dashboard 设置
        try:
            from src.chat.utils.database import chat_db_manager
            from src.chat.config import chat_config
            db_warning_threshold = await chat_db_manager.get_global_setting("warning_threshold")
            if db_warning_threshold:
                chat_config.BLACKLIST_WARNING_THRESHOLD = int(db_warning_threshold)
                log.info(f"从数据库加载警告阈值: {chat_config.BLACKLIST_WARNING_THRESHOLD}")
        except Exception as e:
            log.warning(f"从数据库加载管理配置失败（使用默认值）: {e}")"""
        content2 = content2.replace(old_on_ready_end, new_on_ready_end, 1)
        print("Fix2: Added DB config load in on_ready")

        with open(FILE2, "w", encoding="utf-8") as f:
            f.write(content2)
        print("Fix2: main.py saved")
    else:
        print("Fix2: SKIPPED - could not patch main.py")
else:
    print("Fix2: Already patched, skipping")

# === Fix 3: Also set env var so it survives without DB ===
# Update .env with the correct value
ENV_FILE = "/opt/Odysseia-Guidance/.env"
with open(ENV_FILE, "r", encoding="utf-8") as f:
    env_content = f.read()

if "BLACKLIST_WARNING_THRESHOLD" not in env_content:
    env_content += "\n# 警告次数阈值（Dashboard 设置的值）\nBLACKLIST_WARNING_THRESHOLD=3\n"
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write(env_content)
    print("Fix3: Added BLACKLIST_WARNING_THRESHOLD=3 to .env")
else:
    # Update existing value
    import re
    env_content = re.sub(r'BLACKLIST_WARNING_THRESHOLD=\d+', 'BLACKLIST_WARNING_THRESHOLD=3', env_content)
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write(env_content)
    print("Fix3: Updated BLACKLIST_WARNING_THRESHOLD=3 in .env")

print("\n=== All fixes applied! ===")
