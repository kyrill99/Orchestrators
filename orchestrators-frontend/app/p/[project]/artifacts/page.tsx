import { Suspense } from "react";
import { ArtifactsTable } from "@/components/artifacts/artifacts-table";

export default function ArtifactsPage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
          Loading artifacts…
        </div>
      }
    >
      <ArtifactsTable />
    </Suspense>
  );
}
