import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  cacheComponents: true,
  partialPrefetching: true,
  async redirects() {
    // Project pages live under /p/<projectId>/ (lib/project-scope.ts). The entry
    // URL and the old unscoped URLs open the last-used project (cookie
    // orch_project, a project slug), else the default one.
    const lastUsed = [
      { type: "cookie" as const, key: "orch_project", value: "(?<pid>[a-z0-9-]+)" },
    ];
    const toProject = (source: string, page: string) => [
      { source, has: lastUsed, destination: `/p/:pid${page}`, permanent: false },
      { source, destination: `/p/default${page}`, permanent: false },
    ];
    return [
      ...toProject("/", "/agent"),
      ...toProject("/agent", "/agent"),
      ...toProject("/project", "/project"),
      ...toProject("/tasks/:id", "/tasks/:id"),
      ...toProject("/memory", "/memory"),
      { source: "/p/:project", destination: "/p/:project/project", permanent: false },
    ];
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
