"use client";

import { useCallback, useEffect, useState } from "react";
import { Bot, Play, RotateCw, Square } from "lucide-react";
import { controlTaskRun, fetchTaskRun, type TaskRunState } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type Action = "start" | "stop" | "restart";

/** Which agent works on this task, with start / stop / restart (live backend graph). */
export function RunControl({
  taskId,
  onStatusChange,
}: {
  taskId: string;
  /** Fired when a run action changes the task's graph status. */
  onStatusChange?: (status: string) => void;
}) {
  const [state, setState] = useState<TaskRunState | null>(null);
  const [pending, setPending] = useState<Action | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    fetchTaskRun(taskId).then((next) => {
      setState(next);
      if (next.kind === "live") onStatusChange?.(next.status);
    });
  }, [taskId, onStatusChange]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  if (state === null) return null;
  if (state.kind !== "live") {
    return (
      <span className="text-xs text-muted-foreground">
        {state.kind === "offline"
          ? "Agent runs unavailable (backend offline)"
          : "Not in the live task graph"}
      </span>
    );
  }

  const run = state.activeRun;
  const act = (action: Action) => {
    setPending(action);
    setError(null);
    controlTaskRun(taskId, action)
      .catch((e: Error) => setError(e.message))
      .finally(() => {
        setPending(null);
        refresh();
      });
  };

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-2">
        {run ? (
          <span
            className="inline-flex items-center gap-1.5 text-[13px] text-foreground"
            title={
              run.runner === "record"
                ? "Tracked only: no process was launched for this run"
                : `${run.runner} run ${run.id}, attempt ${run.attempt}`
            }
          >
            <span className="relative flex size-2">
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-[#5E6AD2]/60" />
              <span className="relative inline-flex size-2 rounded-full bg-[#5E6AD2]" />
            </span>
            <Bot className="size-3.5 text-[#5E6AD2]" strokeWidth={1.75} />
            {run.agent.id}
            <span className="text-xs text-muted-foreground">
              {run.runner === "record" ? "tracked" : "running"}
            </span>
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">No agent running</span>
        )}
        {run ? (
          <>
            <RunButton action="restart" pending={pending} onClick={act} />
            <RunButton action="stop" pending={pending} onClick={act} />
          </>
        ) : (
          <RunButton
            action="start"
            pending={pending}
            onClick={act}
            disabled={state.status === "closed"}
          />
        )}
      </div>
      {error ? <span className="text-xs text-destructive">{error}</span> : null}
    </div>
  );
}

const LABELS: Record<Action, { label: string; icon: typeof Play }> = {
  start: { label: "Start agent", icon: Play },
  stop: { label: "Stop", icon: Square },
  restart: { label: "Restart", icon: RotateCw },
};

function RunButton({
  action,
  pending,
  onClick,
  disabled = false,
}: {
  action: Action;
  pending: Action | null;
  onClick: (action: Action) => void;
  disabled?: boolean;
}) {
  const { label, icon: Icon } = LABELS[action];
  return (
    <Button
      type="button"
      size="xs"
      variant={action === "stop" ? "destructive" : "outline"}
      disabled={disabled || pending !== null}
      onClick={() => onClick(action)}
    >
      <Icon className={cn(pending === action && action === "restart" && "animate-spin")} />
      {label}
    </Button>
  );
}
