import tempfile
from pathlib import Path
import unittest

from my_liker.stats import StatsStore


class StatsStoreTests(unittest.TestCase):
    def test_duplicate_notification_is_counted_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = StatsStore(Path(directory) / "stats.sqlite3")
            notification = {
                "id": 100,
                "context": {
                    "data": {
                        "actor_id": 200,
                        "actor_name": "Example",
                        "comment_id": 300,
                    }
                },
            }

            store.record_received_like(notification)
            store.record_received_like(notification)
            result = store.aggregate()
            store.close()

        self.assertEqual(result["200"]["received_likes"], 1)
        self.assertEqual(result["200"]["given_likes"], 0)


if __name__ == "__main__":
    unittest.main()
