// Whole-file waveform strip under the pitch view: shows where the zoomed view sits.
// Click to jump there (and play from there); drag to slide the view.

import { useEffect, useRef } from "react";
import type { Clock } from "../audio";
import { useFrame, useSize } from "../hooks";
import { clamp } from "../util";

interface Props {
  peaks: { min: number[]; max: number[] };
  duration: number;
  view: [number, number];
  onView: (view: [number, number]) => void;
  selection: [number, number] | null;
  onSeek: (t: number) => void;
  player: Clock;
}

export function Overview({ peaks, duration, view, onView, selection, onSeek, player }: Props) {
  const [box, size] = useSize<HTMLDivElement>();
  const base = useRef<HTMLCanvasElement>(null);
  const overlay = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = base.current;
    if (!canvas || !size.width) return;
    const ctx = prepare(canvas, size.width, size.height);
    const { width: w, height: h } = size;
    ctx.fillStyle = "#12141a";
    ctx.fillRect(0, 0, w, h);
    const n = peaks.min.length;
    const mid = h / 2;
    ctx.fillStyle = "#3b4252";
    for (let x = 0; x < w; x++) {
      const a = Math.floor((x / w) * n), b = Math.max(a + 1, Math.floor(((x + 1) / w) * n));
      let lo = 0, hi = 0;
      for (let i = a; i < b && i < n; i++) {
        lo = Math.min(lo, peaks.min[i]);
        hi = Math.max(hi, peaks.max[i]);
      }
      ctx.fillRect(x, mid - hi * mid * 0.95, 1, Math.max(1, (hi - lo) * mid * 0.95));
    }
    if (selection) {
      ctx.fillStyle = "rgba(79, 209, 197, 0.18)";
      ctx.fillRect((selection[0] / duration) * w, 0, ((selection[1] - selection[0]) / duration) * w, h);
    }
    if (view[1] - view[0] < duration - 1e-6) {
      const x0 = (view[0] / duration) * w, x1 = (view[1] / duration) * w;
      ctx.fillStyle = "rgba(0, 0, 0, 0.45)";
      ctx.fillRect(0, 0, x0, h);
      ctx.fillRect(x1, 0, w - x1, h);
      ctx.strokeStyle = "rgba(255, 255, 255, 0.35)";
      ctx.strokeRect(x0 + 0.5, 0.5, x1 - x0 - 1, h - 1);
    }
  }, [size, peaks, duration, view, selection]);

  useFrame(() => {
    const canvas = overlay.current;
    if (!canvas || !size.width || !duration) return;
    const ctx = prepare(canvas, size.width, size.height);
    ctx.clearRect(0, 0, size.width, size.height);
    ctx.fillStyle = "#f5f7fa";
    ctx.fillRect(Math.round((player.position() / duration) * size.width), 0, 1.5, size.height);
  });

  const drag = useRef<{ x: number; moved: boolean } | null>(null);
  const centre = (x: number) => {
    const span = view[1] - view[0];
    const t = (x / size.width) * duration;
    const start = clamp(t - span / 2, 0, duration - span);
    onView([start, start + span]);
  };

  return (
    <div className="overview" ref={box}>
      <canvas ref={base} />
      <canvas
        ref={overlay}
        className="overlay"
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId);
          drag.current = { x: e.nativeEvent.offsetX, moved: false };
        }}
        onPointerMove={(e) => {
          if (!drag.current) return;
          if (Math.abs(e.nativeEvent.offsetX - drag.current.x) > 3) drag.current.moved = true;
          if (drag.current.moved) centre(e.nativeEvent.offsetX);
        }}
        onPointerUp={(e) => {
          if (drag.current && !drag.current.moved) {
            onSeek(clamp((e.nativeEvent.offsetX / size.width) * duration, 0, duration));
            centre(e.nativeEvent.offsetX);
          }
          drag.current = null;
        }}
      />
    </div>
  );
}

function prepare(canvas: HTMLCanvasElement, width: number, height: number) {
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
  }
  const ctx = canvas.getContext("2d")!;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return ctx;
}
