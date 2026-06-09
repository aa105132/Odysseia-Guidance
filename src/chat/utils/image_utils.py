import io
import logging
import os
import tempfile
from PIL import Image
from typing import Tuple, List, Dict, Any

log = logging.getLogger(__name__)


# --- 压缩策略常量 ---
NO_COMPRESSION_THRESHOLD_BYTES = 10 * 1024 * 1024  # 10 MB (小于此值不执行迭代压缩)
MAX_IMAGE_SIZE_BYTES = 15 * 1024 * 1024  # 15 MB (硬性物理上限)
TARGET_IMAGE_SIZE_BYTES = 4 * 1024 * 1024  # 4 MB  (大于10MB的图片期望压缩到的目标大小)
MAX_IMAGE_DIMENSION = 4096  # 4096 像素 (最大尺寸)
HIGH_QUALITY = 95  # 用于10MB以下图片的保存质量
INITIAL_QUALITY = 85  # 用于10MB以上图片的初始保存质量
MIN_QUALITY = 50  # 最低可接受质量
QUALITY_STEP = 10  # 每次迭代降低的质量值


def _calculate_sample_indices(total_frames: int, max_frames: int) -> List[int]:
    """按均匀抽样策略计算帧索引，确保首尾帧优先保留。"""
    safe_total_frames = max(1, int(total_frames))
    safe_max_frames = max(1, int(max_frames))

    if safe_total_frames <= safe_max_frames:
        return list(range(safe_total_frames))

    if safe_max_frames == 1:
        return [0]

    # 使用线性插值均匀抽样，天然包含首帧和尾帧
    sampled = [
        int(i * (safe_total_frames - 1) / (safe_max_frames - 1))
        for i in range(safe_max_frames)
    ]

    # 去重，防止极端情况下出现重复索引
    unique_indices: List[int] = []
    seen = set()
    for idx in sampled:
        if idx not in seen:
            unique_indices.append(idx)
            seen.add(idx)

    if unique_indices[-1] != safe_total_frames - 1:
        unique_indices[-1] = safe_total_frames - 1

    return unique_indices


def extract_image_frames_for_ai(
    image_bytes: bytes, mime_type: str = "", max_gif_frames: int = 4
) -> Tuple[List[Image.Image], Dict[str, Any]]:
    """
    将输入图片转换为适合模型识别的帧列表。

    - 静态图: 返回 1 帧。
    - 动态图（GIF/APNG）: 按上限抽取关键帧并返回多帧。
    """
    if not image_bytes:
        raise ValueError("输入图片为空，无法提取帧。")

    safe_max_frames = max(1, int(max_gif_frames or 1))

    with Image.open(io.BytesIO(image_bytes)) as img:
        source_format = (img.format or "").upper()
        total_frames = max(1, int(getattr(img, "n_frames", 1) or 1))
        is_animated = bool(getattr(img, "is_animated", False))

        lower_mime_type = (mime_type or "").lower()
        should_split_frames = (lower_mime_type == "image/gif" or is_animated) and total_frames > 1

        frame_indices = (
            _calculate_sample_indices(total_frames, safe_max_frames)
            if should_split_frames
            else [0]
        )

        extracted_frames: List[Image.Image] = []
        for idx in frame_indices:
            try:
                img.seek(idx)
                frame = img.copy()
                if frame.mode != "RGBA":
                    frame = frame.convert("RGBA")
                extracted_frames.append(frame)
            except Exception as e:
                log.warning(f"提取第 {idx} 帧失败: {e}")

        if not extracted_frames:
            raise ValueError("未能从图片中提取任何可用帧。")

        return extracted_frames, {
            "is_animated": should_split_frames,
            "total_frames": total_frames,
            "sampled_frames": len(extracted_frames),
            "frame_indices": frame_indices,
            "source_format": source_format,
        }


