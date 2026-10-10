import { Suspense } from "react";
import { TaskIssuePage } from "@/components/task/task-issue-page";

export default function TaskPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  return (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
          Loading issue…
        </div>
      }
    >
      <TaskIssueLoader params={params} />
    </Suspense>
  );
}

async function TaskIssueLoader({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <TaskIssuePage id={id} />;
}
