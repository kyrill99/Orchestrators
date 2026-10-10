"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Bug, ImageIcon, MessageSquare, Plus, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { IssueDetail } from "@/components/issues/issue-detail";
import { IssueStatusIcon, Person } from "@/components/issues/issue-parts";
import {
  ISSUES_CHANGED,
  attachmentSrc,
  createIssue,
  formatWhen,
  listIssues,
  type Issue,
} from "@/lib/issues";
import { pageContext } from "@/lib/screenshot";
import { cn } from "@/lib/utils";

const filters = [
  { value: "open,in_progress", label: "Open" },
  { value: "closed", label: "Closed" },
  { value: "open,in_progress,closed", label: "All" },
] as const;

type Filter = (typeof filters)[number]["value"];

export function IssuesExplorer() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const selectedId = searchParams.get("id");

  const [filter, setFilter] = useState<Filter>("open,in_progress");
  const [query, setQuery] = useState("");
  const [issues, setIssues] = useState<Issue[] | null>(null);
  const [drafts, setDrafts] = useState<Issue[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  const refresh = useCallback(() => setVersion((v) => v + 1), []);

  useEffect(() => {
    window.addEventListener(ISSUES_CHANGED, refresh);
    return () => window.removeEventListener(ISSUES_CHANGED, refresh);
  }, [refresh]);

  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      Promise.all([
        listIssues({ status: filter, q: query.trim() }),
        listIssues({ status: "draft", author: "me" }),
      ])
        .then(([list, mine]) => {
          if (cancelled) return;
          setIssues(list);
          setDrafts(mine);
          setError(null);
        })
        .catch(() => {
          if (!cancelled) setError("Couldn’t reach the backend.");
        });
    }, query ? 200 : 0);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [filter, query, version]);

  const select = useCallback(
    (id: string | null) => {
      router.replace(id ? `${pathname}?id=${encodeURIComponent(id)}` : pathname, {
        scroll: false,
      });
    },
    [router, pathname],
  );

  async function newIssue() {
    try {
      const draft = await createIssue({
        status: "draft",
        context: { userAgent: pageContext().userAgent },
      });
      refresh();
      select(draft.id);
    } catch {
      setError("Couldn’t create an issue.");
    }
  }

  return (
    <div className="flex h-full overflow-hidden">
      <div className="flex w-80 shrink-0 flex-col border-r border-border bg-background lg:w-96">
        <div className="flex flex-col gap-2 border-b border-border p-2.5">
          <div className="flex items-center gap-1">
            {filters.map((f) => (
              <button
                key={f.value}
                type="button"
                onClick={() => setFilter(f.value)}
                className={cn(
                  "rounded-md px-2 py-1 text-[12px] font-medium transition-colors",
                  filter === f.value
                    ? "bg-accent text-foreground"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                )}
                aria-pressed={filter === f.value}
              >
                {f.label}
              </button>
            ))}
            <Button
              variant="ghost"
              size="xs"
              className="ml-auto text-muted-foreground"
              onClick={() => void newIssue()}
            >
              <Plus />
              New issue
            </Button>
          </div>
          <div className="relative">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search issues or #number…"
              className="h-8 border-transparent bg-accent/60 pl-8 text-sm shadow-none focus-visible:bg-background"
            />
          </div>
        </div>

        <ScrollArea className="min-h-0 flex-1">
          <div className="p-1.5">
            {drafts.length > 0 ? (
              <Section title="Your drafts">
                {drafts.map((issue) => (
                  <IssueRow
                    key={issue.id}
                    issue={issue}
                    selected={issue.id === selectedId}
                    onSelect={select}
                  />
                ))}
              </Section>
            ) : null}
            <Section title={drafts.length > 0 ? "Issues" : null}>
              {error ? (
                <p className="px-2 py-6 text-center text-xs text-muted-foreground">
                  {error}
                </p>
              ) : issues === null ? (
                <p className="px-2 py-6 text-center text-xs text-muted-foreground">
                  Loading…
                </p>
              ) : issues.length === 0 ? (
                <p className="px-2 py-6 text-center text-xs text-muted-foreground">
                  {query ? "No matching issues" : "No issues here"}
                </p>
              ) : (
                issues.map((issue) => (
                  <IssueRow
                    key={issue.id}
                    issue={issue}
                    selected={issue.id === selectedId}
                    onSelect={select}
                  />
                ))
              )}
            </Section>
          </div>
        </ScrollArea>
      </div>

      <div className="min-w-0 flex-1 overflow-auto bg-canvas">
        {selectedId ? (
          <IssueDetail
            key={selectedId}
            id={selectedId}
            onChanged={refresh}
            onDeleted={() => {
              refresh();
              select(null);
            }}
          />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
            <Bug className="size-5 text-muted-foreground" strokeWidth={1.5} />
            <p className="text-sm text-foreground">Select an issue</p>
            <p className="max-w-xs text-xs text-muted-foreground">
              Spotted a problem? Use <span className="font-medium">Report bug</span>{" "}
              at the bottom right of any page: it screenshots the page and opens
              a draft.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string | null;
  children: ReactNode;
}) {
  return (
    <div className="mb-2">
      {title ? (
        <div className="px-2 py-1 text-[11px] font-medium text-muted-foreground">
          {title}
        </div>
      ) : null}
      <ul>{children}</ul>
    </div>
  );
}

function IssueRow({
  issue,
  selected,
  onSelect,
}: {
  issue: Issue;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const shot = issue.attachments[0];
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(issue.id)}
        className={cn(
          "flex w-full items-start gap-2.5 rounded-md px-2 py-2 text-left transition-colors",
          selected ? "bg-accent" : "hover:bg-accent/50",
        )}
        aria-current={selected ? "true" : undefined}
      >
        <IssueStatusIcon status={issue.status} className="mt-0.5" />
        <div className="min-w-0 flex-1">
          <p
            className={cn(
              "truncate text-[13px]",
              selected ? "text-foreground" : "text-foreground/90",
              issue.status === "closed" && "text-muted-foreground",
            )}
          >
            {issue.number ? (
              <span className="mr-1.5 font-mono text-[11px] text-muted-foreground">
                #{issue.number}
              </span>
            ) : null}
            {issue.title || "Untitled draft"}
          </p>
          <p className="mt-0.5 flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <Person person={issue.author} />
            <span>· {formatWhen(issue.created_at)}</span>
            {issue.comment_count ? (
              <span className="inline-flex items-center gap-0.5">
                · <MessageSquare className="size-3" strokeWidth={1.75} />
                {issue.comment_count}
              </span>
            ) : null}
            {issue.attachments.length > 1 ? (
              <span className="inline-flex items-center gap-0.5">
                · <ImageIcon className="size-3" strokeWidth={1.75} />
                {issue.attachments.length}
              </span>
            ) : null}
          </p>
        </div>
        {shot ? (
          // eslint-disable-next-line @next/next/no-img-element -- served by the API
          <img
            src={attachmentSrc(shot)}
            alt=""
            loading="lazy"
            className="h-9 w-14 shrink-0 rounded border border-border object-cover object-top"
          />
        ) : null}
      </button>
    </li>
  );
}
