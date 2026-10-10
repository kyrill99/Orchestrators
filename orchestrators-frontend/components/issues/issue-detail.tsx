"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ImagePlus, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { DraftEditor } from "@/components/issues/draft-editor";
import {
  ContextLine,
  IssueStatusPill,
  Person,
  Screenshots,
} from "@/components/issues/issue-parts";
import { Markdown } from "@/components/issues/markdown";
import {
  addIssueComment,
  closeIssue,
  formatWhen,
  getIssue,
  reopenIssue,
  updateIssue,
  uploadIssueAttachment,
  type Issue,
} from "@/lib/issues";
import { HereLine } from "@/components/presence/presence";
import {
  presenceColor,
  presenceName,
  useAnnouncePresence,
  useEditLock,
} from "@/lib/presence";

export function IssueDetail({
  id,
  onChanged,
  onDeleted,
}: {
  id: string;
  /** The issue changed; the list should refresh. */
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const [issue, setIssue] = useState<Issue | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    getIssue(id)
      .then((next) => {
        setIssue(next);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load"));
  }, [id]);

  useEffect(load, [load]);

  const changed = useCallback(
    (next: Issue) => {
      setIssue((prev) => ({ ...next, comments: next.comments ?? prev?.comments }));
      onChanged();
    },
    [onChanged],
  );

  if (error) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        {error}
      </div>
    );
  }
  if (!issue || issue.id !== id) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Loading…
      </div>
    );
  }

  if (issue.status === "draft") {
    return (
      <article className="mx-auto max-w-2xl px-6 py-8 sm:px-10">
        <p className="mb-1 text-xs text-muted-foreground">
          Draft by {issue.author.name} · started {formatWhen(issue.created_at)}
        </p>
        <h1 className="mb-5 text-xl font-semibold tracking-[-0.02em] text-foreground">
          {issue.title || "Untitled draft"}
        </h1>
        <DraftEditor
          key={issue.id}
          issue={issue}
          onChange={changed}
          onPublished={changed}
          onDiscarded={onDeleted}
        />
      </article>
    );
  }

  return <PublishedIssue key={issue.id} issue={issue} onChange={changed} />;
}

