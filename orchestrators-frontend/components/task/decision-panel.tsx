"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Check, FileText, Loader2, MessageSquareReply, Pencil, Sparkles } from "lucide-react";
import {
  apiBaseUrl,
  fetchEngine,
  fetchTaskArtifacts,
  type EngineWaiting,
  type TaskArtifactFile,
} from "@/lib/api";
import { decide, decidedBy, sendDecisionInput, type TaskDecision } from "@/lib/decisions";
import { statusLabel, type Task } from "@/lib/fixtures";
import { projectPath } from "@/lib/project-scope";
import { cn } from "@/lib/utils";
import { Markdown } from "@/components/issues/markdown";
import { DecisionIcon } from "@/components/task/decision-icon";
import { StatusIcon } from "@/components/task/status-icon";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

const OTHER = "\u0000other";

function when(iso?: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** A blocker counts as done for a decision when it is closed or in review. */
function blockerDone(t: Task): boolean {
  return t.status === "closed" || t.status === "in-review";
}

/**
 * The decision on a decision task: what the agent prepared (question, options
 * with the recommended one highlighted, why), the owner's choice or own answer,
 * input sent back to the preparation, and after it is decided, who decided and
 * a way to change it.
 */
export function DecisionPanel({
  task,
  blockers,
  projectId,
  onChanged,
}: {
  task: Task;
  blockers: Task[];
  projectId: string | null;
  onChanged: () => void;
}) {
  const decision = task.decision ?? null;
  const preparing = task.status === "running";
  const [waiting, setWaiting] = useState<EngineWaiting | null>(null);
  const [changing, setChanging] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

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

  const entry = waiting?.decisions.find((d) => d.id === task.id);
  const done = (text: string) => {
    setMessage(text);
    setChanging(false);
    onChanged();
  };

  return (
    <section className="mb-5 rounded-lg border border-border bg-muted/30 p-4">
      <div className="mb-3 flex items-center gap-2 text-[13px] font-medium text-foreground">
        <DecisionIcon className="size-4 text-violet-600 dark:text-violet-400" />
        Decision
        <span className="ml-auto text-[11px] font-normal text-muted-foreground">
          {preparing
            ? "Agent is preparing…"
            : decision?.state === "resolved"
              ? "Decided"
              : decision
                ? "Waiting for a choice"
                : "Not prepared yet"}
        </span>
      </div>

      {preparing ? (
        <p className="mb-3 flex items-center gap-2 rounded-md bg-[#5E6AD2]/10 px-3 py-2 text-[13px] text-[#5E6AD2]">
          <Loader2 className="size-3.5 animate-spin" />
          Agent is preparing this decision
          {decision?.feedback ? " again with your input" : ""}… Its live steps are below.
        </p>
      ) : null}

      {!decision ? (
        <NotPrepared task={task} blockers={blockers} preparing={preparing} onDone={done} />
      ) : decision.state === "resolved" && !changing ? (
        <Decided decision={decision} onChange={() => setChanging(true)} />
      ) : (
        <>
          <Prepared decision={decision} />
          {decision.state === "pending" ? (
            <AutoNote entry={entry} decision={decision} />
          ) : null}
          <ChooseForm
            taskId={task.id}
            decision={decision}
            changing={decision.state === "resolved"}
            onCancel={decision.state === "resolved" ? () => setChanging(false) : undefined}
            onDone={done}
          />
          {decision.state === "pending" && !preparing ? (
            <SendInput taskId={task.id} onDone={done} />
          ) : null}
        </>
      )}

      {message ? <p className="mt-2 text-xs text-muted-foreground">{message}</p> : null}
      <BasedOn blockers={blockers} projectId={projectId} />
    </section>
  );
}

function NotPrepared({
  task,
  blockers,
  preparing,
  onDone,
}: {
  task: Task;
  blockers: Task[];
  preparing: boolean;
  onDone: (text: string) => void;
}) {
  const open = blockers.filter((b) => !blockerDone(b));
  if (preparing) return null;
  return (
    <>
      <p className="mb-3 text-[13px] text-muted-foreground">
        {open.length
          ? `Waits for ${open.map((b) => `${b.title} (${statusLabel[b.status]})`).join(", ")}. `
          : ""}
        An agent prepares this decision (question, options and a recommendation) once
        everything it depends on is closed or in review
        {open.length ? "." : "; the engine does this while it runs."} You can also answer it
        yourself now.
      </p>
      <ChooseForm
        taskId={task.id}
        decision={{ question: task.title, options: [], state: "pending" }}
        changing={false}
        onDone={onDone}
      />
    </>
  );
}

function Prepared({ decision }: { decision: TaskDecision }) {
  return (
    <>
      <p className="mb-3 text-[15px] leading-snug font-medium text-foreground">
        {decision.question}
      </p>
      {decision.recommended ? (
        <div className="mb-3 rounded-md border-l-2 border-violet-500 bg-violet-500/[0.06] px-3 py-2">
          <p className="flex items-center gap-1.5 text-[13px] font-medium text-foreground">
            <Sparkles className="size-3.5 text-violet-600 dark:text-violet-400" />
            Agent recommends: {decision.recommended}
          </p>
          {decision.recommendation ? (
            <p className="mt-1 text-[13px] leading-relaxed text-muted-foreground">
              {decision.recommendation}
            </p>
          ) : null}
        </div>
      ) : null}
      {decision.feedback ? (
        <p className="mb-3 text-xs text-muted-foreground">
          Your input: “{decision.feedback}”
        </p>
      ) : null}
      {decision.context ? (
        <details className="mb-3 text-[13px]">
          <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
            What the options rest on
          </summary>
          <Markdown source={decision.context} className="mt-2 text-[13px]" />
        </details>
      ) : null}
    </>
  );
}

function AutoNote({
  entry,
  decision,
}: {
  entry?: EngineWaiting["decisions"][number];
  decision: TaskDecision;
}) {
  if (decision.feedback) {
    return (
      <p className="mb-3 text-xs text-muted-foreground">
        You sent input on this decision, so the agent leaves the choice to you.
      </p>
    );
  }
  if (!entry?.auto) return null;
  const inSeconds = entry.autoInSeconds ?? 0;
  return (
    <p className="mb-3 text-xs text-amber-700 dark:text-amber-400">
      The engine is running: the agent takes the recommendation{" "}
      {inSeconds > 0 ? `in ${inSeconds} s` : "now"} unless you choose first. You can change it
      later.
    </p>
  );
}

function ChooseForm({
  taskId,
  decision,
  changing,
  onCancel,
  onDone,
}: {
  taskId: string;
  decision: TaskDecision;
  changing: boolean;
  onCancel?: () => void;
  onDone: (text: string) => void;
}) {
  const options = decision.options ?? [];
  const [chosen, setChosen] = useState<string>(options.length ? "" : OTHER);
  const [other, setOther] = useState("");
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const answer = chosen === OTHER ? other.trim() : chosen;

  const submit = () => {
    setBusy(true);
    setError(null);
    decide(taskId, answer, rationale)
      .then((r) => {
        const parts = [changing ? `Changed to ${answer}.` : `Decided: ${answer}.`];
        if (r.affected?.length) parts.push(`Redoing: ${r.affected.join(", ")}.`);
        if (r.started?.length) parts.push(`Started: ${r.started.join(", ")}.`);
        onDone(parts.join(" "));
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <div>
      {options.length ? (
        <div className="mb-3 grid gap-2 sm:grid-cols-2">
          {options.map((opt) => {
            const recommended = opt === decision.recommended;
            const current = decision.state === "resolved" && opt === decision.chosen;
            return (
              <button
                key={opt}
                type="button"
                onClick={() => setChosen(opt)}
                aria-pressed={chosen === opt}
                className={cn(
                  "flex min-h-14 flex-col items-start gap-1 rounded-lg border bg-background px-3 py-2.5 text-left text-[13px] transition-colors",
                  recommended
                    ? "border-violet-500/50"
                    : "border-black/[0.08] dark:border-white/[0.1]",
                  chosen === opt
                    ? "ring-2 ring-[#5E6AD2]/50"
                    : "hover:border-black/[0.18] dark:hover:border-white/[0.2]",
                )}
              >
                <span className="font-medium text-foreground">{opt}</span>
                <span className="flex gap-1.5">
                  {recommended ? (
                    <span className="rounded-full bg-violet-500/10 px-1.5 text-[10px] font-medium text-violet-700 dark:text-violet-300">
                      Recommended
                    </span>
                  ) : null}
                  {current ? (
                    <span className="rounded-full bg-emerald-500/10 px-1.5 text-[10px] font-medium text-emerald-700 dark:text-emerald-300">
                      Current
                    </span>
                  ) : null}
                </span>
              </button>
            );
          })}
          <button
            type="button"
            onClick={() => setChosen(OTHER)}
            aria-pressed={chosen === OTHER}
            className={cn(
              "flex min-h-14 items-center rounded-lg border border-dashed border-black/[0.12] bg-background px-3 py-2.5 text-left text-[13px] text-muted-foreground transition-colors dark:border-white/[0.15]",
              chosen === OTHER ? "ring-2 ring-[#5E6AD2]/50" : "hover:text-foreground",
            )}
          >
            Other… (your own answer)
          </button>
        </div>
      ) : null}
      {chosen === OTHER ? (
        <Input
          value={other}
          onChange={(e) => setOther(e.target.value)}
          placeholder="Your answer"
          className="mb-2 text-sm"
        />
      ) : null}
      <Textarea
        value={rationale}
        onChange={(e) => setRationale(e.target.value)}
        placeholder="Why? (optional; goes into the decision log and the next tasks' briefs)"
        className="mb-3 min-h-14 text-sm"
      />
      {changing ? (
        <p className="mb-2 text-xs text-muted-foreground">
          Tasks after this decision that already started are redone with the new choice.
        </p>
      ) : null}
      <div className="flex gap-2">
        <Button size="sm" disabled={busy || !answer} onClick={submit}>
          <Check className="size-3.5" /> {changing ? "Change decision" : "Decide"}
        </Button>
        {onCancel ? (
          <Button size="sm" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        ) : null}
      </div>
      {error ? <p className="mt-2 text-xs text-destructive">{error}</p> : null}
    </div>
  );
}

function SendInput({ taskId, onDone }: { taskId: string; onDone: (text: string) => void }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="mt-3 inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
      >
        <MessageSquareReply className="size-3.5" />
        Not ready to choose? Send input back (another option, another criterion)
      </button>
    );
  }
  return (
    <div className="mt-4 border-t border-border pt-3">
      <Textarea
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="e.g. Add an option that combines classic and squad, or compare them on retention too"
        className="mb-2 min-h-14 text-sm"
      />
      <div className="flex gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={busy || !note.trim()}
          onClick={() => {
            setBusy(true);
            setError(null);
            sendDecisionInput(taskId, note)
              .then(() => onDone("Sent. The agent prepares the decision again with your input."))
              .catch((e: Error) => setError(e.message))
              .finally(() => setBusy(false));
          }}
        >
          <MessageSquareReply className="size-3.5" /> Prepare again with this
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
      {error ? <p className="mt-2 text-xs text-destructive">{error}</p> : null}
    </div>
  );
}

function Decided({ decision, onChange }: { decision: TaskDecision; onChange: () => void }) {
  const history = decision.history ?? [];
  return (
    <div>
      <p className="text-xs text-muted-foreground">{decision.question}</p>
      <p className="mt-1 flex flex-wrap items-center gap-2 text-[15px] font-medium text-foreground">
        Decided: {decision.chosen}
        {decision.custom ? (
          <span className="rounded-full bg-muted px-1.5 text-[10px] font-medium text-muted-foreground">
            Own answer
          </span>
        ) : decision.chosen === decision.recommended ? (
          <span className="rounded-full bg-violet-500/10 px-1.5 text-[10px] font-medium text-violet-700 dark:text-violet-300">
            Recommended
          </span>
        ) : null}
      </p>
      <p className="mt-0.5 text-xs text-muted-foreground">
        by {decidedBy(decision)}
        {decision.resolvedAt ? ` · ${when(decision.resolvedAt)}` : ""}
      </p>
      {decision.rationale ? (
        <p className="mt-2 text-[13px] leading-relaxed whitespace-pre-wrap text-muted-foreground">
          {decision.rationale}
        </p>
      ) : null}
      {history.length ? (
        <ul className="mt-2 text-xs text-muted-foreground">
          {history.map((h, i) => (
            <li key={`${h.resolvedAt}-${i}`}>
              Earlier: {h.chosen} (by {decidedBy(h)}
              {h.resolvedAt ? `, ${when(h.resolvedAt)}` : ""})
            </li>
          ))}
        </ul>
      ) : null}
      <Button size="sm" variant="outline" className="mt-3" onClick={onChange}>
        <Pencil className="size-3.5" /> Change decision
      </Button>
    </div>
  );
}

/** The tasks this decision waited for, with links to their pages and reports. */
function BasedOn({ blockers, projectId }: { blockers: Task[]; projectId: string | null }) {
  const [reports, setReports] = useState<Record<string, TaskArtifactFile[]>>({});
  const ids = blockers.map((b) => b.id).join(",");
  useEffect(() => {
    let stopped = false;
    const list = ids ? ids.split(",") : [];
    Promise.all(
      list.map((id) =>
        fetchTaskArtifacts(id)
          .then((a) => [id, a] as const)
          .catch(() => [id, [] as TaskArtifactFile[]] as const),
      ),
    ).then((pairs) => !stopped && setReports(Object.fromEntries(pairs)));
    return () => {
      stopped = true;
    };
  }, [ids]);
  if (!blockers.length) return null;
  return (
    <div className="mt-4 border-t border-border pt-3">
      <p className="mb-1.5 text-[11px] font-medium text-muted-foreground">Based on</p>
      <ul className="flex flex-col gap-1">
        {blockers.map((b) => {
          const files = latestPerFile(reports[b.id] ?? []);
          return (
            <li key={b.id} className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[13px]">
              <Link
                href={projectPath(projectId, `/tasks/${encodeURIComponent(b.id)}`)}
                className="inline-flex items-center gap-1.5 text-foreground hover:underline"
              >
                <StatusIcon status={b.status} />
                {b.title}
              </Link>
              <span className="text-[11px] text-muted-foreground">{statusLabel[b.status]}</span>
              {files.map((f) => (
                <a
                  key={f.id}
                  href={`${apiBaseUrl()}${f.viewUrl}`}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                >
                  <FileText className="size-3" />
                  {f.title || f.filename}
                </a>
              ))}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function latestPerFile(items: TaskArtifactFile[]): TaskArtifactFile[] {
  const byName = new Map<string, TaskArtifactFile>();
  for (const a of items) {
    const prev = byName.get(a.filename);
    if (!prev || prev.createdAt <= a.createdAt) byName.set(a.filename, a);
  }
  return [...byName.values()];
}
