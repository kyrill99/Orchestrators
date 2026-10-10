import { ApiError, apiBaseUrl } from "@/lib/api";
import {
  pagePath,
  projectFetch,
  projectPath,
  rememberProject,
} from "@/lib/project-scope";

export {
  DEFAULT_PROJECT_ID,
  getCurrentProjectId,
} from "@/lib/project-scope";

/**
 * Projects: each has its own task graph and a brief (description) that the
 * planner and task agents receive. The current project is in the URL
 * (`/p/<id>/…`, see lib/project-scope.ts); API calls carry it explicitly.
 */

export type Project = {
  id: string;
  name: string;
  description: string;
  createdBy: string | null;
  createdAt: string;
  /** Emails of teammates whose presence heartbeat is in this project. */
  viewers: string[];
};

async function projectRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await projectFetch(`${apiBaseUrl()}${path}`, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (!response.ok) {
    const text = await response.text();
    let detail = text;
    try {
      const raw: unknown = JSON.parse(text).detail;
      if (typeof raw === "string") {
        detail = raw;
      } else if (Array.isArray(raw)) {
        // FastAPI validation errors: [{loc, msg, ...}]
        detail = raw
          .map((e: { msg?: string; loc?: unknown[] }) =>
            [Array.isArray(e.loc) ? e.loc.join(".") : "", e.msg ?? ""].filter(Boolean).join(": "),
          )
          .join("; ");
      } else if (raw && typeof raw === "object" && "message" in raw) {
        detail = String((raw as { message: unknown }).message);
      }
    } catch {
      // plain-text error body
    }
    throw new ApiError(
      detail || `${path} failed (${response.status})`,
      response.status,
    );
  }
  return response.json() as Promise<T>;
}

export async function fetchProjects(): Promise<Project[]> {
  const data = await projectRequest<Project[]>("/api/projects");
  return Array.isArray(data) ? data : [];
}

export async function fetchCurrentProject(): Promise<Project> {
  return projectRequest<Project>("/api/projects/current");
}

export async function createProject(input: {
  name: string;
  description: string;
}): Promise<Project> {
  return projectRequest<Project>("/api/projects", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function updateProject(
  id: string,
  input: { name?: string; description?: string },
): Promise<Project> {
  return projectRequest<Project>(`/api/projects/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

/**
 * Open project `id` on the same kind of page (agent and memory stay; the
 * project graph, a task or a global page go to the project graph). A full page
 * load, so sockets and presence start over in the new project.
 */
export function switchProject(id: string): void {
  if (typeof window === "undefined") return;
  const page = pagePath(window.location.pathname);
  const target = ["/agent", "/memory"].find((p) => page.startsWith(p)) ?? "/project";
  rememberProject(id);
  window.location.assign(projectPath(id, target));
}
