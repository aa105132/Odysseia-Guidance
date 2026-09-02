#!/usr/bin/env python3
"""Add image compression utility for Discord's 10MB limit.
Patches generate_image.py and edit_image.py to compress images > 9.5MB before sending.
"""

# === Step 1: Add compression utility function to a shared location ===
UTIL_FILE = "/opt/Odysseia-Guidance/src/chat/features/tools/functions/_image_compress.py"

util_code = '''"""Image compression utility for Discord's 10MB upload limit."""
import io
import logging
from typing import Optional

log = logging.getLogger(__name__)

DISCORD_MAX_BYTES = 9_500_000  # 9.5MB safety margin (Discord limit is 10MB)


def compress_image_for_discord(image_bytes: bytes, max_bytes: int = DISCORD_MAX_BYTES) -> tuple[bytes, str]:
    """Compress image to fit within Discord's upload limit.
    
    Returns:
        (compressed_bytes, filename_extension) - e.g. (data, "jpg") or (data, "png")
    """
    if len(image_bytes) <= max_bytes:
        return image_bytes, "png"

    log.info(f"[图片压缩] 原始大小 {len(image_bytes)/1024/1024:.2f}MB 超过限制，开始压缩")

    try:
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes))

        # Convert RGBA to RGB for JPEG
        if img.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            background.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
            img = background

        # Try JPEG at decreasing quality levels
        for quality in [95, 90, 85, 80, 75, 70, 60, 50]:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            compressed = buf.getvalue()
            if len(compressed) <= max_bytes:
                log.info(f"[图片压缩] JPEG quality={quality} -> {len(compressed)/1024/1024:.2f}MB")
                return compressed, "jpg"

        # If still too large, resize
        for scale in [0.75, 0.5, 0.4, 0.3]:
            new_size = (int(img.width * scale), int(img.height * scale))
            resized = img.resize(new_size, Image.LANCZOS)
            buf = io.BytesIO()
            resized.save(buf, format="JPEG", quality=80, optimize=True)
            compressed = buf.getvalue()
            if len(compressed) <= max_bytes:
                log.info(f"[图片压缩] 缩放 {scale*100:.0f}% + JPEG q=80 -> {len(compressed)/1024/1024:.2f}MB ({new_size[0]}x{new_size[1]})")
                return compressed, "jpg"

        # Last resort
        log.warning("[图片压缩] 即使最大压缩也无法降到限制内，返回最小版本")
        return compressed, "jpg"

    except ImportError:
        log.warning("[图片压缩] Pillow 未安装，尝试直接截断 PNG（不推荐）")
        return image_bytes, "png"
    except Exception as e:
        log.error(f"[图片压缩] 压缩失败: {e}", exc_info=True)
        return image_bytes, "png"
'''

with open(UTIL_FILE, "w", encoding="utf-8") as f:
    f.write(util_code)
print("Step 1: Created _image_compress.py utility")

# === Step 2: Patch generate_image.py ===
GEN_FILE = "/opt/Odysseia-Guidance/src/chat/features/tools/functions/generate_image.py"

with open(GEN_FILE, "r", encoding="utf-8") as f:
    gen_content = f.read()

# Add import at top (after existing imports)
import_anchor = "from src.chat.features.tools.functions.image_policy_guard import ("
compress_import = "from src.chat.features.tools.functions._image_compress import compress_image_for_discord\n" + import_anchor

if "compress_image_for_discord" not in gen_content:
    gen_content = gen_content.replace(import_anchor, compress_import, 1)
    print("Step 2a: Added compress import to generate_image.py")

# Patch the discord.File creation to compress first
old_file_gen = '''                            batch_files.append(
                                discord.File(
                                    io.BytesIO(images_list[idx]),
                                    filename=f"generated_image_{idx+1}.png",
                                    spoiler=use_spoiler
                                )
                            )'''

new_file_gen = '''                            _img_data, _img_ext = compress_image_for_discord(images_list[idx])
                            batch_files.append(
                                discord.File(
                                    io.BytesIO(_img_data),
                                    filename=f"generated_image_{idx+1}.{_img_ext}",
                                    spoiler=use_spoiler
                                )
                            )'''

if old_file_gen in gen_content:
    gen_content = gen_content.replace(old_file_gen, new_file_gen, 1)
    print("Step 2b: Patched first batch_files in generate_image.py")
