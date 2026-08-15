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
copywriting_module = importlib.import_module("copywriting")
gateway_module = importlib.import_module("onebot_gateway")
models_module = importlib.import_module("wheel_models")
policy_module = importlib.import_module("recall_policy")
service_module = importlib.import_module("wheel_service")


class FakeApi:
    def __init__(self) -> None:
        self.actions: list[tuple[str, dict[str, Any]]] = []
        self.next_message_id = 100
        self.member_role = "member"

    async def call_action(self, action: str, **params: Any) -> dict[str, Any]:
        self.actions.append((action, params))
        if action == "send_group_msg":
            self.next_message_id += 1
            return {
                "status": "ok",
                "retcode": 0,
                "data": {"message_id": self.next_message_id},
            }
        if action == "get_group_member_info":
            return {
                "status": "ok",
                "retcode": 0,
                "data": {"role": self.member_role},
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
    def make_service(self) -> Any:
        return service_module.WheelService(
            {
                "animation_display_seconds": 0,
                "freeze_display_seconds": 0,
                "countdown_seconds": 0,
                "cleanup_display_seconds": 0,
                "rescue_window_seconds": 300,
                "recall_user_messages": True,
            }
        )

    async def test_full_round_keeps_freeze_frame_and_mutes(self) -> None:
        service = self.make_service()
        bot = FakeBot()
        outcome = models_module.WHEEL_OUTCOMES[0]
        session = models_module.WheelSession(
            group_id=123,
            target_id=456,
            target_name="tester",
            bot=bot,
            outcome=outcome,
            effective_seconds=outcome.requested_seconds,
        )
        session.track_user_message(50)
        service._sessions[123] = session

        await service._run_round(session)

        actions = [action for action, _params in bot.api.actions]
        self.assertEqual(
            actions[:5],
            [
                "send_group_msg",
                "delete_msg",
                "send_group_msg",
                "send_group_msg",
                "set_group_ban",
            ],
        )
        ban_params = bot.api.actions[4][1]
        self.assertEqual(ban_params["group_id"], 123)
        self.assertEqual(ban_params["user_id"], 456)
        self.assertEqual(ban_params["duration"], 7200)
        self.assertEqual(session.state, "muted")
        recalled = [
            params["message_id"]
            for action, params in bot.api.actions
            if action == "delete_msg"
        ]
        self.assertEqual(recalled, [101])
        self.assertIn(102, session.bot_message_ids)

        await service._cancel_task(session.expiry_task)
        await service.terminate()

    async def test_timed_bot_recall_options_are_independent(self) -> None:
        service = service_module.WheelService(
            {
                "animation_display_seconds": 0,
                "freeze_display_seconds": 0,
                "countdown_seconds": 0,
                "rescue_window_seconds": 300,
                "auto_recall_animation": False,
                "recall_animation_on_cleanup": False,
                "auto_recall_freeze_frame": True,
                "recall_freeze_frame_on_cleanup": False,
                "auto_recall_result_after_mute": True,
                "recall_result_on_cleanup": False,
            }
        )
        bot = FakeBot()
        outcome = models_module.WHEEL_OUTCOMES[0]
        session = models_module.WheelSession(
            group_id=123,
            target_id=456,
            target_name="tester",
            bot=bot,
            outcome=outcome,
            effective_seconds=outcome.requested_seconds,
        )
        service._sessions[123] = session

        await service._run_round(session)

        recalled = [
            params["message_id"]
            for action, params in bot.api.actions
            if action == "delete_msg"
        ]
        self.assertEqual(recalled, [102, 103])
        self.assertNotIn(101, recalled)
        self.assertEqual(session.bot_message_ids, [])
        await service._cancel_task(session.expiry_task)
        await service.terminate()

    async def test_admin_is_refused_before_wheel_starts(self) -> None:
        service = self.make_service()
        service.config["privileged_member_messages"] = [
            "第一条 {role}",
            "第二条 {role}",
        ]
        bot = FakeBot()
        bot.api.member_role = "admin"

        await service.start_round(FakeEvent(bot, 55), 123, 456)

        self.assertNotIn(123, service._sessions)
        actions = [action for action, _params in bot.api.actions]
        self.assertEqual(
            actions,
            [
                "get_group_member_info",
                "send_group_msg",
                "delete_msg",
            ],
        )
        sent_message = bot.api.actions[1][1]["message"]
        self.assertIn(
            sent_message[1]["data"]["text"],
            {" 第一条 管理员", " 第二条 管理员"},
        )
        self.assertNotIn("set_group_ban", actions)
        recalled = [
            params["message_id"]
            for action, params in bot.api.actions
            if action == "delete_msg"
        ]
        self.assertEqual(recalled, [55])

    async def test_owner_is_refused_with_default_random_copy(self) -> None:
        service = self.make_service()
        bot = FakeBot()
        bot.api.member_role = "owner"

        await service.start_round(FakeEvent(bot, 56), 123, 456)

        self.assertNotIn(123, service._sessions)
        sent_message = bot.api.actions[1][1]["message"]
        self.assertIn("群主", sent_message[1]["data"]["text"])
        self.assertNotIn(
            "set_group_ban",
            [action for action, _params in bot.api.actions],
        )

    async def test_plea_keeps_command_and_success_copy(self) -> None:
        service = self.make_service()
        bot = FakeBot()
        outcome = models_module.WHEEL_OUTCOMES[3]
        session = models_module.WheelSession(
            group_id=123,
            target_id=456,
            target_name="tester",
            bot=bot,
            outcome=outcome,
            effective_seconds=60,
            state="pending",
        )
        session.track_user_message(50)
        session.track_bot_message(88)
        session.track_bot_message(89)
        session.task = asyncio.create_task(asyncio.sleep(3600))
        service._sessions[123] = session

        await service.handle_plea(FakeEvent(bot, 51), 123, 456)

        self.assertNotIn(123, service._sessions)
        self.assertTrue(session.task.cancelled())
        actions = [action for action, _params in bot.api.actions]
        self.assertIn("send_group_msg", actions)
        self.assertNotIn("set_group_ban", actions)
        recalled = {
            params["message_id"]
            for action, params in bot.api.actions
            if action == "delete_msg"
        }
        self.assertTrue({50, 88, 89}.issubset(recalled))
        self.assertNotIn(51, recalled)
        self.assertNotIn(101, recalled)

    async def test_success_command_and_notice_can_be_recalled(self) -> None:
        service = service_module.WheelService(
            {
                "cleanup_display_seconds": 0,
                "recall_success_command_message": True,
                "auto_recall_success_notice": True,
            }
        )
        bot = FakeBot()
        outcome = models_module.WHEEL_OUTCOMES[3]
        session = models_module.WheelSession(
            group_id=123,
            target_id=456,
            target_name="tester",
            bot=bot,
            outcome=outcome,
            effective_seconds=60,
            state="pending",
        )
        session.task = asyncio.create_task(asyncio.sleep(3600))
        service._sessions[123] = session

        await service.handle_plea(FakeEvent(bot, 51), 123, 456)

        recalled = {
            params["message_id"]
            for action, params in bot.api.actions
            if action == "delete_msg"
        }
        self.assertEqual(recalled, {51, 101})
        self.assertNotIn(123, service._sessions)

    async def test_rescue_keeps_command_and_success_copy(self) -> None:
        service = self.make_service()
        bot = FakeBot()
        outcome = models_module.WHEEL_OUTCOMES[7]
        session = models_module.WheelSession(
            group_id=123,
            target_id=456,
            target_name="tester",
            bot=bot,
            outcome=outcome,
            effective_seconds=300,
            state="muted",
        )
        session.track_user_message(50)
        session.track_bot_message(88)
        session.track_bot_message(89)
        service._sessions[123] = session

        await service.handle_rescue(FakeEvent(bot, 52), 123, 789)

        self.assertNotIn(123, service._sessions)
        ban_calls = [
            params
            for action, params in bot.api.actions
            if action == "set_group_ban"
        ]
        self.assertEqual(len(ban_calls), 1)
        self.assertEqual(ban_calls[0]["duration"], 0)
        self.assertEqual(ban_calls[0]["user_id"], 456)
        recalled = {
            params["message_id"]
            for action, params in bot.api.actions
            if action == "delete_msg"
        }
        self.assertTrue({50, 88, 89}.issubset(recalled))
        self.assertNotIn(52, recalled)
        self.assertNotIn(101, recalled)

    async def test_rescue_is_rejected_before_mute_takes_effect(self) -> None:
        service = self.make_service()
        bot = FakeBot()
        outcome = models_module.WHEEL_OUTCOMES[7]
        session = models_module.WheelSession(
            group_id=123,
            target_id=456,
            target_name="tester",
            bot=bot,
            outcome=outcome,
            effective_seconds=300,
            state="pending",
        )
        session.task = asyncio.create_task(asyncio.sleep(3600))
        service._sessions[123] = session

        await service.handle_rescue(FakeEvent(bot, 53), 123, 789)

        self.assertIs(service._sessions[123], session)
        self.assertEqual(session.state, "pending")
        self.assertFalse(session.task.cancelled())
        self.assertNotIn(
            "set_group_ban",
            [action for action, _params in bot.api.actions],
        )
        await service._cancel_task(session.task)
        await service.terminate()

    def test_all_frames_exist_and_over_limit_results_are_excluded(self) -> None:
        service = self.make_service()
        self.assertEqual(len(models_module.WHEEL_OUTCOMES), 18)
        recognized = [
            (item.frame_index, item.display_name, item.requested_seconds)
            for item in models_module.WHEEL_OUTCOMES
        ]
        self.assertEqual(
            recognized,
            [
                (0, "2小时", 7200),
                (1, "10分钟", 600),
                (2, "3天", 259200),
                (3, "1分钟", 60),
                (4, "1天", 86400),
                (5, "半小时", 1800),
                (6, "1月", 2592000),
                (7, "5分钟", 300),
                (8, "1小时", 3600),
                (9, "2小时", 7200),
                (10, "1年", 31536000),
                (11, "10分钟", 600),
                (12, "3天", 259200),
                (13, "1分钟", 60),
                (14, "1天", 86400),
                (15, "半小时", 1800),
                (16, "1月", 2592000),
                (17, "5分钟", 300),
            ],
        )
        for outcome in models_module.WHEEL_OUTCOMES:
            frame = models_module.FRAME_DIR / outcome.frame_filename
            self.assertTrue(Path(frame).is_file(), outcome.frame_filename)
            stem_parts = Path(outcome.frame_filename).stem.split("_")
            self.assertEqual(int(stem_parts[0]), outcome.frame_index)
            self.assertEqual(
                int(stem_parts[1].removesuffix("s")),
                outcome.requested_seconds,
            )

        year = next(
            item
            for item in models_module.WHEEL_OUTCOMES
            if item.display_name == "1年"
        )
        self.assertGreater(year.requested_seconds, service._max_mute_seconds())
        self.assertEqual(service._max_mute_seconds(), 30 * 24 * 60 * 60)
        eligible = service._eligible_outcomes()
        self.assertEqual(len(eligible), 17)
        self.assertNotIn(year, eligible)
        self.assertTrue(
            all(
                item.requested_seconds <= service._max_mute_seconds()
                for item in eligible
            )
        )

    def test_rebuilt_gif_uses_thirty_millisecond_frame_delays(self) -> None:
        data = models_module.ANIMATION_PATH.read_bytes()
        marker = b"\x21\xf9\x04"
        delays = []
        cursor = 0
        while True:
            offset = data.find(marker, cursor)
            if offset < 0:
                break
            delays.append(
                int.from_bytes(data[offset + 4 : offset + 6], "little") * 10
            )
            cursor = offset + 8
        self.assertEqual(delays, [30] * 18)

    def test_default_self_rescue_countdown_is_ten_seconds(self) -> None:
        service = service_module.WheelService()
        self.assertEqual(service._countdown_seconds(), 10.0)

    def test_legacy_user_recall_is_only_a_fallback(self) -> None:
        legacy_only = policy_module.RecallPolicy.from_config(
            {"recall_user_messages": False}
        )
        overridden = policy_module.RecallPolicy.from_config(
            {
                "recall_user_messages": False,
                "recall_trigger_message": True,
            }
        )

        self.assertFalse(legacy_only.recall_trigger_message)
        self.assertFalse(legacy_only.recall_invalid_command_message)
        self.assertFalse(legacy_only.recall_success_command_message)
        self.assertTrue(overridden.recall_trigger_message)
        self.assertFalse(overridden.recall_invalid_command_message)

    def test_message_id_response_shapes(self) -> None:
        extract = gateway_module.extract_message_id
        self.assertEqual(extract({"message_id": 1}), 1)
        self.assertEqual(extract({"data": {"message_id": 2}}), 2)
        self.assertIsNone(extract({"data": {}}))

    def test_copywriting_is_kept_in_dedicated_module(self) -> None:
        self.assertIn("高性能", copywriting_module.PARDON_TEXT)
        result = copywriting_module.result_text("10分钟", 600, 600, 30)
        self.assertIn("10分钟", result)
        self.assertEqual(len(result.splitlines()), 4)
        self.assertTrue(hasattr(plugin_module.MuteWheelPlugin, "on_group_message"))

    def test_privileged_copy_uses_per_scope_shuffle_bag(self) -> None:
        bag = copywriting_module.PrivilegedMessageShuffleBag()
        configured = [
            "甲 {role}",
            "乙 {role}",
            "丙 {role}",
            "甲 {role}",
        ]

        first_cycle = [bag.next(configured, "admin", 123) for _ in range(3)]
        next_cycle_first = bag.next(configured, "admin", 123)
        other_group_first = bag.next(configured, "admin", 456)

        self.assertEqual(
            set(first_cycle),
            {"甲 管理员", "乙 管理员", "丙 管理员"},
        )
        self.assertNotEqual(first_cycle[-1], next_cycle_first)
        self.assertIn(
            other_group_first,
            {"甲 管理员", "乙 管理员", "丙 管理员"},
        )

    def test_plugin_can_be_imported_as_package(self) -> None:
        plugin_dir = Path(models_module.PLUGIN_DIR)
        sys.path.insert(0, str(plugin_dir.parent))
        try:
            packaged_main = importlib.import_module(f"{plugin_dir.name}.main")
        finally:
            sys.path.pop(0)
        self.assertTrue(hasattr(packaged_main, "MuteWheelPlugin"))


if __name__ == "__main__":
    unittest.main()
