"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Ban, Bot, User, Activity } from "lucide-react";
import type { Task, TaskActivity } from "@/lib/fixtures";
import {
  loadAssignee,
  saveAssignee,
  type AssigneeId,
} from "@/lib/assignees";
import { fetchTask } from "@/lib/taskgraph-api";
import { AssigneePicker } from "@/components/task/assignee-picker";
import { OwnerActions } from "@/components/task/owner-actions";
import { RunControl } from "@/components/task/run-control";
import { StatusBadge } from "@/components/task/status-badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

type FeedItem = { kind: "activity"; at: string; activity: TaskActivity };

export function TaskIssuePage({ id }: { id: string }) {
  const [task, setTask] = useState<Task | null>(null);
  const [related, setRelated] = useState<Task[]>([]);
  const [blockers, setBlockers] = useState<Task[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [assignee, setAssignee] = useState<AssigneeId>("unassigned");

  useEffect(() => {
    setAssignee(loadAssignee(id));
  }, [id]);

  useEffect(() => {
    let cancelled = false;
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
    };
  }, [id]);

  function onAssigneeChange(next: AssigneeId) {
    setAssignee(next);
    saveAssignee(id, next);
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
      <div className="min-w-0 flex-1 overflow-auto">
        <div className="mx-auto max-w-3xl px-6 py-7 sm:px-10">
          <Link
            href="/project"
            className="mb-5 inline-flex items-center gap-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeft className="size-3.5" />
            Project
          </Link>

          <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1">
            <StatusBadge status={task.status} />
            <span className="font-mono text-[11px] text-muted-foreground">
              {task.id}
            </span>
            {task.kind === "task" ? (
              <div className="ml-auto">
                <RunControl taskId={task.id} />
              </div>
            ) : null}
          </div>

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
              <div className="flex items-center gap-2 rounded-md px-1.5 py-1.5 text-[13px]">
                <StatusBadge status={task.status} />
              </div>
              <AssigneePicker value={assignee} onChange={onAssigneeChange} />
            </div>
          </section>

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
            <Textarea
              placeholder="Leave a comment…"
              className="min-h-[88px] resize-none text-[13px]"
              disabled
              aria-label="Comment (coming soon)"
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
            <div className="flex items-center gap-2 rounded-md px-1.5 py-1.5 text-[13px]">
              <StatusBadge status={task.status} />
            </div>
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
