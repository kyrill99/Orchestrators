"use client";

import { useEffect, useState } from "react";
import { apiBaseUrl } from "@/lib/api";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { cn } from "@/lib/utils";

type Commit = {
  hash: string;
  short: string;
  author: string;
  date: string;
  subject: string;
  body: string | null;
  new: boolean;
};

type Changelog = {
  commit: string;
  upstream: string | null;
  since: string | null;
  commits: Commit[];
  pending: Commit[];
};

const units: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 365 * 24 * 3600],
  ["month", 30 * 24 * 3600],
  ["week", 7 * 24 * 3600],
  ["day", 24 * 3600],
  ["hour", 3600],
  ["minute", 60],
];
const rtf = new Intl.RelativeTimeFormat(undefined, {
  numeric: "auto",
  style: "short",
});

function ago(iso: string): string {
  const seconds = (new Date(iso).getTime() - Date.now()) / 1000;
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size)
      return rtf.format(Math.round(seconds / size), unit);
  }
  return "just now";
}

/** Recent commits of the deployed checkout: GET /api/admin/changelog. */
export function ChangelogSheet({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [log, setLog] = useState<Changelog | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    fetch(`${apiBaseUrl()}/api/admin/changelog?limit=50`, {
      signal: controller.signal,
    })
      .then(async (res) => {
        if (!res.ok) {
          const body = await res.json().catch(() => null);
          throw new Error(body?.detail ?? `HTTP ${res.status}`);
        }
        return res.json();
      })
      .then((data: Changelog) => {
        setLog(data);
        setError(null);
      })
      .catch((err: Error) => {
        if (err.name !== "AbortError") setError(err.message);
      });
    return () => controller.abort();
  }, [open]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="flex w-full flex-col gap-0 p-0 sm:max-w-[420px]"
      >
        <SheetHeader className="gap-0.5 border-b border-border px-4 py-3 text-left">
          <SheetTitle className="text-[13px] font-medium">Changelog</SheetTitle>
          <SheetDescription className="text-xs">
            {log ? (
              <>
                Running <span className="font-mono">{log.commit}</span>
                {log.since && (
                  <>
                    {" "}
                    · updated from{" "}
                    <span className="font-mono">{log.since}</span>
                  </>
                )}
              </>
            ) : (
              "Recent commits"
            )}
          </SheetDescription>
        </SheetHeader>

        <ScrollArea className="min-h-0 flex-1">
          <div className="flex flex-col p-1.5">
            {error && (
              <p className="px-2 py-6 text-center text-xs text-destructive">
                {error}
              </p>
            )}
            {!error && !log && (
              <p className="px-2 py-6 text-center text-xs text-muted-foreground">
                Loading…
              </p>
            )}
            {log && log.pending.length > 0 && (
              <Section
                title={`Not deployed yet · ${log.upstream ?? "upstream"}`}
                commits={log.pending}
                muted
              />
            )}
            {log && (
              <Section
                title={log.pending.length > 0 ? "Deployed" : null}
                commits={log.commits}
              />
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}

function Section({
  title,
  commits,
  muted = false,
}: {
  title: string | null;
  commits: Commit[];
  muted?: boolean;
}) {
  return (
    <section className="mb-2">
      {title && (
        <h3 className="px-2.5 pt-2 pb-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
          {title}
        </h3>
      )}
      <ul className="flex flex-col">
        {commits.map((c) => (
          <li
            key={c.hash}
            className={cn(
              "rounded-md px-2.5 py-2 transition-colors hover:bg-accent/60",
              muted && "opacity-70",
            )}
            title={c.body ?? undefined}
          >
            <div className="flex items-start gap-2">
              <span className="min-w-0 flex-1 text-[13px] leading-snug text-foreground">
                {c.subject}
              </span>
              {c.new && (
                <span className="mt-0.5 shrink-0 rounded-sm bg-foreground px-1 text-[10px] font-medium text-background">
                  new
                </span>
              )}
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <span className="font-mono">{c.short}</span>
              <span>·</span>
              <span className="truncate">{c.author}</span>
              <span>·</span>
              <time
                dateTime={c.date}
                title={new Date(c.date).toLocaleString()}
                className="shrink-0"
              >
                {ago(c.date)}
              </time>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
