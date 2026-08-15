"""按 wheel_models.py 的识别结果重建停帧，并固定 GIF 播放速度。"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image


PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))

from wheel_models import ANIMATION_PATH, FRAME_DIR, WHEEL_OUTCOMES  # noqa: E402


# 原 GIF 未写有效延时，常见客户端按约 100 ms/帧播放；30 ms/帧约为三倍速度。
FRAME_DURATION_MS = 30


def set_gif_frame_delay(animation_path: Path, duration_ms: int) -> None:
    """原位修改 GIF 控制块延时，不重新量化或编码画面。"""
    if duration_ms <= 0 or duration_ms % 10:
        raise ValueError("GIF 帧延时必须是正的 10 ms 倍数。")

    data = bytearray(animation_path.read_bytes())
    marker = b"\x21\xf9\x04"
    offsets: list[int] = []
    cursor = 0
    while True:
        offset = data.find(marker, cursor)
        if offset < 0:
            break
        if offset + 7 >= len(data) or data[offset + 7] != 0:
            raise RuntimeError(f"GIF 第 {offset} 字节处的图形控制块不完整。")
        offsets.append(offset)
        cursor = offset + 8

    if len(offsets) != len(WHEEL_OUTCOMES):
        raise RuntimeError(
            f"GIF 有 {len(offsets)} 个帧控制块，"
            f"但识别表有 {len(WHEEL_OUTCOMES)} 项。"
        )

    delay = duration_ms // 10
    delay_bytes = delay.to_bytes(2, "little")
    changed = False
    for offset in offsets:
        if data[offset + 4 : offset + 6] != delay_bytes:
            data[offset + 4 : offset + 6] = delay_bytes
            changed = True
    if changed:
        animation_path.write_bytes(data)


def main() -> None:
    animation_path = ANIMATION_PATH.resolve()
    frame_dir = FRAME_DIR.resolve()
    if animation_path.parent != (PLUGIN_DIR / "img").resolve():
        raise RuntimeError(f"动画路径超出插件 img 目录：{animation_path}")
    if frame_dir.parent != animation_path.parent:
        raise RuntimeError(f"停帧路径超出插件 img 目录：{frame_dir}")

    set_gif_frame_delay(animation_path, FRAME_DURATION_MS)

    frame_dir.mkdir(parents=True, exist_ok=True)
    expected_paths = {
        (frame_dir / outcome.frame_filename).resolve()
        for outcome in WHEEL_OUTCOMES
    }
    with Image.open(animation_path) as rebuilt:
        if rebuilt.n_frames != len(WHEEL_OUTCOMES):
            raise RuntimeError(
                f"GIF 有 {rebuilt.n_frames} 帧，"
                f"但识别表有 {len(WHEEL_OUTCOMES)} 项。"
            )
        for outcome in WHEEL_OUTCOMES:
            rebuilt.seek(outcome.frame_index)
            rebuilt.convert("RGBA").save(
                frame_dir / outcome.frame_filename,
                format="PNG",
            )

    for old_path in frame_dir.glob("*.png"):
        if old_path.resolve() not in expected_paths:
            old_path.unlink()

    print(
        f"已重建 {len(WHEEL_OUTCOMES)} 张停帧；"
        f"GIF 延时为 {FRAME_DURATION_MS} ms/帧。"
    )


if __name__ == "__main__":
    main()
