import { fetchGradiumToken } from "@/lib/api";
import { PcmPlayer } from "./pcm-player";
import { pcmOutputSampleRate } from "./pcm";

const OUTPUT_FORMAT = "pcm_24000";

/**
 * Speak text via Gradium TTS using a short-lived browser token.
 */
export async function speakText(text: string, voiceId?: string): Promise<void> {
  const trimmed = text.trim();
  if (!trimmed) return;

  const { token, tts_url, voice_id } = await fetchGradiumToken();
  const url = new URL(tts_url);
  url.searchParams.set("token", token);

  const player = new PcmPlayer(pcmOutputSampleRate(OUTPUT_FORMAT));
  await player.resume();

  try {
    await new Promise<void>((resolve, reject) => {
      const ws = new WebSocket(url.toString());
      let settled = false;

      const finish = (error?: Error) => {
        if (settled) return;
        settled = true;
        if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
          ws.close();
        }
        if (error) reject(error);
        else resolve();
      };

      ws.addEventListener("open", () => {
        ws.send(
          JSON.stringify({
            type: "setup",
            model_name: "default",
            voice_id: voiceId || voice_id,
            output_format: OUTPUT_FORMAT,
          }),
        );
        ws.send(JSON.stringify({ type: "text", text: trimmed }));
        ws.send(JSON.stringify({ type: "end_of_stream" }));
      });

      ws.addEventListener("message", (event) => {
        let msg: { type: string; audio?: string; message?: string };
        try {
          msg = JSON.parse(String(event.data));
        } catch {
          return;
        }
        if (msg.type === "audio" && msg.audio) {
          player.enqueueBase64(msg.audio);
          return;
        }
        if (msg.type === "end_of_stream") {
          finish();
          return;
        }
        if (msg.type === "error") {
          finish(new Error(msg.message || "Gradium TTS error"));
        }
      });

      ws.addEventListener("error", () => {
        finish(new Error("Gradium TTS WebSocket error"));
      });
      ws.addEventListener("close", () => {
        // Socket may close after end_of_stream; resolve if not already done.
        finish();
      });
    });

    await player.drain();
  } catch (error) {
    player.stop();
    throw error;
  }
}
