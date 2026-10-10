"use client";

import {
  ChevronDown,
  MessageCircle,
  PenSquare,
  SmilePlus,
  TrendingUp,
  AlertTriangle,
  Ban,
  Box,
  GitBranch,
  Brain,
} from "lucide-react";
import {
  progressHealthLabel,
  progressUpdates,
  type ProgressHealth,
  type ProgressMention,
  type ProgressUpdate,
} from "@/lib/fixtures";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";

const healthStyles: Record<
  ProgressHealth,
  { badge: string; Icon: typeof TrendingUp }
> = {
  "on-track": {
    badge: "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-600/15",
    Icon: TrendingUp,
  },
  "at-risk": {
    badge: "bg-amber-50 text-amber-800 ring-1 ring-inset ring-amber-600/15",
    Icon: AlertTriangle,
  },
  blocked: {
    badge: "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-600/15",
    Icon: Ban,
  },
};

const mentionIcon = {
  task: Box,
  decision: GitBranch,
  memory: Brain,
} as const;

export function ProgressPanel() {
  return (
    <aside className="flex h-full w-full flex-col border-l border-border bg-background md:w-[340px] lg:w-[380px]">
      <div className="flex h-10 shrink-0 items-center justify-between border-b border-border px-3">
        <button
          type="button"
          className="inline-flex items-center gap-1 text-[13px] font-medium text-foreground"
        >
          Latest update
          <ChevronDown className="size-3.5 text-muted-foreground" strokeWidth={1.75} />
        </button>
        <div className="flex items-center gap-1">
          <button
            type="button"
            className="px-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            See all
          </button>
          <button
            type="button"
            className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            aria-label="New update"
            disabled
          >
            <PenSquare className="size-3.5" strokeWidth={1.75} />
          </button>
        </div>
      </div>

      <ScrollArea className="flex-1">
        <ul className="flex flex-col gap-3 p-3">
          {progressUpdates.map((update, index) => (
            <li key={update.id}>
              <UpdateCard update={update} latest={index === 0} />
            </li>
          ))}
        </ul>
      </ScrollArea>
    </aside>
  );
}

function UpdateCard({
  update,
  latest,
}: {
  update: ProgressUpdate;
  latest: boolean;
}) {
  const { badge, Icon } = healthStyles[update.health];
  const mentionMap = Object.fromEntries(
    update.mentions.map((m) => [m.id, m]),
  ) as Record<string, ProgressMention>;

  return (
    <article
      className={cn(
        "rounded-xl border border-border bg-background p-3.5 shadow-[0_1px_2px_rgba(0,0,0,0.03)]",
        latest && "ring-1 ring-black/[0.03]",
      )}
    >
      <div className="mb-2.5 flex flex-wrap items-center gap-2">
        <span
          className={cn(
            "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium",
            badge,
          )}
        >
          <Icon className="size-3" strokeWidth={2} />
          {progressHealthLabel[update.health]}
        </span>
        <span className="flex size-5 items-center justify-center rounded-full bg-neutral-200 text-[9px] font-semibold text-neutral-600">
          {update.author.slice(0, 1).toUpperCase()}
        </span>
        <span className="text-xs text-muted-foreground">{update.author}</span>
        <span className="text-xs text-muted-foreground/70">{update.age}</span>
      </div>

      <p className="text-[13px] leading-relaxed text-foreground/90">
        {update.body.map((part, i) => {
          if (part.type === "text") {
            return <span key={i}>{part.value}</span>;
          }
          const mention = mentionMap[part.id];
          if (!mention) return null;
          const MIcon = mentionIcon[mention.kind];
          return (
            <span
              key={i}
              className="mx-0.5 inline-flex items-center gap-1 rounded-md bg-accent px-1.5 py-0.5 text-[12px] font-medium text-foreground align-baseline"
            >
              <MIcon className="size-3 opacity-70" strokeWidth={1.75} />
              {mention.label}
            </span>
          );
        })}
      </p>

      <div className="mt-3 flex items-center gap-1">
        <button
          type="button"
          className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          aria-label="Comment"
          disabled
        >
          <MessageCircle className="size-3.5" strokeWidth={1.75} />
        </button>
        <button
          type="button"
          className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          aria-label="Add reaction"
          disabled
        >
          <SmilePlus className="size-3.5" strokeWidth={1.75} />
        </button>
      </div>
    </article>
  );
}
