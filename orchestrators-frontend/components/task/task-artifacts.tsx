"use client";

import { useEffect, useState } from "react";
import {
  Download,
  ExternalLink,
  FileCode2,
  FileText,
  Maximize2,
  Minimize2,
} from "lucide-react";
import {
  apiBaseUrl,
  fetchTaskArtifacts,
  type TaskArtifactFile,
} from "@/lib/api";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const POLL_MS = 5000;

// Artifact HTML is model-written: scripts run (charts), but in an opaque origin
// (no allow-same-origin), so it cannot read cookies or call /api as the viewer.
// The server sends the same sandbox as a CSP header for direct visits.
const SANDBOX = "allow-scripts allow-popups allow-popups-to-escape-sandbox";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Files the agents saved on this task (mostly HTML reports), with an inline
 * sandboxed viewer. While the task is in review, the latest report opens by
 * default: it is what the owner approves or sends back.
 */
export function TaskArtifacts({
  taskId,
  inReview = false,
}: {
  taskId: string;
  inReview?: boolean;
}) {
  const [items, setItems] = useState<TaskArtifactFile[] | null>(null);
  // undefined: not chosen yet (use the default); null: closed by the user.
  const [openId, setOpenId] = useState<string | null | undefined>(undefined);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let stopped = false;
    const load = () =>
      fetchTaskArtifacts(taskId)
        .then((next) => !stopped && setItems(next))
        .catch(() => !stopped && setItems((prev) => prev ?? []));
    void load();
    const id = window.setInterval(load, POLL_MS);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, [taskId]);

  useEffect(() => {
    if (!expanded) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  if (!items || items.length === 0) return null;

  const latestReport = [...items].reverse().find((a) => a.kind === "html") ?? null;
  const shownId = openId === undefined ? (inReview ? latestReport?.id : null) : openId;
  const shown = items.find((a) => a.id === shownId) ?? null;
  const base = apiBaseUrl();

  return (
    <section
      className={cn(
        "mt-6",
        inReview && "rounded-xl border border-border bg-muted/30 p-3.5",
      )}
    >
      <div className="mb-2.5 flex items-center gap-2">
        <h2 className="text-[13px] font-medium text-foreground">
          {inReview ? "Report for review" : "Artifacts"}
        </h2>
        <span className="text-[11px] text-muted-foreground">{items.length}</span>
      </div>

      <ul className="flex flex-col gap-1">
        {[...items].reverse().map((a) => {
          const active = a.id === shown?.id;
          const Icon = a.kind === "html" ? FileCode2 : FileText;
          return (
            <li key={a.id}>
              <button
                type="button"
                onClick={() => setOpenId(active ? null : a.id)}
                aria-expanded={active}
                className={cn(
                  "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-accent/60",
                  active && "bg-accent/70",
                )}
              >
                <Icon className="size-4 shrink-0 text-muted-foreground" strokeWidth={1.75} />
                <span className="min-w-0 flex-1 truncate text-[13px] text-foreground">
                  {a.title}
                </span>
                <span className="shrink-0 text-[11px] text-muted-foreground">
                  {[a.runId, formatDate(a.updatedAt), formatSize(a.size)]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </button>
            </li>
          );
        })}
      </ul>

      {shown ? (
        <div
          className={cn(
            "flex flex-col overflow-hidden rounded-xl border border-border bg-background",
            expanded
              ? "fixed inset-3 z-50 shadow-2xl sm:inset-6"
              : "mt-2.5",
          )}
        >
          <div className="flex items-center gap-2 border-b border-border px-3 py-2">
            <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-foreground">
              {shown.title}
              <span className="ml-2 font-normal text-muted-foreground">
                {shown.filename}
              </span>
            </span>
            <a
              href={`${base}${shown.viewUrl}`}
              target="_blank"
              rel="noopener noreferrer"
              className={buttonVariants({ variant: "outline", size: "xs" })}
            >
              <ExternalLink /> Open in new tab
            </a>
            <a
              href={`${base}${shown.downloadUrl}`}
              download={shown.filename}
              className={buttonVariants({ variant: "outline", size: "xs" })}
            >
              <Download /> Download
            </a>
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              title={expanded ? "Back to the page (Esc)" : "Full screen"}
              aria-label={expanded ? "Exit full screen" : "Full screen"}
              className={buttonVariants({ variant: "ghost", size: "icon-xs" })}
            >
              {expanded ? <Minimize2 /> : <Maximize2 />}
            </button>
          </div>
          <iframe
            key={`${shown.id}-${shown.updatedAt}`}
            src={`${base}${shown.viewUrl}`}
            title={shown.title}
            sandbox={SANDBOX}
            referrerPolicy="no-referrer"
            className={cn("w-full bg-background",expanded ? "flex-1" : "h-[70vh]")}
          />
        </div>
      ) : null}
      {expanded ? (
        <div
          className="fixed inset-0 z-40 bg-black/40"
          onClick={() => setExpanded(false)}
          aria-hidden
        />
      ) : null}
    </section>
  );
}
