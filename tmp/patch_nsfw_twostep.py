#!/usr/bin/env python3
"""Patch generate_image.py to add SFW-first fallback for NSFW requests.

Strategy:
1. When content_rating == "nsfw", first generate a clean SFW image using the strong SFW model
2. Then use the NSFW edit model to transform the SFW image into the NSFW version
3. If the NSFW edit fails, send the SFW image and tell the user NSFW isn't available right now
"""

import re

FILE = "/opt/Odysseia-Guidance/src/chat/features/tools/functions/generate_image.py"

with open(FILE, "r", encoding="utf-8") as f:
    content = f.read()

# Find the section where images_list is built (after generate_single_image calls)
# We need to insert our NSFW two-step logic right after the existing generation,
# before the "移除正在生成反应" section.

# The anchor: after the multi-image gather block, before "移除"正在生成"反应"
old_anchor = """        # 移除"正在生成"反应
        await remove_reaction(GENERATING_EMOJI)

        if images_list and len(images_list) > 0:"""

new_block = '''        # ===== NSFW 两步生成策略 =====
        # 当 content_rating == "nsfw" 时，先用 SFW 模型画一张干净底图，
        # 再用 NSFW 图生图模型把干净底图改成 NSFW 版本。
        # 如果 NSFW 图生图失败，发 SFW 底图并提示用户。
        _nsfw_two_step_applied = False
        _nsfw_edit_failed = False
        _sfw_fallback_image = None
        if (
            content_rating == "nsfw"
            and images_list
            and len(images_list) > 0
            and not model_name_override
        ):
            try:
                # 第一步：用 SFW 模型重新画一张干净底图
                log.info("[NSFW两步] 第一步：用 SFW 模型生成干净底图")
                sfw_prompt = prompt
                # 移除明显的 NSFW 描述词，让 SFW 模型能画出来
                nsfw_patterns = [
                    r"性感", r"内衣", r"蕾丝", r"透视", r"半透明", r"裸露",
                    r"暴露", r"诱惑", r"魅惑", r"侧卧在.*榻", r"床榻",
                    r"乳", r"胸", r"臀", r"腿", r"丝袜", r"吊带",
                    r"丁字裤", r"裹胸", r"乳贴", r"真空",
                ]
                clean_prompt = sfw_prompt
                for pat in nsfw_patterns:
                    clean_prompt = re.sub(pat, "", clean_prompt)
                clean_prompt = clean_prompt.strip()
                if not clean_prompt or len(clean_prompt) < 10:
                    clean_prompt = sfw_prompt  # fallback to original if too aggressive

                sfw_image = await gemini_imagen_service.generate_single_image(
                    prompt=clean_prompt,
                    negative_prompt=negative_prompt,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    content_rating="sfw",
                )

                if sfw_image:
                    _sfw_fallback_image = sfw_image
                    log.info("[NSFW两步] SFW 干净底图生成成功，进入第二步：NSFW图生图")

                    # 第二步：用 NSFW 图生图模型把干净底图改成 NSFW 版本
                    nsfw_edit_prompt = (
                        f"在这张图的基础上进行修改。保持人物身份、脸、发型、服装风格、画风、构图不变。"
                        f"将画面修改为以下效果：{prompt}"
                    )
                    nsfw_image = await gemini_imagen_service.edit_image(
                        reference_image=sfw_image,
                        edit_prompt=nsfw_edit_prompt,
                        aspect_ratio=aspect_ratio,
                        resolution=resolution,
                        content_rating="nsfw",
                    )

                    if nsfw_image:
                        log.info("[NSFW两步] NSFW 图生图成功，替换最终结果")
                        images_list = [nsfw_image]
                        _nsfw_two_step_applied = True
                    else:
                        log.warning("[NSFW两步] NSFW 图生图失败，使用 SFW 干净底图兜底")
                        images_list = [sfw_image]
                        _nsfw_edit_failed = True
                else:
                    log.warning("[NSFW两步] SFW 干净底图也失败，使用原始 NSFW 文生图结果")
            except Exception as e:
                log.error(f"[NSFW两步] 异常: {e}", exc_info=True)
                # 异常时保持原始 images_list 不变

        # 移除"正在生成"反应
        await remove_reaction(GENERATING_EMOJI)

        if images_list and len(images_list) > 0:'''

