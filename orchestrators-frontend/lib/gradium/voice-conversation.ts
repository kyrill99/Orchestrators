import { fetchGradiumToken, type GradiumToken } from "@/lib/api";
import { base64ToUint8Array, floatToPcm16Base64, pcmInputFormat } from "./pcm";
import { TurnDetector, type SttMessage } from "./turn-detector";

/**
 * A hands-free voice conversation: listen → think → speak → listen, until ended.
 *
 * The mic stays open for the whole conversation. Each owner turn gets its own
 * Gradium STT session (sessions are capped at five minutes, and closing STT while
 * the agent speaks keeps its voice out of the transcript); audio captured while
 * the session connects is buffered, so no words are lost. Replies are spoken
 * with Gradium TTS on one AudioContext created in the click that opened voice
 * mode, so browsers allow playback.
 */

export type VoiceStatus =
  | "starting"
  | "listening"
  | "thinking"
  | "speaking"
  | "muted"
  | "error"
  | "ended";

export type VoiceSnapshot = {
  status: VoiceStatus;
  /** What the owner is saying in the current turn. */
  heard: string;
  /** The agent's latest line. */
  said: string;
  muted: boolean;
  error: string | null;
};

export type VoiceAgent = {
  /** The agent's first line when voice mode opens. */
  open: () => Promise<string>;
  /** The agent's reply to one owner turn. */
  reply: (text: string) => Promise<string>;
};

/** Stage directions for the agent (see CHARACTER in services/agent.py). */
export const VOICE_OPENERS = {
  start:
    "[The owner switched on voice mode. The conversation is new: greet them in a few words and ask your first question.]",
  resume:
    "[The owner switched to voice mode. Pick up where you left off in one short sentence and ask your next question.]",
};

const TTS_FORMAT = "pcm_24000";
const TTS_SAMPLE_RATE = 24000;
/** Consecutive 80 ms steps of "done talking" before a turn ends (Gradium suggests 3). */
const TURN_END_STEPS = 4;
const FLUSH_TIMEOUT_MS = 2000;
const MAX_BUFFERED_CHUNKS = 120; // ~10 s of mic audio while STT connects
const MAX_STT_RETRIES = 3;

export class VoiceConversation {
  private snapshot: VoiceSnapshot = {
    status: "starting",
    heard: "",
    said: "",
    muted: false,
    error: null,
  };
  private readonly listeners = new Set<() => void>();
  private readonly output: AudioContext;
  private readonly outputMeter: AnalyserNode;
  private mic: Mic | null = null;
  private stt: SttTurn | null = null;
  private stopSpeaking: (() => void) | null = null;
  private sttRetries = 0;
  private ended = false;
  private meterData: Float32Array<ArrayBuffer> | null = null;

