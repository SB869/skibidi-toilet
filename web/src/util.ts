export const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

export function noteName(midi: number): string {
  const n = Math.round(midi);
  return `${NOTE_NAMES[((n % 12) + 12) % 12]}${Math.floor(n / 12) - 1}`;
}

export function isBlackKey(midi: number): boolean {
  return [1, 3, 6, 8, 10].includes(((midi % 12) + 12) % 12);
}

/** 206.47 -> "3:26.5" */
export function fmtTime(seconds: number, decimals = 1): string {
  const sign = seconds < 0 ? "-" : "";
  const s = Math.abs(seconds);
  const m = Math.floor(s / 60);
  const rest = (s - m * 60).toFixed(decimals);
  return `${sign}${m}:${rest.padStart(decimals ? 3 + decimals : 2, "0")}`;
}

/** "3:26", "3:26.5", "206" -> seconds; "" -> undefined; garbage -> null */
export function parseTime(text: string): number | undefined | null {
  const t = text.trim();
  if (!t) return undefined;
  const parts = t.split(":").map(Number);
  if (parts.length > 3 || parts.some((p) => !Number.isFinite(p) || p < 0)) return null;
  if (parts.slice(1).some((p) => p >= 60)) return null;
  return parts.reduce((acc, p) => acc * 60 + p, 0);
}

export const pct = (x: number | undefined) => (x === undefined ? "–" : `${Math.round(x * 100)}%`);
export const cents = (x: number | undefined | null) => (x === undefined || x === null ? "–" : `${Math.round(x)}¢`);

export function bias(c: number | undefined): string {
  if (c === undefined) return "–";
  if (Math.abs(c) < 3) return "centred";
  return `${Math.round(Math.abs(c))}¢ ${c > 0 ? "sharp" : "flat"}`;
}

export function clamp(x: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, x));
}
