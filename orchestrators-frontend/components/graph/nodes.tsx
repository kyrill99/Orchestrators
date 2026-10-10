"use client";

import { Handle, Position, type NodeProps } from "@xyflow/react";
import { StatusBadge } from "@/components/task/status-badge";
import type { TaskKind, TaskStatus } from "@/lib/fixtures";
import { cn } from "@/lib/utils";

export type GraphNodeData = {
  title: string;
  kind: TaskKind;
  status: TaskStatus;
};

function NodeChrome({
  data,
  selected,
  className,
}: {
  data: GraphNodeData;
  selected?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "min-w-[168px] max-w-[220px] cursor-pointer border bg-background px-3 py-2.5 shadow-[0_1px_2px_rgba(0,0,0,0.04)] transition-[border-color,box-shadow]",
        selected
          ? "border-foreground/40 shadow-[0_1px_3px_rgba(0,0,0,0.08)]"
          : "border-border hover:border-foreground/20",
        className,
      )}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!size-1.5 !border-0 !bg-neutral-300"
      />
      <p className="text-[13px] leading-snug font-medium text-foreground">
        {data.title}
      </p>
      {data.kind !== "start" ? (
        <div className="mt-1.5">
          <StatusBadge status={data.status} />
        </div>
      ) : null}
      <Handle
        type="source"
        position={Position.Bottom}
        className="!size-1.5 !border-0 !bg-neutral-300"
      />
    </div>
  );
}

export function StartNode({ data, selected }: NodeProps) {
  return (
    <NodeChrome
      data={data as GraphNodeData}
      selected={selected}
      className="rounded-full px-4 text-center"
    />
  );
}

export function TaskNode({ data, selected }: NodeProps) {
  return (
    <NodeChrome
      data={data as GraphNodeData}
      selected={selected}
      className="rounded-md"
    />
  );
}

export function DecisionNode({ data, selected }: NodeProps) {
  const nodeData = data as GraphNodeData;
  return (
    <div className="relative flex h-[108px] w-[108px] cursor-pointer items-center justify-center">
      <Handle
        type="target"
        position={Position.Top}
        className="!size-1.5 !border-0 !bg-neutral-300"
      />
      <div
        className={cn(
          "flex size-[76px] rotate-45 items-center justify-center border bg-background shadow-[0_1px_2px_rgba(0,0,0,0.04)]",
          selected
            ? "border-foreground/40"
            : "border-border hover:border-foreground/20",
        )}
      >
        <div className="-rotate-45 px-1.5 text-center">
          <p className="text-[11px] leading-tight font-medium text-foreground">
            {nodeData.title}
          </p>
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

export function EndNode({ data, selected }: NodeProps) {
  return (
    <NodeChrome
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
