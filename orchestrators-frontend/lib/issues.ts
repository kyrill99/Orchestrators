import { apiBaseUrl } from "@/lib/api";

// Issue list (see orchestrators-backend/issues.py). The author is the
// signed-in user (oauth2-proxy); agents appear as "agent:<id>".

export type IssueStatus = "draft" | "open" | "in_progress" | "closed";

export type IssuePerson = { email: string; name: string };

export type IssueAttachment = {
  id: string;
  issue_id: string;
  filename: string;
  content_type: string;
  size: number;
  author: IssuePerson;
  created_at: string;
  /** Same-origin path, e.g. /api/issues/<id>/attachments/<id>. */
  url: string;
};

export type IssueComment = {
  id: string;
  issue_id: string;
  author: IssuePerson;
  body: string;
  created_at: string;
};

/** What the bug-report button records about the page. */
export type IssueContext = {
  url?: string;
  userAgent?: string;
  viewport?: string;
  devicePixelRatio?: number;
  [key: string]: unknown;
};

export type Issue = {
  id: string;
  /** Null while the issue is a draft. */
  number: number | null;
  title: string;
  description: string;
  status: IssueStatus;
  author: IssuePerson;
  assignee: string | null;
  labels: string[];
  context: IssueContext;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
  closed_by: IssuePerson | null;
  close_reason: string | null;
  attachments: IssueAttachment[];
  comment_count: number;
  /** Only on getIssue. */
  comments?: IssueComment[];
};

export type IssueUpdate = Partial<
  Pick<Issue, "title" | "description" | "status" | "labels" | "context">
> & { assignee?: string };

export const issueStatusLabel: Record<IssueStatus, string> = {
  draft: "Draft",
  open: "Open",
  in_progress: "In progress",
  closed: "Closed",
};

/** Window event fired when issues change outside the Issues page (bug button). */
export const ISSUES_CHANGED = "orchestrators:issues-changed";

export function notifyIssuesChanged() {
  window.dispatchEvent(new Event(ISSUES_CHANGED));
}

/** Absolute URL for an attachment (the API may be on another origin in dev). */
export function attachmentSrc(attachment: IssueAttachment): string {
  return `${apiBaseUrl()}${attachment.url}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl()}/api/issues${path}`, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    throw new Error(
      (typeof detail === "string" ? detail : detail?.message) ??
        `Request failed (${response.status})`,
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

function json(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  };
}

export function listIssues(params: {
  status?: string;
  q?: string;
  author?: string;
}): Promise<Issue[]> {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) search.set(key, value);
  }
  const qs = search.toString();
  return request(qs ? `?${qs}` : "");
}

export function getIssue(id: string): Promise<Issue> {
  return request(`/${encodeURIComponent(id)}`);
}

export function createIssue(body: {
  title?: string;
  description?: string;
  status?: "draft" | "open";
  context?: IssueContext;
}): Promise<Issue> {
  return request("", json("POST", body));
}

export function updateIssue(id: string, body: IssueUpdate): Promise<Issue> {
  return request(`/${encodeURIComponent(id)}`, json("PATCH", body));
}

export function deleteIssue(id: string): Promise<void> {
  return request(`/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function publishIssue(id: string): Promise<Issue> {
  return request(`/${encodeURIComponent(id)}/publish`, json("POST"));
}

export function closeIssue(id: string, reason?: string): Promise<Issue> {
  return request(`/${encodeURIComponent(id)}/close`, json("POST", { reason }));
}

export function reopenIssue(id: string): Promise<Issue> {
  return request(`/${encodeURIComponent(id)}/reopen`, json("POST"));
}

export function addIssueComment(id: string, body: string): Promise<IssueComment> {
  return request(`/${encodeURIComponent(id)}/comments`, json("POST", { body }));
}

export function uploadIssueAttachment(
  id: string,
  file: Blob,
  filename: string,
): Promise<IssueAttachment> {
  const form = new FormData();
  form.append("file", file, filename);
  return request(`/${encodeURIComponent(id)}/attachments`, {
    method: "POST",
    body: form,
  });
}

export function deleteIssueAttachment(
  id: string,
  attachmentId: string,
): Promise<void> {
  return request(
    `/${encodeURIComponent(id)}/attachments/${encodeURIComponent(attachmentId)}`,
    { method: "DELETE" },
  );
}

/** "2 h ago" style for recent dates, else a short date. */
export function formatWhen(iso: string): string {
  const then = new Date(iso);
  const seconds = (Date.now() - then.getTime()) / 1000;
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  if (seconds < 7 * 86400) return `${Math.floor(seconds / 86400)} d ago`;
  return then.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: then.getFullYear() === new Date().getFullYear() ? undefined : "numeric",
  });
}

/** Short "Chrome 141 · macOS" from a user agent string. */
export function describeBrowser(userAgent: string | undefined): string {
  if (!userAgent) return "";
  const browser =
    userAgent.match(/(Edg|OPR|Firefox|Chrome|Version)\/(\d+)/) ?? undefined;
  let name = browser?.[1] ?? "Browser";
  if (name === "Version") name = /Safari/.test(userAgent) ? "Safari" : name;
  if (name === "Edg") name = "Edge";
  if (name === "OPR") name = "Opera";
  const os = /Windows/.test(userAgent)
    ? "Windows"
    : /Android/.test(userAgent)
      ? "Android"
      : /iPhone|iPad/.test(userAgent)
        ? "iOS"
        : /Mac OS X/.test(userAgent)
          ? "macOS"
          : /Linux/.test(userAgent)
            ? "Linux"
            : "";
  return [browser ? `${name} ${browser[2]}` : name, os].filter(Boolean).join(" · ");
}
