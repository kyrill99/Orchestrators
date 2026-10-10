"use client";

import { AvatarStack } from "@/components/presence/presence";
import {
  useMultiplayer,
  useMultiplayerClient,
} from "@/components/multiplayer/provider";
import {
  presenceName,
  usePresenceSnapshot,
  type PresenceEntry,
} from "@/lib/presence";

function where(e: PresenceEntry): string {
  if (e.editing) return `editing ${e.editing}`;
  if (e.where === "agent") return "in the agent chat";
  if (e.where.startsWith("task:")) return `on task ${e.where.slice(5)}`;
  if (e.where.startsWith("issues:")) return "on an issue";
  if (e.where === "graph") return "on the project graph";
  if (e.where === "timeline") return "on the timeline";
  return e.where ? `on ${e.where}` : "online";
}

/** Everyone else online, and where; click one to follow them (Esc stops). */
export function OnlineAvatars({ className }: { className?: string }) {
  const { others } = usePresenceSnapshot();
  const client = useMultiplayerClient();
  const { following } = useMultiplayer();
  if (others.length === 0) return null;
  return (
    <AvatarStack
      className={className}
      people={others}
      size={22}
      max={5}
      describe={(e) =>
        `${presenceName(e)} (${e.email}) · ${where(e)} · ${
          following === e.email ? "following" : "click to follow"
        }`
      }
      onSelect={(e) => client?.follow(following === e.email ? null : e.email)}
    />
  );
}
