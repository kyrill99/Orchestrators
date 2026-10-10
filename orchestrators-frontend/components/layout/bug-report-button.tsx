"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type SyntheticEvent,
} from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { Bug, Loader2, XIcon } from "lucide-react";
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

/**
 * Keep an event from reaching other listeners on document / window, so open
 * popovers, drawers and dialogs underneath do not treat it as an outside
 * press, focus change or key press and close themselves. React listens on the
 * root (the document in the App Router), so stopImmediatePropagation is needed
 * to also skip the other document listeners registered after React's.
 */
const isolate = (event: SyntheticEvent) => {
  event.stopPropagation();
  event.nativeEvent.stopImmediatePropagation();
};

/** Events on the trigger button that must never reach the page. */
const TRIGGER_EVENTS: readonly string[] = [
  "pointerdown",
  "mousedown",
  "pointerup",
  "mouseup",
  "click",
  "touchstart",
];

/**
 * Shell chrome control next to Agent (bottom right): freezes the screen by
 * screenshotting it exactly as it is, shows that frozen image full screen with
 * a bug report form on top, and attaches the image to a draft issue. Nothing
 * underneath is closed or reset; closing the report reveals the live page.
 */
export function BugReportButton() {
  const [draft, setDraft] = useState<Issue | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [frozenUrl, setFrozenUrl] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [filed, setFiled] = useState<Issue | null>(null);
  const busyRef = useRef(false);
  const openRef = useRef(false);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const overlayRef = useRef<HTMLDivElement | null>(null);
  const startRef = useRef<() => void>(() => {});

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

  const close = useCallback(() => {
    openRef.current = false;
    setOpen(false);
    setFrozenUrl((url: string | null) => {
      if (url) URL.revokeObjectURL(url);
      return null;
    });
  }, []);

  // Escape closes the report only. Listen on window in the capture phase so the
  // key never reaches overlays underneath (they would close themselves).
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      // A screenshot preview opened inside the report closes first.
      const nested = overlayRef.current?.querySelector<HTMLElement>(
        '[role="dialog"]',
      );
      if (nested) {
        nested
          .querySelector<HTMLButtonElement>('button[aria-label="Close"]')
          ?.click();
        return;
      }
      close();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, close]);

  async function start() {
    if (busyRef.current || openRef.current) return;
    busyRef.current = true;
    openRef.current = true;
    // Start the capture before anything on screen changes. The overlay and
    // this button are marked SCREENSHOT_IGNORE, so showing the overlay right
    // away does not end up in the shot; the page underneath stays as it was.
    const capture: Promise<Blob | null> = capturePage().catch(() => null);
    setBusy(true);
    setCapturing(true);
    setNote(null);
    setFiled(null);
    setOpen(true);
    try {
      const shot = await capture;
      setCapturing(false);
      if (shot && openRef.current) setFrozenUrl(URL.createObjectURL(shot));
      else if (!shot) {
        setNote("Couldn’t capture the page; you can still describe the problem.");
      }

      // Resume the draft from before a reload, if it is still a draft.
      const savedId = localStorage.getItem(DRAFT_KEY);
      if (savedId) {
        const saved = await getIssue(savedId).catch(() => null);
        if (saved?.status === "draft") {
          remember(saved);
          return;
        }
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
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Could not start a bug report");
    } finally {
      setCapturing(false);
      setBusy(false);
      busyRef.current = false;
    }
  }
  useEffect(() => {
    startRef.current = () => void start();
  });

  // The trigger is handled on window in the capture phase, before any
  // document-level outside-press listener (dropdowns, Base UI dialogs and
  // popovers) sees the press, and focus is kept where it is.
  useEffect(() => {
    const onTrigger = (event: Event) => {
      const button = buttonRef.current;
      if (!button || !(event.target instanceof Node)) return;
      if (!button.contains(event.target)) return;
      event.stopPropagation();
      if (event.type === "pointerdown") {
        event.preventDefault();
        if ((event as PointerEvent).button === 0) startRef.current();
      } else if (event.type === "mousedown") {
        event.preventDefault();
      } else if (event.type === "click" && (event as MouseEvent).detail === 0) {
        // Keyboard activation (Enter / Space) has no pointerdown.
        startRef.current();
      }
    };
    for (const type of TRIGGER_EVENTS) {
      window.addEventListener(type, onTrigger, true);
    }
    return () => {
      for (const type of TRIGGER_EVENTS) {
        window.removeEventListener(type, onTrigger, true);
      }
    };
  }, []);

  /** Re-shoot the live page; the overlay is left out of the capture. */
  async function addScreenshot() {
    if (!draft) return;
    try {
      remember(await attachScreenshot(draft));
      setNote(null);
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Couldn’t capture the page");
    }
  }

  const overlay = open ? (
    <div
      ref={overlayRef}
      {...{ [SCREENSHOT_IGNORE]: "" }}
      role="dialog"
      aria-modal="true"
      aria-label="Report a bug"
      className="pointer-events-auto fixed inset-0 z-[1000] text-sm text-popover-foreground"
      onPointerDown={isolate}
      onPointerUp={isolate}
      onMouseDown={isolate}
      onMouseUp={isolate}
      onClick={isolate}
      onFocus={isolate}
      onBlur={isolate}
      onKeyDown={isolate}
      onKeyUp={isolate}
      onWheel={isolate}
      onTouchStart={isolate}
      onTouchMove={isolate}
    >
      {/* The frozen moment being reported; dimmed live page if capture failed. */}
      {frozenUrl ? (
        // eslint-disable-next-line @next/next/no-img-element -- local object URL
        <img
          src={frozenUrl}
          alt="Frozen screenshot of the page"
          className="absolute inset-0 h-full w-full object-fill select-none"
          draggable={false}
        />
      ) : null}
      <div
        className={cn(
          "absolute inset-0",
          frozenUrl ? "bg-black/15" : "bg-black/30",
        )}
      />
      <div className="absolute top-3 left-3 flex items-center gap-1.5 rounded-md bg-black/70 px-2 py-1 text-[11px] font-medium text-white shadow-sm">
        {capturing ? (
          <>
            <Loader2 className="size-3 animate-spin" strokeWidth={1.75} />
            Freezing screen…
          </>
        ) : frozenUrl ? (
          "Screen frozen — this screenshot is attached to the report"
        ) : (
          "Screen paused"
        )}
      </div>

      <div className="absolute inset-y-0 right-0 flex w-full flex-col border-l border-border bg-popover shadow-2xl sm:max-w-md">
        <div className="relative flex flex-col gap-0.5 border-b border-border p-4 pr-12">
          <h2 className="text-[14px] font-medium text-foreground">
            Report a bug
          </h2>
          <p className="text-[12px] text-muted-foreground">
            Saved as a draft while you type. Close this to finish later.
          </p>
          <button
            type="button"
            onClick={close}
            className="absolute top-3 right-3 inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-black/5 hover:text-foreground"
            aria-label="Close report"
          >
            <XIcon className="size-4" />
          </button>
        </div>
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
              onPublished={(issue: Issue) => {
                remember(null);
                notifyIssuesChanged();
                setFiled(issue);
                close();
                setTimeout(() => setFiled(null), 8000);
              }}
              onDiscarded={() => {
                remember(null);
                notifyIssuesChanged();
                close();
              }}
            />
          ) : busy ? (
            <p className="flex items-center gap-2 text-[12px] text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" strokeWidth={1.75} />
              Preparing draft…
            </p>
          ) : null}
        </div>
      </div>
    </div>
  ) : null;

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        disabled={busy}
        {...{ [SCREENSHOT_IGNORE]: "" }}
        className={cn(
          // Above modal backdrops (z-50) so it can be pressed while a drawer is open.
          "pointer-events-auto relative z-[60] inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-black/5 hover:text-foreground disabled:opacity-60",
          open && "bg-black/5 text-foreground",
        )}
        aria-label="Report a bug"
        title="Report a bug (freezes and screenshots this page)"
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
      {overlay ? createPortal(overlay, document.body) : null}
    </>
  );
}
