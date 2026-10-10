"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Brain,
  ChevronRight,
  CircleDot,
  MessageSquare,
  Wrench,
} from "lucide-react";
import {
  fetchRunSteps,
  fetchTaskRuns,
  type RunRecord,
  type RunStep,
} from "@/lib/api";
import { subscribeRunEvents } from "@/lib/run-events";
import { cn } from "@/lib/utils";

const STEP_POLL_MS = 1500;
const RUNS_POLL_MS = 3000;

/** Live transcript of what the agent does on this task, with a picker for past runs. */
export function AgentActivity({ taskId }: { taskId: string }) {
  const [runs, setRuns] = useState<RunRecord[] | null>(null);
  const [picked, setPicked] = useState<string | null>(null);

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

  if (runs === null) return null;

  const latest = runs.length ? runs[runs.length - 1] : null;
  const shown = runs.find((r) => r.id === picked) ?? latest;
  const live = shown?.status === "running";

  return (
    <section className="mt-8">
      <div className="mb-3 flex items-center gap-2">
        <h2 className="text-[13px] font-medium text-foreground">Agent activity</h2>
        {live ? (
          <span className="inline-flex items-center gap-1.5 text-[11px] text-[#5E6AD2]">
            <span className="relative flex size-1.5">
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-[#5E6AD2]/60" />
              <span className="relative inline-flex size-1.5 rounded-full bg-[#5E6AD2]" />
            </span>
            live
          </span>
        ) : null}
        {runs.length > 1 ? (
          <select
            className="ml-auto rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground"
            value={shown?.id ?? ""}
            onChange={(e) => setPicked(e.target.value || null)}
            aria-label="Run"
          >
            {[...runs].reverse().map((run) => (
              <option key={run.id} value={run.id}>
                Attempt {run.attempt} · {run.status} · {run.agent.id}
              </option>
            ))}
          </select>
        ) : null}
      </div>
      {shown ? (
        // Keyed by run: a new run (or another pick) starts a fresh transcript.
        <RunTranscript key={shown.id} runId={shown.id} status={shown.status} />
      ) : (
        <p className="text-[13px] text-muted-foreground">
          No agent has worked on this task yet. Start one above, or start the engine.
        </p>
      )}
    </section>
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

  return (
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
