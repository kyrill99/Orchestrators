"use client";

import { useEffect, useState } from "react";
import { NotebookPen } from "lucide-react";
import { fetchScratchpad, saveScratchpad, type Scratchpad } from "@/lib/api";
import { subscribeRunEvents } from "@/lib/run-events";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { presenceColor, presenceName, useEditLock } from "@/lib/presence";

const POLL_MS = 3000;

/** The task's markdown scratchpad: live while agents write, editable by humans. */
export function ScratchpadPanel({ taskId }: { taskId: string }) {
  const [pad, setPad] = useState<Scratchpad | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const editing = draft !== null;
  // Soft lock: others see you editing; you see them (it reloads when they save).
  const editor = useEditLock(`task:${taskId}`, "scratchpad", editing);

  useEffect(() => {
    if (editing) return; // don't overwrite what the human is typing
    let stopped = false;
    const load = () =>
      fetchScratchpad(taskId)
        .then((next) => !stopped && setPad(next))
        .catch(() => undefined);
    void load();
    const id = window.setInterval(load, POLL_MS);
    const unsubscribe = subscribeRunEvents((event) => {
      if (event.type === "resync" || (event.type === "scratchpad" && event.data?.taskId === taskId)) {
        void load();
      }
    });
    return () => {
      stopped = true;
      unsubscribe();
      window.clearInterval(id);
    };
  }, [taskId, editing]);

  if (pad === null) return null;

  const save = () => {
    if (draft === null) return;
    setSaving(true);
    setError(null);
    saveScratchpad(taskId, draft)
      .then((next) => {
        setPad(next);
        setDraft(null);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setSaving(false));
  };

  return (
    <section className="mt-8">
      <div className="mb-3 flex items-center gap-2">
        <h2 className="text-[13px] font-medium text-foreground">Scratchpad</h2>
        {pad.updatedAt ? (
          <span className="text-[11px] text-muted-foreground">
            {pad.updatedBy} · {new Date(pad.updatedAt).toLocaleString()}
          </span>
        ) : null}
        {editor ? (
          <span
            className="text-[11px] font-medium"
            style={{ color: presenceColor(editor.email) }}
          >
            {presenceName(editor)} is editing{editing ? " too; the last save wins" : ""}
          </span>
        ) : null}
        <div className="ml-auto flex gap-2">
          {editing ? (
            <>
              <Button type="button" size="xs" variant="outline" disabled={saving} onClick={() => setDraft(null)}>
                Cancel
              </Button>
              <Button type="button" size="xs" disabled={saving} onClick={save}>
                {saving ? "Saving…" : "Save"}
              </Button>
            </>
          ) : (
            <Button type="button" size="xs" variant="outline" onClick={() => setDraft(pad.content)}>
              Edit
            </Button>
          )}
        </div>
      </div>
      {editing ? (
        <Textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          className="min-h-[220px] font-mono text-[12px] leading-relaxed"
          aria-label="Scratchpad"
          autoFocus
        />
      ) : pad.content.trim() ? (
        <pre className="max-h-[420px] overflow-auto rounded-xl border border-border px-3.5 py-3 font-sans text-[13px] leading-relaxed whitespace-pre-wrap text-foreground">
          {pad.content}
        </pre>
      ) : (
        <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
          <NotebookPen className="size-3.5" strokeWidth={1.75} />
          Empty. Agents keep their working notes here; you can add context for them too.
        </p>
      )}
      {error ? <p className="mt-2 text-xs text-destructive">{error}</p> : null}
    </section>
  );
}
