"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Box,
  Brain,
  ChevronDown,
  MousePointer2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { MiniGraph } from "@/components/layout/mini-graph";
import { SystemStats } from "@/components/layout/system-stats";
import { UpdateControl } from "@/components/layout/update-control";

const items = [
  { href: "/agent", label: "Agent", icon: MousePointer2 },
  { href: "/project", label: "Project", icon: Box },
  { href: "/memory", label: "Memory", icon: Brain },
] as const;

export function SideNav() {
  const pathname = usePathname();

  return (
    <aside className="flex w-[220px] shrink-0 flex-col bg-shell px-3 py-3 text-sidebar-foreground">
      <Link
        href="/agent"
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

      <nav className="flex flex-col gap-0.5">
        {items.map(({ href, label, icon: Icon }) => {
          const active =
            pathname === href ||
            (href === "/project" && pathname.startsWith("/tasks"));

          return (
            <Link
              key={href}
              href={href}
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
