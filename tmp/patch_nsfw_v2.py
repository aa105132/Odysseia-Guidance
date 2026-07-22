#!/usr/bin/env python3
"""Patch v2: move NSFW two-step strategy BEFORE the original text2image call.
NSFW requests skip the original NSFW text2image entirely and go straight to SFW→NSFW edit.
"""

FILE = "/opt/Odysseia-Guidance/src/chat/features/tools/functions/generate_image.py"

with open(FILE, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Replace the block from "调用图片生成服务" to "===== NSFW 两步生成策略 =====" 
# with: NSFW two-step FIRST, then fallback to original for SFW

old_block = """        # 调用图片生成服务（每张图一个请求，全部并发执行）
        import asyncio

        images_list = []
        if number_of_images == 1:
            # 单张图直接调用
            result = await gemini_imagen_service.generate_single_image(
                prompt=prompt,
                negative_prompt=negative_prompt,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                content_rating=content_rating,
                model_name_override=model_name_override,
                openai_image_size=openai_image_size,
                openai_response_format=openai_response_format,
                openai_stream=openai_stream,
                openai_quality=openai_quality,
                openai_style=openai_style,
                openai_image_api_mode=openai_image_api_mode,
            )
            if result:
                images_list = [result]
        else:
            # 多张图：每张图一个请求，全部并发执行
            max_concurrent_tasks = max(
                1, int(GEMINI_IMAGEN_CONFIG.get("MAX_CONCURRENT_IMAGE_TASKS", 3))
            )
            semaphore = asyncio.Semaphore(max_concurrent_tasks)

            async def _generate_one_image() -> Optional[bytes]:
                async with semaphore:
                    return await gemini_imagen_service.generate_single_image(
                        prompt=prompt,
                        negative_prompt=negative_prompt,
                        aspect_ratio=aspect_ratio,
                        resolution=resolution,
                        content_rating=content_rating,
                        model_name_override=model_name_override,
                        openai_image_size=openai_image_size,
                        openai_response_format=openai_response_format,
                        openai_stream=openai_stream,
                        openai_quality=openai_quality,
                        openai_style=openai_style,
                        openai_image_api_mode=openai_image_api_mode,
                    )

            tasks = [
                _generate_one_image()
                for _ in range(number_of_images)
            ]

            # 并发执行所有请求
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 收集成功的结果
            failed_count = 0
            for result in results:
                if isinstance(result, Exception):
                    failed_count += 1
                    log.warning(f"图片生成失败: {result}")
                elif result:
                    images_list.append(result)

            if failed_count > 0:
                log.warning(f"共 {number_of_images} 个请求，{failed_count} 个失败")

        # ===== NSFW 两步生成策略 =====
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
                # 异常时保持原始 images_list 不变"""

new_block = """        # ===== NSFW 两步生成策略（在原始文生图之前执行） =====
        # 当 content_rating == "nsfw" 且没有手动指定模型时，
        # 直接跳过原始 NSFW 文生图，走 SFW画底图→NSFW图生图改色 两步策略。
        # 如果两步都失败，再 fallback 到原始 NSFW 文生图。
        import asyncio

        _nsfw_two_step_applied = False
        _nsfw_edit_failed = False
        _sfw_fallback_image = None
        images_list = []

        if (
            content_rating == "nsfw"
            and not model_name_override
            and number_of_images == 1
        ):
            try:
                # 第一步：用 SFW 模型画一张干净底图
                log.info("[NSFW两步] 第一步：用 SFW 模型生成干净底图")
                # 移除明显的 NSFW 描述词，让 SFW 模型能画出来
                nsfw_patterns = [
                    r"性感", r"内衣", r"蕾丝", r"透视", r"半透明", r"裸露",
                    r"暴露", r"诱惑", r"魅惑", r"侧卧在.*榻", r"床榻",
                    r"乳", r"胸", r"臀", r"丝袜", r"吊带",
                    r"丁字裤", r"裹胸", r"乳贴", r"真空",
                ]
                clean_prompt = prompt
                for pat in nsfw_patterns:
                    clean_prompt = re.sub(pat, "", clean_prompt)
                clean_prompt = clean_prompt.strip()
                if not clean_prompt or len(clean_prompt) < 10:
                    clean_prompt = prompt

                sfw_image = await gemini_imagen_service.generate_single_image(
                    prompt=clean_prompt,
                    negative_prompt=negative_prompt,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    content_rating="sfw",
                )

                if sfw_image:
                    _sfw_fallback_image = sfw_image
                    log.info("[NSFW两步] SFW 干净底图成功，进入第二步：NSFW图生图")

                    # 第二步：用 NSFW 图生图模型改成 NSFW 版本
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
                    log.warning("[NSFW两步] SFW 干净底图失败，fallback 到原始 NSFW 文生图")
            except Exception as e:
                log.error(f"[NSFW两步] 异常: {e}", exc_info=True)

        # 如果两步策略没有产出图片（未执行、SFW失败、或异常），走原始文生图
        if not images_list:
            log.info("[NSFW两步] 未走两步或两步失败，执行原始文生图")
            if number_of_images == 1:
                result = await gemini_imagen_service.generate_single_image(
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    content_rating=content_rating,
                    model_name_override=model_name_override,
                    openai_image_size=openai_image_size,
                    openai_response_format=openai_response_format,
                    openai_stream=openai_stream,
                    openai_quality=openai_quality,
                    openai_style=openai_style,
                    openai_image_api_mode=openai_image_api_mode,
                )
                if result:
                    images_list = [result]
            else:
                max_concurrent_tasks = max(
                    1, int(GEMINI_IMAGEN_CONFIG.get("MAX_CONCURRENT_IMAGE_TASKS", 3))
                )
                semaphore = asyncio.Semaphore(max_concurrent_tasks)

                async def _generate_one_image() -> Optional[bytes]:
                    async with semaphore:
                        return await gemini_imagen_service.generate_single_image(
                            prompt=prompt,
                            negative_prompt=negative_prompt,
                            aspect_ratio=aspect_ratio,
                            resolution=resolution,
                            content_rating=content_rating,
                            model_name_override=model_name_override,
                            openai_image_size=openai_image_size,
                            openai_response_format=openai_response_format,
                            openai_stream=openai_stream,
                            openai_quality=openai_quality,
                            openai_style=openai_style,
                            openai_image_api_mode=openai_image_api_mode,
                        )

                tasks = [
                    _generate_one_image()
                    for _ in range(number_of_images)
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                failed_count = 0
                for result in results:
                    if isinstance(result, Exception):
                        failed_count += 1
                        log.warning(f"图片生成失败: {result}")
                    elif result:
                        images_list.append(result)
                if failed_count > 0:
                    log.warning(f"共 {number_of_images} 个请求，{failed_count} 个失败")"""

if old_block not in content:
    print("ERROR: old block not found!")
    # Try to find partial match
    idx = content.find("调用图片生成服务")
    if idx >= 0:
        print(f"Found '调用图片生成服务' at position {idx}")
        print(f"Context: {content[idx:idx+200]}")
    exit(1)

content = content.replace(old_block, new_block, 1)

# 2. Remove the old NSFW two-step block that was AFTER the original generation
# (it's now redundant since we moved it before)
# The old block starts with "# ===== NSFW 两步生成策略 =====" and ends before "# 移除"
# But we already replaced it in step 1, so the old post-generation block should be gone.
# Let's check if there's a duplicate.
if content.count("[NSFW两步]") > 6:
    print("WARNING: possible duplicate NSFW two-step blocks!")

with open(FILE, "w", encoding="utf-8") as f:
    f.write(content)

print("generate_image.py patched v2 successfully!")
print("NSFW two-step now runs BEFORE original text2image, skipping it entirely when successful.")
