const DEFAULT_API_URL = "http://localhost:8000";

export function apiBaseUrl(): string {
  // Empty (production build) means same-origin /api, routed by oauth2-proxy.
  return (
    process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? DEFAULT_API_URL
  );
}

export type GradiumToken = {
  token: string;
  expires_at?: string | null;
  voice_id: string;
  asr_url: string;
  tts_url: string;
};

export type AgentChatResponse = {
  reply: string;
  interaction_id: string;
};

export async function fetchGradiumToken(): Promise<GradiumToken> {
  const response = await fetch(`${apiBaseUrl()}/api/voice/token`, {
    method: "POST",
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || "Could not get Gradium token");
  }
  return response.json();
}

/** "voice" asks for short, speakable replies with less thinking time. */
export type AgentMode = "text" | "voice";

export async function sendAgentMessage(
  message: string,
  previousInteractionId?: string | null,
  mode: AgentMode = "text",
): Promise<AgentChatResponse> {
  const response = await fetch(`${apiBaseUrl()}/api/agent/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      previous_interaction_id: previousInteractionId || undefined,
      mode,
    }),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || "Agent chat failed");
  }
  return response.json();
}
