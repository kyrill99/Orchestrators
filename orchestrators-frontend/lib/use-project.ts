"use client";

import { useParams } from "next/navigation";
import { projectIdFromPath } from "@/lib/project-scope";

/**
 * The project in the URL (`/p/<id>/…`), or null on global pages. Suspends while
 * prerendering project pages, so call it inside a Suspense boundary.
 */
export function useProjectId(): string | null {
  const params = useParams<{ project?: string }>();
  const raw = params?.project;
  return typeof raw === "string" ? projectIdFromPath(`/p/${raw}`) : null;
}
