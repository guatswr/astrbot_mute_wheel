"""禁言大转盘各类消息的撤回策略。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_MISSING = object()


@dataclass(frozen=True, slots=True)
class RecallPolicy:
    auto_recall_animation: bool
    recall_animation_on_cleanup: bool
    auto_recall_freeze_frame: bool
    recall_freeze_frame_on_cleanup: bool
    auto_recall_result_after_mute: bool
    recall_result_on_cleanup: bool
    auto_recall_temporary_notice: bool
    auto_recall_privileged_notice: bool
    auto_recall_success_notice: bool
    auto_recall_failure_notice: bool
    recall_trigger_message: bool
    recall_invalid_command_message: bool
    recall_success_command_message: bool

    @classmethod
    def from_config(cls, config: Any) -> "RecallPolicy":
        # 旧配置只控制群友消息；新选项缺失时继续尊重它。
        legacy_user_recall = _config_bool(
            config,
            "recall_user_messages",
            True,
        )
        return cls(
            auto_recall_animation=_config_bool(
                config, "auto_recall_animation", True
            ),
            recall_animation_on_cleanup=_config_bool(
                config, "recall_animation_on_cleanup", True
            ),
            auto_recall_freeze_frame=_config_bool(
                config, "auto_recall_freeze_frame", False
            ),
            recall_freeze_frame_on_cleanup=_config_bool(
                config, "recall_freeze_frame_on_cleanup", True
            ),
            auto_recall_result_after_mute=_config_bool(
                config, "auto_recall_result_after_mute", False
            ),
            recall_result_on_cleanup=_config_bool(
                config, "recall_result_on_cleanup", True
            ),
            auto_recall_temporary_notice=_config_bool(
                config, "auto_recall_temporary_notice", True
            ),
            auto_recall_privileged_notice=_config_bool(
                config, "auto_recall_privileged_notice", False
            ),
            auto_recall_success_notice=_config_bool(
                config, "auto_recall_success_notice", False
            ),
            auto_recall_failure_notice=_config_bool(
                config, "auto_recall_failure_notice", True
            ),
            recall_trigger_message=_config_bool(
                config, "recall_trigger_message", legacy_user_recall
            ),
            recall_invalid_command_message=_config_bool(
                config,
                "recall_invalid_command_message",
                legacy_user_recall,
            ),
            recall_success_command_message=_config_bool(
                config, "recall_success_command_message", False
            ),
        )


def _config_bool(config: Any, key: str, default: bool) -> bool:
    try:
        value = config.get(key, _MISSING)
    except AttributeError:
        return default
    if value is _MISSING:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off", ""}:
            return False
    return bool(value)
