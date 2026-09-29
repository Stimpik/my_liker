import unittest

from my_liker.notifications import NotificationKind, classify_notification


class ClassifyNotificationTests(unittest.TestCase):
    def test_solution_vote(self) -> None:
        notification = {
            "action": "voted",
            "context": {
                "data": {
                    "actor_id": 101,
                    "comment_is_solutions_thread": True,
                    "submission_id": 202,
                    "vote_target_type": "comment",
                }
            },
        }

        self.assertEqual(
            classify_notification(notification),
            NotificationKind.SOLUTION_VOTE,
        )

    def test_comment_vote(self) -> None:
        notification = {
            "action": "voted",
            "context": {
                "data": {
                    "actor_id": 101,
                    "comment_is_solutions_thread": False,
                    "submission_id": None,
                    "vote_target_type": "comment",
                }
            },
        }

        self.assertEqual(
            classify_notification(notification),
            NotificationKind.COMMENT_VOTE,
        )

    def test_reply_is_always_left_for_manual_review(self) -> None:
        notification = {
            "action": "replied",
            "context": {"data": {"submission_id": 202}},
        }

        self.assertEqual(
            classify_notification(notification),
            NotificationKind.MANUAL_REPLY,
        )

    def test_promotional_and_course_events_are_ignored(self) -> None:
        for action in ("price_dropped", "opened"):
            with self.subTest(action=action):
                self.assertEqual(
                    classify_notification({"action": action}),
                    NotificationKind.IGNORED,
                )

    def test_inconsistent_vote_is_not_treated_as_a_solution(self) -> None:
        notification = {
            "action": "voted",
            "context": {
                "data": {
                    "comment_is_solutions_thread": True,
                    "submission_id": None,
                    "vote_target_type": "comment",
                }
            },
        }

        self.assertEqual(
            classify_notification(notification),
            NotificationKind.UNKNOWN_VOTE,
        )


if __name__ == "__main__":
    unittest.main()
