"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import Link from "next/link";
import { Check, ChevronDown, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { presenceColor, presenceInitial } from "@/lib/presence";
import {
  createProject,
  fetchProjects,
  getCurrentProjectId,
  switchProject,
  type Project,
} from "@/lib/projects";
import { cn } from "@/lib/utils";

/** Top-bar project name + dropdown to switch / create projects. */
export function ProjectSwitcher({ fallbackName }: { fallbackName: string }) {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [currentId, setCurrentId] = useState<string>("default");
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    try {
      const list = await fetchProjects();
      setProjects(list);
      setLoadFailed(false);
    } catch {
      setLoadFailed(true);
    }
  }, []);

  useEffect(() => {
    setCurrentId(getCurrentProjectId());
    void load();
  }, [load]);

  useEffect(() => {
    if (!open) return;
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    function onPointerDown(event: PointerEvent) {
      const root = rootRef.current;
      if (root && event.target instanceof Node && !root.contains(event.target)) {
        setOpen(false);
        setCreating(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        setCreating(false);
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, load]);

  const current = projects?.find((p) => p.id === currentId) ?? null;
  const name = current?.name || fallbackName;

  return (
    <div ref={rootRef} className="relative flex min-w-0 items-center">
      <Link
        href="/project"
        className="truncate font-medium text-foreground transition-opacity hover:opacity-70"
      >
        {name}
      </Link>
      <button
        type="button"
        aria-label="Switch project"
        aria-expanded={open}
        onClick={() => {
          setOpen((v) => !v);
          setCreating(false);
        }}
        className="ml-0.5 inline-flex size-5 shrink-0 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
      >
        <ChevronDown className="size-3.5" strokeWidth={1.75} />
      </button>

      {open ? (
        <div className="absolute top-full left-0 z-50 mt-1.5 w-72 rounded-lg border border-border bg-popover p-1 text-popover-foreground shadow-lg">
          {creating ? (
            <NewProjectForm onCancel={() => setCreating(false)} />
          ) : (
            <>
              <div className="px-2 pt-1.5 pb-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
                Projects
              </div>
              {loadFailed && !projects ? (
                <div className="px-2 py-2 text-[12px] text-muted-foreground">
                  Projects are unavailable right now.
                </div>
              ) : projects === null ? (
                <div className="px-2 py-2 text-[12px] text-muted-foreground">
                  Loading…
                </div>
              ) : projects.length === 0 ? (
                <div className="px-2 py-2 text-[12px] text-muted-foreground">
                  No projects yet.
                </div>
              ) : (
                <ul className="max-h-72 overflow-y-auto">
                  {projects.map((project) => (
                    <li key={project.id}>
                      <button
                        type="button"
                        onClick={() => {
                          if (project.id === currentId) {
                            setOpen(false);
                            return;
                          }
                          switchProject(project.id);
                        }}
                        className={cn(
                          "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] transition-colors hover:bg-muted",
                          project.id === currentId && "bg-muted/60",
                        )}
                      >
                        <span className="flex size-3.5 shrink-0 items-center justify-center">
                          {project.id === currentId ? (
                            <Check className="size-3.5" strokeWidth={2} />
                          ) : null}
                        </span>
                        <span className="min-w-0 flex-1 truncate">
                          {project.name || project.id}
                        </span>
                        <ViewerDots viewers={project.viewers ?? []} />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <div className="my-1 h-px bg-border" />
              <button
                type="button"
                onClick={() => setCreating(true)}
                className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <Plus className="size-3.5" strokeWidth={1.75} />
                New project
              </button>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}

function ViewerDots({ viewers }: { viewers: string[] }) {
  if (viewers.length === 0) return null;
  const shown = viewers.slice(0, 4);
  const extra = viewers.length - shown.length;
  return (
    <span className="flex shrink-0 items-center -space-x-1">
      {shown.map((email) => (
        <span
          key={email}
          title={email}
          className="flex size-4 items-center justify-center rounded-full text-[9px] font-semibold text-white ring-1 ring-popover"
          style={{ backgroundColor: presenceColor(email) }}
        >
          {presenceInitial({ email, name: "" })}
        </span>
      ))}
      {extra > 0 ? (
        <span className="pl-1.5 text-[10px] text-muted-foreground">+{extra}</span>
      ) : null}
    </span>
  );
}

function NewProjectForm({ onCancel }: { onCancel: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);
    try {
      const project = await createProject({
        name: trimmed,
        description: description.trim(),
      });
      switchProject(project.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create project");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-2 p-2">
      <div className="text-[13px] font-medium">New project</div>
      <Input
        autoFocus
        placeholder="Project name"
        value={name}
        onChange={(event) => setName(event.target.value)}
        disabled={busy}
      />
      <Textarea
        placeholder="What is this project about? Goals, constraints, context…"
        value={description}
        onChange={(event) => setDescription(event.target.value)}
        disabled={busy}
        className="max-h-48 min-h-20 text-[13px]"
      />
      <p className="text-[11px] leading-snug text-muted-foreground">
        Planner and task agents get this as their brief.
      </p>
      {error ? (
        <p className="text-[11px] leading-snug text-destructive">{error}</p>
      ) : null}
      <div className="flex justify-end gap-1.5">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button type="submit" size="sm" disabled={busy || !name.trim()}>
          {busy ? "Creating…" : "Create"}
        </Button>
      </div>
    </form>
  );
}
