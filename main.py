from __future__ import annotations

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:
    from .wheel_models import PLEA_TEXTS, RESCUE_TEXT, TRIGGER_TEXT
    from .wheel_service import WheelService
except ImportError:  # 兼容直接执行 main.py 的本地测试环境。
    from wheel_models import PLEA_TEXTS, RESCUE_TEXT, TRIGGER_TEXT
    from wheel_service import WheelService


@register(
    "astrbot_plugin_mute_wheel",
    "Taropoi",
    "QQ群禁言大转盘：支持求饶、群友救援与自动撤回",
    "1.2.0",
)
class MuteWheelPlugin(Star):
    """AstrBot 插件入口；具体流程由 WheelService 负责。"""

    def __init__(
        self,
        context: Context,
        config: AstrBotConfig | None = None,
    ) -> None:
        super().__init__(context)
        self.service = WheelService(config)

    async def initialize(self) -> None:
        await self.service.validate_assets()

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def on_group_message(self, event: AstrMessageEvent) -> None:
        """将群消息路由到转盘、求饶或救援流程。"""
        text = _normalize_text(event.message_str)
        if text not in {TRIGGER_TEXT, RESCUE_TEXT, *PLEA_TEXTS}:
            return

        event.stop_event()
        group_id = _event_group_id(event)
        sender_id = _event_sender_id(event)
        if group_id is None or sender_id is None:
            logger.warning("禁言大转盘无法从群消息中取得群号或发送者 QQ。")
            return

        if text == TRIGGER_TEXT:
            await self.service.start_round(event, group_id, sender_id)
        elif text in PLEA_TEXTS:
            await self.service.handle_plea(event, group_id, sender_id)
        else:
            await self.service.handle_rescue(event, group_id, sender_id)

    async def terminate(self) -> None:
        await self.service.terminate()


def _normalize_text(text: str) -> str:
    return "".join(str(text or "").split()).lower()


def _event_group_id(event: AstrMessageEvent) -> int | None:
    getter = getattr(event, "get_group_id", None)
    value = getter() if callable(getter) else None
    value = value or getattr(event.message_obj, "group_id", None)
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _event_sender_id(event: AstrMessageEvent) -> int | None:
    getter = getattr(event, "get_sender_id", None)
    value = getter() if callable(getter) else None
    if value in (None, ""):
        sender = getattr(event.message_obj, "sender", None)
        value = getattr(sender, "user_id", None)
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
