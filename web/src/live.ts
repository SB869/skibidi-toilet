// The microphone, live: pitch, level and edge as you sing, a reference playing in your
// headphones on the same clock, and uncompressed recording.

import type { Clock } from "./audio";

/** What happens when, in seconds from the moment playback starts. */
export interface Plan {
  clicks: number[];          // count-in (and optional click-track) times; the first is accented
  guideAt: number;           // when the reference audio starts playing
  guideOffset: number;       // and from how far into it
  guideAudibleFrom: number;  // keep it silent until then (e.g. through the count-in)
}

export interface Reading {
  time: number;          // seconds since playback started (or where it stopped)
  midi: number | null;   // sung pitch, null when not singing
  level: number;         // RMS, dBFS
  peak: number;          // dBFS
  edge: number | null;   // presence-band over body-band energy, dB, while singing
}

// Must match analysis.EDGE_BAND / BODY_BAND on the server.
const EDGE_BAND: [number, number] = [1000, 4000];
const BODY_BAND: [number, number] = [80, 1000];
const SINGING_DBFS = -50;   // quieter than this isn't treated as singing

export class LiveSession implements Clock {
  readonly ctx: AudioContext;
  private stream: MediaStream;
  private analyser: AnalyserNode;
  private recorder: AudioWorkletNode;
  private guideGain: GainNode;
  private guideVolume = 0.8;
  private guide: AudioBufferSourceNode | null = null;
  private clickGain: GainNode;
  private clickSounds: [AudioBuffer, AudioBuffer];
  private scheduled: AudioBufferSourceNode[] = [];
  private chunks: Float32Array[][] = [];
  private finished: (() => void) | null = null;
  private startedAt = 0;
  private stoppedAt = 0;
  private wave: Float32Array<ArrayBuffer>;
  private spectrum: Float32Array<ArrayBuffer>;
  private recent: number[] = [];

  playing = false;
  recording = false;

  static async open(): Promise<LiveSession> {
    const stream = await navigator.mediaDevices.getUserMedia({
      // Voice-call processing smears pitch and squashes dynamics: wrong for singing.
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
    });
    const ctx = new AudioContext({ latencyHint: "interactive" });
    await ctx.audioWorklet.addModule("/recorder-worklet.js");
    return new LiveSession(ctx, stream);
  }

  private constructor(ctx: AudioContext, stream: MediaStream) {
    this.ctx = ctx;
    this.stream = stream;
    const input = ctx.createMediaStreamSource(stream);
    this.analyser = ctx.createAnalyser();
    this.analyser.fftSize = 2048;
    this.analyser.smoothingTimeConstant = 0;
    input.connect(this.analyser);

    this.recorder = new AudioWorkletNode(ctx, "recorder");
    const mute = ctx.createGain();
    mute.gain.value = 0;
    input.connect(this.recorder).connect(mute).connect(ctx.destination);  // keeps the worklet running
    this.recorder.port.onmessage = (event) => {
      if (event.data.type === "chunk") this.chunks.push(event.data.data);
      else if (event.data.type === "done") {
        this.finished?.();
        this.finished = null;
      }
    };

    this.guideGain = ctx.createGain();
    this.guideGain.connect(ctx.destination);
    this.clickGain = ctx.createGain();
    this.clickGain.gain.value = 0.6;
    this.clickGain.connect(ctx.destination);
    this.clickSounds = [clickSound(ctx, 2400), clickSound(ctx, 1600)];
    this.wave = new Float32Array(this.analyser.fftSize);
    this.spectrum = new Float32Array(this.analyser.frequencyBinCount);
  }

  get sampleRate() {
    return this.ctx.sampleRate;
  }

  position(): number {
    return this.playing ? Math.max(0, this.ctx.currentTime - this.startedAt) : this.stoppedAt;
  }

