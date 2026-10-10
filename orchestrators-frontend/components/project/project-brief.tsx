"use client";

import { useEffect, useRef, useState } from "react";
import { FileText } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  fetchCurrentProject,
  updateProject,
  type Project,
} from "@/lib/projects";
import { cn } from "@/lib/utils";

/** "Brief" button in the project header: shows / edits the project description. */
export function ProjectBrief() {
  const [project, setProject] = useState<Project | null>(null);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [nameDraft, setNameDraft] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const current = await fetchCurrentProject();
        if (!cancelled) setProject(current);
      } catch {
        // Backend without projects: hide the brief.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      const root = rootRef.current;
      if (root && event.target instanceof Node && !root.contains(event.target)) {
        if (!editing) setOpen(false);
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open, editing]);

  if (!project) return null;

  async function save() {
    if (!project || busy) return;
    setBusy(true);
    setError(null);
    try {
      const name = nameDraft.trim();
      const updated = await updateProject(project.id, {
        description: draft,
        ...(name && name !== project.name ? { name } : {}),
      });
      setProject(updated);
      setEditing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div ref={rootRef} className="relative ml-auto">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => {
          setOpen((v) => !v);
          setEditing(false);
          setError(null);
        }}
        className={cn(
          "inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-[13px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
          open && "bg-muted text-foreground",
        )}
      >
        <FileText className="size-3.5" strokeWidth={1.75} />
        Project brief
      </button>

      {open ? (
        <div className="absolute top-full right-0 z-50 mt-1.5 w-96 max-w-[calc(100vw-2rem)] rounded-lg border border-border bg-popover p-3 text-popover-foreground shadow-lg">
          <div className="mb-1 flex items-center justify-between gap-2">
            {editing ? (
              <input
                aria-label="Project name"
                value={nameDraft}
                onChange={(event) => setNameDraft(event.target.value)}
                disabled={busy}
                className="h-7 min-w-0 flex-1 rounded-md border border-input bg-transparent px-2 text-[13px] font-medium outline-none focus-visible:border-ring"
              />
            ) : (
              <span className="truncate text-[13px] font-medium">
                {project.name || project.id}
              </span>
            )}
            {!editing ? (
              <Button
                type="button"
                variant="ghost"
                size="xs"
                onClick={() => {
                  setDraft(project.description ?? "");
                  setNameDraft(project.name ?? "");
                  setEditing(true);
                }}
              >
                Edit
              </Button>
            ) : null}
          </div>
          <p className="mb-2 text-[11px] text-muted-foreground">
            Planner and task agents get this as their brief.
          </p>
          {editing ? (
            <div className="flex flex-col gap-2">
              <Textarea
                autoFocus
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                disabled={busy}
                className="max-h-72 min-h-28 text-[13px]"
              />
              {error ? (
                <p className="text-[11px] text-destructive">{error}</p>
              ) : null}
              <div className="flex justify-end gap-1.5">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setEditing(false);
                    setError(null);
                  }}
                  disabled={busy}
                >
                  Cancel
                </Button>
                <Button
                  type="button"
                  size="sm"
                  onClick={() => void save()}
                  disabled={busy}
                >
                  {busy ? "Saving…" : "Save"}
                </Button>
              </div>
            </div>
          ) : (
            <p className="max-h-72 overflow-y-auto text-[13px] leading-relaxed whitespace-pre-wrap text-foreground/90">
              {project.description?.trim() ? (
                project.description
              ) : (
                <span className="text-muted-foreground">No brief yet.</span>
              )}
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
}
