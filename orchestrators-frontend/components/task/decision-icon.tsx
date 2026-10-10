import { Signpost, type LucideProps } from "lucide-react";

/** The one icon for decision tasks: graph nodes, lists, timeline, nav. */
export function DecisionIcon(props: LucideProps) {
  return <Signpost strokeWidth={1.75} aria-label="Decision" {...props} />;
}
