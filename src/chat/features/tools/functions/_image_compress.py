"""Image compression utility for Discord's 10MB upload limit."""
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

        # 检查是否有透明通道（RGBA/LA/P 模式）
        has_alpha = img.mode in ("RGBA", "LA", "P")
        if has_alpha:
            if img.mode == "P":
                img = img.convert("RGBA")

            # 有透明通道的图片：优先用 PNG 压缩（保留透明）
            for optimize in [True, True]:
                for bit_depth in [8]:
                    buf = io.BytesIO()
                    img.save(buf, format="PNG", optimize=optimize, compress_level=9)
                    compressed = buf.getvalue()
                    if len(compressed) <= max_bytes:
                        log.info(f"[图片压缩] PNG(保留透明) -> {len(compressed)/1024/1024:.2f}MB")
                        return compressed, "png"

            # PNG 还是太大：先缩小尺寸，仍然用 PNG
            for scale in [0.75, 0.5, 0.4, 0.3]:
                new_size = (int(img.width * scale), int(img.height * scale))
                resized = img.resize(new_size, Image.LANCZOS)
                buf = io.BytesIO()
                resized.save(buf, format="PNG", optimize=True, compress_level=9)
                compressed = buf.getvalue()
                if len(compressed) <= max_bytes:
                    log.info(f"[图片压缩] PNG(保留透明) 缩放 {scale*100:.0f}% -> {len(compressed)/1024/1024:.2f}MB ({new_size[0]}x{new_size[1]})")
                    return compressed, "png"

            # 实在不行才转 JPEG（丢透明通道）
            log.warning("[图片压缩] 透明图片过大，降级为 JPEG（丢失透明通道）")
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
            img = background
        else:
            # 没有透明通道：直接转 RGB
            if img.mode != "RGB":
                img = img.convert("RGB")

        # JPEG 压缩
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
