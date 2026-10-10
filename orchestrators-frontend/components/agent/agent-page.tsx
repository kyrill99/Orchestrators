"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowUp,
  AudioLines,
  Box,
  ChevronDown,
  ClipboardList,
  Loader2,
  Paperclip,
  Search,
  Target,
} from "lucide-react";
import {
  agentExamples,
  chatMessages as seedMessages,
  recentChats,
  type AgentExample,
  type ChatMessage,
} from "@/lib/fixtures";
import { sendAgentMessage, type AgentMode } from "@/lib/api";
import {
  VOICE_OPENERS,
  VoiceConversation,
} from "@/lib/gradium/voice-conversation";
import { VoiceMode } from "@/components/agent/voice-mode";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type Mode = "welcome" | "interview" | "voice";

const exampleIcons = {
  "ex-objective": Target,
  "ex-success": ClipboardList,
  "ex-priorities": Search,
} as const;

export function AgentPage() {
  const [mode, setMode] = useState<Mode>("welcome");
  const [messages, setMessages] = useState<ChatMessage[]>(seedMessages);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [voice, setVoice] = useState<VoiceConversation | null>(null);
  const interactionIdRef = useRef<string | null>(null);
  const voiceRef = useRef<VoiceConversation | null>(null);
  const submittingRef = useRef(false);

  useEffect(() => {
    return () => voiceRef.current?.end();
  }, []);

  /** One agent turn; the reply (and the owner's message, if shown) lands in the chat. */
  const callAgent = useCallback(
    async (text: string, mode: AgentMode, { showOwner }: { showOwner: boolean }) => {
      if (showOwner) {
        setMessages((prev) => [
          ...prev,
          { id: `u-${Date.now()}`, role: "user", content: text },
        ]);
      }
      const { reply, interaction_id } = await sendAgentMessage(
        text,
        interactionIdRef.current,
        mode,
      );
      interactionIdRef.current = interaction_id;
      setMessages((prev) => [
        ...prev,
        { id: `a-${Date.now()}`, role: "agent", content: reply },
      ]);
      return reply;
    },
    [],
  );

  const submitMessage = useCallback(
    async (raw: string) => {
      const text = raw.trim();
      if (!text || submittingRef.current) return;

      submittingRef.current = true;
      setBusy(true);
      setError(null);
      setDraft("");
      setMode("interview");

      try {
        await callAgent(text, "text", { showOwner: true });
      } catch (err) {
        setError(err instanceof Error ? err.message : "Something went wrong");
      } finally {
        submittingRef.current = false;
        setBusy(false);
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
          interactionIdRef.current ? VOICE_OPENERS.resume : VOICE_OPENERS.start,
          "voice",
          { showOwner: false },
        ),
      reply: (text) => callAgent(text, "voice", { showOwner: true }),
    });
    voiceRef.current = conversation;
    setVoice(conversation);
    setError(null);
    setMode("voice");
    void conversation.start();
  }, [callAgent]);

  const endVoice = useCallback(() => {
    voiceRef.current?.end();
    voiceRef.current = null;
    setVoice(null);
    setMode("interview");
  }, []);

  const resetChat = useCallback(() => {
    voiceRef.current?.end();
    voiceRef.current = null;
    setVoice(null);
    interactionIdRef.current = null;
    setMessages(seedMessages);
    setDraft("");
    setError(null);
    setMode("welcome");
  }, []);

  return (
    <div className="flex h-full flex-col overflow-hidden bg-background">
      <div className="flex h-11 shrink-0 items-center px-4">
        <button
          type="button"
          onClick={resetChat}
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[13px] font-medium text-foreground transition-colors hover:bg-accent"
        >
          New chat
          <ChevronDown className="size-3.5 text-muted-foreground" strokeWidth={1.75} />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        <div className="mx-auto flex min-h-full max-w-2xl flex-col px-6 pb-10 pt-6 sm:pt-16">
          {mode === "voice" && voice ? (
            <VoiceMode conversation={voice} onEnd={endVoice} />
          ) : mode === "welcome" ? (
            <Welcome
              draft={draft}
              setDraft={setDraft}
              busy={busy}
              error={error}
              onVoice={startVoice}
              onSend={() => void submitMessage(draft)}
              onFocus={() => setMode("interview")}
              onExample={(example) => {
                setMode("interview");
                void submitMessage(example.description);
              }}
              onRecent={() => setMode("interview")}
            />
          ) : (
            <Interview
              messages={messages}
              draft={draft}
              setDraft={setDraft}
              busy={busy}
              error={error}
              onVoice={startVoice}
              onSend={() => void submitMessage(draft)}
              onNewChat={resetChat}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function Welcome({
  draft,
  setDraft,
  busy,
  error,
  onVoice,
  onSend,
  onFocus,
  onExample,
  onRecent,
}: {
  draft: string;
  setDraft: (value: string) => void;
  busy: boolean;
  error: string | null;
  onVoice: () => void;
  onSend: () => void;
  onFocus: () => void;
  onExample: (example: AgentExample) => void;
  onRecent: () => void;
}) {
  return (
    <>
      <div className="mb-10 text-center">
        <h1 className="text-xl font-semibold tracking-[-0.02em] text-foreground">
          Start with strategy
        </h1>
        <p className="mt-2 text-[13px] text-muted-foreground">
          I’ll interview you about goals, priorities, and success — never
          implementation details. Type, or switch to voice and talk it through.
        </p>
      </div>

      <Composer
        draft={draft}
        setDraft={setDraft}
        busy={busy}
        error={error}
        onVoice={onVoice}
        onSend={onSend}
        onFocus={onFocus}
      />

      <section className="mt-10">
        <h2 className="mb-3 text-[13px] font-medium text-muted-foreground">
          Get started with some examples
        </h2>
        <div className="grid gap-2 sm:grid-cols-3">
          {agentExamples.map((example) => {
            const Icon =
              exampleIcons[example.id as keyof typeof exampleIcons] ?? Box;
            return (
              <button
                key={example.id}
                type="button"
                onClick={() => onExample(example)}
                className="rounded-xl border border-border bg-background p-3.5 text-left transition-colors hover:bg-accent/60"
              >
                <Icon
                  className="mb-3 size-4 text-muted-foreground"
                  strokeWidth={1.75}
                />
                <p className="text-[13px] font-medium text-foreground">
                  {example.title}
                </p>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                  {example.description}
                </p>
              </button>
            );
          })}
        </div>
      </section>

      <section className="mt-10">
        <h2 className="mb-2 text-[13px] font-medium text-muted-foreground">
          Recent
        </h2>
        <ul className="divide-y divide-border rounded-xl border border-border">
          {recentChats.map((chat) => (
            <li key={chat.id}>
              <button
                type="button"
                onClick={onRecent}
                className="flex w-full items-center justify-between gap-3 px-3.5 py-2.5 text-left text-[13px] transition-colors hover:bg-accent/60"
              >
                <span className="truncate text-foreground">{chat.title}</span>
                <span className="shrink-0 text-xs text-muted-foreground">
                  {chat.age}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}

function Interview({
  messages,
  draft,
  setDraft,
  busy,
  error,
  onVoice,
  onSend,
  onNewChat,
}: {
  messages: ChatMessage[];
  draft: string;
  setDraft: (value: string) => void;
  busy: boolean;
  error: string | null;
  onVoice: () => void;
  onSend: () => void;
  onNewChat: () => void;
}) {
  return (
    <>
      <div className="mb-6 flex items-center justify-between">
        <p className="text-[13px] font-medium text-foreground">
          Objective interview
        </p>
        <button
          type="button"
          onClick={onNewChat}
          className="text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          New chat
        </button>
      </div>

      <div className="mb-8 flex flex-col gap-3">
        {messages.map((message) => (
          <div
            key={message.id}
            className={cn(
              "max-w-[92%] rounded-2xl px-3.5 py-2.5 text-[13px] leading-relaxed",
              message.role === "agent"
                ? "self-start bg-accent text-foreground"
                : "self-end bg-foreground text-background",
            )}
          >
            {message.content}
          </div>
        ))}
        {busy && (
          <div className="inline-flex items-center gap-2 self-start rounded-2xl bg-accent px-3.5 py-2.5 text-[13px] text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" />
            Thinking…
          </div>
        )}
      </div>

      <Composer
        draft={draft}
        setDraft={setDraft}
        busy={busy}
        error={error}
        onVoice={onVoice}
        onSend={onSend}
      />
    </>
  );
}

function Composer({
  draft,
  setDraft,
  busy,
  error,
  onVoice,
  onSend,
  onFocus,
}: {
  draft: string;
  setDraft: (value: string) => void;
  busy: boolean;
  error: string | null;
  onVoice: () => void;
  onSend: () => void;
  onFocus?: () => void;
}) {
  const canSend = draft.trim().length > 0 && !busy;

  return (
    <div>
      <div className="rounded-2xl border border-border bg-background shadow-[0_1px_2px_rgba(0,0,0,0.04)]">
        <textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onFocus={onFocus}
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
