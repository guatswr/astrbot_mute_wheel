from __future__ import annotations

from typing import Any


VALID_MEMBER_ROLES = frozenset({"owner", "admin", "member"})


async def send_group_message(
    bot: Any,
    group_id: int,
    message: list[dict[str, Any]],
) -> Any:
    response = await bot.api.call_action(
        "send_group_msg",
        group_id=group_id,
        message=message,
    )
    raise_for_onebot_error(response, "send_group_msg")
    return extract_message_id(response)


async def set_group_ban(
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
    raise_for_onebot_error(response, "set_group_ban")


async def get_group_member_role(
    bot: Any,
    group_id: int,
    user_id: int,
) -> str | None:
    response = await bot.api.call_action(
        "get_group_member_info",
        group_id=group_id,
        user_id=user_id,
        no_cache=True,
    )
    raise_for_onebot_error(response, "get_group_member_info")
    return extract_member_role(response)


async def recall_message(bot: Any, message_id: Any) -> None:
    response = await bot.api.call_action("delete_msg", message_id=message_id)
    raise_for_onebot_error(response, "delete_msg")


def at_segment(user_id: int) -> dict[str, Any]:
    return {"type": "at", "data": {"qq": str(user_id)}}


def text_segment(text: str) -> dict[str, Any]:
    return {"type": "text", "data": {"text": text}}


def extract_message_id(response: Any) -> Any:
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


def extract_member_role(response: Any) -> str | None:
    data = response.get("data") if isinstance(response, dict) else None
    if data is None:
        data = getattr(response, "data", None)
    if isinstance(data, dict):
        role = data.get("role")
    else:
        role = getattr(data, "role", None)
    return normalize_member_role(role)


def event_sender_role(event: Any) -> str | None:
    message_obj = getattr(event, "message_obj", None)
    candidates = [getattr(message_obj, "sender", None)]
    raw_message = getattr(message_obj, "raw_message", None)
    candidates.append(getattr(raw_message, "sender", None))
    if isinstance(raw_message, dict):
        candidates.append(raw_message.get("sender"))
    else:
        getter = getattr(raw_message, "get", None)
        if callable(getter):
            candidates.append(getter("sender"))

    for sender in candidates:
        role = sender.get("role") if isinstance(sender, dict) else getattr(
            sender,
            "role",
            None,
        )
        normalized = normalize_member_role(role)
        if normalized is not None:
            return normalized
    return None


def normalize_member_role(role: Any) -> str | None:
    normalized = str(role or "").lower()
    return normalized if normalized in VALID_MEMBER_ROLES else None


def raise_for_onebot_error(response: Any, action: str) -> None:
    if not isinstance(response, dict):
        return
    status = response.get("status")
    retcode = response.get("retcode")
    if status not in (None, "ok", "async") or retcode not in (None, 0):
        raise RuntimeError(f"OneBot {action} 调用失败：{response!r}")
