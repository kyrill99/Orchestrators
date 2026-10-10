"use client";

import { Bot, CircleUserRound } from "lucide-react";
import { Menu } from "@base-ui/react/menu";
import {
  ASSIGNEES,
  assigneeLabel,
  getAssignee,
  type Assignee,
  type AssigneeId,
} from "@/lib/assignees";
import { cn } from "@/lib/utils";

export function AssigneeAvatar({
  assignee,
  className,
}: {
  assignee: Assignee;
  className?: string;
}) {
  if (assignee.kind === "unassigned") {
    return (
      <span
        className={cn(
          "inline-flex size-4 shrink-0 items-center justify-center text-muted-foreground",
          className,
        )}
        aria-hidden
      >
        <CircleUserRound className="size-4" strokeWidth={1.5} />
      </span>
    );
  }

  if (assignee.kind === "agent") {
    return (
      <span
        className={cn(
          "inline-flex size-4 shrink-0 items-center justify-center rounded-full bg-violet-100 text-violet-700",
          className,
        )}
        aria-hidden
      >
        <Bot className="size-2.5" strokeWidth={2} />
      </span>
    );
  }

  return (
    <span
      className={cn(
        "inline-flex size-4 shrink-0 items-center justify-center rounded-full text-[9px] font-semibold text-white",
        assignee.color ?? "bg-neutral-500",
        className,
      )}
      aria-hidden
    >
      {assignee.name.slice(0, 1)}
    </span>
  );
}

export function AssigneePicker({
  value,
  onChange,
}: {
  value: AssigneeId;
  onChange: (id: AssigneeId) => void;
}) {
  const current = getAssignee(value);

  return (
    <Menu.Root>
      <Menu.Trigger
        className={cn(
          "flex w-full items-center gap-2 rounded-md px-1.5 py-1.5 text-left text-[13px] text-foreground",
          "outline-none transition-colors hover:bg-accent/70",
          "focus-visible:bg-accent/70 focus-visible:ring-2 focus-visible:ring-ring/40",
        )}
        aria-label={`Assignee: ${assigneeLabel(current)}`}
      >
        <AssigneeAvatar assignee={current} />
        <span
          className={cn(
            "min-w-0 truncate",
            current.kind === "unassigned" && "text-muted-foreground",
          )}
        >
          {current.kind === "human" && current.role
            ? `${current.name} — ${current.role}`
            : current.name}
        </span>
      </Menu.Trigger>

      <Menu.Portal>
        <Menu.Positioner className="z-50 outline-none" sideOffset={4} align="start">
          <Menu.Popup
            className={cn(
              "min-w-[240px] origin-[var(--transform-origin)] rounded-lg border border-border bg-background p-1 shadow-md outline-none",
              "data-[ending-style]:scale-95 data-[ending-style]:opacity-0",
              "data-[starting-style]:scale-95 data-[starting-style]:opacity-0",
              "transition-[transform,opacity] duration-100",
            )}
          >
            <Menu.RadioGroup
              value={value}
              onValueChange={(next) => {
                if (typeof next === "string") onChange(next as AssigneeId);
              }}
            >
              {ASSIGNEES.map((option) => (
                <Menu.RadioItem
                  key={option.id}
                  value={option.id}
                  className={cn(
                    "flex cursor-default items-center gap-2.5 rounded-md px-2 py-1.5 text-[13px] outline-none select-none",
                    "data-[highlighted]:bg-accent data-[highlighted]:text-foreground",
                    "data-[checked]:bg-accent/50",
                  )}
                >
                  <AssigneeAvatar assignee={option} />
                  <span className="min-w-0 flex-1 truncate">
                    {option.kind === "human" && option.role
                      ? `${option.name} — ${option.role}`
                      : option.name}
                  </span>
                </Menu.RadioItem>
              ))}
            </Menu.RadioGroup>
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  );
}