function PublishedIssue({
  issue,
  onChange,
}: {
  issue: Issue;
  onChange: (issue: Issue) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(issue.title);
  const [description, setDescription] = useState(issue.description);
  const [closing, setClosing] = useState(false);
  const [reason, setReason] = useState("");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  async function act(name: string, fn: () => Promise<Issue>) {
    setBusy(name);
    setError(null);
    try {
      onChange(await fn());
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    if (await act("save", () => updateIssue(issue.id, { title, description }))) {
      setEditing(false);
    }
  }

  async function postComment() {
    if (!comment.trim()) return;
    const ok = await act("comment", async () => {
      await addIssueComment(issue.id, comment);
      return getIssue(issue.id);
    });
    if (ok) setComment("");
  }

  async function upload(files: FileList | null) {
    const file = files?.[0];
    if (!file) return;
    await act("upload", async () => {
      await uploadIssueAttachment(issue.id, file, file.name);
      return getIssue(issue.id);
    });
    if (fileInput.current) fileInput.current.value = "";
  }

  const closed = issue.status === "closed";

  return (
    <article className="mx-auto max-w-2xl px-6 py-8 sm:px-10">
      <IssuePresence issue={issue} editing={editing} onChange={onChange} />
      <p className="mb-2 flex flex-wrap items-center gap-x-1.5 text-xs text-muted-foreground">
        <span className="font-mono">#{issue.number}</span>
        <span>·</span>
        <Person person={issue.author} />
        <span>opened {formatWhen(issue.created_at)}</span>
        {issue.updated_at !== issue.created_at ? (
          <span>· updated {formatWhen(issue.updated_at)}</span>
        ) : null}
      </p>

      {editing ? (
        <div className="mb-6 flex flex-col gap-2">
          <Input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            aria-label="Title"
            className="h-9 text-[15px] font-medium"
          />
          <Textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            aria-label="Description"
            className="min-h-40 text-[13px]"
          />
          <div className="flex justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setTitle(issue.title);
                setDescription(issue.description);
                setEditing(false);
              }}
            >
              Cancel
            </Button>
            <Button size="sm" onClick={() => void save()} disabled={busy !== null}>
              {busy === "save" ? <Loader2 className="animate-spin" /> : null}
              Save
            </Button>
          </div>
        </div>
      ) : (
        <h1 className="mb-4 text-xl font-semibold tracking-[-0.02em] text-foreground">
          {issue.title}
        </h1>
      )}

      <div className="mb-6 flex flex-wrap items-center gap-2">
        <IssueStatusPill status={issue.status} />
        {issue.assignee ? (
          <span className="text-[12px] text-muted-foreground">
            Assigned to <span className="text-foreground">{issue.assignee}</span>
          </span>
        ) : null}
        {issue.labels.map((label) => (
          <Badge key={label} variant="outline">
            {label}
          </Badge>
        ))}
        <div className="ml-auto flex items-center gap-1">
          {!editing ? (
            <Button variant="ghost" size="sm" onClick={() => setEditing(true)}>
              Edit
            </Button>
          ) : null}
          {!closed ? (
            <Button
              variant="ghost"
              size="sm"
              disabled={busy !== null}
              onClick={() =>
                void act("progress", () =>
                  updateIssue(issue.id, {
                    status: issue.status === "in_progress" ? "open" : "in_progress",
                  }),
                )
              }
            >
              {issue.status === "in_progress" ? "Stop progress" : "Start progress"}
            </Button>
          ) : null}
          {closed ? (
            <Button
              variant="outline"
              size="sm"
              disabled={busy !== null}
              onClick={() => void act("reopen", () => reopenIssue(issue.id))}
            >
              Reopen
            </Button>
          ) : (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setClosing((v) => !v)}
              aria-expanded={closing}
            >
              Close
            </Button>
          )}
        </div>
      </div>

      {closing && !closed ? (
        <form
          className="mb-6 flex items-center gap-2"
          onSubmit={async (event) => {
            event.preventDefault();
            if (await act("close", () => closeIssue(issue.id, reason))) {
              setClosing(false);
              setReason("");
            }
          }}
        >
          <Input
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Why? (fixed, duplicate, won’t fix…) optional"
            aria-label="Close reason"
            autoFocus
            className="text-[13px]"
          />
          <Button type="submit" size="sm" disabled={busy !== null}>
            {busy === "close" ? <Loader2 className="animate-spin" /> : null}
            Close issue
          </Button>
        </form>
      ) : null}

      {closed ? (
        <p className="mb-6 rounded-md bg-muted px-3 py-2 text-[12px] text-muted-foreground">
          Closed {issue.closed_at ? formatWhen(issue.closed_at) : ""}
          {issue.closed_by ? ` by ${issue.closed_by.name}` : ""}
          {issue.close_reason ? `: ${issue.close_reason}` : ""}
        </p>
      ) : null}

      {error ? <p className="mb-4 text-[12px] text-destructive">{error}</p> : null}

      {!editing ? (
        issue.description.trim() ? (
          <Markdown source={issue.description} className="mb-6" />
        ) : (
          <p className="mb-6 text-[13px] text-muted-foreground">No description.</p>
        )
      ) : null}

      <div className="mb-4">
        <Screenshots attachments={issue.attachments} />
      </div>
      <div className="mb-6 flex items-center gap-3">
        <ContextLine context={issue.context} />
        <input
          ref={fileInput}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          className="hidden"
          onChange={(event) => void upload(event.target.files)}
        />
        <Button
          variant="ghost"
          size="xs"
          className="ml-auto text-muted-foreground"
          onClick={() => fileInput.current?.click()}
          disabled={busy !== null}
        >
          {busy === "upload" ? <Loader2 className="animate-spin" /> : <ImagePlus />}
          Add image
        </Button>
      </div>

      <Separator className="mb-6" />

      <h2 className="mb-3 text-[13px] font-medium text-foreground">
        Comments{issue.comments?.length ? ` (${issue.comments.length})` : ""}
      </h2>
      <ul className="mb-4 space-y-4">
        {(issue.comments ?? []).map((c) => (
          <li key={c.id} className="text-[13px]">
            <p className="mb-1 flex items-center gap-1.5 text-xs text-muted-foreground">
              <Person person={c.author} />
              <span>· {formatWhen(c.created_at)}</span>
            </p>
            <Markdown source={c.body} className="text-[13px]" />
          </li>
        ))}
      </ul>
      <form
        className="flex flex-col gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void postComment();
        }}
      >
        <Textarea
          value={comment}
          onChange={(event) => setComment(event.target.value)}
          placeholder="Leave a comment…"
          aria-label="Comment"
          className="min-h-20 text-[13px]"
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              void postComment();
            }
          }}
        />
        <div className="flex justify-end">
          <Button type="submit" size="sm" disabled={busy !== null || !comment.trim()}>
            {busy === "comment" ? <Loader2 className="animate-spin" /> : null}
            Comment
          </Button>
        </div>
      </form>
    </article>
  );
}

/**
 * Who else has this issue open, and a soft lock on editing it: while your
 * editor is open others see "<you> is editing"; when someone else saves (stops
 * editing), the issue is re-fetched so their changes show up.
 */
function IssuePresence({
  issue,
  editing,
  onChange,
}: {
  issue: Issue;
  editing: boolean;
  onChange: (issue: Issue) => void;
}) {
  const where = `issues:${issue.id}`;
  const here = useAnnouncePresence(where);
  const editor = useEditLock(where, "description", editing);
  const editorEmail = editor?.email ?? null;
  const previous = useRef<string | null>(null);

  useEffect(() => {
    if (previous.current && !editorEmail) {
      getIssue(issue.id).then(onChange, () => {});
    }
    previous.current = editorEmail;
  }, [editorEmail, issue.id, onChange]);

  if (here.length === 0) return null;
  return (
    <div className="mb-3 flex flex-wrap items-center gap-3">
      <HereLine people={here} />
      {editor ? (
        <span
          className="text-[12px] font-medium"
          style={{ color: presenceColor(editor.email) }}
        >
          {presenceName(editor)} is editing this issue
          {editing ? " too; the last save wins" : ""}
        </span>
      ) : null}
    </div>
  );
}
