"use client";

import { useEffect, useMemo, useState } from "react";
import { FileText, Folder, Search } from "lucide-react";
import { memoryTree, type MemoryFile, type MemoryFolder } from "@/lib/fixtures";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const scopeLabel = {
  project: "Project",
  user: "Owner",
  global: "Orchestrator",
} as const;

export function MemoryExplorer() {
  // Fixtures until the backend answers; they stay if it is unreachable.
  const [tree, setTree] = useState<MemoryFolder[]>(memoryTree);
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string>(
    memoryTree[0]?.files[0]?.id ?? "",
  );

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_URL}/api/memory/tree`, { signal: controller.signal })
      .then((res) => (res.ok ? res.json() : Promise.reject(res.status)))
      .then((data: MemoryFolder[]) => setTree(data))
      .catch(() => {});
    return () => controller.abort();
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return tree;
    return tree
      .map((folder) => ({
        ...folder,
        files: folder.files.filter(
          (file) =>
            file.name.toLowerCase().includes(q) ||
            file.path.toLowerCase().includes(q) ||
            file.content.toLowerCase().includes(q),
        ),
      }))
      .filter((folder) => folder.files.length > 0);
  }, [query, tree]);

  const selected: MemoryFile | undefined = useMemo(() => {
    for (const folder of tree) {
      const file = folder.files.find((f) => f.id === selectedId);
      if (file) return file;
    }
    return filtered[0]?.files[0];
  }, [selectedId, filtered, tree]);

  return (
    <div className="flex h-full overflow-hidden">
      <div className="flex w-64 shrink-0 flex-col border-r border-border bg-background sm:w-72">
        <div className="border-b border-border p-2.5">
          <div className="relative">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search memory…"
              className="h-8 border-transparent bg-accent/60 pl-8 text-sm shadow-none focus-visible:bg-background"
            />
          </div>
        </div>
        <ScrollArea className="flex-1">
          <div className="p-1.5">
            {filtered.length === 0 ? (
              <p className="px-2 py-6 text-center text-xs text-muted-foreground">
                No matching files
              </p>
            ) : (
              filtered.map((folder) => (
                <div key={folder.id} className="mb-2">
                  <div className="mb-0.5 flex items-center gap-1.5 px-2 py-1 text-[11px] font-medium text-muted-foreground">
                    <Folder className="size-3" strokeWidth={1.75} />
                    {folder.name}
                  </div>
                  <ul>
                    {folder.files.map((file) => (
                      <li key={file.id}>
                        <button
                          type="button"
                          onClick={() => setSelectedId(file.id)}
                          className={cn(
                            "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] transition-colors",
                            selected?.id === file.id
                              ? "bg-accent text-foreground"
                              : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                          )}
                        >
                          <FileText
                            className="size-3.5 shrink-0 opacity-70"
                            strokeWidth={1.75}
                          />
                          <span className="truncate">{file.name}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ))
            )}
          </div>
        </ScrollArea>
      </div>

      <div className="min-w-0 flex-1 overflow-auto bg-canvas">
        {selected ? (
          <article className="mx-auto max-w-2xl px-6 py-8 sm:px-10">
            <p className="mb-2 text-xs text-muted-foreground">{selected.path}</p>
            <h1 className="mb-6 text-xl font-semibold tracking-[-0.02em] text-foreground">
              {selected.name.replace(/\.md$/, "")}
            </h1>
            {selected.scope ? (
              <p className="-mt-4 mb-6 text-xs text-muted-foreground">
                {scopeLabel[selected.scope]} · {selected.kind} · confidence{" "}
                {Math.round((selected.confidence ?? 0) * 100)}% · used{" "}
                {selected.uses ?? 0}×
              </p>
            ) : null}
            <div className="space-y-3 text-[15px] leading-relaxed text-foreground/90">
              {selected.content
                .trim()
                .split("\n")
                .map((line, index) => {
                  if (line.startsWith("# ")) {
                    return null;
                  }
                  if (line.startsWith("- ")) {
                    return (
                      <p key={index} className="pl-1">
                        <span className="mr-2 text-muted-foreground">•</span>
                        {line.slice(2)}
                      </p>
                    );
                  }
                  if (!line.trim()) {
                    return <div key={index} className="h-2" />;
                  }
                  return <p key={index}>{line}</p>;
                })}
            </div>
          </article>
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            Select a file
          </div>
        )}
      </div>
    </div>
  );
}
