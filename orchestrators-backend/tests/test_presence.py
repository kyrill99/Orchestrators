import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from presence import PresenceStore  # noqa: E402


class PresenceStoreTest(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.changes = []
        self.store = PresenceStore(
            ttl=15, clock=lambda: self.now, on_change=self.changes.append
        )

    def test_update_and_list(self):
        self.store.update("a@x.com", "a", "agent", typing=True, conversation_id="c1")
        self.store.update("b@x.com", "b", "task:t1")
        entries = self.store.active()
        self.assertEqual([e["email"] for e in entries], ["a@x.com", "b@x.com"])
        self.assertTrue(entries[0]["typing"])
        self.assertEqual(entries[0]["conversation_id"], "c1")
        self.assertEqual(entries[1]["where"], "task:t1")

    def test_expires(self):
        self.store.update("a@x.com", "a", "agent")
        self.now += 16
        self.assertEqual(self.store.active(), [])

    def test_change_only_published_on_change(self):
        self.store.update("a@x.com", "a", "agent")
        self.store.update("a@x.com", "a", "agent")
        self.assertEqual(len(self.changes), 1)
        self.store.update("a@x.com", "a", "agent", typing=True)
        self.assertEqual(len(self.changes), 2)

    def test_leave(self):
        self.store.update("a@x.com", "a", "agent")
        self.store.leave("a@x.com")
        self.assertEqual(self.store.active(), [])
        self.assertEqual(len(self.changes), 2)


if __name__ == "__main__":
    unittest.main()
