"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Hourglass, Play, Square } from "lucide-react";
import {
  fetchEngine,
  setEngine,
  type EngineStatus,
  type EngineWaiting,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { DecisionIcon } from "@/components/task/decision-icon";
import { projectPath } from "@/lib/project-scope";
import { useProjectId } from "@/lib/use-project";
import { cn } from "@/lib/utils";

const POLL_MS = 3000;

/** Short label for why a running engine is idle, e.g. "Waiting for review (1)". */
function waitingLabel(w: EngineWaiting): string | null {
  switch (w.reason) {
    case "review":
      return `Waiting for review (${w.review.length})`;
    case "decision":
      return `Waiting for decision (${w.decisions.filter((d) => !d.auto).length})`;
    case "decision-auto":
      return "Agent is about to decide";
    case "preparing-decision":
      return "Preparing a decision";
    case "retrying":
      return `Retrying (${w.retrying?.length ?? 0})`;
    case "decision-changed":
      return "Redoing after a decision change";
    case "proposal":
      return `Changes to approve (${w.proposals.length})`;
    case "assigned-to-person":
      return `Assigned to people (${w.assignedToPeople?.length ?? 0})`;
    case "needs-restart":
      return `Needs restart (${w.needsRestart.length})`;
    case "ask-all":
      return "Ask-all: start tasks yourself";
    case "blocked":
      return "Blocked";
    default:
      return null;
  }
}

/** Start / stop the engine that keeps agents working on ready tasks. */
export function EngineToggle() {
  const [engine, setState] = useState<EngineStatus | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const projectId = useProjectId();

  const refresh = useCallback(() => {
    fetchEngine()
      .then((next) => setState(next))
      .catch(() => setState(null));
  }, []);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  if (engine === null) return null;

  const running = engine.state === "running";
  const active = engine.activeRuns.length;
  const canRun = engine.runner !== "record";

  const toggle = () => {
    let stopRuns = false;
    if (running && active > 0) {
      stopRuns = window.confirm(
        `Also stop the ${active} active agent run${active === 1 ? "" : "s"}?\n` +
          "OK stops them now; Cancel lets them finish.",
      );
    }
    setPending(true);
    setError(null);
    setEngine(running ? "stop" : "start", { stopRuns })
      .then(setState)
      .catch((e: Error) => setError(e.message))
      .finally(() => setPending(false));
  };

  const waiting = engine.waiting;
  // Running but idle: say what it waits for, so it does not look broken.
  const hint =
    running && active === 0 && waiting ? waitingLabel(waiting) : null;
  const focusHref = waiting?.focus
    ? projectPath(projectId, `/tasks/${encodeURIComponent(waiting.focus.id)}`)
    : null;

  const title = !canRun
    ? "No agent runner configured on the server (GEMINI_API_KEY is not set)"
    : running
      ? `Engine running: ${active} of ${engine.maxRuns} agents busy, ${engine.queue.length} task(s) queued` +
        (hint && waiting ? `. ${waiting.summary}` : "")
      : `Engine stopped. ${engine.queue.length} ready task(s) would start.`;

  const hintClass =
    "inline-flex max-w-44 min-w-0 items-center gap-1 overflow-hidden whitespace-nowrap rounded-md px-1.5 py-0.5 text-xs text-amber-700 dark:text-amber-400";

  // Decisions that wait for the owner: always shown, running or not.
  const yours = (waiting?.decisions ?? []).filter((d) => !d.auto);

  return (
    <div className="flex items-center gap-2" title={error ?? title}>
      {yours.length > 0 ? (
        <Link
          href={
            yours.length === 1
              ? projectPath(projectId, `/tasks/${encodeURIComponent(yours[0].id)}`)
              : projectPath(projectId, "/decisions")
          }
          title={yours.map((d) => `${d.title}: ${d.question}`).join("\n")}
          className="inline-flex items-center gap-1 rounded-full bg-violet-500/10 px-2 py-0.5 text-xs font-medium text-violet-700 hover:bg-violet-500/15 dark:text-violet-300"
        >
          <DecisionIcon className="size-3 shrink-0" />
          {yours.length === 1 ? "1 decision" : `${yours.length} decisions`}
        </Link>
      ) : null}
      {hint && waiting ? (
        focusHref ? (
          <Link
            href={focusHref}
            title={waiting.summary}
            className={cn(hintClass, "hover:bg-accent")}
          >
            <Hourglass className="size-3 shrink-0" />
            <span className="truncate">{hint}</span>
          </Link>
        ) : (
          <span title={waiting.summary} className={hintClass}>
            <Hourglass className="size-3 shrink-0" />
            <span className="truncate">{hint}</span>
          </span>
        )
      ) : null}
      <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
        <span
          className={cn(
            "inline-flex size-1.5 rounded-full",
            running ? "bg-[#5E6AD2]" : "bg-neutral-300",
            running && active > 0 && "animate-pulse",
          )}
        />
        {running ? "Engine" : "Engine off"}
        <span className="tabular-nums">
          {active}/{engine.maxRuns}
        </span>
      </span>
      <Button
        type="button"
        size="xs"
        variant={running ? "outline" : "default"}
        disabled={pending || (!running && !canRun)}
        onClick={toggle}
      >
        {running ? <Square /> : <Play />}
        {running ? "Stop" : "Start"}
      </Button>
      {error ? <span className="max-w-48 truncate text-xs text-destructive">{error}</span> : null}
    </div>
  );
}
