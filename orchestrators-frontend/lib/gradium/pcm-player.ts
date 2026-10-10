import { base64ToUint8Array } from "./pcm";

/** Schedules PCM Int16 chunks for gapless playback. */
export class PcmPlayer {
  private ctx: AudioContext;
  private nextTime = 0;
  private stopped = false;

  constructor(sampleRate: number) {
    this.ctx = new AudioContext({ sampleRate });
  }

  async resume() {
    if (this.ctx.state === "suspended") {
      await this.ctx.resume();
    }
  }

  enqueueBase64(base64: string) {
    if (this.stopped) return;
    const bytes = base64ToUint8Array(base64);
    const aligned =
      bytes.byteLength % 2 === 0 ? bytes : bytes.subarray(0, bytes.byteLength - 1);
    const samples = new Int16Array(
      aligned.buffer,
      aligned.byteOffset,
      aligned.byteLength / 2,
    );
    const float32 = new Float32Array(samples.length);
    for (let i = 0; i < samples.length; i += 1) {
      float32[i] = samples[i] / 32768;
    }
    const buffer = this.ctx.createBuffer(1, float32.length, this.ctx.sampleRate);
    buffer.copyToChannel(float32, 0);
    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.ctx.destination);
    const start = Math.max(this.ctx.currentTime + 0.02, this.nextTime);
    source.start(start);
    this.nextTime = start + buffer.duration;
  }

  /** Wait until scheduled audio finishes, then close the context. */
  async drain() {
    if (this.stopped) return;
    const remainingMs = Math.max(
      0,
      (this.nextTime - this.ctx.currentTime) * 1000,
    );
    await new Promise((resolve) => window.setTimeout(resolve, remainingMs + 80));
    this.stop();
  }

  stop() {
    if (this.stopped) return;
    this.stopped = true;
    void this.ctx.close().catch(() => undefined);
  }
}