  /** How late a recording runs behind what it was sung to: the reference's way out to your
      headphones plus your voice's way in, as far as the browser can tell (seconds). */
  latency(): number {
    const settings = this.stream.getAudioTracks()[0]?.getSettings() as { latency?: number } | undefined;
    return (this.ctx.outputLatency || 0) + (this.ctx.baseLatency || 0) + (settings?.latency ?? 0);
  }

  setGuideVolume(volume: number) {
    this.guideVolume = volume;
    this.guideGain.gain.setTargetAtTime(volume, this.ctx.currentTime, 0.02);
  }

  /** Start the clock shortly from now: clicks and reference as planned; optionally record. */
  start(guide: AudioBuffer | null, record: boolean, plan: Plan) {
    if (this.ctx.state === "suspended") void this.ctx.resume();
    const when = this.ctx.currentTime + 0.15;
    const gain = this.guideGain.gain;
    gain.cancelScheduledValues(this.ctx.currentTime);
    if (plan.guideAudibleFrom > plan.guideAt) {
      gain.setValueAtTime(0, when);
      gain.setValueAtTime(0, when + plan.guideAudibleFrom - 0.02);
      gain.linearRampToValueAtTime(this.guideVolume, when + plan.guideAudibleFrom);
    } else {
      gain.setValueAtTime(this.guideVolume, when);
    }
    if (guide) {
      this.guide = this.ctx.createBufferSource();
      this.guide.buffer = guide;
      this.guide.connect(this.guideGain);
      this.guide.start(when + Math.max(0, plan.guideAt), Math.max(0, plan.guideOffset));
    }
    this.scheduled = plan.clicks.filter((t) => t >= 0).map((t, i) => {
      const click = this.ctx.createBufferSource();
      click.buffer = this.clickSounds[i === 0 ? 0 : 1];
      click.connect(this.clickGain);
      click.start(when + t);
      return click;
    });
    if (record) {
      this.chunks = [];
      this.recorder.port.postMessage({ type: "start", frame: Math.round(when * this.ctx.sampleRate) });
    }
    this.startedAt = when;
    this.playing = true;
    this.recording = record;
  }

  /** Stop. Resolves with the recorded channels when a recording was running. */
  async stop(): Promise<Float32Array[] | null> {
    if (!this.playing) return null;
    this.stoppedAt = this.position();
    this.playing = false;
    for (const source of [this.guide, ...this.scheduled]) {
      try {
        source?.stop();
      } catch {
        // already finished
      }
    }
    this.guide = null;
    this.scheduled = [];
    if (!this.recording) return null;
    this.recording = false;
    await new Promise<void>((resolve) => {
      this.finished = resolve;
      this.recorder.port.postMessage({ type: "stop" });
    });
    return joinChunks(this.chunks);
  }

  read(): Reading {
    this.analyser.getFloatTimeDomainData(this.wave);
    let power = 0, peak = 0;
    for (const v of this.wave) {
      power += v * v;
      peak = Math.max(peak, Math.abs(v));
    }
    const level = 10 * Math.log10(power / this.wave.length + 1e-12);
    let midi: number | null = null;
    if (level > SINGING_DBFS) {
      const hz = yin(this.wave, this.ctx.sampleRate);
      if (hz) midi = 69 + 12 * Math.log2(hz / 440);
    }
    // a short median steadies the readout against single-frame octave slips
    if (midi === null) this.recent = [];
    else {
      this.recent = [...this.recent.slice(-2), midi];
      midi = [...this.recent].sort((a, b) => a - b)[Math.floor(this.recent.length / 2)];
    }
    let edge: number | null = null;
    if (midi !== null) {
      this.analyser.getFloatFrequencyData(this.spectrum);
      const binHz = this.ctx.sampleRate / this.analyser.fftSize;
      const band = ([lo, hi]: [number, number]) => {
        let sum = 0;
        for (let i = Math.ceil(lo / binHz); i < Math.min(this.spectrum.length, hi / binHz); i++) sum += 10 ** (this.spectrum[i] / 10);
        return sum;
      };
      edge = 10 * Math.log10((band(EDGE_BAND) + 1e-12) / (band(BODY_BAND) + 1e-12));
    }
    return { time: this.position(), midi, level, peak: 20 * Math.log10(peak + 1e-9), edge };
  }

