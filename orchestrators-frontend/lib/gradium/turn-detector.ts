/**
 * Decides when the owner has finished speaking, from Gradium STT messages.
 * Follows Gradium's turn-taking recipe: once there is text, wait for a few
 * consecutive steps where the longest VAD horizon says the speaker is done,
 * then flush; the matching `flushed` message closes the turn.
 * Plain logic with no imports, so it can run in Node for tests.
 */

export type SttMessage = {
  type: string;
  text?: string;
  vad?: Array<{ horizon_s?: number; inactivity_prob?: number }>;
  flush_id?: number;
};

export type TurnSignal = "flush" | "done" | null;

export type TurnDetectorOptions = {
  /** Inactivity probability above which a step counts as "done talking". */
  threshold?: number;
  /** Consecutive steps (80 ms each) above the threshold before flushing. */
  steps?: number;
  /** Flush anyway after this long without new words (noisy rooms). */
  silenceMs?: number;
};

export class TurnDetector {
  private readonly threshold: number;
  private readonly steps: number;
  private readonly silenceMs: number;
  private segments: string[] = [];
  private highSteps = 0;
  private lastTextAt = 0;
  private flushRequested = false;

  constructor(options: TurnDetectorOptions = {}) {
    this.threshold = options.threshold ?? 0.5;
    this.steps = options.steps ?? 3;
    this.silenceMs = options.silenceMs ?? 2500;
  }

  get text(): string {
    return this.segments.join(" ").replace(/\s+/g, " ").trim();
  }

  /** Feed one server message. "flush": send a flush now. "done": the turn is complete. */
  onMessage(msg: SttMessage, now: number = Date.now()): TurnSignal {
    if (msg.type === "text" && msg.text?.trim()) {
      this.segments.push(msg.text.trim());
      this.lastTextAt = now;
      this.highSteps = 0;
      return null;
    }
    if ((msg.type === "step" || msg.type === "vad") && msg.vad?.length) {
      if (this.flushRequested || this.segments.length === 0) return null;
      const longest = msg.vad[msg.vad.length - 1];
      this.highSteps =
        (longest?.inactivity_prob ?? 0) > this.threshold ? this.highSteps + 1 : 0;
      if (this.highSteps >= this.steps || now - this.lastTextAt > this.silenceMs) {
        this.flushRequested = true;
        return "flush";
      }
      return null;
    }
    if (msg.type === "flushed") {
      return "done";
    }
    return null;
  }
}
