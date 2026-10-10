"use client";

import { useCallback, useEffect, useMemo } from "react";
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
import { fetchActiveRuns } from "@/lib/api";
import { layoutGraph } from "@/lib/taskgraph-api";
import { useTaskGraph } from "@/lib/use-task-graph";

export function TaskGraph() {
  const router = useRouter();
  const { data, error, loading } = useTaskGraph();

  const built = useMemo(() => {
    const tasks = data?.tasks ?? [];
    const edges = data?.edges ?? [];
    const positions = layoutGraph(tasks, edges);
    const nodes: Node[] = tasks.map((task) => ({
      id: task.id,
      type: task.kind,
      position: positions[task.id] ?? { x: 0, y: 0 },
      data: {
        title: task.title,
        kind: task.kind,
        status: task.status,
      } satisfies GraphNodeData,
    }));
    const flowEdges: Edge[] = edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: "smoothstep",
      style: { stroke: "#e4e4e7", strokeWidth: 1.25 },
    }));
    return { nodes, edges: flowEdges };
  }, [data]);

  const [nodes, setNodes, onNodesChange] = useNodesState(built.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(built.edges);

  useEffect(() => {
    setNodes(built.nodes);
    setEdges(built.edges);
  }, [built, setNodes, setEdges]);

  // Mark tasks an agent is working on (from the live backend graph).
  useEffect(() => {
    const refresh = () =>
      fetchActiveRuns().then((runs) => {
        const agentFor = new Map(runs.map((run) => [run.taskId, run.agent.id]));
        setNodes((current) =>
          current.map((node) => {
            const agent = agentFor.get(node.id);
            if ((node.data as GraphNodeData).agent === agent) return node;
            return { ...node, data: { ...node.data, agent } };
          }),
        );
      });
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [setNodes]);

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
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
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
        <Controls
          showInteractive={false}
          position="bottom-left"
          className="!m-3 !overflow-hidden !rounded-md !border !border-border !bg-background !shadow-none"
        />
      </ReactFlow>
    </div>
  );
}
