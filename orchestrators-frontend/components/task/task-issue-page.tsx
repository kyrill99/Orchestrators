import Link from "next/link";
import { ArrowLeft, Link2, Ban } from "lucide-react";
import { getTask, tasks } from "@/lib/fixtures";
import { StatusBadge } from "@/components/task/status-badge";
import { Separator } from "@/components/ui/separator";

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
          </div>

          <h1 className="text-[22px] leading-snug font-semibold tracking-[-0.02em] text-foreground">
            {task.title}
          </h1>

          <section className="mt-7">
            <p className="text-[15px] leading-relaxed text-foreground/90 whitespace-pre-wrap">
              {task.description}
            </p>
          </section>

          <Separator className="my-8" />

          <section>
            <h2 className="mb-3 text-[13px] font-medium text-foreground">
              Activity
            </h2>
            {task.activity.length === 0 ? (
              <p className="text-sm text-muted-foreground">No activity yet.</p>
            ) : (
              <ul className="space-y-3">
                {task.activity.map((item) => (
                  <li key={item.id} className="flex gap-3 text-sm">
                    <span className="mt-2 size-1.5 shrink-0 rounded-full bg-muted-foreground/40" />
                    <div className="min-w-0">
                      <p className="text-foreground">{item.text}</p>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        <time dateTime={item.at}>{formatUtc(item.at)}</time>
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
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
