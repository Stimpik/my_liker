import unittest

from my_liker.browser import StepikBrowser


class VoteValueTests(unittest.TestCase):
    def test_positive_vote_is_inferred_from_type_31_id(self) -> None:
        self.assertEqual(StepikBrowser._vote_value("31-123", {}), "epic")

    def test_side_loaded_value_has_priority(self) -> None:
        self.assertEqual(
            StepikBrowser._vote_value("31-123", {"31-123": "custom"}),
            "custom",
        )

    def test_unknown_vote_type_is_not_treated_as_positive(self) -> None:
        self.assertIsNone(StepikBrowser._vote_value("32-123", {}))


if __name__ == "__main__":
    unittest.main()
