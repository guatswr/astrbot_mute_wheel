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
    frame_filename: str
    display_name: str
    requested_seconds: int


# 每个条目对应 GIF 中的一个扇区。重复条目会自然形成相应权重。
WHEEL_OUTCOMES: tuple[WheelOutcome, ...] = (
    WheelOutcome("00_2_hours.png", "2小时", 2 * 60 * 60),
    WheelOutcome("01_10_minutes.png", "10分钟", 10 * 60),
    WheelOutcome("02_3_days.png", "3天", 3 * 24 * 60 * 60),
    WheelOutcome("03_1_minute.png", "1分钟", 60),
    WheelOutcome("04_1_day.png", "1天", 24 * 60 * 60),
    WheelOutcome("05_30_minutes.png", "半小时", 30 * 60),
    WheelOutcome("06_1_month.png", "1月", 30 * 24 * 60 * 60),
    WheelOutcome("07_5_minutes.png", "5分钟", 5 * 60),
    WheelOutcome("08_1_hour.png", "1小时", 60 * 60),
    WheelOutcome("09_2_hours.png", "2小时", 2 * 60 * 60),
    WheelOutcome("10_1_year.png", "1年", 365 * 24 * 60 * 60),
    WheelOutcome("11_10_minutes.png", "10分钟", 10 * 60),
    WheelOutcome("12_3_days.png", "3天", 3 * 24 * 60 * 60),
    WheelOutcome("13_1_minute.png", "1分钟", 60),
    WheelOutcome("14_1_day.png", "1天", 24 * 60 * 60),
    WheelOutcome("15_30_minutes.png", "半小时", 30 * 60),
    WheelOutcome("16_1_month.png", "1月", 30 * 24 * 60 * 60),
    WheelOutcome("17_5_minutes.png", "5分钟", 5 * 60),
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
