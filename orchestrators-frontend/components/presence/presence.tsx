"use client";

import {
  presenceColor,
  presenceInitial,
  presenceName,
  typingText,
  type PresenceEntry,
} from "@/lib/presence";
import { cn } from "@/lib/utils";

/** Overlapping coloured initials; a pulsing ring marks someone typing. */
export function AvatarStack({
  people,
  size = 20,
  max = 4,
  className,
}: {
  people: PresenceEntry[];
  size?: number;
  max?: number;
  className?: string;
}) {
  if (people.length === 0) return null;
  const shown = people.slice(0, max);
  const extra = people.length - shown.length;
  return (
    <span className={cn("inline-flex items-center", className)}>
      {shown.map((person, index) => (
        <span
          key={person.email}
          title={`${presenceName(person)}${person.typing ? " is typing…" : " is here"}`}
          className={cn(
            "relative inline-flex shrink-0 items-center justify-center rounded-full font-semibold text-white ring-2 ring-background",
            person.typing && "animate-pulse",
          )}
          style={{
            width: size,
            height: size,
            fontSize: Math.max(8, Math.round(size * 0.48)),
            backgroundColor: presenceColor(person.email),
            marginLeft: index === 0 ? 0 : -Math.round(size / 4),
          }}
        >
          {presenceInitial(person)}
        </span>
      ))}
      {extra > 0 ? (
        <span
          className="ml-1 text-[10px] font-medium text-muted-foreground"
          title={people.slice(max).map((p) => presenceName(p)).join(", ")}
        >
          +{extra}
        </span>
      ) : null}
    </span>
  );
}

/** "Lorenz is typing…" with animated dots; renders a fixed-height row. */
export function TypingLine({
  people,
  className,
}: {
  people: PresenceEntry[];
  className?: string;
}) {
  const text = typingText(people);
  return (
    <div
      className={cn(
        "flex h-5 items-center gap-1.5 text-[12px] text-muted-foreground",
        className,
      )}
      aria-live="polite"
    >
      {text ? (
        <>
          <span className="inline-flex items-center gap-0.5" aria-hidden="true">
            <span className="size-1 animate-bounce rounded-full bg-muted-foreground [animation-delay:-0.3s]" />
            <span className="size-1 animate-bounce rounded-full bg-muted-foreground [animation-delay:-0.15s]" />
            <span className="size-1 animate-bounce rounded-full bg-muted-foreground" />
          </span>
          <span>{text}</span>
        </>
      ) : null}
    </div>
  );
}

/** "Lorenz and Vanya are here" with avatars, plus who is typing. */
export function HereLine({
  people,
  className,
}: {
  people: PresenceEntry[];
  className?: string;
}) {
  if (people.length === 0) return null;
  const names = people.map((p) => presenceName(p));
  const here =
    names.length === 1
      ? `${names[0]} is here`
      : names.length === 2
        ? `${names[0]} and ${names[1]} are here`
        : `${names[0]} and ${names.length - 1} others are here`;
  const typing = typingText(people);
  return (
    <div
      className={cn(
        "inline-flex items-center gap-2 text-[12px] text-muted-foreground",
        className,
      )}
      aria-live="polite"
    >
      <AvatarStack people={people} size={20} />
      <span>{typing || here}</span>
    </div>
  );
}
