"""Classification of Stepik notification API objects."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Mapping


class NotificationKind(StrEnum):
    """Notification groups used by the automation policy."""

    SOLUTION_VOTE = "solution_vote"
    COMMENT_VOTE = "comment_vote"
    MANUAL_REPLY = "manual_reply"
    IGNORED = "ignored"
    UNKNOWN_VOTE = "unknown_vote"


def classify_notification(notification: Mapping[str, Any]) -> NotificationKind:
    """Classify a Stepik notification using stable structured fields.

    Text and ``html_text`` are deliberately ignored because their wording depends
    on the selected interface language.
    """

    action = notification.get("action")
    data = _context_data(notification)

    if action == "replied":
        return NotificationKind.MANUAL_REPLY

    if action != "voted":
        return NotificationKind.IGNORED

    if data.get("vote_target_type") != "comment":
        return NotificationKind.UNKNOWN_VOTE

    is_solution = data.get("comment_is_solutions_thread")
    submission_id = data.get("submission_id")

    if is_solution is True and submission_id is not None:
        return NotificationKind.SOLUTION_VOTE
    if is_solution is False and submission_id is None:
        return NotificationKind.COMMENT_VOTE
    return NotificationKind.UNKNOWN_VOTE


def _context_data(notification: Mapping[str, Any]) -> Mapping[str, Any]:
    context = notification.get("context")
    if not isinstance(context, Mapping):
        return {}

    data = context.get("data")
    if not isinstance(data, Mapping):
        return {}
    return data
