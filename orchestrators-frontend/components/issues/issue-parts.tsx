"use client";

import { useEffect, useState } from "react";
import { Bot, Globe, Monitor, X } from "lucide-react";
import { StatusIcon } from "@/components/task/status-icon";
import {
  attachmentSrc,
  describeBrowser,
  issueStatusLabel,
  type IssueAttachment,
  type IssueContext,
  type IssuePerson,
  type IssueStatus,
} from "@/lib/issues";
import { cn } from "@/lib/utils";

export function IssueStatusIcon({
  status,
  className,
}: {
  status: IssueStatus;
  className?: string;
}) {
  if (status === "draft") {
    return (
      <svg
        viewBox="0 0 16 16"
        fill="none"
        aria-hidden
        className={cn("size-3.5 shrink-0", className)}
      >
        <circle
          cx="8"
          cy="8"
          r="6"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeDasharray="2.2 2.2"
          className="text-neutral-400"
        />
      </svg>
    );
  }
  return (
    <StatusIcon
      status={status === "in_progress" ? "running" : status}
      className={className}
    />
  );
}

export function IssueStatusPill({ status }: { status: IssueStatus }) {
  return (
    <span className="inline-flex h-6 items-center gap-1.5 rounded-md border border-border px-2 text-[12px] font-medium text-foreground">
      <IssueStatusIcon status={status} />
      {issueStatusLabel[status]}
    </span>
  );
}

export function Person({ person }: { person: IssuePerson }) {
  const agent = person.email.startsWith("agent:");
  return (
    <span className="inline-flex items-center gap-1" title={person.email}>
      {agent ? (
        <Bot className="size-3 shrink-0" strokeWidth={1.75} />
      ) : (
        <span className="flex size-4 shrink-0 items-center justify-center rounded-full bg-foreground/80 text-[9px] font-semibold text-background uppercase">
          {person.name.slice(0, 1)}
        </span>
      )}
      <span className="truncate">{person.name}</span>
    </span>
  );
}

/** Screenshot thumbnails; click opens the full image. */
export function Screenshots({
  attachments,
  onRemove,
  size = "md",
}: {
  attachments: IssueAttachment[];
  onRemove?: (attachment: IssueAttachment) => void;
  size?: "sm" | "md";
}) {
  const [open, setOpen] = useState<IssueAttachment | null>(null);
  if (attachments.length === 0) return null;
  return (
    <>
      <div className="flex flex-wrap gap-2">
        {attachments.map((a) => (
          <div key={a.id} className="group relative">
            <button
              type="button"
              onClick={() => setOpen(a)}
              className={cn(
                "block overflow-hidden rounded-md border border-border bg-muted transition-opacity hover:opacity-90",
                size === "sm" ? "h-20 w-32" : "h-32 w-52",
              )}
              aria-label={`Open ${a.filename}`}
            >
              {/* eslint-disable-next-line @next/next/no-img-element -- served by the API, not optimizable */}
              <img
                src={attachmentSrc(a)}
                alt={a.filename}
                className="size-full object-cover object-top"
              />
            </button>
            {onRemove ? (
              <button
                type="button"
                onClick={() => onRemove(a)}
                className="absolute top-1 right-1 flex size-5 items-center justify-center rounded-full bg-foreground/80 text-background opacity-90 transition-opacity hover:opacity-100"
                aria-label="Remove screenshot"
              >
                <X className="size-3" />
              </button>
            ) : null}
          </div>
        ))}
      </div>
      {open ? <Lightbox attachment={open} onClose={() => setOpen(null)} /> : null}
    </>
  );
}

function Lightbox({
  attachment,
  onClose,
}: {
  attachment: IssueAttachment;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal
      aria-label={attachment.filename}
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 p-6 backdrop-blur-xs"
      onClick={onClose}
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- served by the API */}
      <img
        src={attachmentSrc(attachment)}
        alt={attachment.filename}
        className="max-h-full max-w-full rounded-md shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      />
      <button
        type="button"
        onClick={onClose}
        className="absolute top-4 right-4 flex size-8 items-center justify-center rounded-full bg-white/10 text-white hover:bg-white/20"
        aria-label="Close"
      >
        <X className="size-4" />
      </button>
    </div>
  );
}

/** Page URL, browser and viewport recorded with a bug report. */
export function ContextLine({ context }: { context: IssueContext }) {
  if (!context.url && !context.userAgent) return null;
  let path = context.url ?? "";
  try {
    const url = new URL(path);
    path = url.pathname + url.search;
  } catch {}
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-muted-foreground">
      {context.url ? (
        <a
          href={context.url}
          className="inline-flex items-center gap-1 hover:text-foreground"
          title={context.url}
        >
          <Globe className="size-3" strokeWidth={1.75} />
          {path}
        </a>
      ) : null}
      {context.userAgent ? (
        <span className="inline-flex items-center gap-1" title={context.userAgent}>
          <Monitor className="size-3" strokeWidth={1.75} />
          {describeBrowser(context.userAgent)}
          {context.viewport ? ` · ${context.viewport}` : ""}
          {context.devicePixelRatio && context.devicePixelRatio !== 1
            ? ` @${context.devicePixelRatio}x`
            : ""}
        </span>
      ) : null}
    </div>
  );
}
