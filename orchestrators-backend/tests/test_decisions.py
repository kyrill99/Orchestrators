"""Decision tasks: lifecycle, preparation runs, auto-decide, owner answers and
changes, downstream briefs, and the engine's retries of infrastructure failures.

Gemini is never called: the scripted fake client from test_gemini_engine.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from artifacts import ArtifactStore  # noqa: E402
from db import Database  # noqa: E402
from services.gemini_worker import GeminiRunner, build_brief  # noqa: E402
from taskgraph import Actor, RecordRunner, RunManager, TaskGraphError, TaskGraphService  # noqa: E402
from taskgraph.engine import Engine  # noqa: E402
from taskgraph.loop import decide, decision_feedback  # noqa: E402
from taskgraph.models import CreateTask, RequestDecision, SetStatus  # noqa: E402
from taskgraph.runlog import RunLog, Scratchpads  # noqa: E402
from test_gemini_engine import FakeClient, call, reply  # noqa: E402

H = Actor(type="human", id="lorenz")
GEMINI = Actor(type="agent", id="gemini")
OPTIONS = ["Classic arcade", "Puzzle floors", "Squad tactics"]


class FakeRunner(RecordRunner):
    name = "fake"


def prepared(task_id="pick", recommended="Puzzle floors"):
    return RequestDecision(
        task_id=task_id, question="Which direction goes to production?", options=OPTIONS,
        context="- Classic: solid, familiar\n- Puzzle: most novel", recommended=recommended,
        recommendation="Puzzle floors tested best and fits the 'fresh' goal; costs more level design.")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(os.path.join(self.tmp.name, "o.db"))
        self.addCleanup(self.db.close)
        self.svc = TaskGraphService()
        self.svc.submit(H, [
            CreateTask(id="a", title="Prototype classic"),
            CreateTask(id="b", title="Prototype puzzle"),
            CreateTask(id="c", title="Prototype squad"),
            CreateTask(id="pick", title="Select winning direction", kind="decision",
                       description="Decide which prototype goes to production.",
                       blocked_by=["a", "b", "c"]),
            CreateTask(id="polish", title="Polish the game", blocked_by=["pick"]),
        ])
        self.now = datetime.now(timezone.utc)
        self.runs = RunManager(self.svc, FakeRunner())

    def engine(self, **kw):
        kw.setdefault("max_runs", 3)
        kw.setdefault("decision_grace", 0)
        kw.setdefault("retry_backoff", 0)
        kw.setdefault("clock", lambda: self.now)
        return Engine(self.svc, kw.pop("runs", self.runs), self.db, **kw)

    def finish_protos(self, status="in-review"):
        for t in ("a", "b", "c"):
            self.svc.submit(H, [SetStatus(task_id=t, status="running")])
            self.svc.submit(H, [SetStatus(task_id=t, status=status)])

    def prepare(self, **kw):
        self.svc.submit(GEMINI, [prepared(**kw)], "prepared")


class LifecycleTest(Base):
    def test_actionable_once_blockers_are_closed_or_in_review(self):
        self.assertEqual(self.svc.actionable_decisions(), [])
        self.svc.submit(H, [SetStatus(task_id="a", status="closed")])
        self.svc.submit(H, [SetStatus(task_id="b", status="running"), SetStatus(task_id="b", status="in-review")])
        self.assertEqual(self.svc.actionable_decisions(), [])  # c is still open
        self.assertEqual([w["id"] for w in next(b for b in self.svc.blocked() if b["id"] == "pick")["waitingOn"]],
                         ["c"])  # in-review b does not block the decision
        self.svc.submit(H, [SetStatus(task_id="c", status="running"), SetStatus(task_id="c", status="in-review")])
        self.assertEqual([t["id"] for t in self.svc.actionable_decisions()], ["pick"])
        self.assertNotIn("pick", [b["id"] for b in self.svc.blocked()])
        # Only decisions: a work task still needs closed blockers.
        self.svc.submit(H, [CreateTask(id="w", title="Work", blocked_by=["c"])])
        with self.assertRaises(TaskGraphError):
            self.svc.submit(H, [SetStatus(task_id="w", status="running")])

    def test_answer_closes_the_decision_and_the_next_task_starts(self):
        self.finish_protos()
        self.prepare()
        out = decide(self.svc, self.runs, H, "pick", "Classic arcade", "", engine=self.engine())
        task = self.svc.task("pick")
        self.assertEqual(task["status"], "closed")
        d = task["decision"]
        self.assertEqual((d["chosen"], d["auto"], d["custom"], d["resolvedBy"]),
                         ("Classic arcade", False, False, {"type": "human", "id": "lorenz"}))
        self.assertEqual(d["rationale"], "(no rationale given)")
        self.assertEqual(out["started"], ["polish"])
        # The prototypes stay in review: deciding does not approve them.
        self.assertEqual({self.svc.task(t)["status"] for t in "abc"}, {"in-review"})

    def test_own_answer_and_answer_before_preparation(self):
        self.finish_protos()
        decide(self.svc, self.runs, H, "pick", "Merge classic and squad", "best of both")
        d = self.svc.task("pick")["decision"]
        self.assertEqual((d["chosen"], d["question"]), ("Merge classic and squad",
                                                        "Decide which prototype goes to production."))
        self.assertEqual(self.svc.task("pick")["status"], "closed")
        # Answered early, blockers done later: the engine closes it then.
        svc = self.svc
        svc.submit(H, [CreateTask(id="x", title="X"),
                       CreateTask(id="d2", title="Later choice", kind="decision", blocked_by=["x"])])
        decide(svc, self.runs, H, "d2", "Option 1", "early")
        self.assertEqual(svc.task("d2")["status"], "open")
        svc.submit(H, [SetStatus(task_id="x", status="running"), SetStatus(task_id="x", status="in-review")])
        self.engine().dispatch(H, "go")
        self.assertEqual(svc.task("d2")["status"], "closed")

    def test_custom_answer_is_marked(self):
        self.finish_protos()
        self.prepare()
        decide(self.svc, self.runs, H, "pick", "Something else", "my call")
        self.assertTrue(self.svc.task("pick")["decision"]["custom"])

    def test_recommendation_must_be_an_option(self):
        self.finish_protos()
        with self.assertRaises(TaskGraphError):
            self.svc.submit(GEMINI, [prepared(recommended="Racing")], "prep")
        self.svc.submit(GEMINI, [prepared(recommended="puzzle floors")], "prep")
        self.assertEqual(self.svc.task("pick")["decision"]["recommended"], "Puzzle floors")


class PrepRunTest(Base):
    def setUp(self):
        super().setUp()
        self.runlog = RunLog(self.db)
        self.pads = Scratchpads(self.db)
        self.arts = ArtifactStore(self.db, os.path.join(self.tmp.name, "files")).scoped("default")
        for t, name in (("a", "classic"), ("b", "puzzle"), ("c", "squad")):
            self.arts.save(t, "report.html", f"<p>{name} prototype</p>", title=f"{name} report",
                           kind="html", created_by="gemini")

    def gemini(self, client):
        return GeminiRunner(self.svc, self.runlog, self.pads, artifacts=self.arts,
                            client_factory=lambda: client, builtin_tools=[], retry_delays=())

    def test_engine_prepares_the_decision(self):
        self.finish_protos()
        client = FakeClient([
            reply(call("prepare_decision", {"question": "q", "options": ["x", "y"], "recommended": "x",
                                            "recommendation": "because", "context": "c"}, id="p0"),
                  call("save_artifact", {"filename": "decision.html", "title": "Decision: direction",
                                         "content": "<!doctype html><h1>Compare</h1>"})),
            reply(call("prepare_decision", {
                "question": "Which direction goes to production?", "options": OPTIONS,
                "recommended": "Puzzle floors", "recommendation": "Most novel; tested best.",
                "context": "- Classic: [report](/api/artifacts/x/view)"})),
        ])
        runner = self.gemini(client)
        runs = RunManager(self.svc, runner)
        engine = self.engine(runs=runs)
        engine.start(H, "go")
        started = engine.tick()
        self.assertEqual([r.task_id for r in started], ["pick"])
        self.assertTrue(runner.join(started[0].id, 5))
        run = self.svc.run(started[0].id)
        self.assertEqual(run.status, "succeeded")
        self.assertIn("Recommends Puzzle floors", run.note)
        task = self.svc.task("pick")
        self.assertEqual((task["status"], task["assignee"]), ("open", None))  # waits, not in review
        d = task["decision"]
        self.assertEqual((d["state"], d["options"], d["recommended"]), ("pending", OPTIONS, "Puzzle floors"))
        self.assertEqual(d["recommendation"], "Most novel; tested best.")
        self.assertEqual(d["requestedBy"], {"type": "agent", "id": "gemini"})
        self.assertEqual([a["filename"] for a in self.arts.list("pick")], ["decision.html"])
        # The first prepare_decision came before the report was saved: refused.
        first_result = [s for s in self.runlog.steps(run.id) if s["kind"] == "tool_result"][0]
        self.assertIn("save the comparison report first", first_result["payload"]["result"])
        first = client.interactions.calls[0]
        self.assertIn("# Role: decider", first["system_instruction"])
        names = {t.get("name") for t in first["tools"]}
        self.assertIn("prepare_decision", names)
        self.assertNotIn("create_subtask", names)
        self.assertIn("/api/artifacts/", first["input"])  # blockers' reports are linked
        self.assertIn("puzzle report", first["input"])
        self.assertIn("Finish with prepare_decision", first["input"])

    def test_owner_input_prepares_again_and_pauses_auto_decide(self):
        self.finish_protos()
        self.prepare()
        client = FakeClient([reply(
            call("save_artifact", {"filename": "decision.html", "title": "Decision v2", "content": "<p>v2</p>"}),
            call("prepare_decision", {"question": "Which direction?", "options": [*OPTIONS, "Classic + squad"],
                                      "recommended": "Classic + squad", "recommendation": "As asked.",
                                      "context": "c"}))])
        runner = self.gemini(client)
        runs = RunManager(self.svc, runner)
        engine = self.engine(runs=runs)
        out = decision_feedback(self.svc, runs, H, "pick", "Add an option that combines classic + squad",
                                engine=engine)
        self.assertEqual(out["started"], ["pick"])
        self.assertTrue(runner.join(out["run"]["id"], 5))
        self.assertIn("combines classic + squad", client.interactions.calls[0]["input"])
        d = self.svc.task("pick")["decision"]
        self.assertEqual((d["state"], d["recommended"]), ("pending", "Classic + squad"))
        self.assertEqual(d["feedback"], "Add an option that combines classic + squad")
        engine.start(H, "go")
        self.assertEqual(engine.tick(), [])  # nothing to prepare, and the owner decides this one
        self.assertEqual(self.svc.task("pick")["decision"]["state"], "pending")
        w = engine.status()["waiting"]
        self.assertEqual(w["reason"], "review")
        self.assertIn("Waiting for your decision: Select winning direction", w["summary"])
        self.assertEqual(w["focus"]["id"], "pick")


class AutoDecideTest(Base):
    def test_running_engine_takes_the_recommendation_and_starts_the_next_task(self):
        self.finish_protos()
        self.prepare()
        engine = self.engine()
        engine.start(H, "go")
        started = engine.tick()
        task = self.svc.task("pick")
        d = task["decision"]
        self.assertEqual((task["status"], d["chosen"], d["auto"]), ("closed", "Puzzle floors", True))
        self.assertEqual(d["resolvedBy"], {"type": "agent", "id": "agent"})
        self.assertIn("Decided automatically by the agent", d["rationale"])
        self.assertIn("tested best", d["rationale"])
        self.assertEqual([r.task_id for r in started], ["polish"])
        self.assertIn("decided automatically 'Puzzle floors'", task["activity"][-1]["text"])

    def test_no_recommendation_means_preparation_first(self):
        self.finish_protos()
        self.svc.submit(H, [RequestDecision(task_id="pick", question="Which one?", options=OPTIONS)])
        engine = self.engine()
        engine.start(H, "go")
        self.assertEqual([r.task_id for r in engine.tick()], ["pick"])  # a prep run, no guess
        self.assertEqual(self.svc.task("pick")["decision"]["state"], "pending")

    def test_ask_all_and_a_stopped_engine_wait_for_the_owner(self):
        self.finish_protos()
        self.prepare()
        engine = self.engine()
        self.assertEqual(engine.auto_decide(), [])  # stopped
        w = engine.status()["waiting"]
        self.assertIn("Waiting for your decision: Select winning direction", w["summary"])
        self.assertFalse(w["decisions"][0]["auto"])
        self.svc.update_settings(H, autonomy="ask-all")
        engine.start(H, "go")
        self.assertEqual(engine.tick(), [])
        self.assertEqual(self.svc.task("pick")["decision"]["state"], "pending")
        w = engine.status()["waiting"]
        self.assertIn("Waiting for your decision: Select winning direction", w["summary"])

    def test_grace_period(self):
        self.finish_protos()
        self.prepare()
        engine = self.engine(decision_grace=300)
        self.now = datetime.now(timezone.utc)
        engine.start(H, "go")
        engine.tick()
        self.assertEqual(self.svc.task("pick")["decision"]["state"], "pending")
        w = engine.status()["waiting"]
        self.assertEqual(w["decisions"][0]["auto"], True)
        self.assertIn("The agent decides in 300 s unless you choose: Select winning direction "
                      "(recommends Puzzle floors)", w["summary"])
        self.now += timedelta(seconds=301)
        engine.tick()
        self.assertEqual(self.svc.task("pick")["decision"]["chosen"], "Puzzle floors")

    def test_ask_structural_proposes_once(self):
        self.finish_protos()
        self.prepare()
        self.svc.update_settings(H, autonomy="ask-structural")
        engine = self.engine()
        engine.start(H, "go")
        engine.tick()
        engine.tick()
        self.assertEqual(len(self.svc.changesets("proposed")), 1)
        self.assertEqual(self.svc.task("pick")["decision"]["state"], "pending")

    def test_waiting_while_preparing(self):
        self.finish_protos()
        engine = self.engine()
        self.assertEqual([t["id"] for t in engine.decision_queue()], ["pick"])
        engine.start(H, "go")
        engine.tick()
        w = engine.status()["waiting"]
        self.assertEqual(w["preparingDecisions"], [{"id": "pick", "title": "Select winning direction"}])
        self.assertIn("Agent is preparing a decision: Select winning direction", w["summary"])


class OverrideTest(Base):
    def test_owner_changes_an_agent_decision(self):
        self.finish_protos()
        self.prepare()
        engine = self.engine()
        engine.start(H, "go")
        polish_run = engine.tick()[0]
        self.runs.finish(GEMINI, polish_run.id, "succeeded", "polished the puzzle game")
        self.assertEqual(self.svc.task("polish")["status"], "in-review")
        out = decide(self.svc, self.runs, H, "pick", "Classic arcade", "Classic suits the audience",
                     engine=engine)
        self.assertEqual(out["affected"], ["polish"])
        d = self.svc.task("pick")["decision"]
        self.assertEqual((d["chosen"], d["auto"], d["history"][0]["chosen"], d["history"][0]["auto"]),
                         ("Classic arcade", False, "Puzzle floors", True))
        polish = self.svc.task("polish")
        comments = [a["text"] for a in polish["activity"] if a["op"] == "comment.add"]
        self.assertIn("Decision changed: 'Select winning direction' is now 'Classic arcade' "
                      "(was 'Puzzle floors')", comments[-1])
        # Reopened by a person: the engine runs it again (here already, via decide).
        self.assertEqual(out["started"], ["polish"])
        self.assertEqual(self.svc.task("polish")["status"], "running")
        self.assertIn("changed the decision from 'Puzzle floors' to 'Classic arcade'",
                      self.svc.task("pick")["activity"][-1]["text"])
        w = engine.status()["waiting"]
        self.assertEqual(w["decisionChanges"][0]["previous"], "Puzzle floors")
        self.assertIn("Decision changed: Select winning direction is now 'Classic arcade'", w["summary"])

    def test_a_running_task_restarts(self):
        self.finish_protos()
        self.prepare()
        decide(self.svc, self.runs, H, "pick", "Puzzle floors", "ok", engine=self.engine())
        first = self.svc.runs(task_id="polish", active=True)[0]
        decide(self.svc, self.runs, H, "pick", "Squad tactics", "changed my mind", engine=self.engine())
        runs = self.svc.runs(task_id="polish")
        self.assertEqual((runs[0].status, runs[-1].status, runs[-1].restart_of), ("stopped", "running", first.id))
        self.assertIn("Squad tactics", runs[-1].instructions)
        with self.assertRaises(TaskGraphError):
            decide(self.svc, self.runs, H, "pick", "Squad tactics", "again")


class BriefTest(Base):
    def test_downstream_brief_leads_with_the_decision(self):
        self.finish_protos()
        self.prepare()
        engine = self.engine()
        engine.start(H, "go")
        run = engine.tick()[0]
        db_pads = Scratchpads(self.db)
        brief = build_brief(self.svc, db_pads, RunLog(self.db), None, run, self.svc.state.tasks["polish"])
        self.assertIn("## Decisions this task must follow", brief)
        self.assertIn("**Chosen: Puzzle floors**", brief)
        self.assertIn("Question: Which direction goes to production?", brief)
        self.assertIn("Rationale: Puzzle floors tested best", brief)
        self.assertIn("Decided by the agent, automatically", brief)
        self.assertIn("Not chosen: Classic arcade, Squad tactics.", brief)
        self.assertLess(brief.index("Chosen: Puzzle floors"), brief.index("## Description"))


class RetryTest(unittest.TestCase):
    """Runs that failed on the infrastructure are retried; the agent's own failures are not."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(os.path.join(self.tmp.name, "o.db"))
        self.addCleanup(self.db.close)
        self.svc = TaskGraphService()
        self.svc.submit(H, [CreateTask(id="spike", title="Spike"), CreateTask(id="other", title="Other")])
        self.runs = RunManager(self.svc, FakeRunner())
        self.now = datetime.now(timezone.utc)
        self.engine = Engine(self.svc, self.runs, self.db, max_runs=1, retry_backoff=60,
                             clock=lambda: self.now)
        self.engine.start(H, "go")

    def fail_run(self, task_id, kind):
        run = self.svc.runs(task_id=task_id, active=True)[0]
        self.svc.end_run(GEMINI, run.id, "failed", note="Gemini request failed: 400", failure_kind=kind)
        self.now = datetime.now(timezone.utc)  # the clock at the failure

    def test_api_failures_are_retried_after_a_backoff_up_to_the_cap(self):
        self.assertEqual([r.task_id for r in self.engine.tick()], ["spike"])
        for retry in (1, 2):
            self.fail_run("spike", "api")
            self.assertEqual(self.svc.runs(task_id="spike")[-1].failure_kind, "api")
            started = self.engine.tick()  # within the backoff: the other task goes first
            if retry == 1:
                self.assertEqual([r.task_id for r in started], ["other"])
                self.runs.finish(GEMINI, started[0].id, "succeeded", "done")
            w = self.engine.status()["waiting"]
            self.assertEqual(w["retrying"][0]["id"], "spike")
            self.assertIn("Retrying in 60 s after an API error: Spike", w["summary"])
            self.now += timedelta(seconds=61)
            started = self.engine.tick()
            self.assertEqual([r.task_id for r in started], ["spike"])
            self.assertEqual(started[0].reason, f"engine: retrying after an API error (automatic retry {retry} of 2)")
        activity = [a for a in self.svc.task("spike")["activity"] if a["op"] == "run.started"]
        self.assertIn("automatic retry 2 of 2", activity[-1]["reason"])
        self.fail_run("spike", "infra")  # a third failure in a row: the cap holds
        self.now += timedelta(seconds=61)
        self.assertEqual(self.engine.tick(), [])
        w = self.engine.status()["waiting"]
        self.assertEqual(w["retrying"], [])
        self.assertEqual(w["needsRestart"][0]["why"],
                         "last run failed: an infrastructure error, after 2 automatic retries")

    def test_the_agents_own_failure_is_not_retried(self):
        self.engine.max_runs = 2
        run = self.engine.tick()[0]
        self.runs.finish(GEMINI, run.id, "failed", "I could not do it")
        self.assertEqual(self.svc.run(run.id).failure_kind, "agent")
        self.now += timedelta(seconds=600)
        self.assertNotIn("spike", [r.task_id for r in self.engine.tick()])
        for kind in ("checks", "budget"):
            self.svc.submit(H, [SetStatus(task_id="spike", status="open")], "again")
            again = [r for r in self.engine.tick() if r.task_id == "spike"]
            self.assertEqual(len(again), 1)
            self.fail_run("spike", kind)
            self.now += timedelta(seconds=600)
            self.assertNotIn("spike", [r.task_id for r in self.engine.tick()])


class GeminiFailureKindTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(os.path.join(self.tmp.name, "o.db"))
        self.addCleanup(self.db.close)
        self.svc = TaskGraphService()
        self.svc.submit(H, [CreateTask(id="t", title="T")])

    def run_with(self, client, **kw):
        runner = GeminiRunner(self.svc, RunLog(self.db), Scratchpads(self.db), client_factory=lambda: client,
                              builtin_tools=[], retry_delays=(), **kw)
        run = RunManager(self.svc, runner).start(H, "t", "go", agent_id="gemini")
        self.assertTrue(runner.join(run.id, 5))
        return self.svc.run(run.id), client

    def test_kinds(self):
        run, _ = self.run_with(FakeClient([RuntimeError("503 unavailable")]))
        self.assertEqual((run.status, run.failure_kind), ("failed", "api"))
        self.svc.submit(H, [SetStatus(task_id="t", status="open")])
        run, _ = self.run_with(FakeClient([], default=reply(call("add_comment", {"body": "x"}))), max_steps=2)
        self.assertEqual(run.failure_kind, "budget")
        run, _ = self.run_with(FakeClient([reply(call("finish_run", {"outcome": "failed", "summary": "no"}))]))
        self.assertEqual(run.failure_kind, "agent")

    def test_empty_tool_results_are_not_sent_as_empty_text(self):
        from unittest import mock

        from services.agent_tools import AgentTools

        real = AgentTools.dispatch

        def dispatch(self, name, args):
            return "" if name == "read_scratchpad" else real(self, name, args)

        client = FakeClient([reply(call("read_scratchpad", {})),
                             reply(call("finish_run", {"outcome": "succeeded", "summary": "ok"}))])
        with mock.patch.object(AgentTools, "dispatch", dispatch):
            self.run_with(client)
        sent = client.interactions.calls[1]["input"]
        self.assertEqual(sent[0]["result"], "(empty result)")


if __name__ == "__main__":
    unittest.main()
