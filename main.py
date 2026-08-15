from __future__ import annotations

import asyncio
import base64
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register


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


@register(
    "astrbot_plugin_mute_wheel",
    "Taropoi",
    "QQ群禁言大转盘：支持求饶、群友救援与自动撤回",
    "1.0.0",
)
class MuteWheelPlugin(Star):
    def __init__(
        self,
        context: Context,
        config: AstrBotConfig | None = None,
    ) -> None:
        super().__init__(context)
        self.config = config or {}
        self._sessions: dict[int, WheelSession] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._image_cache: dict[Path, str] = {}

    async def initialize(self) -> None:
        missing = [
            path
            for path in (
                ANIMATION_PATH,
                *(FRAME_DIR / item.frame_filename for item in WHEEL_OUTCOMES),
            )
            if not path.is_file()
        ]
        if missing:
            logger.error(
                "禁言大转盘缺少图片素材：%s",
                ", ".join(str(path) for path in missing),
            )
            return
        logger.info("禁言大转盘已加载：18 个等权扇区可用。")

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def on_group_message(self, event: AstrMessageEvent) -> None:
        """处理转盘触发、本人求饶和群友救援。"""
        text = self._normalize_text(event.message_str)
        if text not in {TRIGGER_TEXT, RESCUE_TEXT, *PLEA_TEXTS}:
            return

        event.stop_event()
        group_id = self._event_group_id(event)
        sender_id = self._event_sender_id(event)
        if group_id is None or sender_id is None:
            logger.warning("禁言大转盘无法从群消息中取得群号或发送者 QQ。")
            return

        if text == TRIGGER_TEXT:
            await self._start_round(event, group_id, sender_id)
        elif text in PLEA_TEXTS:
            await self._handle_plea(event, group_id, sender_id)
        else:
            await self._handle_rescue(event, group_id, sender_id)

    async def _start_round(
        self,
        event: AstrMessageEvent,
        group_id: int,
        sender_id: int,
    ) -> None:
        incoming_id = getattr(event.message_obj, "message_id", None)
        lock = self._lock_for(group_id)

        async with lock:
            if group_id in self._sessions:
                busy = True
            else:
                busy = False
                outcome = secrets.choice(WHEEL_OUTCOMES)
                effective_seconds = min(
                    outcome.requested_seconds,
                    self._max_mute_seconds(),
                )
                session = WheelSession(
                    group_id=group_id,
                    target_id=sender_id,
                    target_name=self._event_sender_name(event, sender_id),
                    bot=event.bot,
                    outcome=outcome,
                    effective_seconds=effective_seconds,
                )
                session.track_user_message(incoming_id)
                self._sessions[group_id] = session
                session.task = asyncio.create_task(
                    self._run_round(session),
                    name=f"mute-wheel-{group_id}-{sender_id}",
                )

        if busy:
            await self._temporary_notice(
                event.bot,
                group_id,
                sender_id,
                "轮盘还在运转啦！高性能的我一次只能处理一位笨蛋。",
                incoming_id,
            )

    async def _run_round(self, session: WheelSession) -> None:
        try:
            animation_id = await self._send_image(session, ANIMATION_PATH)
            session.track_bot_message(animation_id)
            await asyncio.sleep(self._animation_seconds())
            await self._recall_one(session, animation_id)

            if not await self._session_is(session, "spinning"):
                return

            frame_path = FRAME_DIR / session.outcome.frame_filename
            frame_id = await self._send_image(session, frame_path)
            session.track_bot_message(frame_id)
            await asyncio.sleep(self._freeze_seconds())
            await self._recall_one(session, frame_id)

            if not await self._session_is(session, "spinning"):
                return

            result_id = await self._send_result_message(session)
            session.track_bot_message(result_id)
            async with self._lock_for(session.group_id):
                if (
                    self._sessions.get(session.group_id) is not session
                    or session.state != "spinning"
                ):
                    return
                session.state = "pending"

            await asyncio.sleep(self._countdown_seconds())

            async with self._lock_for(session.group_id):
                if (
                    self._sessions.get(session.group_id) is not session
                    or session.state != "pending"
                ):
                    return
                session.state = "muting"
                await self._set_group_ban(
                    session.bot,
                    session.group_id,
                    session.target_id,
                    session.effective_seconds,
                )
                session.state = "muted"

            session.expiry_task = asyncio.create_task(
                self._expire_session(session),
                name=f"mute-wheel-expiry-{session.group_id}-{session.target_id}",
            )
            logger.info(
                "禁言大转盘已执行：group=%s user=%s result=%s duration=%ss",
                session.group_id,
                session.target_id,
                session.outcome.display_name,
                session.effective_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "禁言大转盘执行失败：group=%s user=%s",
                session.group_id,
                session.target_id,
            )
            await self._fail_session(session, exc)

    async def _handle_plea(
        self,
        event: AstrMessageEvent,
        group_id: int,
        sender_id: int,
    ) -> None:
        incoming_id = getattr(event.message_obj, "message_id", None)
        task: asyncio.Task[Any] | None = None
        session: WheelSession | None = None
        notice: str | None = None

        async with self._lock_for(group_id):
            session = self._sessions.get(group_id)
            if session is None:
                notice = "咦？现在又没有人被塞口球，认错给谁看呀？"
            elif sender_id != session.target_id:
                notice = "又不是你抽中的，不许替别人抢着认错啦。"
            elif session.state not in {"spinning", "pending"}:
                notice = "太迟啦，禁言指令已经执行啦！现在只能等群友发送nssb。"
            else:
                session.state = "pardoned"
                session.track_user_message(incoming_id)
                task = session.task

        if notice is not None:
            await self._temporary_notice(
                event.bot,
                group_id,
                sender_id,
                notice,
                incoming_id,
            )
            return

        assert session is not None
        await self._cancel_task(task)
        message = [
            self._at_segment(session.target_id),
            self._text_segment(
                " 哼……都这么诚恳了，高性能的我就勉为其难放你一马！"
                "这次转盘作废，可别以为每次卖萌都管用哦。"
            ),
        ]
        await self._announce_and_cleanup(session, message)

    async def _handle_rescue(
        self,
        event: AstrMessageEvent,
        group_id: int,
        sender_id: int,
    ) -> None:
        incoming_id = getattr(event.message_obj, "message_id", None)
        task: asyncio.Task[Any] | None = None
        session: WheelSession | None = None
        notice: str | None = None
        was_muted = False

        async with self._lock_for(group_id):
            session = self._sessions.get(group_id)
            if session is None:
                notice = "目前没有需要救援的对象。高性能雷达可没有漏看哦。"
            elif sender_id == session.target_id:
                notice = "不行！nssb 是友情救援，本人不能自己捞自己。"
            elif session.state not in {"spinning", "pending", "muted"}:
                notice = "救援程序已经在运行了，稍微等一下啦。"
            else:
                was_muted = session.state == "muted"
                session.state = "rescuing"
                if was_muted:
                    try:
                        await self._set_group_ban(
                            session.bot,
                            session.group_id,
                            session.target_id,
                            0,
                        )
                    except Exception:
                        session.state = "muted"
                        notice = (
                            "解禁失败了……才、才不是我不高性能！"
                            "请检查ATRI是不是群管理员。"
                        )
                    else:
                        session.state = "rescued"
                else:
                    session.state = "rescued"
                    task = session.task

                if notice is None:
                    session.track_user_message(incoming_id)

        if notice is not None:
            await self._temporary_notice(
                event.bot,
                group_id,
                sender_id,
                notice,
                incoming_id,
            )
            return

        assert session is not None
        await self._cancel_task(task)
        await self._cancel_task(session.expiry_task)
        action = "从静音模式里捞出来了" if was_muted else "在执行前成功拦下了禁言"
        message = [
            self._at_segment(sender_id),
            self._text_segment(" 友情救援认证成功！"),
            self._at_segment(session.target_id),
            self._text_segment(
                f" 已经被你{action}。哼哼，高性能机器人也会尊重群友的选择啦！"
            ),
        ]
        await self._announce_and_cleanup(session, message)

    async def _announce_and_cleanup(
        self,
        session: WheelSession,
        message: list[dict[str, Any]],
    ) -> None:
        try:
            message_id = await self._send_group_message(
                session.bot,
                session.group_id,
                message,
            )
            session.track_bot_message(message_id)
        except Exception:
            logger.exception("禁言大转盘发送收尾文案失败。")

        await asyncio.sleep(self._cleanup_seconds())
        await self._recall_session_messages(session)
        async with self._lock_for(session.group_id):
            if self._sessions.get(session.group_id) is session:
                self._sessions.pop(session.group_id, None)

    async def _fail_session(
        self,
        session: WheelSession,
        error: Exception,
    ) -> None:
        async with self._lock_for(session.group_id):
            if self._sessions.get(session.group_id) is not session:
                return
            session.state = "failed"

        try:
            message_id = await self._send_group_message(
                session.bot,
                session.group_id,
                [
                    self._at_segment(session.target_id),
                    self._text_segment(
                        " 呜……转盘模块执行失败了。请确认机器人拥有群管理员权限，"
                        "并检查 AstrBot 控制台日志。"
                    ),
                ],
            )
            session.track_bot_message(message_id)
        except Exception:
            logger.error("禁言大转盘连错误提示都发送失败了：%r", error)

        await asyncio.sleep(self._cleanup_seconds())
        await self._recall_session_messages(session)
        async with self._lock_for(session.group_id):
            if self._sessions.get(session.group_id) is session:
                self._sessions.pop(session.group_id, None)

    async def _expire_session(self, session: WheelSession) -> None:
        window = self._rescue_window_seconds()
        timeout = session.effective_seconds
        if window > 0:
            timeout = min(timeout, window)
        try:
            await asyncio.sleep(timeout)
            async with self._lock_for(session.group_id):
                if (
                    self._sessions.get(session.group_id) is session
                    and session.state == "muted"
                ):
                    self._sessions.pop(session.group_id, None)
        except asyncio.CancelledError:
            raise

    async def _send_result_message(self, session: WheelSession) -> Any:
        delay = self._format_seconds(self._countdown_seconds())
        outcome = session.outcome
        if outcome.requested_seconds > session.effective_seconds:
            duration_note = (
                f"盘面虽然写着「{outcome.display_name}」，但单次禁言有上限，"
                f"所以实际执行 {self._format_seconds(session.effective_seconds)}。"
            )
        else:
            duration_note = f"这次的禁言时长是「{outcome.display_name}」。"

        text = (
            f" 锵锵——指针停下啦！{duration_note}"
            f"高性能的我会在 {delay} 后执行。"
            "本人发送“wssb”或“亚托莉我错了”可以求饶；"
            "其他群友发送“nssb”可以友情救援。"
        )
        return await self._send_group_message(
            session.bot,
            session.group_id,
            [self._at_segment(session.target_id), self._text_segment(text)],
        )

    async def _send_image(self, session: WheelSession, path: Path) -> Any:
        encoded = self._image_cache.get(path)
        if encoded is None:
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            self._image_cache[path] = encoded
        return await self._send_group_message(
            session.bot,
            session.group_id,
            [{"type": "image", "data": {"file": f"base64://{encoded}"}}],
        )

    async def _send_group_message(
        self,
        bot: Any,
        group_id: int,
        message: list[dict[str, Any]],
    ) -> Any:
        response = await bot.api.call_action(
            "send_group_msg",
            group_id=group_id,
            message=message,
        )
        self._raise_for_onebot_error(response, "send_group_msg")
        return self._extract_message_id(response)

    async def _set_group_ban(
        self,
        bot: Any,
        group_id: int,
        user_id: int,
        duration: int,
    ) -> None:
        response = await bot.api.call_action(
            "set_group_ban",
            group_id=group_id,
            user_id=user_id,
            duration=duration,
        )
        self._raise_for_onebot_error(response, "set_group_ban")

    async def _recall_one(
        self,
        session: WheelSession,
        message_id: Any,
    ) -> None:
        if message_id is None or str(message_id) in session.recalled_message_ids:
            return
        try:
            response = await session.bot.api.call_action(
                "delete_msg",
                message_id=message_id,
            )
            self._raise_for_onebot_error(response, "delete_msg")
        except Exception as exc:
            logger.debug("撤回消息 %s 失败：%r", message_id, exc)
        else:
            session.recalled_message_ids.add(str(message_id))

    async def _recall_session_messages(self, session: WheelSession) -> None:
        message_ids = list(reversed(session.bot_message_ids))
        if self._recall_user_messages():
            message_ids.extend(reversed(session.user_message_ids))
        for message_id in message_ids:
            await self._recall_one(session, message_id)

    async def _temporary_notice(
        self,
        bot: Any,
        group_id: int,
        sender_id: int,
        text: str,
        incoming_id: Any,
    ) -> None:
        notice_id: Any = None
        try:
            notice_id = await self._send_group_message(
                bot,
                group_id,
                [self._at_segment(sender_id), self._text_segment(f" {text}")],
            )
            await asyncio.sleep(self._cleanup_seconds())
        except Exception:
            logger.exception("禁言大转盘发送临时提示失败。")

        for message_id in (
            notice_id,
            incoming_id if self._recall_user_messages() else None,
        ):
            if message_id is None:
                continue
            try:
                await bot.api.call_action("delete_msg", message_id=message_id)
            except Exception as exc:
                logger.debug("撤回临时消息 %s 失败：%r", message_id, exc)

    async def _session_is(self, session: WheelSession, state: str) -> bool:
        async with self._lock_for(session.group_id):
            return (
                self._sessions.get(session.group_id) is session
                and session.state == state
            )

    async def _cancel_task(self, task: asyncio.Task[Any] | None) -> None:
        if task is None or task.done() or task is asyncio.current_task():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def _lock_for(self, group_id: int) -> asyncio.Lock:
        lock = self._locks.get(group_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[group_id] = lock
        return lock

    def _animation_seconds(self) -> float:
        return self._config_float("animation_display_seconds", 3.0)

    def _freeze_seconds(self) -> float:
        return self._config_float("freeze_display_seconds", 3.0)

    def _countdown_seconds(self) -> float:
        return self._config_float("countdown_seconds", 30.0)

    def _cleanup_seconds(self) -> float:
        return self._config_float("cleanup_display_seconds", 3.0)

    def _rescue_window_seconds(self) -> int:
        return self._config_int("rescue_window_seconds", 300, minimum=0)

    def _max_mute_seconds(self) -> int:
        # QQ 的常见单次禁言上限为 30 天；仍允许管理员调低此值。
        return self._config_int(
            "max_mute_seconds",
            30 * 24 * 60 * 60,
            minimum=1,
            maximum=30 * 24 * 60 * 60,
        )

    def _recall_user_messages(self) -> bool:
        return bool(self.config.get("recall_user_messages", True))

    def _config_float(self, key: str, default: float) -> float:
        try:
            return max(0.0, float(self.config.get(key, default)))
        except (TypeError, ValueError):
            return default

    def _config_int(
        self,
        key: str,
        default: int,
        *,
        minimum: int,
        maximum: int | None = None,
    ) -> int:
        try:
            value = int(self.config.get(key, default))
        except (TypeError, ValueError):
            value = default
        value = max(minimum, value)
        return min(value, maximum) if maximum is not None else value

    @staticmethod
    def _normalize_text(text: str) -> str:
        return "".join(str(text or "").split()).lower()

    @staticmethod
    def _event_group_id(event: AstrMessageEvent) -> int | None:
        getter = getattr(event, "get_group_id", None)
        value = getter() if callable(getter) else None
        value = value or getattr(event.message_obj, "group_id", None)
        try:
            return int(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @staticmethod
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

    @staticmethod
    def _event_sender_name(event: AstrMessageEvent, fallback_id: int) -> str:
        getter = getattr(event, "get_sender_name", None)
        value = getter() if callable(getter) else None
        return str(value or fallback_id).strip()[:64]

    @staticmethod
    def _at_segment(user_id: int) -> dict[str, Any]:
        return {"type": "at", "data": {"qq": str(user_id)}}

    @staticmethod
    def _text_segment(text: str) -> dict[str, Any]:
        return {"type": "text", "data": {"text": text}}

    @staticmethod
    def _extract_message_id(response: Any) -> Any:
        if isinstance(response, dict):
            if response.get("message_id") is not None:
                return response["message_id"]
            data = response.get("data")
            if isinstance(data, dict) and data.get("message_id") is not None:
                return data["message_id"]
        message_id = getattr(response, "message_id", None)
        if message_id is not None:
            return message_id
        data = getattr(response, "data", None)
        if isinstance(data, dict):
            return data.get("message_id")
        return getattr(data, "message_id", None)

    @staticmethod
    def _raise_for_onebot_error(response: Any, action: str) -> None:
        if not isinstance(response, dict):
            return
        status = response.get("status")
        retcode = response.get("retcode")
        if status not in (None, "ok", "async") or retcode not in (None, 0):
            raise RuntimeError(f"OneBot {action} 调用失败：{response!r}")

    @staticmethod
    def _format_seconds(seconds: float | int) -> str:
        value = int(seconds)
        if value <= 0:
            return "立刻"
        if value % 86400 == 0:
            return f"{value // 86400}天"
        if value % 3600 == 0:
            return f"{value // 3600}小时"
        if value % 60 == 0:
            return f"{value // 60}分钟"
        return f"{value}秒"

    async def terminate(self) -> None:
        tasks: list[asyncio.Task[Any]] = []
        for session in self._sessions.values():
            for task in (session.task, session.expiry_task):
                if task is not None and not task.done():
                    task.cancel()
                    tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._sessions.clear()
        self._locks.clear()
        self._image_cache.clear()

