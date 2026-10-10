import { Suspense } from "react";
import { ProjectScope } from "@/components/layout/project-scope";

/** Project-scoped pages: `/p/<projectId>/agent`, `/project`, `/tasks/<id>`, `/memory`. */
export default function ProjectLayout({ children }: LayoutProps<"/p/[project]">) {
  // The project id is only known per request, so URL hooks below suspend while
  // prerendering.
  return (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
          Loading…
        </div>
      }
    >
      <ProjectScope>{children}</ProjectScope>
    </Suspense>
  );
}
