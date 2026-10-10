import { fetchGradiumToken } from "@/lib/api";
import { floatToPcm16Base64, pcmInputFormat } from "./pcm";

export type SttHandlers = {
  onPartial: (text: string) => void;
  onReady?: () => void;
  onTurnEnd?: () => void;
  onError?: (error: Error) => void;
};

export type SttSession = {
  stop: () => Promise<void>;
};

/**
 * Stream microphone audio to Gradium STT and surface live transcript text.
 * Uses a short-lived token from the Orchestrators backend (API key stays server-side).
 */
export async function startSttSession(handlers: SttHandlers): Promise<SttSession> {
  const { token, asr_url } = await fetchGradiumToken();
  const url = new URL(asr_url);
  url.searchParams.set("token", token);

  const mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      channelCount: 1,
    },
  });

  const audioContext = new AudioContext();
  const inputFormat = pcmInputFormat(audioContext.sampleRate);
  const source = audioContext.createMediaStreamSource(mediaStream);
  // ScriptProcessor matches Gradium's browser recipe; buffer ~85 ms at 48 kHz.
  const processor = audioContext.createScriptProcessor(4096, 1, 1);
  const ws = new WebSocket(url.toString());

  let closed = false;
  let ready = false;
  let turnArmed = false;

  const cleanup = async () => {
    if (closed) return;
    closed = true;
    try {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "end_of_stream" }));
      }
    } catch {
      /* ignore */
    }
    try {
      processor.disconnect();
      source.disconnect();
    } catch {
      /* ignore */
    }
    mediaStream.getTracks().forEach((track) => track.stop());
    if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
      ws.close();
    }
    await audioContext.close().catch(() => undefined);
  };

  ws.addEventListener("open", () => {
    ws.send(
      JSON.stringify({
        type: "setup",
        model_name: "default",
        input_format: inputFormat,
        json_config: {
          language: "en",
          delay_in_frames: 16,
        },
      }),
    );
  });

  ws.addEventListener("message", (event) => {
    let msg: {
      type: string;
      text?: string;
      message?: string;
      vad?: Array<{ inactivity_prob?: number }>;
    };
    try {
      msg = JSON.parse(String(event.data));
    } catch {
      return;
    }

    if (msg.type === "ready") {
      ready = true;
      handlers.onReady?.();
      return;
    }
    if (msg.type === "text" && msg.text) {
      turnArmed = true;
      handlers.onPartial(msg.text);
      return;
    }
    if (msg.type === "step" && turnArmed && msg.vad?.length) {
      const horizon = msg.vad[msg.vad.length - 1];
      if ((horizon?.inactivity_prob ?? 0) > 0.55) {
        turnArmed = false;
        handlers.onTurnEnd?.();
      }
      return;
    }
    if (msg.type === "error") {
      handlers.onError?.(new Error(msg.message || "Gradium STT error"));
      void cleanup();
    }
  });

  ws.addEventListener("error", () => {
    handlers.onError?.(new Error("Gradium STT WebSocket error"));
    void cleanup();
  });

  processor.onaudioprocess = (event) => {
    if (closed || !ready || ws.readyState !== WebSocket.OPEN) return;
    const samples = event.inputBuffer.getChannelData(0);
    ws.send(
      JSON.stringify({
        type: "audio",
        audio: floatToPcm16Base64(samples),
      }),
    );
  };

  // Keep the processor in the graph without feeding mic audio to speakers.
  const mute = audioContext.createGain();
  mute.gain.value = 0;
  source.connect(processor);
  processor.connect(mute);
  mute.connect(audioContext.destination);

  return { stop: cleanup };
}
