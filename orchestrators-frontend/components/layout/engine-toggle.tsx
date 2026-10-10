"use client";

import { useCallback, useEffect, useState } from "react";
import { Play, Square } from "lucide-react";
import { fetchEngine, setEngine, type EngineStatus } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const POLL_MS = 3000;

/** Start / stop the engine that keeps agents working on ready tasks. */
export function EngineToggle() {
  const [engine, setState] = useState<EngineStatus | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    fetchEngine()
      .then((next) => setState(next))
      .catch(() => setState(null));
  }, []);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  if (engine === null) return null;

  const running = engine.state === "running";
  const active = engine.activeRuns.length;
  const canRun = engine.runner !== "record";

  const toggle = () => {
    let stopRuns = false;
    if (running && active > 0) {
      stopRuns = window.confirm(
        `Also stop the ${active} active agent run${active === 1 ? "" : "s"}?\n` +
          "OK stops them now; Cancel lets them finish.",
      );
    }
    setPending(true);
    setError(null);
    setEngine(running ? "stop" : "start", { stopRuns })
      .then(setState)
      .catch((e: Error) => setError(e.message))
      .finally(() => setPending(false));
  };

  const title = !canRun
    ? "No agent runner configured on the server (GEMINI_API_KEY is not set)"
    : running
      ? `Engine running: ${active} of ${engine.maxRuns} agents busy, ${engine.queue.length} task(s) queued`
      : `Engine stopped. ${engine.queue.length} ready task(s) would start.`;

  return (
    <div className="flex items-center gap-2" title={error ?? title}>
      <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
        <span
          className={cn(
            "inline-flex size-1.5 rounded-full",
            running ? "bg-[#5E6AD2]" : "bg-neutral-300",
            running && active > 0 && "animate-pulse",
          )}
        />
        {running ? "Engine" : "Engine off"}
        <span className="tabular-nums">
          {active}/{engine.maxRuns}
        </span>
      </span>
      <Button
        type="button"
        size="xs"
        variant={running ? "outline" : "default"}
        disabled={pending || (!running && !canRun)}
        onClick={toggle}
      >
        {running ? <Square /> : <Play />}
        {running ? "Stop" : "Start"}
      </Button>
      {error ? <span className="max-w-48 truncate text-xs text-destructive">{error}</span> : null}
    </div>
  );
}