  /** Call inside the click that opens voice mode. */
  constructor(private readonly agent: VoiceAgent) {
    this.output = new AudioContext();
    this.outputMeter = this.output.createAnalyser();
    this.outputMeter.connect(this.output.destination);
    void this.output.resume();
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = () => this.snapshot;

  async start(): Promise<void> {
    try {
      this.mic = await openMic((audio) => this.stt?.push(audio));
      if (this.ended) {
        this.mic.close();
        return;
      }
      this.set({ status: "thinking" });
      const line = await this.agent.open();
      if (this.ended) return;
      await this.speak(line);
      this.listen();
    } catch (error) {
      this.fail(error);
    }
  }

  /** Stop the agent mid-sentence and hand the turn back to the owner. */
  interrupt = () => {
    if (this.snapshot.status === "speaking") this.stopSpeaking?.();
  };

  toggleMute = () => {
    const muted = !this.snapshot.muted;
    this.set({ muted });
    if (muted && this.snapshot.status === "listening") {
      this.stt?.close();
      this.stt = null;
      this.set({ status: "muted", heard: "" });
    } else if (!muted && this.snapshot.status === "muted") {
      this.listen();
    }
  };

  end = () => this.shutdown("ended");

  /** Loudness 0–1 of whoever is talking, for the orb. */
  level(): number {
    const { status } = this.snapshot;
    const meter =
      status === "speaking"
        ? this.outputMeter
        : status === "listening"
          ? this.mic?.meter
          : null;
    if (!meter) return 0;
    if (this.meterData?.length !== meter.fftSize) {
      this.meterData = new Float32Array(meter.fftSize);
    }
    meter.getFloatTimeDomainData(this.meterData);
    let sum = 0;
    for (const sample of this.meterData) sum += sample * sample;
    return Math.min(1, Math.sqrt(sum / this.meterData.length) * 5);
  }

  private set(patch: Partial<VoiceSnapshot>) {
    this.snapshot = { ...this.snapshot, ...patch };
    this.listeners.forEach((listener) => listener());
  }

  private fail(error: unknown) {
    if (this.ended) return;
    const blocked = error instanceof DOMException && error.name === "NotAllowedError";
    this.set({
      error: blocked
        ? "Microphone access is blocked. Allow it in the browser, then try again."
        : error instanceof Error
          ? error.message
          : "Voice mode stopped.",
    });
    this.shutdown("error");
  }

  private shutdown(status: "ended" | "error") {
    if (this.ended) return;
    this.ended = true;
    this.stt?.close();
    this.stt = null;
    this.stopSpeaking?.();
    this.mic?.close();
    this.mic = null;
    void this.output.close().catch(() => undefined);
    this.set({ status });
  }

  private listen(heardBefore = "") {
    if (this.ended || !this.mic) return;
    if (this.snapshot.muted) {
      this.set({ status: "muted", heard: "" });
      return;
    }
    this.set({ status: "listening", heard: heardBefore });
    const turn = new SttTurn(this.mic.format, {
      ready: () => {
        this.sttRetries = 0;
      },
      heard: (text) => this.set({ heard: join(heardBefore, text) }),
      done: (text) => {
        if (this.stt !== turn) return;
        this.stt = null;
        void this.respond(join(heardBefore, text));
      },
      lost: (text) => {
        if (this.stt !== turn) return;
        this.stt = null;
        this.sttRetries += 1;
        if (this.sttRetries > MAX_STT_RETRIES) {
          this.fail(new Error("Lost the connection to speech recognition."));
          return;
        }
        this.listen(join(heardBefore, text));
      },
    });
    this.stt = turn;
    void turn.connect(fetchGradiumToken());
  }

  private async respond(text: string) {
    if (!text.trim()) {
      this.listen();
      return;
    }
    this.set({ status: "thinking", heard: text, error: null });
    let line: string;
    try {
      line = await this.agent.reply(text);
    } catch (error) {
      if (this.ended) return;
      this.set({
        error: error instanceof Error ? error.message : "The agent did not answer.",
      });
      this.listen();
      return;
    }
    if (this.ended) return;
    await this.speak(line);
    this.listen();
  }

  private async speak(line: string): Promise<void> {
    if (this.ended) return;
    this.set({ status: "speaking", said: line, heard: "" });
    const text = speakable(line);
    if (!text) return;
    // Gradium tokens live ~5 s, so fetch one right before each session.
    let token: GradiumToken;
    try {
      token = await fetchGradiumToken();
    } catch {
      this.set({ error: "Couldn't play the reply aloud." });
      return; // the line stays on screen; carry on listening
    }
    if (this.ended) return;

    const url = new URL(token.tts_url);
    url.searchParams.set("token", token.token);
    const ws = new WebSocket(url.toString());
    const sources = new Set<AudioBufferSourceNode>();
    let nextStart = 0;
    let streamDone = false;
    let played = false;
    let interrupted = false;

    await new Promise<void>((resolve) => {
      let settled = false;
      let watchdog: number | null = null;
      const finish = () => {
        if (settled) return;
        settled = true;
        if (watchdog !== null) window.clearTimeout(watchdog);
        this.stopSpeaking = null;
        if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
          ws.close();
        }
        resolve();
      };
      const finishWhenPlayed = () => {
        if (!streamDone) return;
        if (sources.size === 0) {
          finish();
        } else if (watchdog === null) {
          // If playback stalls (e.g. the browser keeps audio suspended), move on anyway.
          const remaining = Math.max(0, nextStart - this.output.currentTime);
          watchdog = window.setTimeout(this.stopSpeaking ?? finish, remaining * 1000 + 1500);
        }
      };
      this.stopSpeaking = () => {
        interrupted = true;
        sources.forEach((source) => {
          source.onended = null;
          try {
            source.stop();
          } catch {
            /* already stopped */
          }
        });
        sources.clear();
        finish();
      };

      ws.addEventListener("open", () => {
        ws.send(
          JSON.stringify({
            type: "setup",
            model_name: "default",
            voice_id: token.voice_id,
            output_format: TTS_FORMAT,
          }),
        );
        ws.send(JSON.stringify({ type: "text", text }));
        ws.send(JSON.stringify({ type: "end_of_stream" }));
      });
      ws.addEventListener("message", (event) => {
        let msg: { type: string; audio?: string };
        try {
          msg = JSON.parse(String(event.data));
        } catch {
          return;
        }
        if (msg.type === "audio" && msg.audio && !settled) {
          played = true;
          const source = this.output.createBufferSource();
          source.buffer = pcm16ToBuffer(this.output, msg.audio);
          source.connect(this.outputMeter);
          const at = Math.max(this.output.currentTime + 0.05, nextStart);
          source.start(at);
          nextStart = at + source.buffer.duration;
          sources.add(source);
          source.onended = () => {
            sources.delete(source);
            finishWhenPlayed();
          };
        } else if (msg.type === "end_of_stream" || msg.type === "error") {
          streamDone = true;
          finishWhenPlayed();
        }
      });
      const closed = () => {
        streamDone = true;
        finishWhenPlayed();
      };
      ws.addEventListener("error", closed);
      ws.addEventListener("close", closed);
    });
    if (!played && !interrupted && !this.ended) {
      this.set({ error: "Couldn't play the reply aloud." });
    }
  }
}

