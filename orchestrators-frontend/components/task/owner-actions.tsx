"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, HelpCircle, Undo2 } from "lucide-react";
import { apiBaseUrl, GUI_ACTOR } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

type Decision = {
  question: string;
  options: string[];
  context?: string | null;
  state: "pending" | "resolved";
  chosen?: string | null;
  rationale?: string | null;
};

type LiveTask = { status: string; decision?: Decision | null };

async function post(path: string, body: unknown) {
  const res = await fetch(`${apiBaseUrl()}/api/taskgraph${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw new Error(data?.detail?.message ?? `Request failed (${res.status})`);
  }
  return res.json();
}

/**
 * The owner's part of the loop: approve or send back a finished task, and answer a
 * strategy decision. Approving or deciding starts whatever the task unblocks.
 */
export function OwnerActions({ taskId }: { taskId: string }) {
  const [task, setTask] = useState<LiveTask | null>(null);
  const [note, setNote] = useState("");
  const [chosen, setChosen] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    fetch(`${apiBaseUrl()}/api/taskgraph/tasks/${encodeURIComponent(taskId)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((t) => setTask(t))
      .catch(() => setTask(null));
  }, [taskId]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 4000);
    return () => clearInterval(id);
  }, [refresh]);

  const act = (path: string, body: Record<string, unknown>, done: (r: { started?: string[] }) => string) => {
    setBusy(true);
    setError(null);
    post(path, { actor: GUI_ACTOR, ...body })
      .then((r) => {
        setMessage(done(r));
        setNote("");
        refresh();
        setTimeout(() => window.location.reload(), 1200);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  const started = (r: { started?: string[] }) =>
    r.started && r.started.length ? ` Started: ${r.started.join(", ")}.` : "";

  if (!task) return null;
  const decision: Decision | null =
    task.decision && task.decision.state === "pending" ? task.decision : null;
  if (task.status !== "in-review" && !decision) return null;

  return (
    <div className="mb-5 rounded-lg border border-border bg-muted/40 p-4">
      {decision ? (
        <>
          <div className="mb-2 flex items-center gap-2 text-[13px] font-medium text-foreground">
            <HelpCircle className="size-4" />
            Strategy question for you
          </div>
          <p className="mb-3 text-sm text-foreground">{decision.question}</p>
          {decision.context ? (
            <p className="mb-3 text-xs text-muted-foreground">{decision.context}</p>
          ) : null}
          {decision.options.length ? (
            <div className="mb-3 flex flex-wrap gap-2">
              {decision.options.map((opt) => (
                <Button
                  key={opt}
                  size="sm"
                  variant={chosen === opt ? "default" : "outline"}
                  onClick={() => setChosen(opt)}
                >
                  {opt}
                </Button>
              ))}
            </div>
          ) : null}
          <Textarea
            value={decision.options.length ? note : chosen || note}
            onChange={(e) => (decision.options.length ? setNote(e.target.value) : setChosen(e.target.value))}
            placeholder={decision.options.length ? "Why? (goes into the decision log)" : "Your answer"}
            className="mb-3 min-h-16 text-sm"
          />
          <Button
            size="sm"
            disabled={busy || !chosen.trim()}
            onClick={() =>
              act(`/tasks/${encodeURIComponent(taskId)}/decide`,
                { chosen, rationale: (decision.options.length ? note : "").trim() || chosen },
                (r) => `Decided.${started(r)}`)
            }
          >
            <Check className="size-3.5" /> Decide
          </Button>
        </>
      ) : (
        <>
          <div className="mb-2 text-[13px] font-medium text-foreground">
            The agent finished this task. Review its result below.
          </div>
          <Textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Feedback (optional when approving; tells the agent what to change when sending back)"
            className="mb-3 min-h-16 text-sm"
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={busy}
              onClick={() =>
                act(`/tasks/${encodeURIComponent(taskId)}/review`, { approve: true, note },
                  (r) => `Approved.${started(r)}`)
              }
            >
              <Check className="size-3.5" /> Approve
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy || !note.trim()}
              title={note.trim() ? undefined : "Write what should change first"}
              onClick={() =>
                act(`/tasks/${encodeURIComponent(taskId)}/review`, { approve: false, note },
                  () => "Sent back. The agent is working on it again.")
              }
            >
              <Undo2 className="size-3.5" /> Send back
            </Button>
          </div>
        </>
      )}
      {message ? <p className="mt-2 text-xs text-muted-foreground">{message}</p> : null}
      {error ? <p className="mt-2 text-xs text-destructive">{error}</p> : null}
    </div>
  );
}
