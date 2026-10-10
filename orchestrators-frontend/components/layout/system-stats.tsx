"use client";

import { useEffect, useState } from "react";
import { apiBaseUrl } from "@/lib/api";
import { cn } from "@/lib/utils";

type SystemStats = {
  cpu: { percent: number; cores: number | null; load: number[] };
  memory: { total: number; used: number; swap_total: number; swap_used: number };
};

const POLL_MS = 5000;

const gb = (bytes: number) => (bytes / 1024 ** 3).toFixed(1);

/** Host CPU and memory bars from GET /api/admin/system. */
export function SystemStats() {
  const [stats, setStats] = useState<SystemStats | null>(null);

  useEffect(() => {
    const refresh = () =>
      fetch(`${apiBaseUrl()}/api/admin/system`)
        .then((res) => (res.ok ? res.json() : Promise.reject(res.status)))
        .then(setStats)
        .catch(() => setStats(null));
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, []);

  if (!stats) return null;

  const { cpu, memory } = stats;
  const memPercent = (100 * memory.used) / memory.total;
  const swap = memory.swap_total
    ? `, swap ${gb(memory.swap_used)} / ${gb(memory.swap_total)} GB`
    : "";

  return (
    <div className="flex flex-col gap-1.5 px-2 text-[11px] text-muted-foreground">
      <Meter
        label="CPU"
        percent={cpu.percent}
        value={`${Math.round(cpu.percent)}%`}
        title={`${cpu.cores ?? "?"} cores, load ${cpu.load.join(" / ")}`}
      />
      <Meter
        label="Memory"
        percent={memPercent}
        value={`${gb(memory.used)} / ${gb(memory.total)} GB`}
        title={`${Math.round(memPercent)}% used${swap}`}
      />
    </div>
  );
}

function Meter({
  label,
  percent,
  value,
  title,
}: {
  label: string;
  percent: number;
  value: string;
  title: string;
}) {
  const clamped = Math.min(100, Math.max(0, percent));
  return (
    <div className="flex flex-col gap-1" title={title}>
      <div className="flex items-center justify-between">
        <span>{label}</span>
        <span className="font-mono tabular-nums">{value}</span>
      </div>
      <div
        className="h-1 overflow-hidden rounded-full bg-sidebar-accent"
        role="meter"
        aria-label={label}
        aria-valuenow={Math.round(clamped)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className={cn(
            "h-full rounded-full transition-[width] duration-500",
            clamped >= 90
              ? "bg-destructive"
              : clamped >= 75
                ? "bg-amber-500"
                : "bg-foreground/40",
          )}
          style={{ width: `${clamped}%` }}
        />
      </div>
    </div>
  );
}
