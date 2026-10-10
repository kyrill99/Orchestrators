"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Loader2, Sparkles } from "lucide-react";
import { fetchEngine, type EngineWaiting } from "@/lib/api";
import { decidedBy } from "@/lib/decisions";
import type { Task } from "@/lib/fixtures";
import { projectPath } from "@/lib/project-scope";
import { useTaskGraph } from "@/lib/use-task-graph";
import { useProjectId } from "@/lib/use-project";
import { DecisionIcon } from "@/components/task/decision-icon";

function when(iso?: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/**
 * The project's decisions: what waits for a choice (with the agent's
 * recommendation, or that it is still being prepared), and what was decided,
 * by whom (a person, or the agent automatically) and when.
 */
export function DecisionLog() {
  const projectId = useProjectId();
  const { data, error } = useTaskGraph();
  const [waiting, setWaiting] = useState<EngineWaiting | null>(null);

  useEffect(() => {
    let stopped = false;
    const load = () =>
      fetchEngine()
        .then((e) => !stopped && setWaiting(e.waiting ?? null))
        .catch(() => undefined);
    void load();
    const id = window.setInterval(load, 5000);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, []);

  if (error) {
    return <p className="p-8 text-sm text-destructive">{error}</p>;
  }
  if (!data) {
    return <p className="p-8 text-sm text-muted-foreground">Loading decisions…</p>;
  }

  const all = data.tasks.filter((t) => t.kind === "decision" || t.decision);
  const open = all.filter((t) => t.decision?.state !== "resolved" && t.status !== "closed");
  const decided = all
    .filter((t) => t.decision?.state === "resolved")
    .sort((a, b) => (b.decision?.resolvedAt ?? "").localeCompare(a.decision?.resolvedAt ?? ""));
  const autoFor = new Map((waiting?.decisions ?? []).map((d) => [d.id, d]));
  const href = (t: Task) => projectPath(projectId, `/tasks/${encodeURIComponent(t.id)}`);

  return (
    <div className="h-full overflow-auto">
      <div className="mx-auto max-w-3xl px-6 py-7 sm:px-10">
        <h1 className="flex items-center gap-2 text-xl font-semibold tracking-[-0.02em] text-foreground">
          <DecisionIcon className="size-5 text-violet-600 dark:text-violet-400" />
          Decisions
        </h1>
        <p className="mt-1 text-[13px] text-muted-foreground">
          An agent prepares each decision once the work it depends on is done: options, a
          recommendation and a comparison report. While the engine runs, the agent takes the
          recommendation unless you choose first; you can change any decision later.
        </p>

        <h2 className="mt-7 mb-2 text-[13px] font-medium text-foreground">
          Waiting ({open.length})
        </h2>
        {open.length === 0 ? (
          <p className="text-[13px] text-muted-foreground">Nothing waits for a decision.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {open.map((t) => {
              const d = t.decision;
              const auto = autoFor.get(t.id);
              return (
                <li key={t.id}>
                  <Link
                    href={href(t)}
                    className="block rounded-lg border border-border px-3.5 py-3 transition-colors hover:bg-accent/40"
                  >
                    <p className="flex items-center gap-2 text-[13px] font-medium text-foreground">
                      <DecisionIcon className="size-3.5 shrink-0 text-violet-600 dark:text-violet-400" />
                      {t.title}
                    </p>
                    {t.status === "running" ? (
                      <p className="mt-1 flex items-center gap-1.5 text-xs text-[#5E6AD2]">
                        <Loader2 className="size-3 animate-spin" /> Agent is preparing this decision…
                      </p>
                    ) : d ? (
                      <>
                        <p className="mt-1 text-[13px] text-foreground/90">{d.question}</p>
                        {d.recommended ? (
                          <p className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                            <Sparkles className="size-3 text-violet-600 dark:text-violet-400" />
                            Recommends {d.recommended} · {d.options.length} options
                          </p>
                        ) : null}
                        <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
                          {d.feedback
                            ? "You sent input; it waits for your choice."
                            : auto?.auto
                              ? `The agent decides ${auto.autoInSeconds ? `in ${auto.autoInSeconds} s` : "now"} unless you choose.`
                              : "Waiting for your choice."}
                        </p>
                      </>
                    ) : (
                      <p className="mt-1 text-xs text-muted-foreground">
                        Not prepared yet: waits for the work it depends on to be closed or in review.
                      </p>
                    )}
                  </Link>
                </li>
              );
            })}
          </ul>
        )}

        <h2 className="mt-8 mb-2 text-[13px] font-medium text-foreground">
          Decided ({decided.length})
        </h2>
        {decided.length === 0 ? (
          <p className="text-[13px] text-muted-foreground">No decisions yet.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {decided.map((t) => {
              const d = t.decision!;
              return (
                <li key={t.id}>
                  <Link
                    href={href(t)}
                    className="block rounded-lg border border-border px-3.5 py-3 transition-colors hover:bg-accent/40"
                  >
                    <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
                      <DecisionIcon className="size-3.5 shrink-0" />
                      {t.title}
                    </p>
                    <p className="mt-1 flex flex-wrap items-center gap-2 text-[14px] font-medium text-foreground">
                      {d.chosen}
                      {d.custom ? (
                        <span className="rounded-full bg-muted px-1.5 text-[10px] font-medium text-muted-foreground">
                          Own answer
                        </span>
                      ) : null}
                      {d.history?.length ? (
                        <span className="rounded-full bg-amber-500/10 px-1.5 text-[10px] font-medium text-amber-700 dark:text-amber-300">
                          Changed from {d.history[d.history.length - 1].chosen}
                        </span>
                      ) : null}
                    </p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      by {decidedBy(d)}
                      {d.resolvedAt ? ` · ${when(d.resolvedAt)}` : ""}
                    </p>
                    {d.rationale ? (
                      <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{d.rationale}</p>
                    ) : null}
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
