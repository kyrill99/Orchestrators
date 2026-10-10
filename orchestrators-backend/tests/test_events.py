import asyncio
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from events import RESYNC, Broker, PublishingEventStore  # noqa: E402


async def drain(sub):
    out = []
    while not sub.queue.empty():
        out.append(sub.queue.get_nowait())
    return out


class BrokerTest(unittest.IsolatedAsyncioTestCase):
    async def test_broadcast_to_multiple_subscribers_by_channel(self):
        b = Broker()
        a1 = b.subscribe(["a"])
        a2 = b.subscribe(["a", "b"])
        only_b = b.subscribe(["b"])
        b.publish("a", "x", {"n": 1})
        b.publish("b", "y", {"n": 2})
        self.assertEqual([e.type for e in await drain(a1)], ["x"])
        self.assertEqual([e.type for e in await drain(a2)], ["x", "y"])
        self.assertEqual([e.data for e in await drain(only_b)], [{"n": 2}])
        b.unsubscribe(a1)
        self.assertEqual(b.subscriber_count, 2)

    async def test_slow_subscriber_gets_resync_and_does_not_block(self):
        b = Broker(queue_size=3)
        slow = b.subscribe(["a"])
        fast = b.subscribe(["a"])
        for i in range(10):
            b.publish("a", "x", i)
            await drain(fast)
        events = await drain(slow)
        self.assertEqual([e.type for e in events], [RESYNC])

    async def test_replay_after_last_event_id(self):
        b = Broker()
        first = b.publish("a", "x", 1)
        b.publish("b", "x", 2)
        b.publish("a", "x", 3)
        sub = b.subscribe(["a"], last_event_id=first.id)
        self.assertEqual([e.data for e in await drain(sub)], [3])

    async def test_unknown_or_evicted_id_asks_for_resync(self):
        b = Broker(history=2)
        first = b.publish("a", "x", 1)
        for i in range(5):
            b.publish("a", "x", i)
        for last in (first.id, "otherboot.3", "garbage"):
            sub = b.subscribe(["a"], last_event_id=last)
            self.assertEqual([e.type for e in await drain(sub)], [RESYNC], last)

    async def test_publish_from_worker_thread(self):
        b = Broker()
        sub = b.subscribe(["a"])
        t = threading.Thread(target=b.publish, args=("a", "x", 7))
        t.start()
        t.join()
        event = await asyncio.wait_for(sub.queue.get(), 2)
        self.assertEqual(event.data, 7)

    async def test_taskgraph_events_published_on_graph_channel(self):
        from taskgraph import TaskGraphService
        from taskgraph.models import Actor
        from taskgraph.store import MemoryEventStore

        b = Broker()
        sub = b.subscribe(["graph"])
        graph = TaskGraphService(PublishingEventStore(MemoryEventStore(), b, "graph"))
        graph.create_task(Actor(type="human", id="lorenz"), title="Ship it")
        events = await drain(sub)
        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(events[0].type, "event")
        self.assertEqual(events[0].data["seq"], 1)
        self.assertIn("taskIds", events[0].data)
        self.assertEqual(len(graph.graph()["tasks"]), 1)

    def test_encode(self):
        e = Broker().publish("a", "message", {"text": "hi\nthere"})
        wire = e.encode()
        self.assertTrue(wire.startswith(f"id: {e.id}\nevent: message\ndata: "))
        self.assertTrue(wire.endswith("\n\n"))
        self.assertEqual(wire.count("\n"), 4)  # newlines in data are JSON-escaped


if __name__ == "__main__":
    unittest.main()
