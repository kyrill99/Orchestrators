"use client";

import { useCallback, useEffect } from "react";
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
import { edges as fixtureEdges, tasks } from "@/lib/fixtures";
import { nodeTypes, type GraphNodeData } from "@/components/graph/nodes";
import { fetchActiveRuns } from "@/lib/api";

const layout: Record<string, { x: number; y: number }> = {
  start: { x: 420, y: 0 },
  research: { x: 380, y: 120 },
  "proto-1": { x: 80, y: 280 },
  "proto-2": { x: 380, y: 280 },
  "proto-3": { x: 680, y: 280 },
  decision: { x: 400, y: 460 },
  implement: { x: 380, y: 620 },
  deploy: { x: 400, y: 760 },
};

const initialNodes: Node[] = tasks.map((task) => ({
  id: task.id,
  type: task.kind,
  position: layout[task.id] ?? { x: 0, y: 0 },
  data: {
    title: task.title,
    kind: task.kind,
    status: task.status,
  } satisfies GraphNodeData,
}));

const initialEdges: Edge[] = fixtureEdges.map((edge) => ({
  id: edge.id,
  source: edge.source,
  target: edge.target,
  type: "smoothstep",
  style: { stroke: "#e4e4e7", strokeWidth: 1.25 },
}));

export function TaskGraph() {
  const router = useRouter();
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, , onEdgesChange] = useEdgesState(initialEdges);

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
