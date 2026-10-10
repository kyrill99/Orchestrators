"use client";

import { Menu } from "@base-ui/react/menu";
import { statusLabel, type TaskStatus } from "@/lib/fixtures";
import { cn } from "@/lib/utils";
import { StatusIcon } from "@/components/task/status-icon";

const STATUSES: TaskStatus[] = ["open", "running", "in-review", "closed"];

export function StatusPicker({
  value,
  onChange,
  disabled = false,
}: {
  value: TaskStatus;
  onChange: (status: TaskStatus) => void;
  disabled?: boolean;
}) {
  return (
    <Menu.Root>
      <Menu.Trigger
        disabled={disabled}
        className={cn(
          "flex w-full items-center gap-2 rounded-md px-1.5 py-1.5 text-left text-[13px] text-foreground",
          "outline-none transition-colors hover:bg-accent/70",
          "focus-visible:bg-accent/70 focus-visible:ring-2 focus-visible:ring-ring/40",
          "disabled:pointer-events-none disabled:opacity-50",
        )}
        aria-label={`Status: ${statusLabel[value]}`}
      >
        <StatusIcon status={value} />
        <span className="min-w-0 truncate">{statusLabel[value]}</span>
      </Menu.Trigger>

      <Menu.Portal>
        <Menu.Positioner className="z-50 outline-none" sideOffset={4} align="start">
          <Menu.Popup
            className={cn(
              "min-w-[180px] origin-[var(--transform-origin)] rounded-lg border border-border bg-background p-1 shadow-md outline-none",
              "data-[ending-style]:scale-95 data-[ending-style]:opacity-0",
              "data-[starting-style]:scale-95 data-[starting-style]:opacity-0",
              "transition-[transform,opacity] duration-100",
            )}
          >
            <Menu.RadioGroup
              value={value}
              onValueChange={(next) => {
                if (typeof next === "string") onChange(next as TaskStatus);
              }}
            >
              {STATUSES.map((status) => (
                <Menu.RadioItem
                  key={status}
                  value={status}
                  className={cn(
                    "flex cursor-default items-center gap-2.5 rounded-md px-2 py-1.5 text-[13px] outline-none select-none",
                    "data-[highlighted]:bg-accent data-[highlighted]:text-foreground",
                    "data-[checked]:bg-accent/50",
                  )}
                >
                  <StatusIcon status={status} />
                  <span>{statusLabel[status]}</span>
                </Menu.RadioItem>
              ))}
            </Menu.RadioGroup>
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  );
}
