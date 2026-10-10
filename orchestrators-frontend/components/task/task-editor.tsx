"use client";

import { useEffect, useState } from "react";
import { Ban, Plus, Save, X } from "lucide-react";
import type { Task } from "@/lib/fixtures";
import { fetchTaskGraph, submitTaskOps, type TaskOp } from "@/lib/taskgraph-api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

type Props = {
  task: Task;
  onSaved: () => void;
  onCancel: () => void;
  onDeleted: () => void;
};

type Option = { id: string; title: string };

/** Edit a task's title, description, dates and dependencies, or delete it. */
export function TaskEditor({ task, onSaved, onCancel, onDeleted }: Props) {
  const [title, setTitle] = useState<string>(task.title);
  const [description, setDescription] = useState<string>(task.description);
  const [startDate, setStartDate] = useState<string>(task.startDate);
  const [endDate, setEndDate] = useState<string>(task.endDate);
  const [blockedBy, setBlockedBy] = useState<string[]>(task.blockedBy);
  const [options, setOptions] = useState<Option[]>([]);
  const [adding, setAdding] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchTaskGraph()
      .then((graph) =>
        setOptions(
          graph.tasks
            .filter((t) => t.id !== task.id)
            .map((t) => ({ id: t.id, title: t.title })),
        ),
      )
      .catch(() => setOptions([]));
  }, [task.id]);

  const titleOf = (id: string): string =>
    options.find((o) => o.id === id)?.title ?? id;

  function save() {
    const ops: TaskOp[] = [];
    const fields: Record<string, string> = {};
    if (title.trim() && title.trim() !== task.title) fields.title = title.trim();
    if (description !== task.description) fields.description = description;
    if (startDate && startDate !== task.startDate) fields.startDate = startDate;
    if (endDate && endDate !== task.endDate) fields.endDate = endDate;
    if (Object.keys(fields).length > 0) {
      ops.push({ op: "task.update", taskId: task.id, ...fields });
    }
    for (const id of task.blockedBy) {
      if (!blockedBy.includes(id)) {
        ops.push({ op: "relation.remove", source: id, target: task.id, type: "blocks" });
      }
    }
    for (const id of blockedBy) {
      if (!task.blockedBy.includes(id)) {
        ops.push({ op: "relation.add", source: id, target: task.id, type: "blocks" });
      }
    }
    if (ops.length === 0) {
      onCancel();
      return;
    }
    setBusy(true);
    setError(null);
    submitTaskOps(ops, "Edited in the GUI")
      .then(() => onSaved())
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  }

  function remove() {
    if (!window.confirm(`Delete "${task.title}"? Links to it are removed too.`)) return;
    setBusy(true);
    setError(null);
    submitTaskOps([{ op: "task.delete", taskId: task.id }], "Deleted in the GUI")
      .then(() => onDeleted())
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  }

  const inputClass =
    "h-8 w-full rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:border-ring";

  return (
    <div className="mt-1 flex flex-col gap-3 rounded-lg border border-border p-4">
      <label className="flex flex-col gap-1 text-[11px] font-medium text-muted-foreground">
        Title
        <input
          className={inputClass}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </label>
      <label className="flex flex-col gap-1 text-[11px] font-medium text-muted-foreground">
        Description (the task agent works from this)
        <Textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="min-h-24 text-sm"
        />
      </label>
      <div className="flex gap-3">
        <label className="flex flex-1 flex-col gap-1 text-[11px] font-medium text-muted-foreground">
          Start
          <input
            type="date"
            className={inputClass}
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
          />
        </label>
        <label className="flex flex-1 flex-col gap-1 text-[11px] font-medium text-muted-foreground">
          End
          <input
            type="date"
            className={inputClass}
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
          />
        </label>
      </div>
      <div className="flex flex-col gap-1.5">
        <span className="text-[11px] font-medium text-muted-foreground">Blocked by</span>
        <div className="flex flex-wrap gap-1.5">
          {blockedBy.length === 0 ? (
            <span className="text-xs text-muted-foreground">Nothing</span>
          ) : null}
          {blockedBy.map((id) => (
            <span
              key={id}
              className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-0.5 text-xs"
            >
              {titleOf(id)}
              <button
                type="button"
                aria-label={`Remove ${titleOf(id)}`}
                className="text-muted-foreground hover:text-foreground"
                onClick={() => setBlockedBy(blockedBy.filter((b) => b !== id))}
              >
                <X className="size-3" />
              </button>
            </span>
          ))}
        </div>
        <div className="flex gap-2">
          <select
            className={inputClass}
            value={adding}
            onChange={(e) => setAdding(e.target.value)}
          >
            <option value="">Add a task this one waits on…</option>
            {options
              .filter((o) => !blockedBy.includes(o.id))
              .map((o) => (
                <option key={o.id} value={o.id}>
                  {o.title}
                </option>
              ))}
          </select>
          <Button
            size="sm"
            variant="outline"
            disabled={!adding}
            onClick={() => {
              if (adding) setBlockedBy([...blockedBy, adding]);
              setAdding("");
            }}
          >
            <Plus className="size-3.5" /> Add
          </Button>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <Button size="sm" disabled={busy || !title.trim()} onClick={save}>
          <Save className="size-3.5" /> Save
        </Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
        <Button
          size="sm"
          variant="destructive"
          className="ml-auto"
          disabled={busy}
          onClick={remove}
        >
          <Ban className="size-3.5" /> Delete task
        </Button>
      </div>
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
    </div>
  );
}
