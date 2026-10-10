import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:  # The store doesn't need FastAPI; stub it where it isn't installed.
    import fastapi  # noqa: F401
except ImportError:  # pragma: no cover
    for name in ("fastapi", "fastapi.responses", "sse_starlette", "sse_starlette.sse"):
        sys.modules.setdefault(name, mock.MagicMock(name=name))
try:
    import services.agent  # noqa: F401
except ImportError:  # pragma: no cover — no google-genai in this environment
    sys.modules["services.agent"] = mock.MagicMock(name="services.agent")

from conversations import ConversationStore  # noqa: E402
from db import Database  # noqa: E402
from identity import User  # noqa: E402

KYRILL = User(email="k@x", name="Kyrill")
LORENZ = User(email="l@x", name="Lorenz")


class ReactionsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.dir.name, "t.db"))
        self.store = ConversationStore(self.db)
        self.cid = self.store.create(KYRILL, "t")
        self.mine = self.store.add_message(self.cid, "user", "hello there", user=KYRILL)
        self.reply = self.store.add_message(self.cid, "agent", "x" * 200)

    def tearDown(self):
        self.db.close()
        self.dir.cleanup()

    def test_upsert_remove_counts(self):
        m = self.store.react(self.cid, self.reply.id, LORENZ, 1)
        self.assertEqual((m.reactions.up, m.reactions.down), (1, 0))
        self.store.react(self.cid, self.reply.id, KYRILL, -1)
        m = self.store.react(self.cid, self.reply.id, LORENZ, -1)  # change vote
        self.assertEqual((m.reactions.up, m.reactions.down), (0, 2))
        m = self.store.react(self.cid, self.reply.id, LORENZ, 0)  # remove
        self.assertEqual((m.reactions.up, m.reactions.down), (0, 1))
        self.assertEqual([v.name for v in m.reactions.voters], ["Kyrill"])
        msgs = {x.id: x for x in self.store.messages(self.cid)}
        self.assertEqual(msgs[self.reply.id].reactions.down, 1)
        self.assertEqual(msgs[self.mine.id].reactions.up, 0)
        self.assertIsNone(self.store.react("nope", self.reply.id, LORENZ, 1))

    def test_summary_text_delivered_once(self):
        self.store.react(self.cid, self.reply.id, LORENZ, 1)
        self.store.react(self.cid, self.mine.id, LORENZ, -1)
        text = self.store.take_reactions(self.cid)
        self.assertTrue(text.startswith("[Reactions since your last reply: Lorenz upvoted your message: \""))
        self.assertIn("Lorenz downvoted Kyrill's message: \"hello there\"", text)
        self.assertTrue(text.endswith("]\n\n"))
        self.assertNotIn("x" * 81, text)
        self.assertEqual(self.store.take_reactions(self.cid), "")


if __name__ == "__main__":
    unittest.main()
