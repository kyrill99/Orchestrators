"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { apiBaseUrl } from "@/lib/api";
import { cn } from "@/lib/utils";

type UpdateStatus = {
  commit: string | null;
  requested: boolean;
  last: {
    state: "running" | "ok" | "up_to_date" | "failed";
    from: string;
    to: string | null;
    detail: string | null;
  } | null;
};

/** Self-update: POST /api/admin/update, then poll until the run settles. */
export function UpdateControl() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);

  const refresh = useCallback(() => {
    fetch(`${apiBaseUrl()}/api/admin/update`)
      .then((res) => (res.ok ? res.json() : Promise.reject(res.status)))
      .then(setStatus)
      .catch(() => {});
  }, []);

  const busy = !!status && (status.requested || status.last?.state === "running");

  useEffect(() => {
    refresh();
    if (!busy) return;
    // The backend restarts mid-update, so failed polls are expected.
    const id = setInterval(refresh, 3000);
    return () => clearInterval(id);
  }, [busy, refresh]);

  if (!status) return null;

  const update = () =>
    fetch(`${apiBaseUrl()}/api/admin/update`, { method: "POST" })
      .then((res) => res.json())
      .then(setStatus)
      .catch(() => {});

  const note = busy
    ? "Updating…"
    : status.last?.state === "failed"
      ? `Update failed: ${status.last.detail ?? "see journal"}`
      : status.last?.state === "up_to_date"
        ? "Up to date"
        : null;

  return (
    <div className="mt-auto flex flex-col gap-1 px-2 text-[12px] text-muted-foreground">
      {note && (
        <span
          className={cn(
            "truncate",
            status.last?.state === "failed" && !busy && "text-destructive",
          )}
          title={note}
        >
          {note}
        </span>
      )}
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
        <span className="ml-auto font-mono text-[11px]">{status.commit}</span>
      </button>
    </div>
  );
}
