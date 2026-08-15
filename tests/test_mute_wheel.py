from __future__ import annotations

import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path
from typing import Any


def install_astrbot_stubs() -> None:
    class FakeLogger:
        def __getattr__(self, _name: str):
            return lambda *_args, **_kwargs: None

    class Star:
        def __init__(self, context: Any) -> None:
            self.context = context

    class Context:
        pass

    class EventMessageType:
        GROUP_MESSAGE = 1

    class PlatformAdapterType:
        AIOCQHTTP = 1

    def passthrough_filter(_value: Any):
        return lambda function: function

    filter_object = types.SimpleNamespace(
        EventMessageType=EventMessageType,
        PlatformAdapterType=PlatformAdapterType,
        event_message_type=passthrough_filter,
        platform_adapter_type=passthrough_filter,
    )

    def register(*_args: Any, **_kwargs: Any):
        return lambda cls: cls

    astrbot_module = types.ModuleType("astrbot")
    api_module = types.ModuleType("astrbot.api")
    event_module = types.ModuleType("astrbot.api.event")
    star_module = types.ModuleType("astrbot.api.star")

    api_module.AstrBotConfig = dict
    api_module.logger = FakeLogger()
    event_module.AstrMessageEvent = object
    event_module.filter = filter_object
    star_module.Context = Context
    star_module.Star = Star
    star_module.register = register

    sys.modules.setdefault("astrbot", astrbot_module)
    sys.modules.setdefault("astrbot.api", api_module)
    sys.modules.setdefault("astrbot.api.event", event_module)
    sys.modules.setdefault("astrbot.api.star", star_module)


install_astrbot_stubs()
plugin_module = importlib.import_module("main")


class FakeApi:
    def __init__(self) -> None:
        self.actions: list[tuple[str, dict[str, Any]]] = []
        self.next_message_id = 100

    async def call_action(self, action: str, **params: Any) -> dict[str, Any]:
        self.actions.append((action, params))
        if action == "send_group_msg":
            self.next_message_id += 1
            return {
                "status": "ok",
                "retcode": 0,
                "data": {"message_id": self.next_message_id},
            }
        return {"status": "ok", "retcode": 0, "data": {}}


class FakeBot:
    def __init__(self) -> None:
        self.api = FakeApi()


class FakeMessage:
    def __init__(self, message_id: int) -> None:
        self.message_id = message_id


class FakeEvent:
    def __init__(self, bot: FakeBot, message_id: int = 50) -> None:
        self.bot = bot
        self.message_obj = FakeMessage(message_id)


class MuteWheelTests(unittest.IsolatedAsyncioTestCase):
    def make_plugin(self) -> Any:
        return plugin_module.MuteWheelPlugin(
            object(),
            {
                "animation_display_seconds": 0,
                "freeze_display_seconds": 0,
                "countdown_seconds": 0,
                "cleanup_display_seconds": 0,
                "rescue_window_seconds": 300,
                "recall_user_messages": True,
            },
        )

    async def test_full_round_sends_retracts_and_mutes(self) -> None:
        plugin = self.make_plugin()
        bot = FakeBot()
        outcome = plugin_module.WHEEL_OUTCOMES[0]
        session = plugin_module.WheelSession(
            group_id=123,
            target_id=456,
            target_name="tester",
            bot=bot,
            outcome=outcome,
            effective_seconds=outcome.requested_seconds,
        )
        session.track_user_message(50)
        plugin._sessions[123] = session

        await plugin._run_round(session)

        actions = [action for action, _params in bot.api.actions]
        self.assertEqual(
            actions[:6],
            [
                "send_group_msg",
                "delete_msg",
                "send_group_msg",
                "delete_msg",
                "send_group_msg",
                "set_group_ban",
            ],
        )
        ban_params = bot.api.actions[5][1]
        self.assertEqual(ban_params["group_id"], 123)
        self.assertEqual(ban_params["user_id"], 456)
        self.assertEqual(ban_params["duration"], 7200)
        self.assertEqual(session.state, "muted")

        await plugin._cancel_task(session.expiry_task)
        await plugin.terminate()

    async def test_plea_cancels_pending_round_and_cleans_messages(self) -> None:
        plugin = self.make_plugin()
        bot = FakeBot()
        outcome = plugin_module.WHEEL_OUTCOMES[3]
        session = plugin_module.WheelSession(
            group_id=123,
            target_id=456,
            target_name="tester",
            bot=bot,
            outcome=outcome,
            effective_seconds=60,
            state="pending",
        )
        session.track_user_message(50)
        session.track_bot_message(101)
        session.task = asyncio.create_task(asyncio.sleep(3600))
        plugin._sessions[123] = session

        await plugin._handle_plea(FakeEvent(bot, 51), 123, 456)

        self.assertNotIn(123, plugin._sessions)
        self.assertTrue(session.task.cancelled())
        actions = [action for action, _params in bot.api.actions]
        self.assertIn("send_group_msg", actions)
        self.assertNotIn("set_group_ban", actions)
        recalled = {
            params["message_id"]
            for action, params in bot.api.actions
            if action == "delete_msg"
        }
        self.assertTrue({50, 51, 101}.issubset(recalled))

    async def test_other_member_can_unmute(self) -> None:
        plugin = self.make_plugin()
        bot = FakeBot()
        outcome = plugin_module.WHEEL_OUTCOMES[7]
        session = plugin_module.WheelSession(
            group_id=123,
            target_id=456,
            target_name="tester",
            bot=bot,
            outcome=outcome,
            effective_seconds=300,
            state="muted",
        )
        plugin._sessions[123] = session

        await plugin._handle_rescue(FakeEvent(bot, 52), 123, 789)

        self.assertNotIn(123, plugin._sessions)
        ban_calls = [
            params
            for action, params in bot.api.actions
            if action == "set_group_ban"
        ]
        self.assertEqual(len(ban_calls), 1)
        self.assertEqual(ban_calls[0]["duration"], 0)
        self.assertEqual(ban_calls[0]["user_id"], 456)

    def test_all_frames_exist_and_year_is_clamped(self) -> None:
        plugin = self.make_plugin()
        self.assertEqual(len(plugin_module.WHEEL_OUTCOMES), 18)
        for outcome in plugin_module.WHEEL_OUTCOMES:
            frame = plugin_module.FRAME_DIR / outcome.frame_filename
            self.assertTrue(Path(frame).is_file(), outcome.frame_filename)

        year = next(
            item
            for item in plugin_module.WHEEL_OUTCOMES
            if item.display_name == "1年"
        )
        self.assertGreater(year.requested_seconds, plugin._max_mute_seconds())
        self.assertEqual(plugin._max_mute_seconds(), 30 * 24 * 60 * 60)

    def test_message_id_response_shapes(self) -> None:
        extract = plugin_module.MuteWheelPlugin._extract_message_id
        self.assertEqual(extract({"message_id": 1}), 1)
        self.assertEqual(extract({"data": {"message_id": 2}}), 2)
        self.assertIsNone(extract({"data": {}}))


if __name__ == "__main__":
    unittest.main()
