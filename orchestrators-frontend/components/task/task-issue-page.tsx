"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Ban, Bot, User, Activity } from "lucide-react";
import type { Task, TaskActivity, TaskStatus } from "@/lib/fixtures";
import {
  loadAssignee,
  saveAssignee,
  type AssigneeId,
} from "@/lib/assignees";
import { fetchTask, setTaskStatus } from "@/lib/taskgraph-api";
import { AssigneePicker } from "@/components/task/assignee-picker";
import { OwnerActions } from "@/components/task/owner-actions";
import { AgentActivity } from "@/components/task/agent-activity";
import { RunControl } from "@/components/task/run-control";
import { StatusPicker } from "@/components/task/status-picker";
import { ScratchpadPanel } from "@/components/task/scratchpad-panel";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { HereLine, TypingLine } from "@/components/presence/presence";
import { notifyTyping, useAnnouncePresence } from "@/lib/presence";

type FeedItem = { kind: "activity"; at: string; activity: TaskActivity };

const STATUSES: TaskStatus[] = ["open", "running", "in-review", "closed"];

export function TaskIssuePage({ id }: { id: string }) {
  const [task, setTask] = useState<Task | null>(null);
  const [related, setRelated] = useState<Task[]>([]);
  const [blockers, setBlockers] = useState<Task[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [statusBusy, setStatusBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [assignee, setAssignee] = useState<AssigneeId>("unassigned");
  const [comment, setComment] = useState("");
  // Teammates with this task open (and whether they are typing here).
  const here = useAnnouncePresence(`task:${id}`);

  useEffect(() => {
    setAssignee(loadAssignee(id));
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    // Re-read quietly while the page is open: agents change status, progress
    // and activity as they work.
    const timer = window.setInterval(() => {
      fetchTask(id)
        .then((next) => {
          if (!cancelled && next) setTask(next);
        })
        .catch(() => undefined);
    }, 3000);
    void (async () => {
      setLoading(true);
      try {
        const next = await fetchTask(id);
        if (cancelled) return;
        setTask(next);
        if (!next) {
          setError(null);
          setLoading(false);
          return;
        }
        const [blocked, relatedTasks] = await Promise.all([
          Promise.all(next.blockedBy.map((tid) => fetchTask(tid))),
          Promise.all(next.relatedTo.map((tid) => fetchTask(tid))),
        ]);
        if (cancelled) return;
        setBlockers(blocked.filter((t): t is Task => Boolean(t)));
        setRelated(relatedTasks.filter((t): t is Task => Boolean(t)));
        setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load task");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [id]);

  function onAssigneeChange(next: AssigneeId) {
    setAssignee(next);
    saveAssignee(id, next);
  }

  const onRunStatusChange = useCallback((status: string) => {
    if (!STATUSES.includes(status as TaskStatus)) return;
    setTask((prev) =>
      prev && prev.status !== status
        ? { ...prev, status: status as TaskStatus }
        : prev,
    );
  }, []);

  async function onStatusChange(next: TaskStatus) {
    if (!task || task.status === next || statusBusy) return;
    const previous = task.status;
    setStatusBusy(true);
    setStatusError(null);
    setTask({ ...task, status: next });
    try {
      await setTaskStatus(id, next);
    } catch (err) {
      setTask({ ...task, status: previous });
      setStatusError(err instanceof Error ? err.message : "Could not update status");
    } finally {
      setStatusBusy(false);
    }
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Loading issue…
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-sm text-red-600">
        {error}
      </div>
    );
  }

  if (!task) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
        <p className="text-sm font-medium text-foreground">Issue not found</p>
        <p className="max-w-sm text-xs text-muted-foreground">
          No task exists for{" "}
          <code className="rounded bg-muted px-1 font-mono text-[11px]">
            {id}
          </code>
          .
        </p>
        <Link
          href="/project"
          className="inline-flex items-center gap-1.5 text-xs font-medium text-foreground underline-offset-4 hover:underline"
        >
          <ArrowLeft className="size-3.5" />
          Back to Project
        </Link>
      </div>
    );
  }

  const feed: FeedItem[] = task.activity
    .map((activity) => ({ kind: "activity" as const, at: activity.at, activity }))
    .sort((a, b) => a.at.localeCompare(b.at));

  return (
    <div className="flex h-full overflow-hidden">
      <div
        className="min-w-0 flex-1 overflow-auto"
        onKeyDown={(event) => {
          const target = event.target as HTMLElement;
          if (target.tagName === "TEXTAREA" || target.tagName === "INPUT") {
            notifyTyping();
          }
        }}
      >
        <div className="mx-auto max-w-3xl px-6 py-7 sm:px-10">
          <Link
            href="/project"
            className="mb-5 inline-flex items-center gap-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeft className="size-3.5" />
            Project
          </Link>

          <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1">
            <div className="w-[160px]">
              <StatusPicker
                value={task.status}
                onChange={onStatusChange}
                disabled={statusBusy}
              />
            </div>
            <span className="font-mono text-[11px] text-muted-foreground">
              {task.id}
            </span>
            <HereLine people={here} />
            {task.kind === "task" ? (
              <div className="ml-auto">
                <RunControl
                  taskId={task.id}
                  onStatusChange={onRunStatusChange}
                />
              </div>
            ) : null}
          </div>
          {statusError ? (
            <p className="mb-2 text-xs text-destructive">{statusError}</p>
          ) : null}

          <h1 className="text-xl font-semibold tracking-[-0.02em] text-foreground">
            {task.title}
          </h1>

          <div className="mt-4">
            <OwnerActions taskId={task.id} />
          </div>
          {task.description ? (
            <p className="mt-3 text-[13px] leading-relaxed text-muted-foreground">
              {task.description}
            </p>
          ) : null}

          <section className="mt-6 max-w-xs lg:hidden">
            <h2 className="mb-1.5 text-[11px] font-medium text-muted-foreground">
              Properties
            </h2>
            <div className="flex flex-col gap-0.5">
              <StatusPicker
                value={task.status}
                onChange={onStatusChange}
                disabled={statusBusy}
              />
              <AssigneePicker value={assignee} onChange={onAssigneeChange} />
            </div>
          </section>

          {task.kind === "task" ? (
            <>
              <AgentActivity taskId={task.id} />
              <ScratchpadPanel taskId={task.id} />
            </>
          ) : null}

          <section className="mt-8">
            <h2 className="mb-3 text-[13px] font-medium text-foreground">
              Activity
            </h2>
            {feed.length === 0 ? (
              <p className="text-[13px] text-muted-foreground">No activity yet.</p>
            ) : (
              <ul className="flex flex-col gap-3">
                {feed.map((item) => (
                  <li
                    key={item.activity.id}
                    className="flex gap-3 rounded-xl border border-border px-3.5 py-3"
                  >
                    <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-accent text-muted-foreground">
                      <Activity className="size-3.5" strokeWidth={1.75} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-[13px] leading-relaxed text-foreground">
                        {item.activity.text}
                      </p>
                      <p className="mt-1 text-[11px] text-muted-foreground">
                        {item.activity.at}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="mt-8">
            <h2 className="mb-2 text-[13px] font-medium text-foreground">
              Comment
            </h2>
            <TypingLine people={here} className="mb-1" />
            <Textarea
              placeholder="Leave a comment…"
              className="min-h-[88px] resize-none text-[13px]"
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              aria-label="Comment (posting coming soon)"
            />
            <div className="mt-2 flex justify-end">
              <Button type="button" size="sm" disabled>
                Comment
              </Button>
            </div>
          </section>
        </div>
      </div>

      <aside className="hidden w-[280px] shrink-0 overflow-auto border-l border-border p-4 lg:block">
        <section>
          <h2 className="mb-1.5 px-1.5 text-[11px] font-medium text-muted-foreground">
            Properties
          </h2>
          <div className="flex flex-col gap-0.5">
            <StatusPicker
              value={task.status}
              onChange={onStatusChange}
              disabled={statusBusy}
            />
            <AssigneePicker value={assignee} onChange={onAssigneeChange} />
          </div>
        </section>

        <section className="mt-6">
          <h2 className="mb-3 px-1.5 text-[11px] font-medium text-muted-foreground">
            Relations
          </h2>
          {blockers.length === 0 && related.length === 0 ? (
            <p className="px-1.5 text-xs text-muted-foreground">None</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {blockers.map((item) => (
                <li key={item.id}>
                  <Link
                    href={`/tasks/${item.id}`}
                    className="flex items-center gap-2 rounded-md px-2 py-1.5 text-[13px] hover:bg-accent/60"
                  >
                    <Ban className="size-3.5 text-muted-foreground" />
                    <span className="truncate">{item.title}</span>
                  </Link>
                </li>
              ))}
              {related.map((item) => (
                <li key={item.id}>
                  <Link
                    href={`/tasks/${item.id}`}
                    className="flex items-center gap-2 rounded-md px-2 py-1.5 text-[13px] hover:bg-accent/60"
                  >
                    <User className="size-3.5 text-muted-foreground" />
                    <span className="truncate">{item.title}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>

        <div className="mt-6 flex items-center gap-2 px-1.5 text-xs text-muted-foreground">
          <Bot className="size-3.5" />
          Agent-managed task
        </div>
      </aside>
    </div>
  );
}
