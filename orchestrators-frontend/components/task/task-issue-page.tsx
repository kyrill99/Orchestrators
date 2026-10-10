import Link from "next/link";
import { ArrowLeft, Link2, Ban, Bot, User, Activity } from "lucide-react";
import {
  getTask,
  tasks,
  type TaskActivity,
  type TaskComment,
} from "@/lib/fixtures";
import { RunControl } from "@/components/task/run-control";
import { StatusBadge } from "@/components/task/status-badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

type FeedItem =
  | { kind: "comment"; at: string; comment: TaskComment }
  | { kind: "activity"; at: string; activity: TaskActivity };

export function TaskIssuePage({ id }: { id: string }) {
  const task = getTask(id);

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

  const blockedBy = task.blockedBy
    .map((taskId) => tasks.find((t) => t.id === taskId))
    .filter(Boolean);
  const relatedTo = task.relatedTo
    .map((taskId) => tasks.find((t) => t.id === taskId))
    .filter(Boolean);

  const feed: FeedItem[] = [
    ...task.comments.map(
      (comment): FeedItem => ({
        kind: "comment",
        at: comment.at,
        comment,
      }),
    ),
    ...task.activity.map(
      (activity): FeedItem => ({
        kind: "activity",
        at: activity.at,
        activity,
      }),
    ),
  ].sort((a, b) => a.at.localeCompare(b.at));

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

          <h1 className="text-[22px] leading-snug font-semibold tracking-[-0.02em] text-foreground">
            {task.title}
          </h1>

          <section className="mt-7">
            <p className="text-[15px] leading-relaxed text-foreground/90 whitespace-pre-wrap">
              {task.description}
            </p>
          </section>

          <section className="mt-10">
            <h2 className="mb-4 text-[13px] font-medium text-foreground">
              Activity
            </h2>

            {feed.length === 0 ? (
              <p className="mb-4 text-sm text-muted-foreground">
                No activity yet. Agents and humans can discuss strategy here.
              </p>
            ) : (
              <ul className="relative mb-5 space-y-0">
                {feed.map((item, index) => (
                  <li key={`${item.kind}-${item.at}-${index}`} className="relative">
                    {index < feed.length - 1 ? (
                      <span
                        className="absolute top-8 bottom-0 left-[13px] w-px bg-border"
                        aria-hidden
                      />
                    ) : null}
                    {item.kind === "comment" ? (
                      <CommentRow comment={item.comment} />
                    ) : (
                      <ActivityRow activity={item.activity} />
                    )}
                  </li>
                ))}
              </ul>
            )}

            <div className="rounded-xl border border-border bg-background p-3 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
              <Textarea
                readOnly
                placeholder="Leave a comment… agents and humans can reply here"
                aria-label="Comment (UI mock)"
                className="min-h-[72px] resize-none border-0 bg-transparent p-0 text-[13px] shadow-none focus-visible:ring-0"
              />
              <div className="mt-2 flex justify-end">
                <Button type="button" size="sm" disabled>
                  Comment
                </Button>
              </div>
            </div>
          </section>
        </div>
      </div>

      <aside className="hidden w-[280px] shrink-0 overflow-auto border-l border-border bg-background px-5 py-6 md:block">
        <Property label="Status">
          <StatusBadge status={task.status} />
        </Property>

        <Property label="Blocked by">
          {blockedBy.length === 0 ? (
            <span className="text-[13px] text-muted-foreground">None</span>
          ) : (
            <ul className="space-y-1.5">
              {blockedBy.map((item) =>
                item ? (
                  <li key={item.id}>
                    <Link
                      href={`/tasks/${item.id}`}
                      className="inline-flex items-center gap-1.5 text-[13px] text-foreground hover:underline"
                    >
                      <Ban className="size-3 text-muted-foreground" />
                      {item.title}
                    </Link>
                  </li>
                ) : null,
              )}
            </ul>
          )}
        </Property>

        <Property label="Related to">
          {relatedTo.length === 0 ? (
            <span className="text-[13px] text-muted-foreground">None</span>
          ) : (
            <ul className="space-y-1.5">
              {relatedTo.map((item) =>
                item ? (
                  <li key={item.id}>
                    <Link
                      href={`/tasks/${item.id}`}
                      className="inline-flex items-center gap-1.5 text-[13px] text-foreground hover:underline"
                    >
                      <Link2 className="size-3 text-muted-foreground" />
                      {item.title}
                    </Link>
                  </li>
                ) : null,
              )}
            </ul>
          )}
        </Property>
      </aside>
    </div>
  );
}

function CommentRow({ comment }: { comment: TaskComment }) {
  const isAgent = comment.authorKind === "agent";

  return (
    <div className="flex gap-3 pb-5">
      <span
        className={cn(
          "relative z-[1] flex size-7 shrink-0 items-center justify-center rounded-full",
          isAgent
            ? "bg-[#5E6AD2]/15 text-[#5E6AD2]"
            : "bg-neutral-200 text-neutral-700",
        )}
        aria-hidden
      >
        {isAgent ? (
          <Bot className="size-3.5" strokeWidth={1.75} />
        ) : (
          <User className="size-3.5" strokeWidth={1.75} />
        )}
      </span>
      <div className="min-w-0 flex-1 pt-0.5">
        <div className="mb-1 flex flex-wrap items-center gap-x-2 gap-y-0.5">
          <span className="text-[13px] font-medium text-foreground">
            {comment.author}
          </span>
          <span
            className={cn(
              "rounded-full px-1.5 py-px text-[10px] font-medium",
              isAgent
                ? "bg-[#5E6AD2]/10 text-[#5E6AD2]"
                : "bg-neutral-100 text-muted-foreground",
            )}
          >
            {isAgent ? "Agent" : "Human"}
          </span>
          <span className="text-xs text-muted-foreground">
            <time dateTime={comment.at}>{formatUtc(comment.at)}</time>
          </span>
        </div>
        <p className="text-[13px] leading-relaxed text-foreground/90 whitespace-pre-wrap">
          {comment.body}
        </p>
      </div>
    </div>
  );
}

function ActivityRow({ activity }: { activity: TaskActivity }) {
  return (
    <div className="flex gap-3 pb-5">
      <span
        className="relative z-[1] flex size-7 shrink-0 items-center justify-center rounded-full bg-accent text-muted-foreground"
        aria-hidden
      >
        <Activity className="size-3.5" strokeWidth={1.75} />
      </span>
      <div className="min-w-0 flex-1 pt-0.5">
        <p className="text-[13px] text-muted-foreground">
          <span className="text-foreground/90">{activity.text}</span>
          <span className="mx-1.5 text-border">·</span>
          <time dateTime={activity.at} className="text-xs">
            {formatUtc(activity.at)}
          </time>
        </p>
      </div>
    </div>
  );
}

function Property({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-5">
      <p className="mb-1.5 text-xs text-muted-foreground">{label}</p>
      {children}
    </div>
  );
}

function formatUtc(iso: string) {
  const date = new Date(iso);
  const yyyy = date.getUTCFullYear();
  const mm = String(date.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(date.getUTCDate()).padStart(2, "0");
  const hh = String(date.getUTCHours()).padStart(2, "0");
  const min = String(date.getUTCMinutes()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd} ${hh}:${min} UTC`;
}
