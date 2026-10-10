import { ApiError, apiBaseUrl } from "@/lib/api";

/**
 * Projects: each has its own task graph and a brief (description) that the
 * planner and task agents receive. The active project is the `orch_project`
 * cookie, sent with every same-origin request, so all other API calls are
 * project-scoped automatically.
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

export const DEFAULT_PROJECT_ID = "default";
const COOKIE_NAME = "orch_project";

async function projectRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (!response.ok) {
    const text = await response.text();
    let detail = text;
    try {
      detail = JSON.parse(text).detail ?? text;
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

/** The active project id from the cookie ("default" when unset / on the server). */
export function getCurrentProjectId(): string {
  if (typeof document === "undefined") return DEFAULT_PROJECT_ID;
  const parts = document.cookie ? document.cookie.split(";") : [];
  for (const part of parts) {
    const eq = part.indexOf("=");
    if (eq < 0) continue;
    const key = part.slice(0, eq).trim();
    if (key !== COOKIE_NAME) continue;
    const raw = part.slice(eq + 1).trim();
    try {
      const value = decodeURIComponent(raw);
      return value || DEFAULT_PROJECT_ID;
    } catch {
      return raw || DEFAULT_PROJECT_ID;
    }
  }
  return DEFAULT_PROJECT_ID;
}

/** Make `id` the active project and reload so every view refetches. */
export function switchProject(id: string): void {
  if (typeof document === "undefined") return;
  document.cookie = `${COOKIE_NAME}=${encodeURIComponent(id)}; path=/; max-age=31536000; samesite=lax`;
  window.location.assign("/project");
}
