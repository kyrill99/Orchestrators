"use client";

import { Handle, Position, type NodeProps } from "@xyflow/react";
import { useEffect, useState } from "react";
import { Bot, Check, FileText, RotateCw } from "lucide-react";
import { getAssignee, type AssigneeId } from "@/lib/assignees";
import { StatusBadge } from "@/components/task/status-badge";
import { StatusIcon } from "@/components/task/status-icon";
import { DecisionIcon } from "@/components/task/decision-icon";
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
  /** Latest version of each artifact saved on this task. */
  artifacts?: GraphNodeArtifact[];
  /** Number of agent runs (attempts) on this task. */
  cycles?: number;
  /** Model tokens used across this task's agent runs. */
  tokens?: number;
  /** ISO start of the active agent run (live timer while running). */
  activeSince?: string;
  /** Decision tasks: the decision's state and, once decided, the choice. */
  decision?: {
    state: "pending" | "resolved";
    chosen?: string | null;
    recommended?: string | null;
    auto?: boolean;
  } | null;
};

export type GraphNodeArtifact = {
  id: string;
  filename: string;
  title: string;
  /** Absolute URL that renders the artifact (sandboxed). */
  viewUrl: string;
  version: number;
};

const MAX_ARTIFACT_ICONS = 3;

function stopEvent(event: { stopPropagation: () => void }) {
  event.stopPropagation();
}

/** Small clickable file chips; open the artifact in a new tab without opening the task. */
function NodeArtifacts({ artifacts }: { artifacts: GraphNodeArtifact[] }) {
  if (artifacts.length === 0) return null;
  const shown = artifacts.slice(0, MAX_ARTIFACT_ICONS);
  const hidden = artifacts.slice(MAX_ARTIFACT_ICONS);
  return (
    <span className="inline-flex items-center gap-0.5">
      {shown.map((artifact) => {
        const label = artifact.title || artifact.filename;
        const tip = `${label}${
          label !== artifact.filename ? `\n${artifact.filename}` : ""
        } · v${artifact.version}`;
        return (
          <a
            key={artifact.id}
            href={artifact.viewUrl}
            target="_blank"
            rel="noopener noreferrer"
            title={tip}
            aria-label={`Open artifact ${label} (v${artifact.version})`}
            onClick={stopEvent}
            onPointerDown={stopEvent}
            onMouseDown={stopEvent}
            onDoubleClick={stopEvent}
            className="nodrag nopan inline-flex size-[18px] items-center justify-center rounded-[4px] text-muted-foreground transition-colors hover:bg-black/[0.05] hover:text-foreground"
          >
            <FileText className="size-3" strokeWidth={1.75} />
          </a>
        );
      })}
      {hidden.length > 0 ? (
        <span
          className="px-0.5 text-[10px] leading-none text-muted-foreground tabular-nums"
          title={hidden
            .map((a) => `${a.title || a.filename} · v${a.version}`)
            .join("\n")}
        >
          +{hidden.length}
        </span>
      ) : null}
    </span>
  );
}

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