type Mic = {
  format: string;
  meter: AnalyserNode;
  close: () => void;
};

async function openMic(onAudio: (base64: string) => void): Promise<Mic> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      channelCount: 1,
    },
  });
  const ctx = new AudioContext();
  const source = ctx.createMediaStreamSource(stream);
  const meter = ctx.createAnalyser();
  // ScriptProcessor matches Gradium's browser recipe; ~85 ms per chunk at 48 kHz.
  const processor = ctx.createScriptProcessor(4096, 1, 1);
  const silence = ctx.createGain();
  silence.gain.value = 0;
  source.connect(meter);
  source.connect(processor);
  processor.connect(silence);
  silence.connect(ctx.destination);
  processor.onaudioprocess = (event) => {
    onAudio(floatToPcm16Base64(event.inputBuffer.getChannelData(0)));
  };
  return {
    format: pcmInputFormat(ctx.sampleRate),
    meter,
    close: () => {
      processor.onaudioprocess = null;
      processor.disconnect();
      source.disconnect();
      stream.getTracks().forEach((track) => track.stop());
      void ctx.close().catch(() => undefined);
    },
  };
}

type SttEvents = {
  ready: () => void;
  heard: (text: string) => void;
  done: (text: string) => void;
  lost: (text: string) => void;
};

/** One owner turn on one Gradium STT session. */
class SttTurn {
  private ws: WebSocket | null = null;
  private ready = false;
  private closed = false;
  private buffered: string[] = [];
  private flushTimer: number | null = null;
  private readonly detector = new TurnDetector({ steps: TURN_END_STEPS });

  constructor(
    private readonly format: string,
    private readonly on: SttEvents,
  ) {}

  async connect(tokenPromise: Promise<GradiumToken>) {
    let token: GradiumToken;
    try {
      token = await tokenPromise;
    } catch {
      this.lose();
      return;
    }
    if (this.closed) return;
    const url = new URL(token.asr_url);
    url.searchParams.set("token", token.token);
    const ws = new WebSocket(url.toString());
    this.ws = ws;

    ws.addEventListener("open", () => {
      ws.send(
        JSON.stringify({
          type: "setup",
          model_name: "default",
          input_format: this.format,
          json_config: { language: "en", delay_in_frames: 16 },
        }),
      );
    });
    ws.addEventListener("message", (event) => {
      let msg: SttMessage;
      try {
        msg = JSON.parse(String(event.data));
      } catch {
        return;
      }
      if (msg.type === "ready") {
        this.ready = true;
        this.buffered.forEach((audio) => this.send(audio));
        this.buffered = [];
        this.on.ready();
        return;
      }
      if (msg.type === "error") {
        this.lose();
        return;
      }
      const signal = this.detector.onMessage(msg);
      if (msg.type === "text") this.on.heard(this.detector.text);
      if (signal === "flush") {
        ws.send(JSON.stringify({ type: "flush", flush_id: 1 }));
        this.flushTimer = window.setTimeout(() => this.finish(), FLUSH_TIMEOUT_MS);
      } else if (signal === "done") {
        this.finish();
      }
    });
    ws.addEventListener("error", () => this.lose());
    ws.addEventListener("close", () => this.lose());
  }

  push(audio: string) {
    if (this.closed) return;
    if (this.ready) this.send(audio);
    else if (this.buffered.length < MAX_BUFFERED_CHUNKS) this.buffered.push(audio);
  }

  /** Close without reporting anything (mute, end). */
  close() {
    if (this.closed) return;
    this.closed = true;
    if (this.flushTimer !== null) window.clearTimeout(this.flushTimer);
    const ws = this.ws;
    if (!ws) return;
    if (ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({ type: "end_of_stream" }));
      } catch {
        /* closing anyway */
      }
    }
    if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
      ws.close();
    }
  }

  private send(audio: string) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "audio", audio }));
    }
  }

  private finish() {
    if (this.closed) return;
    this.close();
    this.on.done(this.detector.text);
  }

  private lose() {
    if (this.closed) return;
    this.close();
    this.on.lost(this.detector.text);
  }
}

function pcm16ToBuffer(ctx: AudioContext, base64: string): AudioBuffer {
  const bytes = base64ToUint8Array(base64);
  const samples = new Int16Array(
    bytes.buffer,
    bytes.byteOffset,
    Math.floor(bytes.byteLength / 2),
  );
  const buffer = ctx.createBuffer(1, Math.max(1, samples.length), TTS_SAMPLE_RATE);
  const channel = buffer.getChannelData(0);
  for (let i = 0; i < samples.length; i += 1) channel[i] = samples[i] / 32768;
  return buffer;
}

/** Strip what a voice should not read out: markdown, list markers, links. */
function speakable(text: string): string {
  return text
    .replace(/https?:\/\/\S+/g, "")
    .replace(/^\s*(?:[-*•]|\d+\.)\s+/gm, "")
    .replace(/[*_#`>]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function join(before: string, now: string): string {
  return [before, now].filter((part) => part.trim()).join(" ");
}
