// Typed client for the Python server (src/sound/server.py).

export type Kind = "take" | "reference" | "render" | "beatbox" | "track";
export type Series = (number | null)[];

export interface AudioFile {
  path: string;
  name: string;
  kind: Kind;
  duration: number;
  sample_rate: number;
  channels: number;
  size: number;
  modified: number;
}

export interface Library {
  root: string;
  files: AudioFile[];
  notes: string[];
  scales: Record<string, number[]>;
}

export interface Key {
  tonic: number;
  note: string;
  scale: string;
  label: string;
  confidence: number | null;
  source?: "none" | "fixed" | "auto" | "auto-unsure";
}

export interface Summary {
  voiced: number;
  low: string | null;
  high: string | null;
  tuning_cents: number;
  tuning_consistency: number;
  key: Key;
  notes_used: string[];
  in_tune: number;
  median_dev: number | null;
}

export interface Note {
  start: number;
  end: number;
  midi: number;
  name: string;
  cents: number;
}

export interface Analysis {
  file: AudioFile;
  start: number;
  end: number;
  hop: number;
  pitch: Series;
  peaks: { min: number[]; max: number[] };
  summary: Summary;
  notes: Note[];
}

export type Style = "natural" | "autotune";

export interface TuneParams {
  style: Style;       // natural: move whole notes, keep their wobble; autotune: pull every moment to a note
  drift: number;      // natural: 0..1 of the slow wander inside notes to straighten
  humanize: number;   // natural: 0..1 of each note's own lean to keep
  key: string | null; // null = every semitone, "auto", or a note name
  scale: string | null;
  speed: number;
  strength: number;
  hysteresis: number;
  a4: number;
}

export interface FxParams {
  enabled: boolean;
  reference: string | null;  // library path of a vocal to match, or null for a generic heavy sound
  refStart: string;          // "3:26" etc.; blank = from the start
  refEnd: string;
  compression: number;
  grit: number;
  tone: number;
  deess: number;
  width: number;
  doubles: number;
  space: number;
  loudness: number | null;   // LUFS; null = match the reference (or -12)
}

export interface SoundProfile {
  lufs: number;
  dynamics: number;
  brightness: number;
  width: number | null;
}

export interface Render {
  id: string;
  source: string;
  style: Style;
  lufs: number;
  original_lufs: number;
  sound: { output: SoundProfile; reference: SoundProfile | null } | null;
  hop: number;
  key: Key;
  estimate: Key;
  target: Series;
  corrected: Series;
  correction_median: number;
  correction_max: number;
}

export interface CompareRow {
  take: string;
  name: string;
  hop: number;
  first_note?: number;
  tempo?: number;
  octave?: number;
  off_by?: number;
  within_25?: number;
  within_50?: number;
  bias?: number;
  notes_matched?: number;
  notes_compared?: number;
  pitch?: Series;
  reference?: Series;
}

export interface Guide {
  path: string;
  start: number;
  end: number;
  hop: number;            // seconds per pitch frame
  pitch: Series;          // MIDI
  env_hop: number;        // seconds per level/edge frame
  level: Series;          // dB relative to the loud singing
  edge: Series;           // dB, presence over body, while singing
  typical_edge: number | null;
  summary: Summary;
}

export interface Timing {
  tempo: number | null;   // BPM near the section
  beats: Series;          // beat times (s) around it
  entry: number | null;   // when the voice comes in at or after the requested start
}

export interface Comparison {
  reference: { path: string; start: number; end: number; summary: Summary };
  rows: CompareRow[];
}

/** The session open in Ardour, in the reference's time (seconds into the reference file). */
export interface ArdourLink {
  session: string;
  rate: number;
  reference: { track: string; path: string; offset: number };  // offset: where the file's 0:00 sits on the timeline
  span: [number, number] | null;   // the section to work on
  span_from: string | null;        // what it came from: "range selection", "loop range", a track name, …
  playhead: number;
  takes: { id: string; track: string; name: string; start: number; end: number; muted: boolean }[];
}

