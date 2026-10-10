"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  ChevronRight,
  Circle,
  Loader2,
  Minus,
  RefreshCw,
  X,
} from "lucide-react";
import { apiBaseUrl } from "@/lib/api";
import { ChangelogSheet } from "@/components/layout/changelog-sheet";
import { cn } from "@/lib/utils";

type StepState = "pending" | "running" | "done" | "skipped" | "failed";

type UpdateStep = {
  id: string;
  label: string;
  state: StepState;
  detail: string | null;
  started_at: string | null;
  finished_at: string | null;
};

type UpdateStatus = {
  /** Version the running backend started with. */
  commit: string | null;
  requested: boolean;
  last: {
    state: "running" | "ok" | "up_to_date" | "failed";
    from: string;
    to: string | null;
    detail: string | null;
    started_at: string;
    finished_at: string | null;
    steps?: UpdateStep[];
  } | null;
  log: string[] | null;
};

const LOG_LINES = 40;

const seconds = (from: string, to?: string | null) =>
  Math.max(
    0,
    Math.round(
      ((to ? Date.parse(to) : Date.now()) - Date.parse(from)) / 1000,
    ),
  );

const duration = (s: number) =>
  s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;

/**
 * Self-update: POST /api/admin/update, then follow the run step by step
 * (fetch, packages, build, restart, health check) with the log tail.
 */
