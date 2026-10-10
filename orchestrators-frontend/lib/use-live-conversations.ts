"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  conversationStreamUrl,
  getConversation,
  listConversations,
  type ConversationDetail,
  type ConversationMessage,
  type ConversationSummary,
} from "@/lib/api";
import { getCurrentProjectId } from "@/lib/projects";

/** Union by id, oldest first: events and fetches may arrive in any order. */
function mergeMessages(
  current: ConversationMessage[],
  incoming: ConversationMessage[],
): ConversationMessage[] {
  const byId = new Map(current.map((m) => [m.id, m]));
  for (const m of incoming) byId.set(m.id, m);
  return [...byId.values()].sort((a, b) => a.id - b.id);
}

function upsertSummary(
  list: ConversationSummary[],
  summary: ConversationSummary,
): ConversationSummary[] {
  const rest = list.filter((c) => c.id !== summary.id);
  return [...rest, summary].sort((a, b) => b.updated_at - a.updated_at);
}

/**
 * Everyone's agent conversations, kept live over one Server-Sent Events stream
 * (/api/conversations/stream). The list and the selected conversation are
 * re-fetched whenever the stream (re)connects, so nothing is lost while it
 * was down; events in between are merged by id.
 */
export function useLiveConversations(selectedId: string | null) {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [connected, setConnected] = useState(false);
  const selectedRef = useRef(selectedId);

  const refreshList = useCallback(async () => {
    try {
      setConversations(await listConversations());
    } catch {
      // The stream reconnects and retries.
    }
  }, []);

  const refreshDetail = useCallback(async () => {
    const id = selectedRef.current;
    if (!id) return;
    try {
      const fresh = await getConversation(id);
      if (selectedRef.current !== id) return;
      setDetail((prev) =>
        prev && prev.id === id
          ? { ...fresh, messages: mergeMessages(prev.messages, fresh.messages) }
          : fresh,
      );
    } catch {
      // Keep what we have.
    }
  }, []);

  // Follow the selection.
  useEffect(() => {
    selectedRef.current = selectedId;
    void refreshDetail();
  }, [selectedId, refreshDetail]);

  // One stream for the whole page.
  useEffect(() => {
    let source: EventSource | null = null;
    let retry: number | undefined;
    let closed = false;

    const parse = (event: Event) => JSON.parse((event as MessageEvent).data);

    const connect = () => {
      source = new EventSource(conversationStreamUrl());
      source.onopen = () => {
        setConnected(true);
        void refreshList();
        void refreshDetail();
      };
      source.onerror = () => {
        setConnected(false);
        // EventSource retries by itself unless the server answered with an
        // error status (e.g. during a deploy); then start a new one.
        if (source?.readyState === EventSource.CLOSED && !closed) {
          source.close();
          retry = window.setTimeout(connect, 3000);
        }
      };
      source.addEventListener("conversation", (event) => {
        const summary: ConversationSummary = parse(event);
        // Conversations of other projects stream on the same channel; skip them.
        if ((summary.project ?? "default") !== getCurrentProjectId()) return;
        setConversations((list) => upsertSummary(list, summary));
        setDetail((prev) =>
          prev && prev.id === summary.id ? { ...prev, ...summary } : prev,
        );
      });
      source.addEventListener("message", (event) => {
        const { conversation_id, message } = parse(event) as {
          conversation_id: string;
          message: ConversationMessage;
        };
        setDetail((prev) =>
          prev && prev.id === conversation_id
            ? { ...prev, messages: mergeMessages(prev.messages, [message]) }
            : prev,
        );
      });
      source.addEventListener("thinking", (event) => {
        const { conversation_id, thinking } = parse(event) as {
          conversation_id: string;
          thinking: boolean;
        };
        setConversations((list) =>
          list.map((c) => (c.id === conversation_id ? { ...c, thinking } : c)),
        );
        setDetail((prev) =>
          prev && prev.id === conversation_id ? { ...prev, thinking } : prev,
        );
      });
      source.addEventListener("resync", () => {
        void refreshList();
        void refreshDetail();
      });
    };

    connect();
    return () => {
      closed = true;
      window.clearTimeout(retry);
      source?.close();
    };
  }, [refreshList, refreshDetail]);

  /** Start showing a conversation we just created, before any event. */
  const adopt = useCallback((summary: ConversationSummary) => {
    selectedRef.current = summary.id;
    setConversations((list) => upsertSummary(list, summary));
    setDetail((prev) =>
      prev?.id === summary.id ? prev : { ...summary, messages: [] },
    );
  }, []);

  /** Merge messages we got from a POST (in case the stream is down). */
  const addMessages = useCallback(
    (conversationId: string, messages: ConversationMessage[]) => {
      setDetail((prev) =>
        prev && prev.id === conversationId
          ? { ...prev, messages: mergeMessages(prev.messages, messages) }
          : prev,
      );
    },
    [],
  );

  return {
    conversations,
    // A stale detail from the previous selection is never shown.
    detail: detail && detail.id === selectedId ? detail : null,
    connected,
    adopt,
    addMessages,
  };
}
