// Playback: two sample-locked slots ("a" = original, "b" = tuned) that always run together.
// Switching between them just crossfades their gains, so A/B comparison is seamless.

import { clamp } from "./util";

export type Slot = "a" | "b";

/** Anything with a playhead: the player, or a live recording session. */
export interface Clock {
  position(): number;
  readonly playing: boolean;
}
const SLOTS: Slot[] = ["a", "b"];

export class Player implements Clock {
  readonly ctx = new AudioContext();
  private master = this.ctx.createGain();
  private gains: Record<Slot, GainNode>;
  private buffers: Partial<Record<Slot, AudioBuffer>> = {};
  private sources: Partial<Record<Slot, AudioBufferSourceNode>> = {};
  private generation = 0;
  private startedAt = 0;
  private offset = 0;
  private listeners = new Set<() => void>();
  private trims: Record<Slot, number> = { a: 1, b: 1 };

  playing = false;
  active: Slot = "a";
  loop: [number, number] | null = null;

  constructor() {
    this.master.connect(this.ctx.destination);
    this.gains = { a: this.ctx.createGain(), b: this.ctx.createGain() };
    for (const slot of SLOTS) this.gains[slot].connect(this.master);
    this.applyGains(true);
  }

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private emit() {
    for (const fn of this.listeners) fn();
  }

  async fetch(url: string, signal?: AbortSignal): Promise<AudioBuffer> {
    const response = await fetch(url, { signal });
    if (!response.ok) throw new Error(`couldn't load audio (${response.status})`);
    return this.ctx.decodeAudioData(await response.arrayBuffer());
  }

  get duration(): number {
    return Math.max(this.buffers.a?.duration ?? 0, this.buffers.b?.duration ?? 0);
  }

  has(slot: Slot): boolean {
    return this.buffers[slot] !== undefined;
  }

  /** Put audio in a slot. If playing, the new audio joins in at the current position. */
  set(slot: Slot, buffer: AudioBuffer | undefined) {
    this.buffers[slot] = buffer;
    this.stopSource(slot);
    if (this.playing && buffer) {
      const now = this.ctx.currentTime;
      this.startSource(slot, this.positionAt(now), now, this.generation);
    }
    this.emit();
  }

  clear() {
    this.pause();
    this.buffers = {};
    this.offset = 0;
    this.loop = null;
    this.emit();
  }

  position(): number {
    return this.playing ? this.positionAt(this.ctx.currentTime) : this.offset;
  }

  private positionAt(time: number): number {
    let t = this.offset + (time - this.startedAt);
    if (this.loop) {
      const [a, b] = this.loop;
      if (t >= b) t = a + ((t - a) % (b - a));
    }
    return clamp(t, 0, this.duration);
  }

  play(from?: number) {
    if (!this.duration) return;
    if (this.ctx.state === "suspended") void this.ctx.resume();
    this.stopAll();
    let offset = from ?? this.offset;
    if (offset >= this.duration - 0.01) offset = 0;
    if (this.loop && offset >= this.loop[1]) offset = this.loop[0];
    const when = this.ctx.currentTime + 0.01;
    this.startedAt = when;
    this.offset = offset;
    this.playing = true;
    const generation = ++this.generation;
    for (const slot of SLOTS) if (this.buffers[slot]) this.startSource(slot, offset, when, generation);
    this.emit();
  }

  pause() {
    if (!this.playing) return;
    this.offset = this.position();
    this.stopAll();
    this.playing = false;
    this.generation++;
    this.emit();
  }

  toggle() {
    if (this.playing) this.pause();
    else this.play();
  }

  seek(t: number) {
    const target = clamp(t, 0, this.duration);
    if (this.playing) this.play(target);
    else {
      this.offset = target;
      this.emit();
    }
  }

  setLoop(range: [number, number] | null) {
    this.loop = range && range[1] - range[0] > 0.05 ? range : null;
    if (this.playing) this.play(this.position());
    else this.emit();
  }

  setActive(slot: Slot) {
    this.active = slot;
    this.applyGains();
    this.emit();
  }

  /** Level offset for a slot, e.g. to compare versions at equal loudness. */
  setTrim(slot: Slot, db: number) {
    this.trims[slot] = Math.pow(10, db / 20);
    this.applyGains();
  }

  private applyGains(immediate = false) {
    for (const slot of SLOTS) {
      const target = slot === this.active ? this.trims[slot] : 0;
      if (immediate) this.gains[slot].gain.value = target;
      else this.gains[slot].gain.setTargetAtTime(target, this.ctx.currentTime, 0.004);
    }
  }

  private startSource(slot: Slot, offset: number, when: number, generation: number) {
    const source = this.ctx.createBufferSource();
    source.buffer = this.buffers[slot]!;
    if (this.loop) {
      source.loop = true;
      source.loopStart = this.loop[0];
      source.loopEnd = this.loop[1];
    }
    source.connect(this.gains[slot]);
    source.onended = () => {
      if (generation !== this.generation || this.sources[slot] !== source) return;
      this.stopAll();
      this.playing = false;
      this.offset = this.duration;
      this.generation++;
      this.emit();
    };
    source.start(when, Math.min(offset, source.buffer.duration));
    this.sources[slot] = source;
  }

  private stopSource(slot: Slot) {
    const source = this.sources[slot];
    if (!source) return;
    source.onended = null;
    try {
      source.stop();
    } catch {
      // never started; nothing to stop
    }
    source.disconnect();
    delete this.sources[slot];
  }

  private stopAll() {
    for (const slot of SLOTS) this.stopSource(slot);
  }
}