  close() {
    this.stream.getTracks().forEach((t) => t.stop());
    void this.ctx.close();
  }
}

/** A short, bright tick: easy to hear over a reference without masking it. */
function clickSound(ctx: AudioContext, hz: number): AudioBuffer {
  const length = Math.round(ctx.sampleRate * 0.03);
  const buffer = ctx.createBuffer(1, length, ctx.sampleRate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < length; i++) data[i] = Math.sin((2 * Math.PI * hz * i) / ctx.sampleRate) * Math.exp(-i / (length / 6));
  return buffer;
}

/** Pitch of one frame by YIN (de Cheveigné & Kawahara), or null if it isn't clearly pitched. */
export function yin(frame: Float32Array, sampleRate: number, fmin = 70, fmax = 1100, threshold = 0.15): number | null {
  const maxLag = Math.min(Math.floor(sampleRate / fmin), Math.floor(frame.length / 2));
  const minLag = Math.max(2, Math.floor(sampleRate / fmax));
  const width = frame.length - maxLag;
  const d = new Float32Array(maxLag + 2);
  for (let lag = 1; lag <= maxLag + 1; lag++) {
    let sum = 0;
    for (let i = 0; i < width; i++) {
      const diff = frame[i] - frame[i + lag];
      sum += diff * diff;
    }
    d[lag] = sum;
  }
  // cumulative mean normalised difference
  let running = 0;
  d[0] = 1;
  for (let lag = 1; lag <= maxLag + 1; lag++) {
    running += d[lag];
    d[lag] = running > 0 ? (d[lag] * lag) / running : 1;
  }
  for (let lag = minLag; lag <= maxLag; lag++) {
    if (d[lag] < threshold) {
      while (lag + 1 <= maxLag && d[lag + 1] < d[lag]) lag++;
      // parabolic interpolation between neighbouring lags
      const a = d[lag - 1], b = d[lag], c = d[lag + 1];
      const shift = (a - c) / (2 * (a - 2 * b + c) || 1);
      return sampleRate / (lag + (Number.isFinite(shift) ? Math.max(-1, Math.min(1, shift)) : 0));
    }
  }
  return null;
}

function joinChunks(chunks: Float32Array[][]): Float32Array[] {
  if (!chunks.length) return [new Float32Array(0)];
  const channels = chunks[0].length;
  return Array.from({ length: channels }, (_, c) => {
    const out = new Float32Array(chunks.reduce((n, chunk) => n + chunk[c].length, 0));
    let at = 0;
    for (const chunk of chunks) {
      out.set(chunk[c], at);
      at += chunk[c].length;
    }
    return out;
  });
}

/** 32-bit float WAV: no compression, no clipping of hot takes. */
export function encodeWav(channels: Float32Array[], sampleRate: number): Blob {
  const frames = channels[0].length, count = channels.length;
  const bytes = frames * count * 4;
  const buffer = new ArrayBuffer(44 + bytes);
  const view = new DataView(buffer);
  const text = (at: number, s: string) => [...s].forEach((ch, i) => view.setUint8(at + i, ch.charCodeAt(0)));
  text(0, "RIFF");
  view.setUint32(4, 36 + bytes, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 3, true);                   // IEEE float
  view.setUint16(22, count, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * count * 4, true);
  view.setUint16(32, count * 4, true);
  view.setUint16(34, 32, true);
  text(36, "data");
  view.setUint32(40, bytes, true);
  const out = new Float32Array(buffer, 44);
  for (let i = 0; i < frames; i++) for (let c = 0; c < count; c++) out[i * count + c] = channels[c][i];
  return new Blob([buffer], { type: "audio/wav" });
}