export interface TakeCurves {
  hop: number;
  placed: { start: number; pitch: Series };                       // as it sits in Ardour
  aligned: { start: number; hop: number; pitch: Series } | null;  // lined up with the reference note by note
  octave: number;                                                 // semitones from the take up to the reference
  tempo: number | null;
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // not JSON; keep the status text
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

const post = (body: unknown, signal?: AbortSignal): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
  signal,
});

export type Lane = "kick" | "snare" | "hat";
export type BeatStyle = "synth" | "mouth";   // play what the beatbox stands for, or copy the mouth
export type BeatForm = "loop" | "song";       // the groove looped, or laid out as a track
export type BeatBass = "synth" | "bright" | "fm" | "pluck" | "finger";   // the riff's sound in the synth style
export type BeatHats = "pulse" | "straight" | "offbeat";                 // the hats under the drop

/** A beatbox recording read as a groove, and the Strudel beat made from it (src/sound/beatbox.py). */
export interface Beat {
  file: AudioFile;
  bpm: number;
  key: string;
  start: number;
  bars: Record<Lane, string>[];   // per bar: 16 characters, "x" a hit, "." none
  loop: number[];                 // the bars the beat loops
  snare_played: boolean;          // false: no backbeat (the code has a clap to uncomment)
  skipped: [number, number][];    // stretches without a steady beat, left out
  hits: Record<Lane, number>;
  riff: (string | null)[][] | null;   // per bar: the pitched part's note per 16th ("A#2"), when it carries the beatbox
  pulse: number[];                // the 16ths where its bwoms land, bar after bar (0-based)
  style: BeatStyle;
  form: BeatForm;
  bass: BeatBass;
  basses: Record<BeatBass, string>;   // each bass, described
  hats: BeatHats;
  hat_styles: Record<BeatHats, string>;
  code: string;                   // Strudel code
}

function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined) q.set(k, String(v));
  return q.toString();
}

export const api = {
  files: () => call<Library>("/api/files"),

  analysis: (path: string, opts: { start?: number; end?: number; loose?: boolean } = {}) =>
    call<Analysis>(`/api/analysis?${query({ path, ...opts })}`),

  tune: (path: string, params: TuneParams, fx: object | null, signal?: AbortSignal) =>
    call<Render>("/api/tune", post({ path, ...params, fx }, signal)),

  saveRender: (id: string, name?: string) => call<AudioFile>(`/api/renders/${id}/save`, post({ name })),

  compare: (reference: string, takes: string[], refStart?: number, refEnd?: number) =>
    call<Comparison>("/api/compare", post({ reference, takes, ref_start: refStart, ref_end: refEnd })),

  overview: (path: string) => call<{ duration: number; peaks: { min: number[]; max: number[] } }>(`/api/overview?${query({ path })}`),
  timing: (path: string, start: number, end?: number) => call<Timing>(`/api/timing?${query({ path, start, end })}`),
  guide: (path: string, start?: number, end?: number) => call<Guide>(`/api/guide?${query({ path, start, end })}`),

  cut: (path: string, start: number, end: number) => call<AudioFile>("/api/cut", post({ path, start, end })),

  upload: (file: Blob, name: string, kind: Kind, recorded = false) => {
    const form = new FormData();
    form.append("file", file, name);
    form.append("kind", kind);
    form.append("recorded", String(recorded));
    return call<AudioFile>("/api/files", { method: "POST", body: form });
  },

  ardour: () => call<ArdourLink>("/api/ardour"),
  ardourTake: (region: string) => call<TakeCurves>(`/api/ardour/take?${query({ region })}`),
  ardourAddTake: (path: string, at: number) =>
    call<{ track: string; at: number }>("/api/ardour/takes", post({ path, at })),

  beat: (path: string, bars = 4, style: BeatStyle = "synth", form: BeatForm = "loop", bass: BeatBass = "synth", hats: BeatHats = "pulse") =>
    call<Beat>(`/api/beat?${query({ path, bars, style, form, bass, hats })}`),
  beatMidiUrl: (path: string, bars = 4, style: BeatStyle = "synth") => `/api/beat/midi?${query({ path, bars, style })}`,

  audioUrl: (path: string, start?: number, end?: number) => `/api/audio?${query({ path, start, end })}`,
  renderUrl: (id: string) => `/api/renders/${id}/audio`,
};
