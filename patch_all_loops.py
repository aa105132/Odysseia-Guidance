path = "src/chat/features/image_generation/services/gemini_imagen_service.py"
lines = open(path).readlines()

# 在指定循环行前插入 start_time 初始化（若无），在循环体第一行插入 deadline 检查
# 循环行（1-indexed）：1189, 1318, 2075, 2193, 2458, 2560
loops = [1189, 1318, 2075, 2193, 2458, 2560]

# 从后往前处理，避免行号偏移
for ln in sorted(loops, reverse=True):
    i = ln - 1  # 0-indexed of "for attempt in range..."
    loop_line = lines[i]
    assert "for attempt in range" in loop_line, f"line {ln} mismatch: {loop_line!r}"
    indent = loop_line[:len(loop_line) - len(loop_line.lstrip())]
    body_indent = indent + "    "

    # 检查循环体第一行是否已有 deadline 检查
    next_line = lines[i + 1] if i + 1 < len(lines) else ""
    already = "_deadline_exceeded" in next_line or "start_time" in next_line

    if not already:
        # 插入 start_time 初始化（在 for 循环之前）+ 循环体开头检查
        init_line = body_indent + "start_time = time.monotonic()\n"
        check_line = body_indent + 'if self._deadline_exceeded(start_time, "image generation "):\n'
        ret_line = body_indent + "    return None\n"
        lines[i:i] = [init_line]
        # 现在 for 在 i+1，插入检查在 i+2
        lines[i+2:i+2] = [check_line, ret_line]
        print(f"patched loop@{ln}")
    else:
        print(f"loop@{ln} already patched")

open(path, "w").writelines(lines)
import ast
ast.parse(open(path).read())
print("syntax OK")