def _video_suffix_from_mime_type(mime_type: str) -> str:
    normalized = (mime_type or "").split(";", 1)[0].strip().lower()
    return {
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "video/quicktime": ".mov",
        "video/x-msvideo": ".avi",
        "video/x-matroska": ".mkv",
    }.get(normalized, ".mp4")


def extract_video_frames_for_ai(
    video_bytes: bytes,
    mime_type: str = "video/mp4",
    max_video_frames: int = 4,
) -> Tuple[List[Image.Image], Dict[str, Any]]:
    """
    将视频抽取为适合模型识别的关键帧列表。

    视频文件先写入临时文件，再通过 OpenCV 均匀抽样关键帧；
    输出为 PIL Image 列表，供上层拼接成“类似 GIF 的时间序列拼图”。
    """
    if not video_bytes:
        raise ValueError("输入视频为空，无法提取帧。")

    safe_max_frames = max(1, int(max_video_frames or 1))
    temp_path = ""
    cap = None

    try:
        import cv2  # type: ignore

        suffix = _video_suffix_from_mime_type(mime_type)
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(video_bytes)
            temp_path = temp_file.name

        cap = cv2.VideoCapture(temp_path)
        if not cap or not cap.isOpened():
            raise ValueError("OpenCV 无法打开视频文件。")

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_indices = (
            _calculate_sample_indices(total_frames, safe_max_frames)
            if total_frames > 0
            else list(range(safe_max_frames))
        )

        extracted_frames: List[Image.Image] = []
        successful_indices: List[int] = []

        for idx in frame_indices:
            if total_frames > 0:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame_bgr = cap.read()
            if not ok or frame_bgr is None:
                log.warning(f"提取视频第 {idx} 帧失败。")
                continue

            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            if isinstance(frame_rgb, Image.Image):
                frame = frame_rgb.convert("RGB")
            else:
                frame = Image.fromarray(frame_rgb).convert("RGB")
            extracted_frames.append(frame)
            successful_indices.append(idx)

        if not extracted_frames:
            raise ValueError("未能从视频中提取任何可用帧。")

        duration_seconds = (
            round(total_frames / fps, 3)
            if total_frames > 0 and fps > 0
            else None
        )
        return extracted_frames, {
            "is_video": True,
            "is_animated": True,
            "total_frames": total_frames or len(extracted_frames),
            "sampled_frames": len(extracted_frames),
            "frame_indices": successful_indices,
            "fps": fps,
            "duration_seconds": duration_seconds,
            "source_format": (mime_type or "video/mp4"),
        }
    except ImportError as exc:
        raise RuntimeError("缺少 opencv-python-headless，无法抽取视频帧。") from exc
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def extract_video_tail_frame_for_ai(
    video_bytes: bytes,
    mime_type: str = "video/mp4",
) -> Tuple[Image.Image, Dict[str, Any]]:
    """
    提取视频尾帧，供“续写/延长视频”作为下一段图生视频的起点。

    复用视频抽帧逻辑并只采样首尾两帧，避免额外引入 ffmpeg 依赖；
    返回的 PIL Image 已转换为 RGB，调用方可按需保存为 PNG/JPEG bytes。
    """
    frames, frame_meta = extract_video_frames_for_ai(
        video_bytes=video_bytes,
        mime_type=mime_type,
        max_video_frames=2,
    )
    if not frames:
        raise ValueError("未能从视频中提取尾帧。")
    tail_frame = frames[-1].convert("RGB")
    frame_meta = dict(frame_meta)
    frame_meta["tail_frame_index"] = (
        frame_meta.get("frame_indices", [None])[-1]
        if frame_meta.get("frame_indices")
        else None
    )
    return tail_frame, frame_meta


