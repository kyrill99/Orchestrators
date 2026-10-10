"use client";

import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Bot } from "lucide-react";
import { getAssignee, type AssigneeId } from "@/lib/assignees";
import { StatusBadge } from "@/components/task/status-badge";
import { StatusIcon } from "@/components/task/status-icon";
import { AssigneeAvatar } from "@/components/task/assignee-picker";
import { AvatarStack } from "@/components/presence/presence";
import { presenceColor, presenceName, useTaskPresence } from "@/lib/presence";
import { statusLabel, type TaskKind, type TaskStatus } from "@/lib/fixtures";
import { cn } from "@/lib/utils";

export type GraphNodeData = {
  title: string;
  kind: TaskKind;
  status: TaskStatus;
  /** Local / picker assignee for this task. */
  assigneeId?: AssigneeId;
  /** Agent with an active run on this task (live backend graph). */
  agent?: string;
};

/**
 * Teammates who have this task open, as small avatars on the node corner.
 * Someone who selected the node on their graph, or is editing the task, also
 * gets an outline in their color with a name tag (multiplayer).
 */
function NodePresence({ id }: { id: string }) {
  const people = useTaskPresence().get(id) ?? [];
  if (people.length === 0) return null;
  const open = people.filter((p) => p.open);
  const lead =
    people.find((p) => p.open && p.editing) ?? people.find((p) => p.selected);
  const color = lead ? presenceColor(lead.email) : undefined;
  return (
    <>
      {lead ? (
        <>
          <span
            className="pointer-events-none absolute -inset-[3px] z-10 rounded-[inherit] border-2"
            style={{ borderColor: color }}
          />
          <span
            className="pointer-events-none absolute -top-4 left-0 z-10 rounded-sm px-1 text-[9px] leading-[14px] font-medium whitespace-nowrap text-white"
            style={{ backgroundColor: color }}
          >
            {presenceName(lead)}
            {lead.open && lead.editing ? ` · editing ${lead.editing}` : ""}
          </span>
        </>
      ) : null}
      {open.length > 0 ? (
        <span className="pointer-events-none absolute -top-2.5 -right-2 z-10">
          <AvatarStack people={open} size={18} max={3} />
        </span>
      ) : null}
    </>
  );
}

/** Thin status cue on the node's left edge (Linear-style). */
const STATUS_ACCENT: Record<TaskStatus, string> = {
  open: "bg-neutral-300",
  running: "bg-[#5E6AD2]",
  "in-review": "bg-amber-400",
  closed: "bg-emerald-500",
};

const NODE_SURFACE =
  "border bg-background shadow-[0_1px_2px_rgba(0,0,0,0.04),0_4px_12px_-4px_rgba(0,0,0,0.06)] transition-[box-shadow,border-color] duration-150";
const NODE_HOVER =
  "group-hover:shadow-[0_1px_2px_rgba(0,0,0,0.05),0_8px_20px_-6px_rgba(0,0,0,0.10)]";
const NODE_SELECTED = "ring-2 ring-[#5E6AD2]/30";
const NODE_RUNNING =
  "motion-safe:animate-[mini-graph-breathe_2.8s_ease-in-out_infinite]";

/** One border colour per state so classes never fight. */
function borderClass(selected: boolean | undefined, running: boolean): string {
  if (running || selected) return "border-[#5E6AD2]/40";
  return "border-black/[0.08] group-hover:border-black/[0.12]";
}
const HANDLE_CLASS =
  "!size-1.5 !min-h-0 !min-w-0 !border-0 !bg-neutral-400 !opacity-0 transition-opacity group-hover:!opacity-100";

function NodeMeta({ data }: { data: GraphNodeData }) {
  const assignee = getAssignee(data.assigneeId ?? "unassigned");
  const showAssignee = assignee.kind !== "unassigned";

  return (
    <div className="mt-1 flex h-5 items-center justify-between gap-2">
      <span className="inline-flex min-w-0 items-center gap-1 text-[11px] leading-none text-muted-foreground tabular-nums">
        <StatusIcon status={data.status} />
        <span className="truncate">{statusLabel[data.status]}</span>
      </span>
      <span className="inline-flex shrink-0 items-center gap-1">
        {data.agent ? (
          <span
            className="inline-flex items-center gap-1 rounded-full bg-[#5E6AD2]/10 px-1.5 py-px text-[10px] font-medium text-[#5E6AD2]"
            title={`Agent ${data.agent} is working on this task`}
          >
            <Bot className="size-3 shrink-0 animate-pulse" strokeWidth={1.75} />
          </span>
        ) : null}
        {showAssignee ? (
          <AssigneeAvatar
            assignee={assignee}
            className="size-[18px] text-[9px]"
          />
        ) : null}
      </span>
    </div>
  );
}

