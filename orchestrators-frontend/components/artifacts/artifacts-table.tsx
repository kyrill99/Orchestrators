"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ChevronDown,
  ChevronRight,
  Download,
  ExternalLink,
  FileText,
  Search,
} from "lucide-react";
import {
  apiBaseUrl,
  fetchProjectArtifacts,
  type ProjectArtifactFile,
} from "@/lib/api";
import { fetchTaskGraph } from "@/lib/taskgraph-api";
import { projectPath } from "@/lib/project-scope";
import { useProjectId } from "@/lib/use-project";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/** One artifact: the versions saved under the same (task, filename), newest first. */
type ArtifactGroup = {
  key: string;
  latest: ProjectArtifactFile;
  older: ProjectArtifactFile[];
};

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function groupArtifacts(rows: ProjectArtifactFile[]): ArtifactGroup[] {
  const byKey = new Map<string, ProjectArtifactFile[]>();
  for (const row of rows) {
    const key = `${row.taskId}\u0000${row.filename}`;
    const list = byKey.get(key);
    if (list) list.push(row);
    else byKey.set(key, [row]);
  }
  const groups: ArtifactGroup[] = [];
  byKey.forEach((list, key) => {
    const sorted = [...list].sort((a, b) => b.version - a.version);
    groups.push({ key, latest: sorted[0], older: sorted.slice(1) });
  });
  // Most recently updated artifact first.
  groups.sort((a, b) => b.latest.createdAt.localeCompare(a.latest.createdAt));
  return groups;
}

