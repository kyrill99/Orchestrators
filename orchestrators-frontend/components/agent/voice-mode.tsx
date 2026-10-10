"use client";

import { useEffect, useRef, useSyncExternalStore } from "react";
import { Hand, Loader2, Mic, MicOff, PhoneOff } from "lucide-react";
import type {
  VoiceConversation,
  VoiceStatus,
} from "@/lib/gradium/voice-conversation";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const statusLabel: Record<VoiceStatus, string> = {
  starting: "Connecting…",
  listening: "Listening",
  thinking: "Thinking…",
  speaking: "Speaking — tap to interrupt",
  muted: "Muted",
  error: "Voice mode stopped",
  ended: "Voice mode ended",
};

export function VoiceMode({
  conversation,
  onEnd,
}: {
  conversation: VoiceConversation;
  onEnd: () => void;
}) {
  const voice = useSyncExternalStore(
    conversation.subscribe,
    conversation.getSnapshot,
    conversation.getSnapshot,
  );
  const orbRef = useRef<HTMLDivElement>(null);
  const { status } = voice;
  const live = status !== "error" && status !== "ended";

  // Scale the orb with whoever is talking; skipped for reduced motion.
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let frame = 0;
    let level = 0;
    const tick = () => {
      level = level * 0.75 + conversation.level() * 0.25;
      if (orbRef.current) {
        orbRef.current.style.transform = `scale(${1 + level * 0.3})`;
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [conversation]);

  return (
    <div className="flex flex-1 flex-col">
      <div className="mb-6 flex items-center justify-between">
        <p className="inline-flex items-center gap-2 text-[13px] font-medium text-foreground">
          <span
            className={cn(
              "size-1.5 rounded-full",
              live ? "bg-[#5E6AD2]" : "bg-muted-foreground/40",
            )}
          />
          Voice mode
        </p>
        <button
          type="button"
          onClick={onEnd}
          className="text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          Back to chat
        </button>
      </div>

      <div className="flex flex-1 flex-col items-center justify-center gap-6 py-6">
        <button
          type="button"
          onClick={conversation.interrupt}
          disabled={status !== "speaking"}
          aria-label={status === "speaking" ? "Interrupt the agent" : statusLabel[status]}
          className="relative flex size-40 items-center justify-center rounded-full outline-none focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-default"
        >
          <span
            className={cn(
              "absolute inset-0 rounded-full bg-[#5E6AD2]/8 transition-opacity duration-500",
              status === "listening" || status === "speaking" ? "opacity-100" : "opacity-0",
            )}
          />
          <div
            ref={orbRef}
            className={cn(
              "size-24 rounded-full transition-colors duration-300 will-change-transform",
              status === "speaking" && "bg-[#5E6AD2]",
              status === "listening" && "bg-[#5E6AD2]/75",
              status === "thinking" && "animate-pulse bg-[#5E6AD2]/35",
              status === "starting" && "bg-[#5E6AD2]/25",
              (status === "muted" || !live) && "bg-muted-foreground/20",
            )}
          />
          {status === "starting" && (
            <Loader2 className="absolute size-5 animate-spin text-[#5E6AD2]" />
          )}
          {status === "muted" && (
            <MicOff className="absolute size-5 text-muted-foreground" />
          )}
        </button>

        <p className="text-[13px] font-medium text-muted-foreground" aria-live="polite">
          {statusLabel[status]}
        </p>

        <div className="flex min-h-24 max-w-md flex-col items-center gap-2 text-center">
          {voice.said && (
            <p
              className={cn(
                "text-[15px] leading-relaxed transition-colors",
                status === "speaking" ? "text-foreground" : "text-muted-foreground",
              )}
            >
              {voice.said}
            </p>
          )}
          {voice.heard && (status === "listening" || status === "thinking") && (
            <p className="text-[15px] leading-relaxed text-foreground" aria-live="polite">
              {voice.heard}
            </p>
          )}
          {voice.error && (
            <p className="text-xs text-red-600" role="alert">
              {voice.error}
            </p>
          )}
        </div>
      </div>

      <div className="flex flex-col items-center gap-3">
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="lg"
            onClick={conversation.toggleMute}
            disabled={!live}
            aria-pressed={voice.muted}
            className="rounded-full px-4"
          >
            {voice.muted ? <MicOff /> : <Mic />}
            {voice.muted ? "Unmute" : "Mute"}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="lg"
            onClick={conversation.interrupt}
            disabled={status !== "speaking"}
            className="rounded-full px-4"
          >
            <Hand />
            Interrupt
          </Button>
          <Button
            type="button"
            size="lg"
            onClick={onEnd}
            className="rounded-full bg-red-600 px-4 text-white hover:bg-red-600/85"
          >
            <PhoneOff />
            End
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          Talk naturally. I answer when you pause; everything said is kept in the chat.
        </p>
      </div>
    </div>
  );
}