function RunningPing() {
  return (
    <span className="pointer-events-none absolute -top-1 -left-1 z-10 flex size-2.5">
      <span className="absolute inline-flex size-full rounded-full bg-[#5E6AD2] opacity-60 motion-safe:animate-ping" />
      <span className="relative inline-flex size-2.5 rounded-full bg-[#5E6AD2] ring-2 ring-background" />
    </span>
  );
}

function NodeChrome({
  id,
  data,
  selected,
  className,
}: {
  id: string;
  data: GraphNodeData;
  selected?: boolean;
  className?: string;
}) {
  const running = data.status === "running";
  return (
    <div className="group relative w-[220px] cursor-pointer rounded-lg">
      <Handle type="target" position={Position.Top} className={HANDLE_CLASS} />
      <div
        className={cn(
          "relative overflow-hidden rounded-lg py-2 pr-3 pl-3.5",
          NODE_SURFACE,
          NODE_HOVER,
          borderClass(selected, running),
          selected && NODE_SELECTED,
          running && NODE_RUNNING,
          className,
        )}
      >
        <span
          aria-hidden
          className={cn(
            "pointer-events-none absolute inset-y-0 left-0 w-[3px]",
            STATUS_ACCENT[data.status],
          )}
        />
        <p className="line-clamp-2 text-[13px] leading-[18px] font-medium tracking-[-0.005em] text-foreground">
          {data.title}
        </p>
        <NodeMeta data={data} />
      </div>
      {running ? <RunningPing /> : null}
      <NodePresence id={id} />
      <Handle type="source" position={Position.Bottom} className={HANDLE_CLASS} />
    </div>
  );
}

/** Start / end milestones: a small centred pill on the same column width. */
function MilestoneNode({
  id,
  data,
  selected,
}: {
  id: string;
  data: GraphNodeData;
  selected?: boolean;
}) {
  const running = data.status === "running";
  return (
    <div className="group relative flex w-[220px] cursor-pointer justify-center">
      <Handle type="target" position={Position.Top} className={HANDLE_CLASS} />
      <div className="relative rounded-full">
        <div
          className={cn(
            "inline-flex h-7 max-w-[200px] items-center gap-1.5 rounded-full px-3",
            NODE_SURFACE,
            NODE_HOVER,
            borderClass(selected, running),
            selected && NODE_SELECTED,
            running && NODE_RUNNING,
          )}
        >
          <span
            aria-hidden
            className={cn("size-1.5 shrink-0 rounded-full", STATUS_ACCENT[data.status])}
          />
          <span className="truncate text-[12px] leading-none font-medium text-foreground">
            {data.title}
          </span>
          <StatusBadge status={data.status} iconOnly />
        </div>
        {running ? <RunningPing /> : null}
        <NodePresence id={id} />
      </div>
      <Handle type="source" position={Position.Bottom} className={HANDLE_CLASS} />
    </div>
  );
}

export function StartNode({ id, data, selected }: NodeProps) {
  return (
    <MilestoneNode id={id} data={data as GraphNodeData} selected={selected} />
  );
}

export function TaskNode({ id, data, selected }: NodeProps) {
  return (
    <NodeChrome id={id} data={data as GraphNodeData} selected={selected} />
  );
}

export function DecisionNode({ id, data, selected }: NodeProps) {
  const nodeData = data as GraphNodeData;
  const running = nodeData.status === "running";
  return (
    <div className="group relative flex h-[112px] w-[220px] cursor-pointer items-center justify-center">
      <Handle type="target" position={Position.Top} className={HANDLE_CLASS} />
      <div className="relative flex size-[112px] items-center justify-center">
        <NodePresence id={id} />
        <div
          className={cn(
            "absolute size-[78px] rotate-45 rounded-[6px]",
            NODE_SURFACE,
            NODE_HOVER,
            borderClass(selected, running),
            selected && NODE_SELECTED,
            running && NODE_RUNNING,
          )}
        />
        <div className="relative flex max-w-[84px] flex-col items-center gap-1 text-center">
          <p className="line-clamp-3 text-[11px] leading-[14px] font-medium tracking-[-0.005em] text-foreground">
            {nodeData.title}
          </p>
          <span className="inline-flex items-center gap-1">
            <StatusBadge status={nodeData.status} iconOnly />
            {nodeData.assigneeId &&
            nodeData.assigneeId !== "unassigned" ? (
              <AssigneeAvatar
                assignee={getAssignee(nodeData.assigneeId)}
                className="size-4 text-[9px]"
              />
            ) : null}
          </span>
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} className={HANDLE_CLASS} />
    </div>
  );
}

export function EndNode({ id, data, selected }: NodeProps) {
  return (
    <MilestoneNode id={id} data={data as GraphNodeData} selected={selected} />
  );
}

export const nodeTypes = {
  start: StartNode,
  task: TaskNode,
  decision: DecisionNode,
  end: EndNode,
};
