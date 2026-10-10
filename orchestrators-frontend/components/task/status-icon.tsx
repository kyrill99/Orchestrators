import type { TaskStatus } from "@/lib/fixtures";
import { cn } from "@/lib/utils";

/** Linear-style status glyphs (Todo / In Progress / In Review / Done). */
export function StatusIcon({
  status,
  className,
}: {
  status: TaskStatus;
  className?: string;
}) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden
      className={cn("size-3.5 shrink-0", className)}
    >
      {status === "open" ? <TodoIcon /> : null}
      {status === "running" ? <InProgressIcon /> : null}
      {status === "in-review" ? <InReviewIcon /> : null}
      {status === "closed" ? <DoneIcon /> : null}
    </svg>
  );
}

/** Empty circle — Linear "Todo" */
function TodoIcon() {
  return (
    <circle
      cx="8"
      cy="8"
      r="6"
      stroke="currentColor"
      strokeWidth="1.5"
      className="text-neutral-400"
    />
  );
}

/** Left half filled — Linear "In Progress" */
function InProgressIcon() {
  return (
    <>
      <circle cx="8" cy="8" r="6" stroke="#F2C94C" strokeWidth="1.5" />
      <path d="M8 2a6 6 0 0 0 0 12V2Z" fill="#F2C94C" />
    </>
  );
}

/** Three-quarter fill — "In Review" */
function InReviewIcon() {
  return (
    <>
      <circle cx="8" cy="8" r="6" stroke="#F2C94C" strokeWidth="1.5" />
      <path d="M8 2A6 6 0 1 1 2 8L8 8Z" fill="#F2C94C" />
    </>
  );
}

/** Solid indigo check — Linear "Done" */
function DoneIcon() {
  return (
    <>
      <circle cx="8" cy="8" r="7" fill="#5E6AD2" />
      <path
        d="M5.2 8.1 7.1 10l3.7-4.2"
        stroke="white"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </>
  );
}
