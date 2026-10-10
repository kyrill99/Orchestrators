"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowUp,
  AudioLines,
  ChevronDown,
  Loader2,
  Paperclip,
} from "lucide-react";
import {
  ApiError,
  createConversation,
  fetchMe,
  postConversationMessage,
  type AgentMode,
  type ConversationDetail,
  type ConversationMessage,
  type ConversationSummary,
  type Me,
} from "@/lib/api";
import { useLiveConversations } from "@/lib/use-live-conversations";
import {
  notifyTyping,
  stopTyping,
  useAnnouncePresence,
  usePresenceSnapshot,
} from "@/lib/presence";
import { AvatarStack, TypingLine } from "@/components/presence/presence";
import {
  VOICE_OPENERS,
  VoiceConversation,
} from "@/lib/gradium/voice-conversation";
import { VoiceMode } from "@/components/agent/voice-mode";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** A conversation with activity this recent counts as active. */
const ACTIVE_MS = 5 * 60_000;

/** The agent failed; the server stored the error in the conversation. */
class StoredAgentError extends Error {}

export function AgentPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [voice, setVoice] = useState<VoiceConversation | null>(null);
  const live = useLiveConversations(selectedId);
  const { adopt, addMessages } = live;
  const selectedRef = useRef<string | null>(null);
  const detailRef = useRef<ConversationDetail | null>(null);
  const voiceRef = useRef<VoiceConversation | null>(null);
  const submittingRef = useRef(false);
  const now = useNow();
  // Teammates in the agent chat (any conversation), for the header.
  const agentPeople = useAnnouncePresence("agent", selectedId);

  useEffect(() => {
    if (draft.trim()) notifyTyping();
    else stopTyping();
  }, [draft]);

  useEffect(() => {
    detailRef.current = live.detail;
  }, [live.detail]);

  useEffect(() => {
    fetchMe().then(setMe, () => {});
    return () => voiceRef.current?.end();
  }, []);

  /** The selected conversation, created on the first message. */
  const ensureConversation = useCallback(
    async (mode: AgentMode) => {
      if (selectedRef.current) return selectedRef.current;
      const created = await createConversation(mode);
      selectedRef.current = created.id;
      adopt(created);
      setSelectedId(created.id);
      return created.id;
    },
    [adopt],
  );

  /** One agent turn in the shared conversation; resolves to the reply. */
  const callAgent = useCallback(
    async (text: string, mode: AgentMode, { hidden }: { hidden: boolean }) => {
      const id = await ensureConversation(mode);
      try {
        const { message, reply } = await postConversationMessage(
          id,
          text,
          mode,
          hidden,
        );
        addMessages(id, message ? [message, reply] : [reply]);
        return reply.content;
      } catch (err) {
        if (err instanceof ApiError && err.status >= 500) {
          throw new StoredAgentError(err.message);
        }
        throw err;
      }
    },
    [ensureConversation, addMessages],
  );

  const submitMessage = useCallback(
    async (raw: string) => {
      const text = raw.trim();
      if (!text || submittingRef.current) return;

      submittingRef.current = true;
      setSending(true);
      setError(null);
      setDraft("");

      try {
        await callAgent(text, "text", { hidden: false });
      } catch (err) {
        // Agent failures are shown in the conversation, to everyone.
        if (!(err instanceof StoredAgentError)) {
          setError(err instanceof Error ? err.message : "Something went wrong");
        }
      } finally {
        submittingRef.current = false;
        setSending(false);
      }
    },
    [callAgent],
  );

  const startVoice = useCallback(() => {
    if (voiceRef.current) return;
    // Created inside the click, so the browser lets the agent's voice play.
    const conversation = new VoiceConversation({
      open: () =>
        callAgent(
          detailRef.current?.messages.length
            ? VOICE_OPENERS.resume
            : VOICE_OPENERS.start,
          "voice",
          { hidden: true },
        ),
      reply: (text) => callAgent(text, "voice", { hidden: false }),
    });
    voiceRef.current = conversation;
    setVoice(conversation);
    setError(null);
    void conversation.start();
  }, [callAgent]);

  const endVoice = useCallback(() => {
    voiceRef.current?.end();
    voiceRef.current = null;
    setVoice(null);
  }, []);

  const openConversation = useCallback(
    (id: string | null) => {
      endVoice();
      selectedRef.current = id;
      setSelectedId(id);
      setDraft("");
      setError(null);
    },
    [endVoice],
  );

  const resetChat = useCallback(() => openConversation(null), [openConversation]);

  const detail = live.detail;
  const showRail = Boolean(selectedId || voice);

  return (
    <div className="flex h-full flex-col overflow-hidden bg-background">
      <div className="flex h-11 shrink-0 items-center justify-between px-4">
        <button
          type="button"
          onClick={resetChat}
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[13px] font-medium text-foreground transition-colors hover:bg-accent"
        >
          New chat
          <ChevronDown className="size-3.5 text-muted-foreground" strokeWidth={1.75} />
        </button>
        <span className="ml-auto mr-1">
          <AvatarStack people={agentPeople} size={20} />
        </span>
        <span
          className="inline-flex items-center gap-1.5 px-2 text-xs text-muted-foreground"
          title={
            live.connected
              ? "Conversations update live"
              : "Reconnecting to live updates…"
          }
        >
          <span
            className={cn(
              "size-1.5 rounded-full",
              live.connected ? "bg-emerald-500" : "bg-muted-foreground/40",
            )}
          />
          {live.connected ? "Live" : "Offline"}
        </span>
      </div>

      <div className="flex min-h-0 flex-1">
        {showRail && (
          <aside className="hidden w-64 shrink-0 flex-col overflow-auto border-r border-border px-2 pb-4 lg:flex">
            <h2 className="px-2 pt-2 pb-1.5 text-[12px] font-medium text-muted-foreground">
              Conversations
            </h2>
            <ConversationList
              conversations={live.conversations}
              selectedId={selectedId}
              me={me}
              now={now}
              onSelect={openConversation}
              compact
            />
          </aside>
        )}

        <div className="flex min-w-0 flex-1 flex-col">
          {voice ? (
            <div className="min-h-0 flex-1 overflow-auto">
              <div className="mx-auto max-w-2xl px-6 pb-10 pt-6 sm:pt-16">
                <VoiceMode conversation={voice} onEnd={endVoice} />
              </div>
            </div>
          ) : selectedId ? (
            <Interview
              detail={detail}
              me={me}
              draft={draft}
              setDraft={setDraft}
              sending={sending}
              error={error}
              onVoice={startVoice}
              onSend={() => void submitMessage(draft)}
              onNewChat={resetChat}
            />
          ) : (
            <div className="min-h-0 flex-1 overflow-auto">
              <div className="mx-auto flex min-h-full max-w-2xl flex-col px-6 pb-10 pt-6 sm:pt-16">
                <Welcome
                  me={me}
                  conversations={live.conversations}
                  now={now}
                  draft={draft}
                  setDraft={setDraft}
                  busy={sending}
                  error={error}
                  onVoice={startVoice}
                  onSend={() => void submitMessage(draft)}
                  onOpen={openConversation}
                />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(0);
  useEffect(() => {
    const tick = () => setNow(Date.now());
    const first = window.setTimeout(tick, 0);
    const id = window.setInterval(tick, intervalMs);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(id);
    };
  }, [intervalMs]);
  return now;
}

function age(seconds: number, now: number): string {
  const minutes = Math.max(0, Math.floor((now - seconds * 1000) / 60_000));
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

function displayName(name: string): string {
  return name ? name[0].toUpperCase() + name.slice(1) : name;
}

function people(conversation: ConversationSummary, me: Me | null): string {
  const names = conversation.participants.map((p) =>
    p.email === me?.email ? "you" : displayName(p.name),
  );
  if (names.length <= 3) return names.join(", ");
  return `${names.slice(0, 2).join(", ")} +${names.length - 2}`;
}

function LiveDot({
  conversation,
  now,
}: {
  conversation: ConversationSummary;
  now: number;
}) {
  const active = now > 0 && now - conversation.updated_at * 1000 < ACTIVE_MS;
  if (conversation.thinking) {
    return (
      <span className="relative flex size-1.5 shrink-0" title="Agent is answering">
        <span className="absolute inset-0 animate-ping rounded-full bg-[#5E6AD2]/60" />
        <span className="relative size-1.5 rounded-full bg-[#5E6AD2]" />
      </span>
    );
  }
  return (
    <span
      className={cn(
        "size-1.5 shrink-0 rounded-full",
        active ? "bg-emerald-500" : "bg-transparent",
      )}
      title={active ? "Active in the last few minutes" : undefined}
    />
  );
}

function ConversationList({
  conversations,
  selectedId,
  me,
  now,
  onSelect,
  compact = false,
}: {
  conversations: ConversationSummary[];
  selectedId: string | null;
  me: Me | null;
  now: number;
  onSelect: (id: string) => void;
  compact?: boolean;
}) {
  if (conversations.length === 0) {
    return (
      <p className={cn("text-[13px] text-muted-foreground", compact && "px-2")}>
        No conversations yet. Start one above; teammates will see it here live.
      </p>
    );
  }

  return (
    <ul
      className={cn(
        compact
          ? "flex flex-col gap-0.5"
          : "divide-y divide-border rounded-xl border border-border",
      )}
    >
      {conversations.map((c) => (
        <li key={c.id}>
          <button
            type="button"
            onClick={() => onSelect(c.id)}
            aria-current={c.id === selectedId ? "true" : undefined}
            className={cn(
              "flex w-full flex-col gap-0.5 text-left text-[13px] transition-colors",
              compact
                ? "rounded-md px-2 py-1.5 hover:bg-accent"
                : "px-3.5 py-2.5 hover:bg-accent/60",
              c.id === selectedId && "bg-accent",
            )}
          >
            <span className="flex w-full items-center gap-2">
              <LiveDot conversation={c} now={now} />
              <span className="min-w-0 flex-1 truncate text-foreground">
                {c.title || (c.mode === "voice" ? "Voice conversation" : "New conversation")}
              </span>
              <span className="shrink-0 text-xs text-muted-foreground">
                {now > 0 ? age(c.updated_at, now) : ""}
              </span>
            </span>
            <span className="truncate pl-3.5 text-xs text-muted-foreground">
              {c.thinking ? "Agent is answering…" : people(c, me)}
              {!compact && !c.thinking && c.last_message && (
                <>
                  {" · "}
                  {c.last_message.content}
                </>
              )}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function timeOfDayGreeting(date = new Date()): string {
  const hour = date.getHours();
  if (hour < 5) return "Good night";
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  if (hour < 21) return "Good evening";
  return "Good night";
}

function Welcome({
  me,
  conversations,
  now,
  draft,
  setDraft,
  busy,
  error,
  onVoice,
  onSend,
  onOpen,
}: {
  me: Me | null;
  conversations: ConversationSummary[];
  now: number;
  draft: string;
  setDraft: (value: string) => void;
  busy: boolean;
  error: string | null;
  onVoice: () => void;
  onSend: () => void;
  onOpen: (id: string) => void;
}) {
  const [greeting, setGreeting] = useState("Good morning");

  useEffect(() => {
    const update = () => setGreeting(timeOfDayGreeting());
    update();
    const id = window.setInterval(update, 60_000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <>
      <div className="mb-10 text-center">
        <h1 className="text-xl font-semibold tracking-[-0.02em] text-foreground">
          {greeting}
          {me ? `, ${displayName(me.name)}` : ""}
        </h1>
      </div>

      <Composer
        draft={draft}
        setDraft={setDraft}
        busy={busy}
        error={error}
        onVoice={onVoice}
        onSend={onSend}
      />

      <section className="mt-10">
        <h2 className="mb-2 text-[13px] font-medium text-muted-foreground">
          Recent · everyone
        </h2>
        <ConversationList
          conversations={conversations}
          selectedId={null}
          me={me}
          now={now}
          onSelect={onOpen}
        />
      </section>
    </>
  );
}

/** Show the author above the first of a run of messages from one person. */
function authorLabel(
  message: ConversationMessage,
  previous: ConversationMessage | undefined,
  me: Me | null,
): string | null {
  if (message.role !== "user") return null;
  if (
    previous?.role === "user" &&
    previous.author_email === message.author_email
  ) {
    return null;
  }
  const name =
    message.author_email === me?.email
      ? "You"
      : displayName(message.author_name ?? message.author_email ?? "Someone");
  return message.mode === "voice" ? `${name} · voice` : name;
}

function Interview({
  detail,
  me,
  draft,
  setDraft,
  sending,
  error,
  onVoice,
  onSend,
  onNewChat,
}: {
  detail: ConversationDetail | null;
  me: Me | null;
  draft: string;
  setDraft: (value: string) => void;
  sending: boolean;
  error: string | null;
  onVoice: () => void;
  onSend: () => void;
  onNewChat: () => void;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const messages = detail?.messages ?? [];
  const thinking = Boolean(detail?.thinking) || sending;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, thinking]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mx-auto flex w-full max-w-2xl shrink-0 items-center justify-between gap-4 px-6 pt-4 pb-2">
        <div className="min-w-0">
          <p className="truncate text-[13px] font-medium text-foreground">
            {detail?.title || "Objective interview"}
          </p>
          {detail && (
            <p className="truncate text-xs text-muted-foreground">
              {people(detail, me)}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={onNewChat}
          className="shrink-0 text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          New chat
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        <div className="mx-auto flex max-w-2xl flex-col gap-3 px-6 py-4">
          {!detail && (
            <div className="inline-flex items-center gap-2 self-center py-6 text-[13px] text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" />
              Loading…
            </div>
          )}
          {messages.map((message, index) => {
            const label = authorLabel(message, messages[index - 1], me);
            if (message.role === "tool") {
              return (
                <p
                  key={message.id}
                  className="max-w-full self-start truncate font-mono text-[11px] text-muted-foreground"
                  title={message.content}
                >
                  ↳ {message.content}
                </p>
              );
            }
            if (message.role === "error") {
              return (
                <p
                  key={message.id}
                  className="self-start py-1 text-xs text-red-600"
                  role="alert"
                >
                  {message.content}
                </p>
              );
            }
            return (
              <div
                key={message.id}
                className={cn(
                  "flex max-w-[92%] flex-col gap-1",
                  message.role === "agent" ? "self-start" : "items-end self-end",
                )}
              >
                {label && (
                  <span className="px-1 text-[11px] text-muted-foreground">
                    {label}
                  </span>
                )}
                <div
                  className={cn(
                    "text-[13px] leading-relaxed text-foreground",
                    message.role === "agent"
                      ? "py-1"
                      : "rounded-2xl bg-accent px-3.5 py-2.5",
                  )}
                >
                  {message.content}
                </div>
              </div>
            );
          })}
          {thinking && (
            <div className="inline-flex items-center gap-2 self-start py-1 text-[13px] text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" />
              Thinking…
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      <div className="shrink-0 bg-background px-6 pt-3 pb-4">
        <div className="mx-auto max-w-2xl">
          <Composer
            draft={draft}
            setDraft={setDraft}
            busy={sending}
            error={error}
            onVoice={onVoice}
            onSend={onSend}
          />
        </div>
      </div>
    </div>
  );
}

function Composer({
  draft,
  setDraft,
  busy,
  error,
  onVoice,
  onSend,
}: {
  draft: string;
  setDraft: (value: string) => void;
  busy: boolean;
  error: string | null;
  onVoice: () => void;
  onSend: () => void;
}) {
  const canSend = draft.trim().length > 0 && !busy;
  const presence = usePresenceSnapshot();
  const typingHere = presence.others.filter(
    (p) =>
      p.where === "agent" &&
      p.typing &&
      (p.conversation_id ?? null) === presence.conversationId,
  );

  return (
    <div>
      <TypingLine people={typingHere} className="mb-1 px-1" />
      <div className="rounded-2xl border border-border bg-background shadow-[0_1px_2px_rgba(0,0,0,0.04)]">
        <textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              if (canSend) onSend();
            }
          }}
          placeholder="Ask about goals, priorities, or what success looks like…"
          aria-label="Ask the agent"
          disabled={busy}
          className="min-h-[88px] w-full resize-none bg-transparent px-4 pt-3.5 pb-2 text-[14px] outline-none placeholder:text-muted-foreground disabled:opacity-60"
        />
        <div className="flex items-center justify-between px-2.5 pb-2.5">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="gap-1 text-muted-foreground"
            disabled
          >
            Skills
            <ChevronDown className="size-3.5" />
          </Button>
          <div className="flex items-center gap-1">
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              disabled
              aria-label="Attach"
            >
              <Paperclip className="size-3.5" />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={onVoice}
              disabled={busy}
              className="gap-1.5 text-muted-foreground"
            >
              <AudioLines className="size-3.5" />
              Voice
            </Button>
            <Button
              type="button"
              size="icon-sm"
              disabled={!canSend}
              onClick={onSend}
              aria-label="Send"
              className="rounded-full bg-[#5E6AD2] text-white hover:bg-[#5E6AD2]/80 disabled:opacity-40"
            >
              {busy ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <ArrowUp className="size-3.5" />
              )}
            </Button>
          </div>
        </div>
      </div>
      {error && (
        <p className="mt-2 text-xs text-red-600" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
