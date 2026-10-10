"use client";

import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { fetchMe } from "@/lib/api";
import { presenceColor, presenceInitial, presenceName } from "@/lib/presence";
import {
  DEFAULT_PROJECT_ID,
  createProject,
  fetchProjects,
  getCurrentProjectId,
  switchProject,
  type Project,
} from "@/lib/projects";
import { cn } from "@/lib/utils";

/** Sidebar list of projects: current one highlighted, click to switch, + to create. */
export function ProjectList() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [failed, setFailed] = useState<boolean>(false);
  const [currentId, setCurrentId] = useState<string>(DEFAULT_PROJECT_ID);
  const [myEmail, setMyEmail] = useState<string | null>(null);
  const [creating, setCreating] = useState<boolean>(false);

  const load = useCallback(async () => {
    try {
      const list = await fetchProjects();
      setProjects(list);
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    setCurrentId(getCurrentProjectId());
    fetchMe().then(
      (me) => setMyEmail(me.email),
      () => {},
    );
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  if (failed && !projects) return null;
  if (projects === null) return null;

  return (
    <div className="mb-4 flex min-h-0 flex-col">
      <div className="mb-1 flex items-center px-2">
        <span className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
          Projects
        </span>
        <button
          type="button"
          aria-label="New project"
          title="New project"
          onClick={() => setCreating((v) => !v)}
          className="ml-auto inline-flex size-5 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground"
        >
          <Plus className="size-3.5" strokeWidth={1.75} />
        </button>
      </div>

      {creating ? (
        <SidebarNewProjectForm onCancel={() => setCreating(false)} />
      ) : null}

      <ul className="flex max-h-64 flex-col gap-0.5 overflow-y-auto">
        {projects.map((project) => {
          const active = project.id === currentId;
          const viewers = (project.viewers ?? []).filter(
            (email) => email !== myEmail,
          );
          return (
            <li key={project.id}>
              <button
                type="button"
                onClick={() => {
                  if (!active) switchProject(project.id);
                }}
                aria-current={active ? "true" : undefined}
                title={project.description || project.name || project.id}
                className={cn(
                  "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] transition-colors",
                  active
                    ? "bg-sidebar-accent font-medium text-foreground"
                    : "text-muted-foreground hover:bg-sidebar-accent/70 hover:text-foreground",
                )}
              >
                <span className="flex size-4 shrink-0 items-center justify-center">
                  <span
                    className={cn(
                      "size-1.5 rounded-full",
                      active ? "bg-foreground" : "bg-muted-foreground/40",
                    )}
                  />
                </span>
                <span className="min-w-0 flex-1 truncate">
                  {project.name || project.id}
                </span>
                <ViewerInitials viewers={viewers} />
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function ViewerInitials({ viewers }: { viewers: string[] }) {
  if (viewers.length === 0) return null;
  const shown = viewers.slice(0, 3);
  const extra = viewers.length - shown.length;
  const names = viewers
    .map((email) => presenceName({ email, name: "" }))
    .join(", ");
  return (
    <span className="flex shrink-0 items-center -space-x-1" title={names}>
      {shown.map((email) => (
        <span
          key={email}
          className="flex size-4 items-center justify-center rounded-full text-[9px] font-semibold text-white ring-1 ring-shell"
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

function SidebarNewProjectForm({ onCancel }: { onCancel: () => void }) {
  const [name, setName] = useState<string>("");
  const [description, setDescription] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
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
    <form
      onSubmit={onSubmit}
      className="mb-2 flex flex-col gap-1.5 rounded-md bg-sidebar-accent/50 p-2"
    >
      <Input
        autoFocus
        placeholder="Project name"
        value={name}
        onChange={(event) => setName(event.target.value)}
        disabled={busy}
        className="h-7 text-[13px]"
      />
      <Textarea
        placeholder="Goals, constraints, context…"
        value={description}
        onChange={(event) => setDescription(event.target.value)}
        disabled={busy}
        className="max-h-40 min-h-16 text-[12px]"
      />
      <p className="text-[11px] leading-snug text-muted-foreground">
        Planner and task agents get this as their brief
      </p>
      {error ? (
        <p className="text-[11px] leading-snug text-destructive">{error}</p>
      ) : null}
      <div className="flex justify-end gap-1">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onCancel}
          disabled={busy}
        >
          Cancel
        </Button>
        <Button type="submit" size="sm" disabled={busy || !name.trim()}>
          {busy ? "Creating…" : "Create"}
        </Button>
      </div>
    </form>
  );
}
