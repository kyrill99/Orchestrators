"use client";

import { useEffect } from "react";
import { getLoadedProjectId, rememberProject } from "@/lib/project-scope";
import { useProjectId } from "@/lib/use-project";

/**
 * Keeps the tab on the project in the URL. Sockets, presence, the side nav and
 * the top bar are set up once per page load for one project, so a client-side
 * navigation into another project (back/forward, a link) reloads the page;
 * otherwise it records the project as the last used one.
 */
export function ProjectScope({ children }: { children: React.ReactNode }) {
  const projectId = useProjectId();

  useEffect(() => {
    if (!projectId) return;
    if (getLoadedProjectId() !== projectId) {
      window.location.reload();
      return;
    }
    rememberProject(projectId);
  }, [projectId]);

  return children;
}
