"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Background,
  Controls,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { nodeTypes, type GraphNodeData } from "@/components/graph/nodes";
import { ASSIGNEE_CHANGED_EVENT, loadAssignee } from "@/lib/assignees";
import { fetchActiveRuns } from "@/lib/api";
import { layoutGraph } from "@/lib/taskgraph-api";
import { useTaskGraph } from "@/lib/use-task-graph";
import { setSelection } from "@/lib/presence";
import { FlowMultiplayer } from "@/components/multiplayer/cursors";

export function TaskGraph() {
  const router = useRouter();
  const { data, error, loading, reload } = useTaskGraph();
  /** Bumps when local assignees change so nodes rebuild. */
  const [assigneeTick, setAssigneeTick] = useState(0);

  useEffect(() => {
    const onAssignee = () => setAssigneeTick((n) => n + 1);
    window.addEventListener(ASSIGNEE_CHANGED_EVENT, onAssignee);
    window.addEventListener("storage", onAssignee);
    return () => {
      window.removeEventListener(ASSIGNEE_CHANGED_EVENT, onAssignee);
      window.removeEventListener("storage", onAssignee);
    };
  }, []);

  const built = useMemo(() => {
    const tasks = data?.tasks ?? [];
    const edges = data?.edges ?? [];
    const positions = layoutGraph(tasks, edges);
    const seq = data?.seq ?? 0;
    const nodes: Node[] = tasks.map((task) => ({
      id: task.id,
      type: task.kind,
      position: positions[task.id] ?? { x: 0, y: 0 },
      data: {
        title: task.title,
        kind: task.kind,
        status: task.status,
        assigneeId: loadAssignee(task.id),
        seq,
      } satisfies GraphNodeData & { seq: number },
    }));
    const flowEdges: Edge[] = edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: "smoothstep",
      style: { stroke: "#e4e4e7", strokeWidth: 1.25 },
    }));
    return { nodes, edges: flowEdges, seq };
  }, [data, assigneeTick]);

  const [nodes, setNodes, onNodesChange] = useNodesState(built.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(built.edges);

  useEffect(() => {
    setNodes((current) => {
      const prevAgent = new Map(
        current.map((node) => [node.id, (node.data as GraphNodeData).agent]),
      );
      return built.nodes.map((node) => {
        const agent = prevAgent.get(node.id);
        return agent
          ? { ...node, data: { ...(node.data as GraphNodeData), agent } }
          : node;
      });
    });
    setEdges(built.edges);
  }, [built, setNodes, setEdges]);

  useEffect(() => {
    const onFocus = () => {
      setAssigneeTick((n) => n + 1);
      void reload();
    };
    window.addEventListener("focus", onFocus);
    const onVisible = () => {
      if (document.visibilityState === "visible") onFocus();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [reload]);

  useEffect(() => {
    const refresh = () =>
      fetchActiveRuns().then((runs) => {
        const agentFor = new Map(runs.map((run) => [run.taskId, run.agent.id]));
        setNodes((current) =>
          current.map((node) => {
            const data = node.data as GraphNodeData;
            const agent = agentFor.get(node.id);
            if (data.agent === agent) return node;
            return { ...node, data: { ...data, agent } };
          }),
        );
      });
    refresh();
    const id = setInterval(refresh, 2000);
    return () => clearInterval(id);
  }, [setNodes]);

  // Others see what you select as an outline in your color (multiplayer).
  const onSelectionChange = useCallback(({ nodes: selected }: { nodes: Node[] }) => {
    setSelection(selected[0]?.id ?? null);
  }, []);
  useEffect(() => () => setSelection(null), []);

  const onNodeClick: NodeMouseHandler = useCallback(
    (_event, node) => {
      router.push(`/tasks/${node.id}`);
    },
    [router],
  );

  if (loading && !data) {
    return (
      <div className="flex h-full items-center justify-center bg-canvas text-[13px] text-muted-foreground">
        Loading graph…
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="flex h-full items-center justify-center bg-canvas px-6 text-center text-[13px] text-red-600">
        {error}
      </div>
    );
  }

  if (!built.nodes.length) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 bg-canvas px-6 text-center">
        <p className="text-[13px] font-medium text-foreground">No tasks yet</p>
        <p className="max-w-sm text-xs text-muted-foreground">
          Talk to the Agent about goals and success. When the strategy is clear,
          it will create the project graph here.
        </p>
      </div>
    );
  }

  return (
    <div className="h-full w-full bg-canvas">
      <ReactFlow
        key={`${built.seq}-${assigneeTick}`}
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onSelectionChange={onSelectionChange}
        nodeTypes={nodeTypes}
        onNodeClick={onNodeClick}
        fitView
        fitViewOptions={{ padding: 0.24 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        panOnScroll
        className="cursor-grab"
      >
        <Background gap={20} size={1} color="#ececef" />
        <FlowMultiplayer location="graph" />
        <Controls
          showInteractive={false}
          position="bottom-left"
          className="!m-3 !overflow-hidden !rounded-md !border !border-border !bg-background !shadow-none"
        />
      </ReactFlow>
    </div>
  );
}
