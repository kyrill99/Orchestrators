/** Convert Float32 samples to base64-encoded 16-bit little-endian PCM. */
export function floatToPcm16Base64(float32: Float32Array): string {
  const pcm = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i += 1) {
    const sample = Math.max(-1, Math.min(1, float32[i]));
    pcm[i] = sample < 0 ? sample * 32768 : sample * 32767;
  }
  const bytes = new Uint8Array(pcm.buffer);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

export function base64ToUint8Array(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

/** Map AudioContext sample rate to a Gradium STT input_format. */
export function pcmInputFormat(sampleRate: number): string {
  const rounded = Math.round(sampleRate);
  if (rounded === 24000) return "pcm";
  if (rounded === 8000) return "pcm_8000";
  if (rounded === 16000) return "pcm_16000";
  if (rounded === 22050) return "pcm_22050";
  if (rounded === 44100) return "pcm_44100";
  if (rounded === 48000) return "pcm_48000";
  // Closest common rate Gradium accepts
  if (rounded > 44100) return "pcm_48000";
  if (rounded > 22050) return "pcm_44100";
  if (rounded > 16000) return "pcm_22050";
  return "pcm_16000";
}

export function pcmOutputSampleRate(format: string): number {
  if (format === "pcm" || format === "pcm_48000") return 48000;
  if (format === "pcm_24000") return 24000;
  if (format === "pcm_16000") return 16000;
  if (format === "pcm_44100") return 44100;
  return 48000;
}
