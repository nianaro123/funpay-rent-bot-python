"""Compatibility helpers for FunPayAPI behavior changes."""

from __future__ import annotations

import json
import logging
from types import MethodType, SimpleNamespace
from typing import Optional

from FunPayAPI.updater.runner import Runner

from FunPayAPI.common import exceptions

LOGGER = logging.getLogger(__name__)


def patch_send_message_without_bot_prefix(acc) -> None:
    """Retry FunPay messages without FunPayAPI's invisible bot prefix on HTTP 400.

    Some FunPayAPI versions prepend an invisible bot marker (U+2064) to every
    text message. If FunPay rejects that payload with HTTP 400, retry the same
    message through runner/ without the marker. The public API of acc.send_message
    is preserved for the project code.
    """

    original_send_message = acc.send_message

    def send_message_compat(
        self,
        chat_id: int | str,
        text: Optional[str] = None,
        chat_name: Optional[str] = None,
        image_id: Optional[int] = None,
        add_to_ignore_list: bool = True,
        update_last_saved_message: bool = False,
    ):
        try:
            return original_send_message(
                chat_id,
                text,
                chat_name,
                image_id,
                add_to_ignore_list,
                update_last_saved_message,
            )
        except exceptions.RequestFailedError as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code != 400 or image_id is not None or not text:
                raise

            LOGGER.warning(
                "FunPay rejected message with status 400; retrying without bot prefix chat_id=%s",
                chat_id,
            )
            return _send_message_without_bot_prefix(
                self,
                chat_id=chat_id,
                text=text,
                chat_name=chat_name,
                add_to_ignore_list=add_to_ignore_list,
                update_last_saved_message=update_last_saved_message,
            )

    acc.send_message = MethodType(send_message_compat, acc)


def _send_message_without_bot_prefix(
    acc,
    chat_id: int | str,
    text: str,
    chat_name: Optional[str] = None,
    add_to_ignore_list: bool = True,
    update_last_saved_message: bool = False,
):
    headers = {
        "accept": "*/*",
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "x-requested-with": "XMLHttpRequest",
    }
    request = {
        "action": "chat_message",
        "data": {"node": chat_id, "last_message": -1, "content": text},
    }
    objects = [
        {
            "type": "chat_node",
            "id": chat_id,
            "tag": "00000000",
            "data": {"node": chat_id, "last_message": -1, "content": ""},
        }
    ]
    payload = {
        "objects": json.dumps(objects),
        "request": json.dumps(request),
        "csrf_token": acc.csrf_token,
    }

    response = acc.method("post", "runner/", headers, payload, raise_not_200=True)
    json_response = response.json()
    resp = json_response.get("response")
    if not resp:
        raise exceptions.MessageNotDeliveredError(response, None, chat_id)
    if (error_text := resp.get("error")) is not None:
        raise exceptions.MessageNotDeliveredError(response, error_text, chat_id)

    message_id = None
    try:
        message_id = int(json_response["objects"][0]["data"]["messages"][-1]["id"])
    except (KeyError, IndexError, TypeError, ValueError):
        LOGGER.debug("Could not parse sent message id from FunPay response", exc_info=True)

    message_obj = SimpleNamespace(
        id=message_id,
        text=text,
        chat_id=chat_id,
        chat_name=chat_name,
    )

    if getattr(acc, "runner", None) and isinstance(chat_id, int) and message_id is not None:
        if add_to_ignore_list:
            acc.runner.mark_as_by_bot(chat_id, message_id)
        if update_last_saved_message:
            acc.runner.update_last_message(chat_id, text)

    return message_obj


def patch_runner_skip_empty_updates() -> None:
    """Make FunPayAPI Runner tolerate empty long-poll objects from FunPay.

    Since June 2026 FunPay can return runner objects with ``data: false`` for
    unchanged chat/order counters even when older FunPayAPI versions expect a
    populated ``data`` dict. The stock parser then raises TypeError and the
    bot prints "Произошла ошибка при получении событий" on every poll.
    """

    if getattr(Runner, "_rent_bot_skip_empty_updates_patch", False):
        return

    original_parse_updates = Runner.parse_updates

    def parse_updates_compat(self, updates: dict):
        objects = updates.get("objects") if isinstance(updates, dict) else None
        if not isinstance(objects, list):
            return original_parse_updates(self, updates)

        filtered_objects = []
        skipped_empty = 0
        for obj in objects:
            if not isinstance(obj, dict):
                filtered_objects.append(obj)
                continue

            obj_type = obj.get("type")
            if obj_type in {"chat_bookmarks", "orders_counters"} and not obj.get("data"):
                skipped_empty += 1
                tag = obj.get("tag")
                if tag:
                    if obj_type == "chat_bookmarks":
                        self._Runner__last_msg_event_tag = tag
                    else:
                        self._Runner__last_order_event_tag = tag
                continue

            filtered_objects.append(obj)

        if skipped_empty:
            LOGGER.debug("Skipped %s empty FunPay runner object(s).", skipped_empty)

        if not filtered_objects:
            return []

        return original_parse_updates(self, {**updates, "objects": filtered_objects})

    Runner.parse_updates = parse_updates_compat
    Runner._rent_bot_skip_empty_updates_patch = True
