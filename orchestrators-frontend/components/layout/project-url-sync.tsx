"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { getCurrentProjectId } from "@/lib/projects";

const PARAM = "project";
const COOKIE_NAME = "orch_project";

/**
 * Makes every URL say which project it belongs to, so a link to a task (or a
 * cycle, ?run=) opens in the right project for whoever follows it:
 * - `?project=<id>` in the URL wins: if it differs from the active project, the
 *   cookie is switched and the page reloads in that project.
 * - otherwise the current project is added to the address bar (replaceState).
 */
export function ProjectUrlSync() {
  const pathname = usePathname();

  useEffect(() => {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    const wanted = url.searchParams.get(PARAM);
    const current = getCurrentProjectId();
    if (wanted && wanted !== current) {
      document.cookie = `${COOKIE_NAME}=${encodeURIComponent(wanted)}; path=/; max-age=31536000; samesite=lax`;
      window.location.replace(url.toString());
      return;
    }
    if (!wanted) {
      url.searchParams.set(PARAM, current);
      window.history.replaceState(window.history.state, "", url.toString());
    }
  }, [pathname]);

  return null;
}
