"""禁言大转盘的全部群内可见文案
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence


# 临时提示 -----------------------------------------------------------------

BUSY_NOTICE = "轮盘还在运转啦！高性能的我一次只能处理一位笨蛋。"

PLEA_NO_SESSION_NOTICE = "咦？现在又没有人被塞口球，认错给谁看呀？"
PLEA_WRONG_USER_NOTICE = "又不是你抽中的，不许替别人抢着认错啦。"
PLEA_TOO_LATE_NOTICE = "太迟啦，禁言指令已经执行啦！现在只能等群友发送 nssb。"

RESCUE_NO_SESSION_NOTICE = "目前没有需要救援的对象。高性能雷达可没有漏看哦。"
RESCUE_SELF_NOTICE = "不行！nssb 是友情救援，本人不能自己捞自己。"
RESCUE_RUNNING_NOTICE = "救援程序已经在运行了，稍微等一下啦。"
RESCUE_FAILED_NOTICE = (
    "解禁失败了……才、才不是我不高性能！请检查 ATRI 是不是群管理员。"
)


# 管理员 / 群主拒绝文案 ---------------------------------------------------

# 可使用 {role}，发送时会替换成“管理员”或“群主”。
DEFAULT_PRIVILEGED_MEMBER_MESSAGES: tuple[str, ...] = (
    "检测到{role}权限！哼，这个游戏不对你开放哦。",
    "笨蛋{role}，你的权限等级太高啦！高性能的我才不会让转盘白白撞上权限墙呢。",
    "不行不行，{role}不能参加禁言大转盘。万一权限系统闹脾气，可不是我不够高性能哦！",
    "权限扫描完成：{role}。结论是——禁止参赛！哼哼，我可是会提前排除故障的高性能机器人。",
)


# 成功 / 失败文案 ----------------------------------------------------------

PARDON_TEXT = (
    "哼……都这么诚恳了，高性能的我就勉为其难放你一马！"
    "这次转盘作废，可别以为每次卖萌都管用哦。"
)

RESCUE_SUCCESS_PREFIX = "友情救援认证成功！"
RESCUE_SUCCESS_TEMPLATE = (
    "已经被你{action}。哼哼，高性能机器人也会尊重群友的选择啦！"
)
RESCUE_AFTER_MUTE_ACTION = "从口球模式里捞出来了"
RESCUE_BEFORE_MUTE_ACTION = "在执行前成功拦下了禁言"

ROUND_FAILURE_TEXT = (
    "呜……转盘模块执行失败了。请确认机器人拥有群管理员权限，"
)


# 抽奖结果文案 -------------------------------------------------------------

RESULT_NORMAL_DURATION_TEMPLATE = "这次的禁言时长是「{display_name}」。"
RESULT_CLAMPED_DURATION_TEMPLATE = (
    "盘面虽然写着「{display_name}」，但单次禁言有上限，"
    "所以实际执行 {effective_duration}。"
)
RESULT_TEMPLATE = (
    "锵锵——指针停下啦！{duration_note}\n"
    "高性能的我会在 {delay} 后执行。\n"
    "本人发送“wssb”或“亚托莉我错了”可以求饶；\n"
    "其他群友发送“nssb”可以友情救援。"
)


def privileged_member_message(configured: object, role: str) -> str:
    """从 WebUI 自定义文案或默认文案中随机选择一条。"""
    messages = _clean_message_list(configured)
    template = secrets.choice(messages or DEFAULT_PRIVILEGED_MEMBER_MESSAGES)
    role_name = "群主" if role == "owner" else "管理员"
    return template.replace("{role}", role_name)


def result_text(
    display_name: str,
    requested_seconds: int,
    effective_seconds: int,
    countdown_seconds: float,
) -> str:
    if requested_seconds > effective_seconds:
        duration_note = RESULT_CLAMPED_DURATION_TEMPLATE.format(
            display_name=display_name,
            effective_duration=format_seconds(effective_seconds),
        )
    else:
        duration_note = RESULT_NORMAL_DURATION_TEMPLATE.format(
            display_name=display_name,
        )
    return RESULT_TEMPLATE.format(
        duration_note=duration_note,
        delay=format_seconds(countdown_seconds),
    )


def rescue_success_text(was_muted: bool) -> str:
    action = (
        RESCUE_AFTER_MUTE_ACTION if was_muted else RESCUE_BEFORE_MUTE_ACTION
    )
    return RESCUE_SUCCESS_TEMPLATE.format(action=action)


def format_seconds(seconds: float | int) -> str:
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


def _clean_message_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
