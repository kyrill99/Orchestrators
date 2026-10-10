"use client";

import { useEffect, useRef } from "react";
import { useOnViewportChange, useReactFlow, useStoreApi } from "@xyflow/react";
import type { Cursor, MultiplayerClient } from "@/lib/multiplayer/client";
import { presenceColor, presenceName } from "@/lib/presence";
import {
  useCursorRoom,
  useFollowViewport,
  useMultiplayerClient,
} from "@/components/multiplayer/provider";

const IDLE_FADE_MS = 5000;
const GONE_MS = 8000;

type Project = (c: Cursor) => { x: number; y: number; visible: boolean };

/**
 * Draws other people's cursors for one location with smooth interpolation.
 * Positions are recomputed every animation frame through `project`, so they
 * follow scrolling, zoom and resizes. One DOM node per cursor, updated
 * without React re-renders.
 */
function CursorLayer({
  client,
  location,
  project,
}: {
  client: MultiplayerClient;
  location: string;
  project: React.RefObject<Project>;
}) {
  const layer = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = layer.current;
    if (!root) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const nodes = new Map<string, { el: HTMLDivElement; x: number; y: number }>();
    let frame = 0;

    const make = (c: Cursor) => {
      const color = presenceColor(c.email);
      const el = document.createElement("div");
      el.className = "pointer-events-none absolute top-0 left-0 transition-opacity duration-500";
      el.innerHTML = `<svg width="16" height="16" viewBox="0 0 16 16" style="display:block"><path d="M1 1l5.2 13 1.9-5.3L13.5 6.8z" fill="${color}" stroke="white" stroke-width="1.2" stroke-linejoin="round"/></svg><span style="background:${color}" class="ml-3 -mt-1 inline-block rounded-full px-1.5 py-px text-[10px] font-medium whitespace-nowrap text-white shadow-sm"></span>`;
      (el.querySelector("span") as HTMLSpanElement).textContent = presenceName(c);
      root.appendChild(el);
      return el;
    };

    const tick = () => {
      const now = performance.now();
      const seen = new Set<string>();
      for (const c of client.cursors.values()) {
        if (c.location !== location || now - c.at > GONE_MS) continue;
        seen.add(c.session);
        const target = project.current(c);
        let node = nodes.get(c.session);
        if (!node) {
          node = { el: make(c), x: target.x, y: target.y };
          nodes.set(c.session, node);
        }
        const k = reduced ? 1 : 0.3;
        node.x += (target.x - node.x) * k;
        node.y += (target.y - node.y) * k;
        node.el.style.transform = `translate(${node.x}px, ${node.y}px)`;
        node.el.style.opacity = !target.visible ? "0" : now - c.at > IDLE_FADE_MS ? "0.25" : "1";
      }
      for (const [session, node] of nodes) {
        if (!seen.has(session)) {
          node.el.remove();
          nodes.delete(session);
        }
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      for (const node of nodes.values()) node.el.remove();
    };
  }, [client, location, project]);

  return <div ref={layer} className="pointer-events-none fixed inset-0 z-40 overflow-hidden" aria-hidden />;
}

/**
 * Live cursors over a scrolling container (task page, chat). Sends x as a
 * fraction of the width and y in content pixels (scroll included), so people
 * with different window sizes point at the same thing.
 */
export function ContainerCursors({
  location,
  container,
}: {
  location: string;
  container: React.RefObject<HTMLElement | null>;
}) {
  const client = useMultiplayerClient();
  useCursorRoom(location);
  const project = useRef<Project>(() => ({ x: 0, y: 0, visible: false }));

  useEffect(() => {
    const el = container.current;
    if (!client || !el) return;
    project.current = (c) => {
      const rect = el.getBoundingClientRect();
      const y = rect.top + c.y - el.scrollTop;
      return {
        x: rect.left + c.x * rect.width,
        y,
        visible: y >= rect.top && y <= rect.bottom,
      };
    };
    const onMove = (e: PointerEvent) => {
      const rect = el.getBoundingClientRect();
      client.sendCursor(
        location,
        (e.clientX - rect.left) / Math.max(rect.width, 1),
        e.clientY - rect.top + el.scrollTop,
      );
    };
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerleave", client.hideCursor);
    return () => {
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerleave", client.hideCursor);
    };
  }, [client, container, location]);

  if (!client) return null;
  return <CursorLayer client={client} location={location} project={project} />;
}

/**
 * Live cursors on a React Flow canvas, in flow coordinates, so they line up
 * across zoom, pan and window sizes. Render inside <ReactFlow>.
 */
export function FlowCursors({ location }: { location: string }) {
  const client = useMultiplayerClient();
  useCursorRoom(location);
  const flow = useReactFlow();
  const store = useStoreApi();
  const project = useRef<Project>(() => ({ x: 0, y: 0, visible: false }));

  useEffect(() => {
    const dom = store.getState().domNode;
    if (!client || !dom) return;
    project.current = (c) => {
      const p = flow.flowToScreenPosition({ x: c.x, y: c.y });
      const rect = dom.getBoundingClientRect();
      return {
        x: p.x,
        y: p.y,
        visible:
          p.x >= rect.left && p.x <= rect.right && p.y >= rect.top && p.y <= rect.bottom,
      };
    };
    const onMove = (e: PointerEvent) => {
      const p = flow.screenToFlowPosition({ x: e.clientX, y: e.clientY });
      client.sendCursor(location, Math.round(p.x * 10) / 10, Math.round(p.y * 10) / 10);
    };
    dom.addEventListener("pointermove", onMove);
    dom.addEventListener("pointerleave", client.hideCursor);
    return () => {
      dom.removeEventListener("pointermove", onMove);
      dom.removeEventListener("pointerleave", client.hideCursor);
    };
  }, [client, flow, store, location]);

  if (!client) return null;
  return <CursorLayer client={client} location={location} project={project} />;
}

/**
 * Everything multiplayer on a React Flow canvas: live cursors, sharing your
 * viewport (so others can follow you) and mirroring the viewport of whoever
 * you follow. Render inside <ReactFlow>.
 */
export function FlowMultiplayer({ location }: { location: string }) {
  const client = useMultiplayerClient();
  const flow = useReactFlow();
  useOnViewportChange({
    onChange: (viewport) => client?.sendViewport(location, viewport),
  });
  useFollowViewport(location, (v) => {
    void flow.setViewport({ x: v.x, y: v.y, zoom: v.zoom }, { duration: 120 });
  });
  return <FlowCursors location={location} />;
}
