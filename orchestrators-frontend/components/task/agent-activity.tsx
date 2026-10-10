"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Brain,
  Check,
  ChevronRight,
  CircleDot,
  GitCommitHorizontal,
  MessageSquare,
  Wrench,
} from "lucide-react";
import {
  fetchRunSteps,
  fetchTaskRuns,
  type RunLanding,
  type RunRecord,
  type RunStep,
} from "@/lib/api";
import { subscribeRunEvents } from "@/lib/run-events";
import { cn } from "@/lib/utils";

const STEP_POLL_MS = 1500;
const RUNS_POLL_MS = 3000;

/** Live transcript of what the agent does on this task, one "cycle" per run. */
export function AgentActivity({ taskId }: { taskId: string }) {
  const [runs, setRuns] = useState<RunRecord[] | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [now, setNow] = useState<number>(() => Date.now());
  const [copied, setCopied] = useState<boolean>(false);

  // Runs of this task (to find the active one and offer past runs).
  useEffect(() => {
    let stopped = false;
    const load = () =>
      fetchTaskRuns(taskId)
        .then((next) => !stopped && setRuns(next))
        .catch(() => !stopped && setRuns((r) => r ?? []));
    void load();
    const id = window.setInterval(load, RUNS_POLL_MS);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, [taskId]);

  // Deep link: /tasks/<id>?run=<runId> selects that cycle.
  useEffect(() => {
    try {
      const fromUrl = new URLSearchParams(window.location.search).get("run");
      if (fromUrl) setPicked(fromUrl);
    } catch {
      // ignore malformed URLs
    }
  }, [taskId]);

  const anyRunning = Boolean(runs?.some((r) => r.status === "running"));
  useEffect(() => {
    if (!anyRunning) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [anyRunning]);

  const fallback: RunRecord | null = useMemo(() => {
    if (!runs || !runs.length) return null;
    const running = [...runs].reverse().find((r) => r.status === "running");
    return running ?? runs[runs.length - 1];
  }, [runs]);
  const shown: RunRecord | null = (runs ?? []).find((r) => r.id === picked) ?? fallback;
  const shownId: string | null = shown ? shown.id : null;

  // Keep ?run= in sync with the selected cycle, preserving any other params.
  useEffect(() => {
    if (runs === null || !shownId) return;
    try {
      const url = new URL(window.location.href);
      if (url.searchParams.get("run") === shownId) return;
      url.searchParams.set("run", shownId);
      window.history.replaceState(window.history.state, "", url.toString());
    } catch {
      // ignore
    }
  }, [runs, shownId]);

  if (runs === null) return null;

  const live = shown?.status === "running";

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  return (
    <section className="mt-8">
      <div className="mb-3 flex items-center gap-2">
        <h2 className="text-[13px] font-medium text-foreground">Agent activity</h2>
        {runs.length ? (
          <span className="text-[11px] text-muted-foreground">
            {runs.length} {runs.length === 1 ? "cycle" : "cycles"}
          </span>
        ) : null}
        {live ? (
          <span className="inline-flex items-center gap-1.5 text-[11px] text-[#5E6AD2]">
            <span className="relative flex size-1.5">
              <span className="absolute inline-flex size-full rounded-full bg-[#5E6AD2]/60 motion-safe:animate-ping" />
              <span className="relative inline-flex size-1.5 rounded-full bg-[#5E6AD2]" />
            </span>
            live
          </span>
        ) : null}
      </div>
      {shown ? (
        <>
          <ol className="mb-4 flex flex-col overflow-hidden rounded-xl border border-border bg-background">
            {[...runs].reverse().map((run) => (
              <li key={run.id} className="border-b border-border/70 last:border-b-0">
                <CycleRow
                  run={run}
                  selected={run.id === shownId}
                  now={now}
                  onSelect={() => setPicked(run.id)}
                />
              </li>
            ))}
          </ol>
          <div className="mb-2 flex items-center gap-2">
            <StatusDot status={shown.status} />
            <h3 className="text-[12px] font-medium text-foreground">
              Cycle {shown.attempt} transcript
            </h3>
            <span className="text-[11px] text-muted-foreground">{statusText(shown.status)}</span>
            <button
              type="button"
              onClick={() => void copyLink()}
              className="ml-auto inline-flex items-center gap-1 rounded-md border border-border bg-background px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-accent hover:text-foreground"
              title="Copy a link to this cycle"
            >
              {copied ? <Check className="size-3" strokeWidth={2} /> : null}
              {copied ? "Copied" : "Copy link"}
            </button>
          </div>
          {/* Keyed by run: a new run (or another pick) starts a fresh transcript. */}
          <RunTranscript key={shown.id} runId={shown.id} status={shown.status} />
        </>
      ) : (
        <p className="text-[13px] text-muted-foreground">
          No agent has worked on this task yet. Start one above, or start the engine.
        </p>
      )}
    </section>
  );
}

function statusText(status: RunRecord["status"]): string {
  switch (status) {
    case "running":
      return "Running";
    case "succeeded":
      return "Succeeded";
    case "failed":
      return "Failed";
    case "lost":
      return "Lost";
    case "stopped":
      return "Stopped";
    default:
      return String(status);
  }
}

function StatusDot({ status }: { status: RunRecord["status"] }) {
  if (status === "running") {
    return (
      <span className="relative flex size-2 shrink-0">
        <span className="absolute inline-flex size-full rounded-full bg-[#5E6AD2]/60 motion-safe:animate-ping" />
        <span className="relative inline-flex size-2 rounded-full bg-[#5E6AD2]" />
      </span>
    );
  }
  const color: string =
    status === "succeeded"
      ? "bg-emerald-500"
      : status === "failed" || status === "lost"
        ? "bg-red-500"
        : "bg-zinc-400";
  return <span className={cn("inline-flex size-2 shrink-0 rounded-full", color)} />;
}

function duration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function startLabel(at: string): string {
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return at;
  return d.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function clip(text: string, max: number): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max)}…` : flat;
}

function CycleRow({
  run,
  selected,
  now,
  onSelect,
}: {
  run: RunRecord;
  selected: boolean;
  now: number;
  onSelect: () => void;
}) {
  const started: number = new Date(run.startedAt).getTime();
  const ended: number | null = run.endedAt ? new Date(run.endedAt).getTime() : null;
  const running = run.status === "running";
  const span: string = Number.isNaN(started)
    ? ""
    : running
      ? `running for ${duration(now - started)}`
      : ended !== null && !Number.isNaN(ended)
        ? duration(ended - started)
        : "";
  const instructions: string = run.instructions ? clip(run.instructions, 160) : "";
  const note: string = run.note ? clip(run.note, 200) : "";
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={selected ? "true" : undefined}
      className={cn(
        "flex w-full flex-col gap-1 px-3.5 py-2.5 text-left transition-colors",
        selected ? "bg-[#5E6AD2]/[0.07] shadow-[inset_2px_0_0_#5E6AD2]" : "hover:bg-accent/60",
      )}
    >
      <span className="flex w-full items-center gap-2 text-[13px]">
        <StatusDot status={run.status} />
        <span className={cn("font-medium", selected ? "text-[#5E6AD2]" : "text-foreground")}>
          Cycle {run.attempt}
        </span>
        <span className="text-[11px] text-muted-foreground">{statusText(run.status)}</span>
        <span className="min-w-0 truncate font-mono text-[11px] text-muted-foreground">
          {run.agent.id}
        </span>
        <span className="ml-auto shrink-0 pl-2 text-[11px] text-muted-foreground tabular-nums">
          {startLabel(run.startedAt)}
          {span ? ` · ${span}` : ""}
        </span>
      </span>
      {instructions ? (
        <span className="pl-4 text-xs text-muted-foreground">
          <span className="font-medium text-foreground/80">
            {run.attempt > 1 ? "Sent back: " : "Instructions: "}
          </span>
          {instructions}
        </span>
      ) : null}
      {note ? (
        <span className="pl-4 text-xs text-muted-foreground">
          <span className="font-medium text-foreground/80">Summary: </span>
          {note}
        </span>
      ) : null}
    </button>
  );
}

/** Steps of one run, polled with a cursor while the run is active. */
function RunTranscript({ runId, status }: { runId: string; status: string }) {
  const [steps, setSteps] = useState<RunStep[]>([]);
  const [active, setActive] = useState(status === "running");
  const [error, setError] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const cursor = useRef(0);

  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    let busy = false;
    let again = false;
    const poll = async () => {
      if (busy) {
        again = true; // an SSE nudge arrived mid-fetch: fetch once more after
        return;
      }
      busy = true;
      window.clearTimeout(timer);
      let delay: number | null = STEP_POLL_MS * 2;
      try {
        const page = await fetchRunSteps(runId, cursor.current);
        if (stopped) return;
        cursor.current = page.next;
        if (page.steps.length) setSteps((prev) => [...prev, ...page.steps]);
        setActive(page.active);
        setError(null);
        // Drain a backlog right away; otherwise poll while the run is active
        // (SSE nudges usually come first).
        delay = page.steps.length ? 0 : page.active ? STEP_POLL_MS : null;
      } catch (err) {
        if (stopped) return;
        setError(err instanceof Error ? err.message : "Could not load steps");
      } finally {
        busy = false;
      }
      if (again) {
        again = false;
        delay = 0;
      }
      if (!stopped && delay !== null) timer = window.setTimeout(poll, delay);
    };
    void poll();
    const unsubscribe = subscribeRunEvents((event) => {
      if (event.type === "resync" || (event.type === "step" && event.data?.runId === runId)) {
        void poll();
      }
    });
    return () => {
      stopped = true;
      unsubscribe();
      window.clearTimeout(timer);
    };
    // `status` changes when the run ends elsewhere: fetch the last steps.
  }, [runId, status]);

  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [steps]);

  const items = useMemo(() => group(steps), [steps]);
  const landing = useMemo(() => {
    const step = [...steps].reverse().find((s) => s.kind === "landing");
    return step ? (step.payload as unknown as RunLanding) : null;
  }, [steps]);

  return (
    <>
      {landing?.state === "pushed" ? <PushedCommits landing={landing} /> : null}
      <div
        ref={scroller}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
        className="max-h-[520px] overflow-auto rounded-xl border border-border bg-background"
      >
        <ol className="flex flex-col divide-y divide-border/70">
          {items.map((item) => (
            <li key={item.step.seq} className="px-3.5 py-2.5">
              <StepView item={item} />
            </li>
          ))}
          {active ? (
            <li className="flex items-center gap-2 px-3.5 py-2.5 text-xs text-muted-foreground">
              <span className="flex gap-0.5">
                <span className="size-1 animate-bounce rounded-full bg-muted-foreground [animation-delay:-0.3s]" />
                <span className="size-1 animate-bounce rounded-full bg-muted-foreground [animation-delay:-0.15s]" />
                <span className="size-1 animate-bounce rounded-full bg-muted-foreground" />
              </span>
              Working…
            </li>
          ) : null}
          {!items.length && !active ? (
            <li className="px-3.5 py-2.5 text-xs text-muted-foreground">
              This run recorded no steps (it was not executed by an in-process agent).
            </li>
          ) : null}
        </ol>
        {error ? <p className="px-3.5 pb-2 text-xs text-destructive">{error}</p> : null}
      </div>
    </>
  );
}

/** Commits this run pushed to the project's repository, and the files they changed. */
function PushedCommits({ landing }: { landing: RunLanding }) {
  const commits = landing.commits ?? [];
  const files = landing.files ?? [];
  return (
    <div className="mb-3 rounded-xl border border-border bg-background px-3.5 py-2.5 text-[13px]">
      <div className="mb-1.5 flex items-center gap-2">
        <GitCommitHorizontal className="size-3.5 shrink-0 text-emerald-600" strokeWidth={1.75} />
        <span className="font-medium text-foreground">
          Pushed {commits.length} {commits.length === 1 ? "commit" : "commits"} to{" "}
          {landing.branch ?? "main"}
        </span>
        <span className="text-[11px] text-muted-foreground tabular-nums">
          <span className="text-emerald-600">+{landing.added ?? 0}</span>{" "}
          <span className="text-red-600">-{landing.removed ?? 0}</span>
        </span>
        {landing.compareUrl ? (
          <a
            href={landing.compareUrl}
            target="_blank"
            rel="noreferrer"
            className="ml-auto text-[11px] text-[#5E6AD2] hover:underline"
          >
            Compare
          </a>
        ) : null}
      </div>
      <ul className="flex flex-col gap-0.5">
        {commits.map((c) => (
          <li key={c.sha} className="flex min-w-0 items-baseline gap-2">
            {c.url ? (
              <a
                href={c.url}
                target="_blank"
                rel="noreferrer"
                className="shrink-0 font-mono text-[11px] text-[#5E6AD2] hover:underline"
              >
                {c.short.slice(0, 7)}
              </a>
            ) : (
              <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
                {c.short.slice(0, 7)}
              </span>
            )}
            <span className="min-w-0 truncate text-foreground">{c.subject}</span>
          </li>
        ))}
      </ul>
      {files.length ? (
        <details className="mt-1.5">
          <summary className="cursor-pointer text-[11px] text-muted-foreground">
            {files.length} {files.length === 1 ? "file" : "files"} changed
          </summary>
          <ul className="mt-1 flex flex-col gap-0.5 font-mono text-[11px]">
            {files.map((f) => (
              <li key={f.path} className="flex min-w-0 gap-2">
                <span className="min-w-0 truncate text-foreground">{f.path}</span>
                <span className="ml-auto shrink-0 tabular-nums">
                  {f.added === null ? (
                    <span className="text-muted-foreground">binary</span>
                  ) : (
                    <>
                      <span className="text-emerald-600">+{f.added}</span>{" "}
                      <span className="text-red-600">-{f.removed}</span>
                    </>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

type Item = { step: RunStep; result?: RunStep };

/** Pair each tool call with its result. */
function group(steps: RunStep[]): Item[] {
  const items: Item[] = [];
  const open = new Map<string, Item>();
  for (const step of steps) {
    const key = step.payload.callId ?? "";
    if (step.kind === "tool_result" && key && open.has(key)) {
      open.get(key)!.result = step;
      open.delete(key);
      continue;
    }
    const item: Item = { step };
    if (step.kind === "tool_call" && key) open.set(key, item);
    items.push(item);
  }
  return items;
}

function time(at: string) {
  const d = new Date(at);
  return Number.isNaN(d.getTime())
    ? at
    : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function pretty(value: unknown): string {
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  return JSON.stringify(value ?? {}, null, 2);
}

function summarizeArgs(args: unknown): string {
  if (!args || typeof args !== "object") return "";
  const parts = Object.entries(args as Record<string, unknown>).map(([k, v]) => {
    const s = typeof v === "string" ? v : JSON.stringify(v);
    return `${k}: ${s.length > 60 ? `${s.slice(0, 60)}…` : s}`;
  });
  const out = parts.join(", ");
  return out.length > 140 ? `${out.slice(0, 140)}…` : out;
}

function StepView({ item }: { item: Item }) {
  const { step, result } = item;
  const p = step.payload;
  switch (step.kind) {
    case "message":
      return (
        <div className="flex gap-2.5">
          <MessageSquare className="mt-0.5 size-3.5 shrink-0 text-[#5E6AD2]" strokeWidth={1.75} />
          <p className="min-w-0 flex-1 text-[13px] leading-relaxed whitespace-pre-wrap text-foreground">
            {p.text}
          </p>
          <Time at={step.at} />
        </div>
      );
    case "thinking":
      return (
        <details className="group">
          <summary className="flex cursor-pointer list-none items-center gap-2.5 text-xs text-muted-foreground">
            <Brain className="size-3.5 shrink-0" strokeWidth={1.75} />
            <span className="truncate italic">{firstLine(p.text)}</span>
            <Time at={step.at} />
          </summary>
          <p className="mt-2 ml-6 text-xs leading-relaxed whitespace-pre-wrap text-muted-foreground">
            {p.text}
          </p>
        </details>
      );
    case "tool_call":
    case "tool_result": {
      const failed = Boolean(result?.payload.isError ?? (step.kind === "tool_result" && p.isError));
      return (
        <details className="group">
          <summary className="flex cursor-pointer list-none items-center gap-2.5 text-[13px]">
            <ChevronRight className="size-3.5 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" />
            <Wrench
              className={cn("size-3.5 shrink-0", failed ? "text-destructive" : "text-muted-foreground")}
              strokeWidth={1.75}
            />
            <span className="font-mono text-[12px] text-foreground">{p.name}</span>
            {p.builtin ? (
              <span className="rounded bg-accent px-1 text-[10px] text-muted-foreground">built-in</span>
            ) : null}
            <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
              {step.kind === "tool_call" ? summarizeArgs(p.args) : "result"}
            </span>
            {step.kind === "tool_call" && !result ? (
              <span className="text-[11px] text-muted-foreground">running…</span>
            ) : null}
            {failed ? <span className="text-[11px] text-destructive">error</span> : null}
            <Time at={step.at} />
          </summary>
          <div className="mt-2 ml-6 flex flex-col gap-2">
            {step.kind === "tool_call" ? <Block label="Arguments" text={pretty(p.args)} /> : null}
            {result || step.kind === "tool_result" ? (
              <Block
                label="Result"
                text={pretty((result ?? step).payload.result ?? "")}
                error={failed}
              />
            ) : null}
          </div>
        </details>
      );
    }
    case "landing": {
      const landing = p as unknown as RunLanding;
      const pushed = landing.state === "pushed";
      const n = landing.commits?.length ?? 0;
      return (
        <div
          className={cn(
            "flex gap-2.5 text-[13px]",
            pushed ? "text-foreground" : "text-destructive",
          )}
        >
          <GitCommitHorizontal className="mt-0.5 size-3.5 shrink-0" strokeWidth={1.75} />
          <p className="min-w-0 flex-1 whitespace-pre-wrap">
            {pushed
              ? `Pushed ${n} ${n === 1 ? "commit" : "commits"} to ${landing.branch ?? "main"}`
              : `Not pushed: ${landing.error ?? "landing failed"}`}
          </p>
          <Time at={step.at} />
        </div>
      );
    }
    case "error":
      return (
        <div className="flex gap-2.5 text-[13px] text-destructive">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" strokeWidth={1.75} />
          <p className="min-w-0 flex-1 whitespace-pre-wrap">{p.message ?? p.text}</p>
          <Time at={step.at} />
        </div>
      );
    default:
      return (
        <div className="flex items-center gap-2.5 text-xs text-muted-foreground">
          <CircleDot className="size-3.5 shrink-0" strokeWidth={1.75} />
          <span className="min-w-0 flex-1 truncate">
            <span className="font-medium text-foreground">{statusLabel(p.state)}</span>
            {p.note ? ` · ${p.note}` : ""}
            {p.state === "started" && p.model ? ` · ${String(p.model)}` : ""}
          </span>
          <Time at={step.at} />
        </div>
      );
  }
}

function statusLabel(state?: string) {
  switch (state) {
    case "started":
      return "Run started";
    case "succeeded":
      return "Finished";
    case "failed":
      return "Failed";
    case "stopped":
      return "Stopped";
    case "scratchpad_edited":
      return "Scratchpad edited";
    default:
      return state ?? "Status";
  }
}

function firstLine(text?: string) {
  const line = (text ?? "").split("\n").find((l) => l.trim()) ?? "Thinking";
  return line.replace(/\*\*/g, "");
}

function Time({ at }: { at: string }) {
  return <span className="ml-auto shrink-0 pl-2 text-[11px] text-muted-foreground tabular-nums">{time(at)}</span>;
}

function Block({ label, text, error }: { label: string; text: string; error?: boolean }) {
  return (
    <div>
      <p className="mb-1 text-[10px] font-medium tracking-wide text-muted-foreground uppercase">{label}</p>
      <pre
        className={cn(
          "max-h-64 overflow-auto rounded-md bg-accent/60 px-2.5 py-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap",
          error ? "text-destructive" : "text-foreground",
        )}
      >
        {text}
      </pre>
    </div>
  );
}
