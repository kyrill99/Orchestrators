"use client";

import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Bot } from "lucide-react";
import { getAssignee, type AssigneeId } from "@/lib/assignees";
import { StatusBadge } from "@/components/task/status-badge";
import { AssigneeAvatar } from "@/components/task/assignee-picker";
import { AvatarStack } from "@/components/presence/presence";
import { presenceColor, presenceName, useTaskPresence } from "@/lib/presence";
import type { TaskKind, TaskStatus } from "@/lib/fixtures";
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

function NodeMeta({ data }: { data: GraphNodeData }) {
  const assignee = getAssignee(data.assigneeId ?? "unassigned");
  const showAssignee = assignee.kind !== "unassigned";

  return (
    <div className="mt-1.5 flex items-center justify-between gap-2">
      <StatusBadge status={data.status} />
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
            className="size-5 text-[10px]"
          />
        ) : null}
      </span>
    </div>
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
  return (
    <div
      className={cn(
        "relative min-w-[168px] max-w-[220px] cursor-pointer border bg-background px-3 py-2.5 shadow-[0_1px_2px_rgba(0,0,0,0.04)] transition-[border-color,box-shadow]",
        selected
          ? "border-foreground/40 shadow-[0_1px_3px_rgba(0,0,0,0.08)]"
          : "border-border hover:border-foreground/20",
        className,
      )}
    >
      <NodePresence id={id} />
      <Handle
        type="target"
        position={Position.Top}
        className="!size-1.5 !border-0 !bg-neutral-300"
      />
      <p className="text-[13px] leading-snug font-medium text-foreground">
        {data.title}
      </p>
      <NodeMeta data={data} />
      <Handle
        type="source"
        position={Position.Bottom}
        className="!size-1.5 !border-0 !bg-neutral-300"
      />
    </div>
  );
}

export function StartNode({ id, data, selected }: NodeProps) {
  return (
    <NodeChrome
      id={id}
      data={data as GraphNodeData}
      selected={selected}
      className="rounded-full px-4 text-center"
    />
  );
}

export function TaskNode({ id, data, selected }: NodeProps) {
  return (
    <NodeChrome
      id={id}
      data={data as GraphNodeData}
      selected={selected}
      className="rounded-md"
    />
  );
}

export function DecisionNode({ id, data, selected }: NodeProps) {
  const nodeData = data as GraphNodeData;
  return (
    <div className="relative flex h-[120px] w-[120px] cursor-pointer items-center justify-center">
      <NodePresence id={id} />
      <Handle
        type="target"
        position={Position.Top}
        className="!size-1.5 !border-0 !bg-neutral-300"
      />
      <div
        className={cn(
          "flex size-[84px] rotate-45 items-center justify-center border bg-background shadow-[0_1px_2px_rgba(0,0,0,0.04)]",
          selected
            ? "border-foreground/40"
            : "border-border hover:border-foreground/20",
        )}
      >
        <div className="-rotate-45 flex flex-col items-center gap-1 px-1.5 text-center">
          <p className="text-[11px] leading-tight font-medium text-foreground">
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
      <Handle
        type="source"
        position={Position.Bottom}
        className="!size-1.5 !border-0 !bg-neutral-300"
      />
    </div>
  );
}

export function EndNode({ id, data, selected }: NodeProps) {
  return (
    <NodeChrome
      id={id}
      data={data as GraphNodeData}
      selected={selected}
      className="rounded-full px-4 text-center"
    />
  );
}

export const nodeTypes = {
  start: StartNode,
  task: TaskNode,
  decision: DecisionNode,
  end: EndNode,
};
