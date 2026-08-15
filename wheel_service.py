"""转盘会话状态机与生命周期服务。"""

from __future__ import annotations

import asyncio
import base64
import secrets
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

try:
    from . import copywriting
    from .onebot_gateway import (
        at_segment,
        event_sender_role,
        get_group_member_role,
        recall_message,
        send_group_message,
        set_group_ban,
        text_segment,
    )
    from .wheel_models import (
        ANIMATION_PATH,
        FRAME_DIR,
        WHEEL_OUTCOMES,
        WheelOutcome,
        WheelSession,
    )
except ImportError:  # 兼容直接执行 main.py 的本地测试环境。
    import copywriting
    from onebot_gateway import (
        at_segment,
        event_sender_role,
        get_group_member_role,
        recall_message,
        send_group_message,
        set_group_ban,
        text_segment,
    )
    from wheel_models import (
        ANIMATION_PATH,
        FRAME_DIR,
        WHEEL_OUTCOMES,
        WheelOutcome,
        WheelSession,
    )


class WheelService:
    def __init__(self, config: Any | None = None) -> None:
        self.config = config or {}
        self._sessions: dict[int, WheelSession] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._image_cache: dict[Path, str] = {}
        self._privileged_messages = copywriting.PrivilegedMessageShuffleBag()

    async def validate_assets(self) -> None:
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
        logger.info(
            "禁言大转盘已加载：%s 个停帧符合当前禁言上限。",
            len(self._eligible_outcomes()),
        )

    async def start_round(
        self,
        event: AstrMessageEvent,
        group_id: int,
        sender_id: int,
    ) -> None:
        incoming_id = getattr(event.message_obj, "message_id", None)

        role = await self._get_group_member_role(
            event.bot,
            group_id,
            sender_id,
            event,
        )
        if role in {"owner", "admin"}:
            await self._persistent_notice(
                event.bot,
                group_id,
                sender_id,
                self._privileged_messages.next(
                    self.config.get("privileged_member_messages", []),
                    role,
                    group_id,
                ),
                incoming_id,
            )
            return

        lock = self._lock_for(group_id)

        async with lock:
            if group_id in self._sessions:
                busy = True
            else:
                busy = False
                outcome = secrets.choice(self._eligible_outcomes())
                session = WheelSession(
                    group_id=group_id,
                    target_id=sender_id,
                    target_name=self._event_sender_name(event, sender_id),
                    bot=event.bot,
                    outcome=outcome,
                    effective_seconds=outcome.requested_seconds,
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
                copywriting.BUSY_NOTICE,
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
                await set_group_ban(
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

    async def handle_plea(
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
                notice = copywriting.PLEA_NO_SESSION_NOTICE
            elif sender_id != session.target_id:
                notice = copywriting.PLEA_WRONG_USER_NOTICE
            elif session.state not in {"spinning", "pending"}:
                notice = copywriting.PLEA_TOO_LATE_NOTICE
            else:
                session.state = "pardoned"
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
            at_segment(session.target_id),
            text_segment(f" {copywriting.PARDON_TEXT}"),
        ]
        await self._announce_and_cleanup(session, message)

    async def handle_rescue(
        self,
        event: AstrMessageEvent,
        group_id: int,
        sender_id: int,
    ) -> None:
        incoming_id = getattr(event.message_obj, "message_id", None)
        session: WheelSession | None = None
        notice: str | None = None

        async with self._lock_for(group_id):
            session = self._sessions.get(group_id)
            if session is None:
                notice = copywriting.RESCUE_NO_SESSION_NOTICE
            elif sender_id == session.target_id:
                notice = copywriting.RESCUE_SELF_NOTICE
            elif session.state != "muted":
                notice = copywriting.RESCUE_NOT_MUTED_NOTICE
            else:
                session.state = "rescuing"
                try:
                    await set_group_ban(
                        session.bot,
                        session.group_id,
                        session.target_id,
                        0,
                    )
                except Exception:
                    session.state = "muted"
                    notice = copywriting.RESCUE_FAILED_NOTICE
                else:
                    session.state = "rescued"

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
        await self._cancel_task(session.expiry_task)
        message = [
            at_segment(sender_id),
            text_segment(f" {copywriting.RESCUE_SUCCESS_PREFIX}"),
            at_segment(session.target_id),
            text_segment(f" {copywriting.RESCUE_SUCCESS_TEXT}"),
        ]
        await self._announce_and_cleanup(session, message)

    async def _announce_and_cleanup(
        self,
        session: WheelSession,
        message: list[dict[str, Any]],
    ) -> None:
        try:
            await send_group_message(
                session.bot,
                session.group_id,
                message,
            )
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
            message_id = await send_group_message(
                session.bot,
                session.group_id,
                [
                    at_segment(session.target_id),
                    text_segment(f" {copywriting.ROUND_FAILURE_TEXT}"),
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
        outcome = session.outcome
        text = copywriting.result_text(
            outcome.display_name,
            outcome.requested_seconds,
            session.effective_seconds,
            self._countdown_seconds(),
        )
        return await send_group_message(
            session.bot,
            session.group_id,
            [at_segment(session.target_id), text_segment(f" {text}")],
        )

    async def _send_image(self, session: WheelSession, path: Path) -> Any:
        encoded = self._image_cache.get(path)
        if encoded is None:
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            self._image_cache[path] = encoded
        return await send_group_message(
            session.bot,
            session.group_id,
            [{"type": "image", "data": {"file": f"base64://{encoded}"}}],
        )

    async def _get_group_member_role(
        self,
        bot: Any,
        group_id: int,
        user_id: int,
        event: AstrMessageEvent,
    ) -> str | None:
        try:
            role = await get_group_member_role(bot, group_id, user_id)
            if role is not None:
                return role
        except Exception as exc:
            logger.warning(
                "查询群成员权限失败，将尝试读取消息事件：group=%s user=%s error=%r",
                group_id,
                user_id,
                exc,
            )
        return event_sender_role(event)

    async def _recall_one(
        self,
        session: WheelSession,
        message_id: Any,
    ) -> None:
        if message_id is None or str(message_id) in session.recalled_message_ids:
            return
        try:
            await recall_message(session.bot, message_id)
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
            notice_id = await send_group_message(
                bot,
                group_id,
                [at_segment(sender_id), text_segment(f" {text}")],
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
                await recall_message(bot, message_id)
            except Exception as exc:
                logger.debug("撤回临时消息 %s 失败：%r", message_id, exc)

    async def _persistent_notice(
        self,
        bot: Any,
        group_id: int,
        sender_id: int,
        text: str,
        incoming_id: Any,
    ) -> None:
        try:
            await send_group_message(
                bot,
                group_id,
                [at_segment(sender_id), text_segment(f" {text}")],
            )
        except Exception:
            logger.exception("禁言大转盘发送权限提示失败。")

        if incoming_id is None or not self._recall_user_messages():
            return
        try:
            await recall_message(bot, incoming_id)
        except Exception as exc:
            logger.debug("撤回权限触发消息 %s 失败：%r", incoming_id, exc)

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
        return self._config_float("countdown_seconds", 10.0)

    def _cleanup_seconds(self) -> float:
        return self._config_float("cleanup_display_seconds", 3.0)

    def _rescue_window_seconds(self) -> int:
        return self._config_int("rescue_window_seconds", 300, minimum=0)

    def _max_mute_seconds(self) -> int:
        # QQ 的常见单次禁言上限为 30 天；仍允许管理员调低此值。
        return self._config_int(
            "max_mute_seconds",
            30 * 24 * 60 * 60,
            minimum=60,
            maximum=30 * 24 * 60 * 60,
        )

    def _eligible_outcomes(self) -> tuple[WheelOutcome, ...]:
        """只返回能够按停帧标注时长原样执行的结果。"""
        maximum = self._max_mute_seconds()
        return tuple(
            outcome
            for outcome in WHEEL_OUTCOMES
            if outcome.requested_seconds <= maximum
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
    def _event_sender_name(event: AstrMessageEvent, fallback_id: int) -> str:
        getter = getattr(event, "get_sender_name", None)
        value = getter() if callable(getter) else None
        return str(value or fallback_id).strip()[:64]


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
        self._privileged_messages.clear()