export function UpdateControl() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  // The backend is down while it restarts; polls fail during that window.
  const [unreachable, setUnreachable] = useState(false);
  const [changelogOpen, setChangelogOpen] = useState(false);
  const [showLog, setShowLog] = useState(false);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [, setTick] = useState(0);
  // Version this page was loaded with, to offer a reload after an update.
  const [loadedCommit, setLoadedCommit] = useState<string | null>(null);
  const logRef = useRef<HTMLPreElement>(null);

  const last = status?.last ?? null;
  const busy =
    !!status && (status.requested || last?.state === "running");

  const refresh = useCallback((withLog: boolean) => {
    fetch(`${apiBaseUrl()}/api/admin/update${withLog ? `?log=${LOG_LINES}` : ""}`)
      .then((res) => (res.ok ? res.json() : Promise.reject(res.status)))
      .then((data: UpdateStatus) => {
        setLoadedCommit((c) => c ?? data.commit);
        setStatus(data);
        setUnreachable(false);
      })
      .catch(() => setUnreachable(true));
  }, []);

  const failed = last?.state === "failed";

  useEffect(() => {
    refresh(failed);
    if (!busy) return;
    const id = setInterval(() => {
      refresh(true);
      setTick((t) => t + 1); // keep elapsed times moving
    }, 1500);
    return () => clearInterval(id);
  }, [busy, failed, refresh]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [status?.log, showLog]);

  if (!status) return null;

  const update = () => {
    setDismissed(null);
    fetch(`${apiBaseUrl()}/api/admin/update`, { method: "POST" })
      .then((res) => res.json())
      .then(setStatus)
      .catch(() => {});
  };

  const newVersion =
    !!loadedCommit && !!status.commit && status.commit !== loadedCommit;
  const runKey = last?.started_at ?? null;
  const showPanel =
    !!last?.steps && (busy || ((failed || newVersion) && dismissed !== runKey));

  const headline = busy
    ? unreachable
      ? "Restarting…"
      : status.requested && last?.state !== "running"
        ? "Starting update…"
        : `Updating${last?.to ? ` to ${last.to}` : "…"}`
    : failed
      ? "Update failed"
      : last?.state === "up_to_date"
        ? "Up to date"
        : last?.state === "ok" && last.to
          ? `Updated to ${last.to}${
              last.finished_at
                ? ` in ${duration(seconds(last.started_at, last.finished_at))}`
                : ""
            }`
          : null;

  return (
    <div className="flex flex-col gap-1 px-2 text-[12px] text-muted-foreground">
      {showPanel && last?.steps ? (
        <div
          className={cn(
            "flex flex-col gap-1 rounded-md border px-2 py-1.5",
            failed && !busy
              ? "border-destructive/40 bg-destructive/5"
              : "border-black/5 bg-sidebar-accent/50",
          )}
        >
          <div className="flex items-center gap-1.5">
            <span
              className={cn(
                "truncate font-medium text-foreground",
                failed && !busy && "text-destructive",
              )}
              title={last.detail ?? headline ?? undefined}
            >
              {headline}
            </span>
            {!busy && (
              <button
                type="button"
                onClick={() => setDismissed(runKey)}
                className="ml-auto rounded p-0.5 hover:text-foreground"
                title="Dismiss"
              >
                <X className="size-3" strokeWidth={2} />
              </button>
            )}
          </div>

          <ol className="flex flex-col gap-0.5">
            {last.steps.map((step) => (
              <li
                key={step.id}
                className={cn(
                  "flex items-center gap-1.5 text-[11px]",
                  step.state === "pending" || step.state === "skipped"
                    ? "text-muted-foreground/60"
                    : step.state === "failed"
                      ? "text-destructive"
                      : "text-foreground/80",
                )}
                title={step.detail ?? undefined}
              >
                <StepIcon state={step.state} />
                <span className="truncate">{step.label}</span>
                <span className="ml-auto shrink-0 font-mono tabular-nums">
                  {step.started_at && step.state !== "skipped"
                    ? duration(seconds(step.started_at, step.finished_at))
                    : step.state === "skipped"
                      ? "skip"
                      : ""}
                </span>
              </li>
            ))}
          </ol>

          <button
            type="button"
            onClick={() => setShowLog((v) => !v)}
            className="flex items-center gap-1 self-start text-[11px] hover:text-foreground"
          >
            <ChevronRight
              className={cn("size-3 transition-transform", showLog && "rotate-90")}
              strokeWidth={2}
            />
            Log
          </button>
          {showLog && (
            <pre
              ref={logRef}
              className="max-h-40 overflow-auto rounded bg-background p-1.5 font-mono text-[10px] leading-snug whitespace-pre-wrap break-all text-foreground/80"
            >
              {status.log?.length ? status.log.join("\n") : "No output yet."}
            </pre>
          )}

          {newVersion && !busy && (
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="mt-0.5 rounded-md bg-foreground px-2 py-1 text-[11px] font-medium text-background transition-opacity hover:opacity-90"
            >
              Reload to get {status.commit}
            </button>
          )}
        </div>
      ) : (
        headline && (
          <span className="truncate" title={last?.detail ?? headline}>
            {headline}
          </span>
        )
      )}

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={update}
          disabled={busy}
          className="flex items-center gap-2 rounded-md py-1 transition-colors hover:text-foreground disabled:opacity-60"
          title="Pull the latest code, rebuild and restart"
        >
          <RefreshCw
            className={cn("size-3.5 shrink-0", busy && "animate-spin")}
            strokeWidth={1.75}
          />
          Update
        </button>
        <button
          type="button"
          onClick={() => setChangelogOpen(true)}
          className="ml-auto rounded-md py-1 font-mono text-[11px] underline-offset-2 transition-colors hover:text-foreground hover:underline"
          title="Show recent changes"
        >
          {status.commit ?? "changelog"}
        </button>
      </div>
      <ChangelogSheet open={changelogOpen} onOpenChange={setChangelogOpen} />
    </div>
  );
}

function StepIcon({ state }: { state: StepState }) {
  const cls = "size-3 shrink-0";
  switch (state) {
    case "running":
      return <Loader2 className={cn(cls, "animate-spin")} strokeWidth={2} />;
    case "done":
      return <Check className={cls} strokeWidth={2.5} />;
    case "failed":
      return <X className={cls} strokeWidth={2.5} />;
    case "skipped":
      return <Minus className={cls} strokeWidth={2} />;
    default:
      return <Circle className={cn(cls, "opacity-50")} strokeWidth={2} />;
  }
}
