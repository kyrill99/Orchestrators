"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { Bug, Loader2 } from "lucide-react";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { DraftEditor } from "@/components/issues/draft-editor";
import {
  createIssue,
  getIssue,
  notifyIssuesChanged,
  uploadIssueAttachment,
  type Issue,
} from "@/lib/issues";
import {
  SCREENSHOT_IGNORE,
  capturePage,
  pageContext,
  screenshotFilename,
} from "@/lib/screenshot";
import { cn } from "@/lib/utils";

/** The draft this browser is working on, so a reload can pick it up again. */
const DRAFT_KEY = "orchestrators.bugReportDraft";

const nextFrame = () =>
  new Promise<void>((resolve) =>
    requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
  );

/**
 * Shell chrome control next to Agent (bottom right): screenshots the page,
 * creates a draft issue with it and opens a form to submit or discard it.
 */
export function BugReportButton() {
  const [draft, setDraft] = useState<Issue | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [filed, setFiled] = useState<Issue | null>(null);

  const remember = useCallback((issue: Issue | null) => {
    setDraft(issue);
    if (issue) localStorage.setItem(DRAFT_KEY, issue.id);
    else localStorage.removeItem(DRAFT_KEY);
  }, []);

  /** Screenshot the page as it is now and attach it to the draft. */
  const attachScreenshot = useCallback(async (issue: Issue): Promise<Issue> => {
    const blob = await capturePage();
    await uploadIssueAttachment(issue.id, blob, screenshotFilename(blob));
    return getIssue(issue.id);
  }, []);

  async function start() {
    if (busy) return;
    setBusy(true);
    setNote(null);
    setFiled(null);
    try {
      // Resume the draft from before a reload, if it is still a draft.
      const savedId = localStorage.getItem(DRAFT_KEY);
      if (savedId) {
        const saved = await getIssue(savedId).catch(() => null);
        if (saved?.status === "draft") {
          remember(saved);
          setOpen(true);
          return;
        }
      }
      // Take the shot first so the form is not in it; upload once the draft exists.
      let shot: Blob | null = null;
      try {
        shot = await capturePage();
      } catch {
        setNote("Couldn’t capture the page; you can still describe the problem.");
      }
      let issue = await createIssue({ status: "draft", context: pageContext() });
      remember(issue);
      notifyIssuesChanged();
      if (shot) {
        try {
          await uploadIssueAttachment(issue.id, shot, screenshotFilename(shot));
          issue = await getIssue(issue.id);
          remember(issue);
        } catch (e) {
          setNote(e instanceof Error ? e.message : "Screenshot upload failed");
        }
      }
      setOpen(true);
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Could not start a bug report");
      setOpen(true);
    } finally {
      setBusy(false);
    }
  }

  async function addScreenshot() {
    if (!draft) return;
    setOpen(false);
    await new Promise((resolve) => setTimeout(resolve, 250)); // sheet closes
    await nextFrame();
    try {
      remember(await attachScreenshot(draft));
      setNote(null);
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Couldn’t capture the page");
    } finally {
      setOpen(true);
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => void start()}
        disabled={busy}
        {...{ [SCREENSHOT_IGNORE]: "" }}
        className={cn(
          "inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-black/5 hover:text-foreground disabled:opacity-60",
          open && "bg-black/5 text-foreground",
        )}
        aria-label="Report a bug"
        title="Report a bug (screenshots this page)"
      >
        {busy ? (
          <Loader2 className="size-3.5 animate-spin" strokeWidth={1.75} />
        ) : (
          <Bug className="size-3.5" strokeWidth={1.75} />
        )}
        Report bug
      </button>
      {filed?.number ? (
        <Link
          href={`/issues?id=${filed.id}`}
          className="px-1 text-[12px] font-medium text-foreground underline-offset-2 hover:underline"
        >
          Filed #{filed.number}
        </Link>
      ) : null}

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent
          side="right"
          className="w-full gap-0 data-[side=right]:sm:max-w-md"
        >
          <SheetHeader className="border-b border-border">
            <SheetTitle className="text-[14px]">Report a bug</SheetTitle>
            <SheetDescription className="text-[12px]">
              Saved as a draft while you type. Close this to finish later.
            </SheetDescription>
          </SheetHeader>
          <div className="flex-1 overflow-y-auto p-4">
            {note ? (
              <p className="mb-3 text-[12px] text-muted-foreground">{note}</p>
            ) : null}
            {draft ? (
              <DraftEditor
                key={draft.id}
                issue={draft}
                onChange={remember}
                onAddScreenshot={addScreenshot}
                autoFocus
                onPublished={(issue) => {
                  remember(null);
                  notifyIssuesChanged();
                  setFiled(issue);
                  setOpen(false);
                  setTimeout(() => setFiled(null), 8000);
                }}
                onDiscarded={() => {
                  remember(null);
                  notifyIssuesChanged();
                  setOpen(false);
                }}
              />
            ) : null}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
