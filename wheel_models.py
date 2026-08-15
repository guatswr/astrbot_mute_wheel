from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PLUGIN_DIR = Path(__file__).resolve().parent
ANIMATION_PATH = PLUGIN_DIR / "img" / "dzp.gif"
FRAME_DIR = PLUGIN_DIR / "img" / "frames"

TRIGGER_TEXT = "禁言大转盘"
PLEA_TEXTS = frozenset({"wssb", "亚托利我错了", "亚托莉我错了"})
RESCUE_TEXT = "nssb"


@dataclass(frozen=True, slots=True)
class WheelOutcome:
    frame_index: int
    display_name: str
    requested_seconds: int

    @property
    def frame_filename(self) -> str:
        """用帧序号与实际秒数生成可自动校验的文件名。"""
        return f"{self.frame_index:02d}_{self.requested_seconds}s.png"


# 每个条目对应 GIF 中的一个扇区；服务层会排除超过当前禁言上限的条目。
WHEEL_OUTCOMES: tuple[WheelOutcome, ...] = (
    WheelOutcome(0, "2小时", 2 * 60 * 60),
    WheelOutcome(1, "10分钟", 10 * 60),
    WheelOutcome(2, "3天", 3 * 24 * 60 * 60),
    WheelOutcome(3, "1分钟", 60),
    WheelOutcome(4, "1天", 24 * 60 * 60),
    WheelOutcome(5, "半小时", 30 * 60),
    WheelOutcome(6, "1月", 30 * 24 * 60 * 60),
    WheelOutcome(7, "5分钟", 5 * 60),
    WheelOutcome(8, "1小时", 60 * 60),
    WheelOutcome(9, "2小时", 2 * 60 * 60),
    WheelOutcome(10, "1年", 365 * 24 * 60 * 60),
    WheelOutcome(11, "10分钟", 10 * 60),
    WheelOutcome(12, "3天", 3 * 24 * 60 * 60),
    WheelOutcome(13, "1分钟", 60),
    WheelOutcome(14, "1天", 24 * 60 * 60),
    WheelOutcome(15, "半小时", 30 * 60),
    WheelOutcome(16, "1月", 30 * 24 * 60 * 60),
    WheelOutcome(17, "5分钟", 5 * 60),
)


@dataclass(slots=True)
class WheelSession:
    group_id: int
    target_id: int
    target_name: str
    bot: Any
    outcome: WheelOutcome
    effective_seconds: int
    state: str = "spinning"
    bot_message_ids: list[Any] = field(default_factory=list)
    user_message_ids: list[Any] = field(default_factory=list)
    recalled_message_ids: set[str] = field(default_factory=set)
    task: asyncio.Task[Any] | None = None
    expiry_task: asyncio.Task[Any] | None = None

    def track_bot_message(self, message_id: Any) -> None:
        if message_id is not None:
            self.bot_message_ids.append(message_id)

    def track_user_message(self, message_id: Any) -> None:
        if message_id is not None:
            self.user_message_ids.append(message_id)