export function ArtifactsTable() {
  const projectId = useProjectId();
  const [rows, setRows] = useState<ProjectArtifactFile[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [taskTitles, setTaskTitles] = useState<Record<string, string>>({});
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  useEffect(() => {
    let cancelled = false;
    fetchProjectArtifacts()
      .then((data: ProjectArtifactFile[]) => {
        if (!cancelled) setRows(data);
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Could not load artifacts");
          setRows([]);
        }
      });
    fetchTaskGraph()
      .then((snapshot) => {
        if (cancelled) return;
        const titles: Record<string, string> = {};
        for (const task of snapshot.tasks) titles[task.id] = task.title;
        setTaskTitles(titles);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const groups = useMemo<ArtifactGroup[]>(() => {
    const all = groupArtifacts(rows ?? []);
    const q = query.trim().toLowerCase();
    if (!q) return all;
    return all.filter(
      (g) =>
        g.latest.filename.toLowerCase().includes(q) ||
        g.latest.title.toLowerCase().includes(q),
    );
  }, [rows, query]);

  const base = apiBaseUrl();

  const toggle = (key: string) =>
    setExpanded((prev) => ({ ...prev, [key]: !prev[key] }));

  return (
    <div className="flex h-full flex-col gap-4 overflow-auto p-6">
      <div className="flex items-center gap-3">
        <FileText className="size-5 text-muted-foreground" strokeWidth={1.75} />
        <h1 className="text-lg font-semibold">Artifacts</h1>
        <span className="text-sm text-muted-foreground">
          {rows === null ? "" : `${groups.length} shown`}
        </span>
        <div className="relative ml-auto w-72">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by filename or description"
            className="pl-8"
          />
        </div>
      </div>

      {error ? (
        <div className="rounded-md border border-border p-3 text-sm text-destructive">
          {error}
        </div>
      ) : null}

      {rows === null ? (
        <div className="py-12 text-center text-sm text-muted-foreground">
          Loading artifacts…
        </div>
      ) : groups.length === 0 ? (
        <div className="flex flex-col items-center gap-2 py-16 text-center text-sm text-muted-foreground">
          <FileText className="size-8 opacity-50" strokeWidth={1.5} />
          {query.trim()
            ? "No artifacts match this filter."
            : "No artifacts yet. Reports and files that agents save on tasks will appear here."}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-left text-[13px]">
            <thead className="bg-muted/50 text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium">File</th>
                <th className="px-3 py-2 font-medium">Description</th>
                <th className="px-3 py-2 font-medium">Size</th>
                <th className="px-3 py-2 font-medium">Date</th>
                <th className="px-3 py-2 font-medium">Task</th>
                <th className="px-3 py-2 font-medium">Created by</th>
                <th className="px-3 py-2 font-medium">Version</th>
                <th className="px-3 py-2 font-medium" />
              </tr>
            </thead>
            <tbody>
              {groups.map((group) => {
                const a = group.latest;
                const open = expanded[group.key] === true;
                return (
                  <Fragment key={group.key}>
                    <tr className="border-t border-border align-top">
                      <td className="px-3 py-2">
                        <a
                          href={`${base}${a.viewUrl}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="font-medium text-foreground hover:underline"
                        >
                          {a.filename}
                        </a>
                      </td>
                      <td className="max-w-[320px] px-3 py-2 text-muted-foreground">
                        <span className="line-clamp-2">{a.title}</span>
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-muted-foreground">
                        {formatSize(a.size)}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-muted-foreground">
                        {formatDate(a.createdAt)}
                      </td>
                      <td className="px-3 py-2">
                        <Link
                          href={projectPath(projectId, `/tasks/${encodeURIComponent(a.taskId)}`)}
                          className="hover:underline"
                        >
                          {taskTitles[a.taskId] ?? a.taskId}
                        </Link>
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-muted-foreground">
                        {a.createdBy}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2">
                        {group.older.length > 0 ? (
                          <button
                            type="button"
                            onClick={() => toggle(group.key)}
                            className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-muted"
                            aria-expanded={open}
                          >
                            v{a.version}
                            {open ? (
                              <ChevronDown className="size-3.5" />
                            ) : (
                              <ChevronRight className="size-3.5" />
                            )}
                          </button>
                        ) : (
                          <span className="px-1.5 text-muted-foreground">v{a.version}</span>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2">
                        <ArtifactLinks base={base} artifact={a} />
                      </td>
                    </tr>
                    {open
                      ? group.older.map((v) => (
                          <tr
                            key={v.id}
                            className={cn("border-t border-border/50 bg-muted/20 text-muted-foreground")}
                          >
                            <td className="px-3 py-1.5 pl-8">
                              <a
                                href={`${base}${v.viewUrl}`}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="hover:underline"
                              >
                                {v.filename}
                              </a>
                            </td>
                            <td className="max-w-[320px] px-3 py-1.5">
                              <span className="line-clamp-1">{v.title}</span>
                            </td>
                            <td className="whitespace-nowrap px-3 py-1.5">{formatSize(v.size)}</td>
                            <td className="whitespace-nowrap px-3 py-1.5">{formatDate(v.createdAt)}</td>
                            <td className="px-3 py-1.5" />
                            <td className="whitespace-nowrap px-3 py-1.5">{v.createdBy}</td>
                            <td className="whitespace-nowrap px-3 py-1.5 pl-[18px]">v{v.version}</td>
                            <td className="whitespace-nowrap px-3 py-1.5">
                              <ArtifactLinks base={base} artifact={v} />
                            </td>
                          </tr>
                        ))
                      : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ArtifactLinks({
  base,
  artifact,
}: {
  base: string;
  artifact: ProjectArtifactFile;
}) {
  return (
    <span className="inline-flex items-center gap-2">
      <a
        href={`${base}${artifact.viewUrl}`}
        target="_blank"
        rel="noopener noreferrer"
        title="Open"
        className="text-muted-foreground hover:text-foreground"
      >
        <ExternalLink className="size-3.5" />
      </a>
      <a
        href={`${base}${artifact.downloadUrl}`}
        title="Download"
        className="text-muted-foreground hover:text-foreground"
      >
        <Download className="size-3.5" />
      </a>
    </span>
  );
}
