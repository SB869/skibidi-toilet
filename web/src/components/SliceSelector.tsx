// The whole reference as a strip: drag across it to pick the part you'll sing, or click to
// move the start (keeping the length). Marks the beats, the count-in and where the voice enters.

import { useEffect, useRef, useState } from "react";
import type { Series } from "../api";
import { useSize } from "../hooks";
import { clamp, fmtTime } from "../util";

interface Props {
  peaks: { min: number[]; max: number[] };
  duration: number;
  selection: [number, number] | null;
  onSelect: (selection: [number, number]) => void;
  entry: number | null;
  countInFrom: number | null;
  beats: Series;
  clicks?: number[];
}

const DEFAULT_LENGTH_S = 30;

export function SliceSelector({ peaks, duration, selection, onSelect, entry, countInFrom, beats, clicks = [] }: Props) {
  const [box, size] = useSize<HTMLDivElement>();
  const canvas = useRef<HTMLCanvasElement>(null);
  const [dragging, setDragging] = useState<[number, number] | null>(null);
  const drag = useRef<{ x: number; t: number } | null>(null);
  const shown = dragging ?? selection;

  useEffect(() => {
    const el = canvas.current;
    if (!el || !size.width) return;
    const dpr = window.devicePixelRatio || 1;
    el.width = Math.round(size.width * dpr);
    el.height = Math.round(size.height * dpr);
    el.style.width = `${size.width}px`;
    el.style.height = `${size.height}px`;
    const ctx = el.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const { width: w, height: h } = size;
    const x = (t: number) => (t / duration) * w;
    const wave = h - 16, mid = wave / 2;

    ctx.fillStyle = "#12141a";
    ctx.fillRect(0, 0, w, h);
    if (countInFrom !== null && entry !== null) {
      ctx.fillStyle = "rgba(246, 173, 85, 0.18)";
      ctx.fillRect(x(countInFrom), 0, x(entry) - x(countInFrom), wave);
    }
    if (shown) {
      ctx.fillStyle = "rgba(79, 209, 197, 0.16)";
      ctx.fillRect(x(shown[0]), 0, x(shown[1]) - x(shown[0]), wave);
    }
    const n = peaks.min.length;
    ctx.fillStyle = "#4a5266";
    for (let px = 0; px < w; px++) {
      const a = Math.floor((px / w) * n), b = Math.max(a + 1, Math.floor(((px + 1) / w) * n));
      let lo = 0, hi = 0;
      for (let i = a; i < b && i < n; i++) {
        lo = Math.min(lo, peaks.min[i]);
        hi = Math.max(hi, peaks.max[i]);
      }
      ctx.fillRect(px, mid - hi * mid * 0.95, 1, Math.max(1, (hi - lo) * mid * 0.95));
    }
    if (shown) {
      ctx.fillStyle = "#4fd1c5";
      ctx.fillRect(Math.round(x(shown[0])), 0, 2, wave);
      ctx.fillRect(Math.round(x(shown[1])) - 1, 0, 1, wave);
    }
    ctx.fillStyle = "rgba(255, 255, 255, 0.35)";
    for (const b of beats) if (b !== null) ctx.fillRect(Math.round(x(b)), wave - 5, 1, 5);
    if (entry !== null) {
      ctx.fillStyle = "#f6ad55";
      ctx.fillRect(Math.round(x(entry)), 0, 2, wave);
    }
    ctx.fillStyle = "#f6ad55";
    for (const c of clicks) ctx.fillRect(Math.round(x(c)) - 1, 0, 3, 7);
    // time ruler
    ctx.fillStyle = "#161920";
    ctx.fillRect(0, wave, w, 16);
    ctx.font = "10px system-ui, sans-serif";
    ctx.textBaseline = "middle";
    const step = [5, 10, 15, 30, 60, 120].find((s) => (s / duration) * w >= 60) ?? 300;
    for (let t = 0; t <= duration; t += step) {
      ctx.fillStyle = "#8b93a7";
      ctx.fillRect(Math.round(x(t)), wave, 1, 4);
      ctx.fillText(fmtTime(t, 0), x(t) + 3, wave + 9);
    }
  }, [size, peaks, duration, shown, entry, countInFrom, beats, clicks]);

  const timeAt = (offsetX: number) => clamp((offsetX / size.width) * duration, 0, duration);

  return (
    <div className="slice-selector" ref={box}>
      <canvas
        ref={canvas}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId);
          drag.current = { x: e.nativeEvent.offsetX, t: timeAt(e.nativeEvent.offsetX) };
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (!d || Math.abs(e.nativeEvent.offsetX - d.x) < 4) return;
          const t = timeAt(e.nativeEvent.offsetX);
          setDragging([Math.min(d.t, t), Math.max(d.t, t)]);
        }}
        onPointerUp={() => {
          const d = drag.current;
          drag.current = null;
          if (!d) return;
          if (dragging) onSelect(dragging);
          else {
            const length = selection ? selection[1] - selection[0] : DEFAULT_LENGTH_S;
            onSelect([d.t, Math.min(duration, d.t + length)]);
          }
          setDragging(null);
        }}
      />
    </div>
  );
}
