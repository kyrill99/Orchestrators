import { statusLabel, type TaskStatus } from "@/lib/fixtures";
import { StatusIcon } from "@/components/task/status-icon";
import { cn } from "@/lib/utils";

export function StatusBadge({
  status,
  className,
  iconOnly = false,
}: {
  status: TaskStatus;
  className?: string;
  iconOnly?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 text-[13px] text-foreground",
        className,
      )}
    >
      <StatusIcon status={status} />
      {iconOnly ? (
        <span className="sr-only">{statusLabel[status]}</span>
      ) : (
        <span className="text-muted-foreground">{statusLabel[status]}</span>
      )}
    </span>
  );
}
