/**
 * The current project comes from the URL: project-scoped pages live under
 * `/p/<projectId>/…` (agent, project, tasks/<id>, memory). Every project-scoped API
 * call carries it explicitly (header `X-Orch-Project` on fetch, `?project=` on
 * EventSource / WebSocket URLs), so two tabs on different projects never mix.
 *
 * The `orch_project` cookie only remembers the last-used project: old URLs
 * (`/project`, `/tasks/<id>`, `/agent`, `/memory`) redirect to it in
 * next.config.ts, and global pages (issues) fall back to it.
 */

export const DEFAULT_PROJECT_ID = "default";
export const PROJECT_HEADER = "X-Orch-Project";
const COOKIE_NAME = "orch_project";

/** Project id from a `/p/<id>/…` pathname, or null for global pages. */
export function projectIdFromPath(pathname: string): string | null {
  const match = pathname.match(/^\/p\/([^/]+)/);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1]) || null;
  } catch {
    return match[1];
  }
}

/** The page inside the project: `/p/x/tasks/1` → `/tasks/1`; unchanged otherwise. */
export function pagePath(pathname: string): string {
  return projectIdFromPath(pathname) === null
    ? pathname
    : pathname.replace(/^\/p\/[^/]+/, "") || "/";
}

/**
 * URL of a project-scoped page, e.g. `projectPath("a", "/tasks/1")`. Without a
 * project (on a global page) it returns the old URL, which redirects to the
 * last-used project.
 */
export function projectPath(projectId: string | null, page: string): string {
  return projectId ? `/p/${encodeURIComponent(projectId)}${page}` : page;
}

function readCookie(): string {
  const parts = document.cookie ? document.cookie.split(";") : [];
  for (const part of parts) {
    const eq = part.indexOf("=");
    if (eq < 0 || part.slice(0, eq).trim() !== COOKIE_NAME) continue;
    const raw = part.slice(eq + 1).trim();
    try {
      return decodeURIComponent(raw) || DEFAULT_PROJECT_ID;
    } catch {
      return raw || DEFAULT_PROJECT_ID;
    }
  }
  return DEFAULT_PROJECT_ID;
}

/** The project this document was loaded for (state outside the page belongs to it). */
let loadedProject: string | null = null;

/**
 * The current project: from the URL on project pages, else the project this
 * tab was loaded with (the last-used one). "default" on the server.
 */
export function getCurrentProjectId(): string {
  if (typeof window === "undefined") return DEFAULT_PROJECT_ID;
  const fromUrl = projectIdFromPath(window.location.pathname);
  if (loadedProject === null) loadedProject = fromUrl ?? readCookie();
  return fromUrl ?? loadedProject;
}

/** The project the tab's long-lived state (sockets, presence, side nav) was set up for. */
export function getLoadedProjectId(): string {
  getCurrentProjectId();
  return loadedProject ?? DEFAULT_PROJECT_ID;
}

/** Remember `id` as the last-used project (target of old-URL redirects). */
export function rememberProject(id: string): void {
  if (typeof document === "undefined") return;
  document.cookie = `${COOKIE_NAME}=${encodeURIComponent(id)}; path=/; max-age=31536000; samesite=lax`;
}

/** `fetch` for project-scoped API calls: adds the current project header. */
export function projectFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set(PROJECT_HEADER, getCurrentProjectId());
  return fetch(input, { ...init, headers });
}

/** Adds `project=<current>` to an EventSource / WebSocket URL (no custom headers there). */
export function withProjectQuery(url: string): string {
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}project=${encodeURIComponent(getCurrentProjectId())}`;
}
