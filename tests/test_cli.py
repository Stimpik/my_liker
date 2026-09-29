import tempfile
from pathlib import Path
import unittest

from my_liker.cli import (
    PlannedAction,
    _build_action_plan,
    _format_notification,
    _load_plan_cache,
    _save_plan_cache,
)
from my_liker.notifications import NotificationKind
from my_liker.settings import Settings


class FakeBrowser:
    def __init__(self, solution: dict | dict[int, dict] | None) -> None:
        self.solution = solution

    def find_solution(self, *, step_id: int, user_id: int) -> dict | None:
        if self.solution and all(
            isinstance(key, int) for key in self.solution
        ):
            return self.solution.get(user_id)
        return self.solution


class FormatNotificationTests(unittest.TestCase):
    def test_format_contains_ids_and_actor_without_html(self) -> None:
        notification = {
            "id": 123,
            "html_text": "<a>localized and unsafe for classification</a>",
            "context": {"data": {"actor_id": 456, "actor_name": "Example"}},
        }

        line = _format_notification(notification, NotificationKind.SOLUTION_VOTE)

        self.assertIn("notification=123", line)
        self.assertIn("actor_id=456", line)
        self.assertIn("actor='Example'", line)
        self.assertNotIn("localized", line)

    def test_already_liked_solution_is_planned_for_mark_read(self) -> None:
        notification = _solution_vote_notification()
        browser = FakeBrowser(
            {"id": 300, "vote": "31-300", "_current_vote_value": "epic"}
        )

        actions = _build_action_plan(browser, [notification], Settings())

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, "mark_read")
        self.assertEqual(actions[0].reason, "already_liked")

    def test_missing_like_is_applied_before_mark_read(self) -> None:
        notification = _solution_vote_notification()
        browser = FakeBrowser(
            {"id": 300, "vote": None, "_current_vote_value": None}
        )

        actions = _build_action_plan(browser, [notification], Settings())

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, "like_and_mark_read")
        self.assertEqual(actions[0].comment_id, 300)

    def test_preferred_author_is_checked_on_encountered_step(self) -> None:
        notification = _solution_vote_notification()
        browser = FakeBrowser(
            {
                200: {
                    "id": 300,
                    "vote": "31-300",
                    "_current_vote_value": "epic",
                },
                201: {
                    "id": 301,
                    "vote": None,
                    "_current_vote_value": None,
                    "_user_name": "Preferred",
                },
            }
        )
        settings = Settings(
            max_actions_per_run=5,
            preferred_authors_enabled=True,
            preferred_authors=frozenset({201}),
        )

        actions = _build_action_plan(browser, [notification], settings)

        self.assertEqual(len(actions), 2)
        self.assertEqual(actions[0].kind, "mark_read")
        self.assertEqual(actions[1].kind, "like_preferred")
        self.assertEqual(actions[1].actor_id, 201)

    def test_blacklist_has_priority_over_preferred_list(self) -> None:
        notification = _solution_vote_notification()
        browser = FakeBrowser({"id": 301, "vote": None})
        settings = Settings(
            preferred_authors_enabled=True,
            blacklist_enabled=True,
            preferred_authors=frozenset({200}),
            blacklist=frozenset({200}),
        )

        actions = _build_action_plan(browser, [notification], settings)

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, "mark_read")
        self.assertEqual(actions[0].reason, "blacklisted_author")

    def test_plan_cache_round_trip(self) -> None:
        action = PlannedAction(
            "mark_read",
            {"id": 100},
            200,
            "Example",
            "already_liked",
            300,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            _save_plan_cache(path, [action])
            loaded = _load_plan_cache(path)

        self.assertEqual(loaded, [action])

    def test_plan_contains_more_actions_than_execution_batch_limit(self) -> None:
        first = _solution_vote_notification()
        second = _solution_vote_notification()
        second["id"] = 101
        second["context"]["data"]["actor_id"] = 201
        browser = FakeBrowser(
            {
                200: {"id": 300, "vote": None},
                201: {"id": 301, "vote": None},
            }
        )

        actions = _build_action_plan(
            browser,
            [first, second],
            Settings(max_actions_per_run=1),
        )

        self.assertEqual(len(actions), 2)


def _solution_vote_notification() -> dict:
    return {
        "id": 100,
        "action": "voted",
        "context": {
            "data": {
                "actor_id": 200,
                "actor_name": "Example",
                "comment_is_solutions_thread": True,
                "submission_id": 400,
                "vote_target_type": "comment",
            },
            "target": {"step_id": 500},
        },
    }


if __name__ == "__main__":
    unittest.main()