function formatTokens(n: number): string {
  if (n < 1000) return `${n}`;
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 100_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  const ss = String(sec).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** Live "m:ss" since the active run started; ticks every second. */
function RunTimer({ since }: { since: string }) {
  const start = Date.parse(since);
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  if (Number.isNaN(start)) return null;
  return (
    <span
      className="shrink-0 font-medium text-[#5E6AD2]"
      title={`Running since ${new Date(start).toLocaleString()}`}
    >
      {formatElapsed(now - start)}
    </span>
  );
}

/** Cycle count, token usage and live run timer; muted and compact. */
function NodeRunStats({ data }: { data: GraphNodeData }) {
  const cycles = data.cycles ?? 0;
  const tokens = data.tokens ?? 0;
  const since = data.status === "running" ? data.activeSince : undefined;
  if (cycles <= 0 && tokens <= 0 && !since) return null;
  return (
    <span className="ml-0.5 inline-flex shrink-0 items-center gap-1.5 text-[10px] leading-none text-muted-foreground tabular-nums">
      {since ? <RunTimer since={since} /> : null}
      {cycles > 0 ? (
        <span
          className="inline-flex items-center gap-0.5"
          title={`${cycles} agent ${cycles === 1 ? "run" : "runs"} on this task`}
        >
          <RotateCw className="size-2.5" strokeWidth={2} />
          {cycles}
        </span>
      ) : null}
      {tokens > 0 ? (
        <span title={`${tokens.toLocaleString()} tokens used by agent runs`}>
          {formatTokens(tokens)} tok
        </span>
      ) : null}
    </span>
  );
}

function NodeMeta({ data }: { data: GraphNodeData }) {
  const assignee = getAssignee(data.assigneeId ?? "unassigned");
  const showAssignee = assignee.kind !== "unassigned";

  return (
    <div className="mt-1 flex h-5 items-center justify-between gap-2">
      <span className="inline-flex min-w-0 items-center gap-1 text-[11px] leading-none text-muted-foreground tabular-nums">
        <StatusIcon status={data.status} />
        <span className="truncate">{statusLabel[data.status]}</span>
        <NodeRunStats data={data} />
      </span>
      <span className="inline-flex shrink-0 items-center gap-1">
        {data.artifacts && data.artifacts.length > 0 ? (
          <NodeArtifacts artifacts={data.artifacts} />
        ) : null}
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

/** What a decision node shows under the diamond: its state, or the choice. */
function decisionCaption(data: GraphNodeData): {
  text: string;
  className: string;
  title?: string;
} {
  const d = data.decision;
  if (data.status === "running") {
    return { text: "Preparing…", className: "text-[#5E6AD2]" };
  }
  if (d?.state === "resolved") {
    return {
      text: d.chosen ?? "Decided",
      className: "text-emerald-700 dark:text-emerald-400",
      title: `Decided: ${d.chosen}${d.auto ? " (by the agent, automatically)" : ""}`,
    };
  }
  if (d?.state === "pending") {
    return {
      text: "Your choice",
      className: "text-violet-700 dark:text-violet-300",
      title: d.recommended ? `Recommended: ${d.recommended}` : undefined,
    };
  }
  return { text: "Not prepared", className: "text-muted-foreground" };
}

/** Decision tasks: the flowchart diamond, with the state or the choice below it. */
export function DecisionNode({ id, data, selected }: NodeProps) {
  const nodeData = data as GraphNodeData;
  const running = nodeData.status === "running";
  const pending = nodeData.decision?.state === "pending" && !running;
  const caption = decisionCaption(nodeData);
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
            pending ? "border-violet-500/50" : borderClass(selected, running),
            selected && NODE_SELECTED,
            running && NODE_RUNNING,
          )}
        />
        <div className="relative flex max-w-[84px] flex-col items-center gap-0.5 text-center">
          <DecisionIcon
            className={cn(
              "size-3 shrink-0",
              pending ? "text-violet-600 dark:text-violet-400" : "text-muted-foreground",
            )}
          />
          <p className="line-clamp-3 text-[11px] leading-[14px] font-medium tracking-[-0.005em] text-foreground">
            {nodeData.title}
          </p>
          <span className="inline-flex items-center gap-1">
            <StatusBadge status={nodeData.status} iconOnly />
            {nodeData.artifacts && nodeData.artifacts.length > 0 ? (
              <NodeArtifacts artifacts={nodeData.artifacts.slice(-1)} />
            ) : null}
          </span>
        </div>
        {running ? <RunningPing /> : null}
      </div>
      <span
        title={caption.title}
        className={cn(
          "pointer-events-none absolute top-[104px] left-1/2 max-w-[200px] -translate-x-1/2 truncate rounded-full bg-background px-1.5 text-[10px] leading-4 font-medium",
          caption.className,
        )}
      >
        {nodeData.decision?.state === "resolved" ? (
          <Check className="mr-0.5 inline size-2.5 align-[-1px]" strokeWidth={2.5} />
        ) : null}
        {caption.text}
        {nodeData.decision?.state === "resolved" && nodeData.decision.auto ? " · auto" : ""}
      </span>
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
