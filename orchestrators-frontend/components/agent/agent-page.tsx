"use client";

import { useState } from "react";
import {
  ArrowUp,
  Box,
  ChevronDown,
  ClipboardList,
  Paperclip,
  Search,
  Target,
} from "lucide-react";
import {
  agentExamples,
  chatMessages,
  recentChats,
  type AgentExample,
} from "@/lib/fixtures";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type Mode = "welcome" | "interview";

const exampleIcons = {
  "ex-objective": Target,
  "ex-success": ClipboardList,
  "ex-priorities": Search,
} as const;

export function AgentPage() {
  const [mode, setMode] = useState<Mode>("welcome");

  return (
    <div className="flex h-full flex-col overflow-hidden bg-background">
      <div className="flex h-11 shrink-0 items-center px-4">
        <button
          type="button"
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[13px] font-medium text-foreground transition-colors hover:bg-accent"
        >
          New chat
          <ChevronDown className="size-3.5 text-muted-foreground" strokeWidth={1.75} />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        <div className="mx-auto flex min-h-full max-w-2xl flex-col px-6 pb-10 pt-6 sm:pt-16">
          {mode === "welcome" ? (
            <Welcome
              onStart={() => setMode("interview")}
              onExample={() => setMode("interview")}
            />
          ) : (
            <Interview onNewChat={() => setMode("welcome")} />
          )}
        </div>
      </div>
    </div>
  );
}

function Welcome({
  onStart,
  onExample,
}: {
  onStart: () => void;
  onExample: (example: AgentExample) => void;
}) {
  return (
    <>
      <div className="mb-10 text-center">
        <h1 className="text-xl font-semibold tracking-[-0.02em] text-foreground">
          Start with strategy
        </h1>
        <p className="mt-2 text-[13px] text-muted-foreground">
          I’ll interview you about goals, priorities, and success — never
          implementation details.
        </p>
      </div>

      <Composer onFocus={onStart} />

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
                onClick={onStart}
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

function Interview({ onNewChat }: { onNewChat: () => void }) {
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
        {chatMessages.map((message) => (
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
      </div>

      <Composer />
    </>
  );
}

function Composer({ onFocus }: { onFocus?: () => void }) {
  return (
    <div className="rounded-2xl border border-border bg-background shadow-[0_1px_2px_rgba(0,0,0,0.04)]">
      <textarea
        readOnly
        onFocus={onFocus}
        onClick={onFocus}
        placeholder="Ask about goals, priorities, or what success looks like…"
        aria-label="Ask the agent (UI mock)"
        className="min-h-[88px] w-full resize-none bg-transparent px-4 pt-3.5 pb-2 text-[14px] outline-none placeholder:text-muted-foreground"
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
            size="icon-sm"
            disabled
            aria-label="Send"
            className="rounded-full bg-[#5E6AD2] text-white hover:bg-[#5E6AD2]/80 disabled:opacity-40"
          >
            <ArrowUp className="size-3.5" />
          </Button>
        </div>
      </div>
    </div>
  );
}
