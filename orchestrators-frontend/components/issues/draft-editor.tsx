"use client";

import { useEffect, useRef, useState } from "react";
import { Camera, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ContextLine, Screenshots } from "@/components/issues/issue-parts";
import {
  deleteIssue,
  deleteIssueAttachment,
  publishIssue,
  updateIssue,
  type Issue,
} from "@/lib/issues";

/**
 * Edit a draft issue: title, description (autosaved, so the draft survives a
 * reload), screenshots. Submit publishes it; Discard deletes it.
 */
export function DraftEditor({
  issue,
  onChange,
  onPublished,
  onDiscarded,
  onAddScreenshot,
  autoFocus,
}: {
  issue: Issue;
  onChange: (issue: Issue) => void;
  onPublished: (issue: Issue) => void;
  onDiscarded: () => void;
  /** Capture another screenshot of the page (bug-report flow only). */
  onAddScreenshot?: () => Promise<void>;
  autoFocus?: boolean;
}) {
  const [title, setTitle] = useState(issue.title);
  const [description, setDescription] = useState(issue.description);
  const [busy, setBusy] = useState<"submit" | "discard" | "shot" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const saved = useRef({ title: issue.title, description: issue.description });

  // Autosave after a short pause in typing.
  useEffect(() => {
    if (title === saved.current.title && description === saved.current.description) {
      return;
    }
    const timer = setTimeout(() => {
      const next = { title, description };
      updateIssue(issue.id, next)
        .then((updated) => {
          saved.current = next;
          onChange(updated);
        })
        .catch(() => {});
    }, 700);
    return () => clearTimeout(timer);
  }, [title, description, issue.id, onChange]);

  async function submit() {
    if (!title.trim()) {
      setError("Add a title first.");
      return;
    }
    setBusy("submit");
    setError(null);
    try {
      await updateIssue(issue.id, { title, description });
      saved.current = { title, description };
      onPublished(await publishIssue(issue.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not submit");
      setBusy(null);
    }
  }

  async function discard() {
    setBusy("discard");
    setError(null);
    try {
      await deleteIssue(issue.id);
      onDiscarded();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not discard");
      setBusy(null);
    }
  }

  async function addScreenshot() {
    if (!onAddScreenshot) return;
    setBusy("shot");
    try {
      await onAddScreenshot();
    } finally {
      setBusy(null);
    }
  }

  async function removeScreenshot(attachmentId: string) {
    try {
      await deleteIssueAttachment(issue.id, attachmentId);
      onChange({
        ...issue,
        attachments: issue.attachments.filter((a) => a.id !== attachmentId),
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not remove screenshot");
    }
  }

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <Input
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        placeholder="What went wrong?"
        aria-label="Title"
        autoFocus={autoFocus}
        className="h-9 text-[14px] font-medium"
      />
      <Textarea
        value={description}
        onChange={(event) => setDescription(event.target.value)}
        placeholder="Steps to reproduce, what you expected, what happened. Markdown works."
        aria-label="Description"
        className="max-h-72 min-h-28 text-[13px]"
      />
      <Screenshots
        attachments={issue.attachments}
        onRemove={(a) => void removeScreenshot(a.id)}
        size="sm"
      />
      <ContextLine context={issue.context} />
      {error ? <p className="text-[12px] text-destructive">{error}</p> : null}
      <div className="flex items-center gap-2 pt-1">
        {onAddScreenshot ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => void addScreenshot()}
            disabled={busy !== null}
          >
            {busy === "shot" ? <Loader2 className="animate-spin" /> : <Camera />}
            {issue.attachments.length ? "Add screenshot" : "Take screenshot"}
          </Button>
        ) : null}
        <div className="ml-auto flex items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => void discard()}
            disabled={busy !== null}
          >
            {busy === "discard" ? <Loader2 className="animate-spin" /> : null}
            Discard
          </Button>
          <Button type="submit" size="sm" disabled={busy !== null}>
            {busy === "submit" ? <Loader2 className="animate-spin" /> : null}
            Submit
          </Button>
        </div>
      </div>
    </form>
  );
}
