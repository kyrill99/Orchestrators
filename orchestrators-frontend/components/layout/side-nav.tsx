"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Box,
  Brain,
  Bug,
  ChevronDown,
  MousePointer2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { pagePath, projectPath } from "@/lib/project-scope";
import { useProjectId } from "@/lib/use-project";
import { MiniGraph } from "@/components/layout/mini-graph";
import { ProjectList } from "@/components/layout/project-list";
import { SystemStats } from "@/components/layout/system-stats";
import { UpdateControl } from "@/components/layout/update-control";

// Agent, Project and Memory are per project (/p/<id>/…); issues are global.
const items = [
  { href: "/agent", label: "Agent", icon: MousePointer2, scoped: true },
  { href: "/project", label: "Project", icon: Box, scoped: true },
  { href: "/memory", label: "Memory", icon: Brain, scoped: true },
  { href: "/issues", label: "Issues", icon: Bug, scoped: false },
] as const;

export function SideNav() {
  const pathname = pagePath(usePathname());
  const projectId = useProjectId();

  return (
    <aside className="flex w-[220px] shrink-0 flex-col bg-shell px-3 py-3 text-sidebar-foreground">
      <Link
        href={projectPath(projectId, "/agent")}
        className="mb-4 flex items-center gap-2 rounded-md px-1.5 py-1 transition-colors hover:bg-sidebar-accent"
      >
        <span className="flex size-5 items-center justify-center rounded-full bg-foreground text-[10px] font-semibold text-background">
          O
        </span>
        <span className="truncate text-[13px] font-medium">Orchestrators</span>
        <ChevronDown
          className="ml-auto size-3.5 text-muted-foreground"
          strokeWidth={1.75}
        />
      </Link>

      <ProjectList />

      <nav className="flex flex-col gap-0.5">
        {items.map(({ href, label, icon: Icon, scoped }) => {
          const active =
            pathname === href ||
            (href === "/project" && pathname.startsWith("/tasks"));

          return (
            <Link
              key={href}
              href={scoped ? projectPath(projectId, href) : href}
              className={cn(
                "flex items-center gap-2 rounded-md px-2 py-1.5 text-[13px] transition-colors",
                active
                  ? "bg-sidebar-accent font-medium text-foreground"
                  : "text-muted-foreground hover:bg-sidebar-accent/70 hover:text-foreground",
              )}
              aria-current={active ? "page" : undefined}
            >
              <Icon className="size-4 shrink-0 opacity-80" strokeWidth={1.75} />
              {label}
            </Link>
          );
        })}
      </nav>


      <div className="mt-auto flex flex-col gap-3">
        <MiniGraph />
        <SystemStats />
        <UpdateControl />
      </div>
    </aside>
  );
}
