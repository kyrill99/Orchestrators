import { Suspense } from "react";
import { IssuesExplorer } from "@/components/issues/issues-explorer";

export default function IssuesPage() {
  // The explorer reads ?id= (useSearchParams), so it renders on the client.
  return (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
          Loading issues…
        </div>
      }
    >
      <IssuesExplorer />
    </Suspense>
  );
}