def sanitize_image(image_bytes: bytes) -> Tuple[bytes, str]:
    """
    对输入的图片字节数据进行智能预处理和压缩。
    - **如果图片 < 10MB**: 只进行必要的尺寸调整和格式统一，以高质量保存。
    - **如果图片 >= 10MB**: 执行"尽力压缩"策略，尝试将图片压缩至 4MB 以下。
    - **最终检查**: 任何情况下，处理后的图片都不能超过 15MB 的物理上限。

    内存优化：确保所有 BytesIO 缓冲区在使用后立即关闭，防止内存泄漏。
    """
    if not image_bytes:
        raise ValueError("输入的图片字节数据不能为空。")

    original_byte_size = len(image_bytes)
    log.info(f"开始处理图片，原始大小: {original_byte_size / 1024:.2f} KB。")

    input_buffer = None
    output_buffer = None

    try:
        # 使用上下文管理器确保输入缓冲区被正确关闭
        input_buffer = io.BytesIO(image_bytes)

        with Image.open(input_buffer) as img:
            # --- 1. 尺寸调整 (对所有图片都执行) ---
            if img.width > MAX_IMAGE_DIMENSION or img.height > MAX_IMAGE_DIMENSION:
                log.info(
                    f"图片尺寸 {img.size} 超过最大限制 {MAX_IMAGE_DIMENSION}px，将进行缩放。"
                )
                img.thumbnail(
                    (MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.Resampling.LANCZOS
                )
                log.info(f"图片已缩放至: {img.size}")

            # --- 2. 格式转换 (对所有图片都执行) ---
            if img.mode != "RGBA":
                img = img.convert("RGBA")

            processed_bytes = b""

            # --- 3. 根据原始大小选择不同策略 ---
            if original_byte_size < NO_COMPRESSION_THRESHOLD_BYTES:
                # --- 策略A: 小于10MB，高质量保存 ---
                log.info("图片小于10MB，执行高质量保存。")
                output_buffer = io.BytesIO()
                img.save(output_buffer, format="WEBP", quality=HIGH_QUALITY)
                processed_bytes = output_buffer.getvalue()
                output_buffer.close()
                output_buffer = None
            else:
                # --- 策略B: 大于等于10MB，尽力压缩 ---
                log.info("图片大于等于10MB，执行迭代压缩。")
                quality = INITIAL_QUALITY
                while quality >= MIN_QUALITY:
                    output_buffer = io.BytesIO()
                    img.save(output_buffer, format="WEBP", quality=quality)
                    processed_bytes = output_buffer.getvalue()
                    output_buffer.close()
                    output_buffer = None

                    log.debug(
                        f"尝试使用质量 {quality} 进行压缩，大小为: {len(processed_bytes) / 1024:.2f} KB。"
                    )

                    if len(processed_bytes) <= NO_COMPRESSION_THRESHOLD_BYTES:
                        log.info(
                            f"压缩成功，文件大小满足目标要求。最终质量: {quality}。"
                        )
                        break

                    quality -= QUALITY_STEP
                else:
                    log.warning(
                        f"即便使用最低质量 {MIN_QUALITY}，文件大小 ({len(processed_bytes) / 1024:.2f} KB) "
                        f"仍未达到 {NO_COMPRESSION_THRESHOLD_BYTES / 1024 / 1024:.2f} MB 的目标。"
                    )

            # --- 4. 最终检查 (对所有图片都执行) ---
            if len(processed_bytes) > MAX_IMAGE_SIZE_BYTES:
                raise ValueError(
                    f"图片经过处理后大小 ({len(processed_bytes) / 1024 / 1024:.2f} MB) "
                    f"仍然超过了物理上限 {MAX_IMAGE_SIZE_BYTES / 1024 / 1024:.0f} MB。"
                )

            log.info(
                f"图片处理完成。原始大小: {original_byte_size / 1024:.2f} KB -> "
                f"处理后大小: {len(processed_bytes) / 1024:.2f} KB."
            )

            return processed_bytes, "image/webp"
    except Exception as e:
        log.error(f"图片处理过程中发生严重错误: {e}", exc_info=True)
        raise
    finally:
        # 确保所有缓冲区都被关闭
        if input_buffer is not None:
            try:
                input_buffer.close()
            except Exception:
                pass
        if output_buffer is not None:
            try:
                output_buffer.close()
            except Exception:
                pass