# Check if the anchor exists
if old_anchor not in content:
    print("ERROR: anchor not found! File may have changed.")
    print("Looking for partial match...")
    if "移除" in content and "正在生成" in content:
        # Find the actual line
        for line in content.split("\n"):
            if "正在生成" in line:
                print(f"Found: {line}")
    exit(1)

content = content.replace(old_anchor, new_block, 1)

# Now modify the success message to include NSFW fallback notification
# Find the return dict for success case
old_success = '''            return {
                "success": True,
                "skip_ai_response": True,
                "images_generated": actual_count,
                "cost": actual_cost,
                "message": "图片已成功生成并发送给用户，预告消息已发送，无需再回复。"
            }'''

new_success = '''            # NSFW 两步策略的特殊提示
            if _nsfw_edit_failed:
                return {
                    "success": True,
                    "skip_ai_response": True,
                    "images_generated": actual_count,
                    "cost": actual_cost,
                    "message": "图片已成功生成并发送给用户。注意：NSFW图生图失败了，发的是SFW干净底图。请用你的语气告诉用户：NSFW内容暂时画不出来，先给你一张正常版本的图饱饱眼福，稍后再试NSFW。"
                }
            elif _nsfw_two_step_applied:
                return {
                    "success": True,
                    "skip_ai_response": True,
                    "images_generated": actual_count,
                    "cost": actual_cost,
                    "message": "图片已成功生成并发送给用户。NSFW两步策略成功：先SFW画底图再NSFW图生图改色。无需再回复。"
                }

            return {
                "success": True,
                "skip_ai_response": True,
                "images_generated": actual_count,
                "cost": actual_cost,
                "message": "图片已成功生成并发送给用户，预告消息已发送，无需再回复。"
            }'''

if old_success not in content:
    print("WARNING: success return block not found exactly, trying fuzzy match...")
    # Try to find it
    idx = content.find('"图片已成功生成并发送给用户，预告消息已发送，无需再回复。"')
    if idx >= 0:
        # Find the enclosing return block
        start = content.rfind("return {", max(0, idx - 200), idx)
        end = content.find("}", idx)
        if start >= 0 and end >= 0:
            old_success = content[start:end+1]
            print(f"Found fuzzy match at position {start}")
        else:
            print("ERROR: Could not find success return block")
            exit(1)

content = content.replace(old_success, new_success, 1)

# Also modify the generation_failed return to suggest SFW fallback
old_fail = '''            return {
                "generation_failed": True,
                "reason": "generation_failed",
                "hint": "图片生成失败了，可能是技术原因或描述不够清晰。请用自己的语气告诉用户生成失败了，建议他们稍微调整一下描述再试试。不要指责用户的请求不当。"
            }'''

new_fail = '''            # NSFW 两步策略：如果原始 NSFW 文生图完全失败，尝试 SFW 兜底
            if content_rating == "nsfw" and _sfw_fallback_image:
                # SFW 底图之前已经成功，但 NSFW 编辑失败了
                # 这个分支理论上不会到这里，因为 images_list 已经有 SFW 底图了
                pass

            return {
                "generation_failed": True,
                "reason": "generation_failed",
                "hint": "图片生成失败了，可能是技术原因或描述不够清晰。请用自己的语气告诉用户生成失败了，建议他们稍微调整一下描述再试试。不要指责用户的请求不当。"
            }'''

if old_fail in content:
    content = content.replace(old_fail, new_fail, 1)
    print("Generation failed block updated")
else:
    print("WARNING: generation_failed block not found (non-critical)")

with open(FILE, "w", encoding="utf-8") as f:
    f.write(content)

print("generate_image.py patched successfully!")
