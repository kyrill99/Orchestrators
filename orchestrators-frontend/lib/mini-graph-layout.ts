export type LayoutEdge = { source: string; target: string };

/** Node position in the unit square: x by dependency depth, y within the layer. */
export type LayoutPoint = { x: number; y: number };

export type GraphLayout = {
  points: Map<string, LayoutPoint>;
  /** Number of dependency layers (columns). */
  layers: number;
  /** Most nodes in any one layer (rows). */
  maxLayerSize: number;
};

/**
 * Cheap layered layout for small previews: each task sits in the column of its
 * longest blocker chain, and nodes in a column are ordered by the average row
 * of their blockers (one barycenter pass) so edges cross less. O(V + E log E).
 */
export function layeredLayout(
  ids: readonly string[],
  edges: readonly LayoutEdge[],
): GraphLayout {
  const known = new Set(ids);
  const parents = new Map<string, string[]>(ids.map((id) => [id, []]));
  const children = new Map<string, string[]>(ids.map((id) => [id, []]));
  const indegree = new Map<string, number>(ids.map((id) => [id, 0]));
  for (const { source, target } of edges) {
    if (!known.has(source) || !known.has(target) || source === target) continue;
    parents.get(target)!.push(source);
    children.get(source)!.push(target);
    indegree.set(target, indegree.get(target)! + 1);
  }

  // Longest-path depth via Kahn's algorithm. Nodes left over (a cycle, which
  // the backend forbids) stay in column 0.
  const depth = new Map<string, number>(ids.map((id) => [id, 0]));
  const queue = ids.filter((id) => indegree.get(id) === 0);
  for (let i = 0; i < queue.length; i++) {
    const id = queue[i];
    for (const child of children.get(id)!) {
      depth.set(child, Math.max(depth.get(child)!, depth.get(id)! + 1));
      const left = indegree.get(child)! - 1;
      indegree.set(child, left);
      if (left === 0) queue.push(child);
    }
  }

  const columns: string[][] = [];
  for (const id of ids) {
    const d = depth.get(id)!;
    (columns[d] ??= []).push(id);
  }

  const row = new Map<string, number>();
  columns.forEach((column, d) => {
    if (d > 0) {
      const center = (id: string) => {
        const ps = parents.get(id)!.filter((p) => row.has(p));
        if (ps.length === 0) return 0.5;
        return ps.reduce((sum, p) => sum + row.get(p)!, 0) / ps.length;
      };
      const keyed = column.map((id, index) => ({ id, index, key: center(id) }));
      keyed.sort((a, b) => a.key - b.key || a.index - b.index);
      column.splice(0, column.length, ...keyed.map((k) => k.id));
    }
    column.forEach((id, i) => row.set(id, (i + 0.5) / column.length));
  });

  const layers = Math.max(columns.length, 1);
  const points = new Map<string, LayoutPoint>();
  columns.forEach((column, d) => {
    const x = layers === 1 ? 0.5 : d / (layers - 1);
    for (const id of column) points.set(id, { x, y: row.get(id)! });
  });

  return {
    points,
    layers,
    maxLayerSize: Math.max(1, ...columns.map((c) => c.length)),
  };
}