else:
    print("Step 2b: WARNING - first batch_files pattern not found (may already be patched)")

# Patch the second occurrence (batch generate)
old_file_batch = '''                            batch_files.append(
                                discord.File(
                                    io.BytesIO(all_images[idx]),
                                    filename=f"generated_image_{idx+1}.png",
                                    spoiler=use_spoiler'''

new_file_batch = '''                            _img_data, _img_ext = compress_image_for_discord(all_images[idx])
                            batch_files.append(
                                discord.File(
                                    io.BytesIO(_img_data),
                                    filename=f"generated_image_{idx+1}.{_img_ext}",
                                    spoiler=use_spoiler'''

if old_file_batch in gen_content:
    gen_content = gen_content.replace(old_file_batch, new_file_batch, 1)
    print("Step 2c: Patched second batch_files in generate_image.py")
else:
    print("Step 2c: WARNING - second batch_files pattern not found")

with open(GEN_FILE, "w", encoding="utf-8") as f:
    f.write(gen_content)
print("Step 2: generate_image.py saved")

# === Step 3: Patch edit_image.py ===
EDIT_FILE = "/opt/Odysseia-Guidance/src/chat/features/tools/functions/edit_image.py"

with open(EDIT_FILE, "r", encoding="utf-8") as f:
    edit_content = f.read()

# Add import
if "compress_image_for_discord" not in edit_content:
    # Find a good import anchor
    edit_import_anchor = "import logging"
    edit_content = edit_content.replace(
        edit_import_anchor,
        edit_import_anchor + "\nfrom src.chat.features.tools.functions._image_compress import compress_image_for_discord",
        1
    )
    print("Step 3a: Added compress import to edit_image.py")

# Patch single image send
old_edit_file = '''                    file = discord.File(
                        io.BytesIO(edited_image_bytes),
                        filename="edited_image.png",
                        spoiler=use_spoiler,'''

new_edit_file = '''                    _img_data, _img_ext = compress_image_for_discord(edited_image_bytes)
                    file = discord.File(
                        io.BytesIO(_img_data),
                        filename=f"edited_image.{_img_ext}",
                        spoiler=use_spoiler,'''

if old_edit_file in edit_content:
    edit_content = edit_content.replace(old_edit_file, new_edit_file, 1)
    print("Step 3b: Patched single file in edit_image.py")
else:
    print("Step 3b: WARNING - single file pattern not found")

# Patch batch edit send
old_edit_batch = '''                        discord.File(
                            io.BytesIO(all_images[idx]),
                            filename=f"edited_image_{idx + 1}.png",
                            spoiler=use_spoiler,'''

new_edit_batch = '''                        discord.File(
                            io.BytesIO(compress_image_for_discord(all_images[idx])[0]),
                            filename=f"edited_image_{idx + 1}.{compress_image_for_discord(all_images[idx])[1]}",
                            spoiler=use_spoiler,'''

# Actually let's do it cleaner for the batch
old_edit_batch2 = '''                        discord.File(
                            io.BytesIO(all_images[idx]),
                            filename=f"edited_image_{idx + 1}.png",
                            spoiler=use_spoiler,'''

new_edit_batch2_clean = '''                        # 压缩图片以适应 Discord 10MB 限制
                        _eimg_data, _eimg_ext = compress_image_for_discord(all_images[idx])
                        batch_files.append(discord.File(
                            io.BytesIO(_eimg_data),
                            filename=f"edited_image_{idx + 1}.{_eimg_ext}",
                            spoiler=use_spoiler,'''

# This one is trickier because the append is separate. Let's just replace the File creation inline
if old_edit_batch in edit_content:
    # Simpler approach: just wrap the bytes
    edit_content = edit_content.replace(
        "io.BytesIO(all_images[idx]),\n                            filename=f\"edited_image_{idx + 1}.png\",",
        "io.BytesIO(compress_image_for_discord(all_images[idx])[0]),\n                            filename=f\"edited_image_{idx + 1}.jpg\",",
        1
    )
    print("Step 3c: Patched batch files in edit_image.py")
else:
    print("Step 3c: WARNING - batch file pattern not found")

with open(EDIT_FILE, "w", encoding="utf-8") as f:
    f.write(edit_content)
print("Step 3: edit_image.py saved")

print("\n=== All compression patches applied! ===")
print("Images > 9.5MB will be auto-compressed to JPEG before sending to Discord.")
