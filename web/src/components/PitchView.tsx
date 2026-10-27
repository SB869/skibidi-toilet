// Piano-roll pitch display: note lanes down the side, time across, pitch curves on top.
// Wheel zooms around the cursor, shift+wheel (or a sideways swipe) pans, drag selects,
// click seeks, double-click zooms back out.

import { useEffect, useMemo, useRef, type PointerEvent } from "react";
import type { Clock } from "../audio";
import type { Series } from "../api";
import { useFrame, useSize } from "../hooks";
import { clamp, fmtTime, isBlackKey, noteName } from "../util";

export interface Curve {
  values: Series;  // one MIDI value per frame; null = silence
  hop: number;     // seconds per frame
  color: string;
  width?: number;
  alpha?: number;
  step?: boolean;  // draw as held notes
  maxJump?: number; // break the line where it leaps further than this (semitones)
}

interface Props {
  duration: number;
  curves: Curve[];
  view: [number, number];
  onView: (view: [number, number]) => void;
  selection: [number, number] | null;
  onSelect: (selection: [number, number] | null) => void;
  onSeek: (t: number) => void;
  player: Clock;
  inScale?: Set<number> | null;  // pitch classes to highlight
  range?: [number, number];      // MIDI range to show; default fits the curves
  timeOffset?: number;           // add to the ruler's labels (e.g. to show song time)
}

export const GUTTER = 46;
const RULER = 20;
const MIN_SPAN = 0.25;

export function PitchView(props: Props) {
  const { duration, curves, view, selection, inScale } = props;
  const [box, size] = useSize<HTMLDivElement>();
  const base = useRef<HTMLCanvasElement>(null);
  const overlay = useRef<HTMLCanvasElement>(null);
  const latest = useRef(props);
  latest.current = props;

  const fitted = useMemo(() => pitchRange(curves), [curves]);
  const range = props.range ?? fitted;

  // Main layer: lanes, grid, curves, selection.
  useEffect(() => {
    const canvas = base.current;
    if (!canvas || !size.width) return;
    const ctx = setup(canvas, size.width, size.height);
    drawLanes(ctx, size.width, size.height, range, inScale ?? null);
    drawRuler(ctx, size.width, size.height, view, props.timeOffset ?? 0);
    const plot = { x: GUTTER, y: RULER, w: size.width - GUTTER, h: size.height - RULER };
    ctx.save();
    ctx.beginPath();
    ctx.rect(plot.x, plot.y, plot.w, plot.h);
    ctx.clip();
    for (const curve of curves) drawCurve(ctx, curve, view, range, plot);
    if (selection) {
      const x0 = xOf(selection[0], view, size.width), x1 = xOf(selection[1], view, size.width);
      ctx.fillStyle = "rgba(79, 209, 197, 0.10)";
      ctx.fillRect(x0, plot.y, x1 - x0, plot.h);
      ctx.fillStyle = "rgba(79, 209, 197, 0.6)";
      ctx.fillRect(x0, plot.y, 1, plot.h);
      ctx.fillRect(x1 - 1, plot.y, 1, plot.h);
    }
    ctx.restore();
  }, [size, curves, view, selection, inScale, range, props.timeOffset]);

  // Top layer: the playhead, redrawn every frame; follows playback when it runs off screen.
  useFrame(() => {
    const canvas = overlay.current;
    const { player, view: v, onView, duration: d } = latest.current;
    if (!canvas || !size.width) return;
    const ctx = setup(canvas, size.width, size.height, false);
    ctx.clearRect(0, 0, size.width, size.height);
    const t = player.position();
    if (player.playing && (t > v[1] || t < v[0]) && v[1] - v[0] < d) {
      const span = v[1] - v[0];
      const start = clamp(t - span * 0.05, 0, d - span);
      onView([start, start + span]);
      return;
    }
    if (t < v[0] || t > v[1]) return;
    const x = xOf(t, v, size.width);
    ctx.fillStyle = "#f5f7fa";
    ctx.fillRect(Math.round(x), 0, 1.5, size.height);
    ctx.beginPath();
    ctx.moveTo(x - 5, 0);
    ctx.lineTo(x + 5, 0);
    ctx.lineTo(x, 7);
    ctx.fill();
  });

  // Wheel: needs a non-passive listener to stop the page scrolling.
  useEffect(() => {
    const canvas = overlay.current;
    if (!canvas) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const { view: v, onView, duration: d } = latest.current;
      const width = canvas.clientWidth;
      const span = v[1] - v[0];
      const sideways = e.shiftKey ? e.deltaY : e.deltaX;
      if (Math.abs(sideways) > Math.abs(e.deltaY) || e.shiftKey) {
        const shift = (sideways / (width - GUTTER)) * span;
        const start = clamp(v[0] + shift, 0, Math.max(0, d - span));
        onView([start, start + span]);
        return;
      }
      const t = timeAt(e.offsetX, v, width);
      const newSpan = clamp(span * Math.exp(e.deltaY * 0.0015), Math.min(MIN_SPAN, d), d);
      const start = clamp(t - ((t - v[0]) / span) * newSpan, 0, d - newSpan);
      onView([start, start + newSpan]);
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, []);

  const drag = useRef<{ x: number; t: number; moved: boolean } | null>(null);

  const onPointerDown = (e: PointerEvent<HTMLCanvasElement>) => {
    if (e.button !== 0) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { x: e.nativeEvent.offsetX, t: timeAt(e.nativeEvent.offsetX, view, size.width), moved: false };
  };
  const onPointerMove = (e: PointerEvent<HTMLCanvasElement>) => {
    const d = drag.current;
    if (!d) return;
    if (Math.abs(e.nativeEvent.offsetX - d.x) > 4) d.moved = true;
    if (d.moved) {
      const t = clamp(timeAt(e.nativeEvent.offsetX, view, size.width), 0, duration);
      props.onSelect([Math.min(d.t, t), Math.max(d.t, t)]);
    }
  };
  const onPointerUp = (e: PointerEvent<HTMLCanvasElement>) => {
    const d = drag.current;
    drag.current = null;
    if (!d || d.moved) return;
    if (!e.shiftKey) props.onSelect(null);
    props.onSeek(clamp(d.t, 0, duration));
  };

  return (
    <div className="pitch-view" ref={box}>
      <canvas ref={base} />
      <canvas
        ref={overlay}
        className="overlay"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onDoubleClick={() => props.onView([0, duration])}
      />
    </div>
  );
}

// ---------------------------------------------------------------- drawing

function setup(canvas: HTMLCanvasElement, width: number, height: number, clear = true) {
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
  }
  const ctx = canvas.getContext("2d")!;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (clear) ctx.clearRect(0, 0, width, height);
  return ctx;
}

export function xOf(t: number, view: [number, number], width: number) {
  return GUTTER + ((t - view[0]) / (view[1] - view[0])) * (width - GUTTER);
}

function timeAt(x: number, view: [number, number], width: number) {
  return view[0] + ((x - GUTTER) / (width - GUTTER)) * (view[1] - view[0]);
}

function yOf(midi: number, range: [number, number], height: number) {
  return RULER + ((range[1] - midi) / (range[1] - range[0])) * (height - RULER);
}

function pitchRange(curves: Curve[]): [number, number] {
  const values: number[] = [];
  for (const c of curves) for (const v of c.values) if (v !== null) values.push(v);
  if (!values.length) return [48, 72];
  values.sort((a, b) => a - b);
  const lo = values[Math.floor(values.length * 0.005)];
  const hi = values[Math.ceil(values.length * 0.995) - 1];
  const mid = (lo + hi) / 2;
  const half = Math.max(7, (hi - lo) / 2 + 2);
  return [Math.floor(mid - half), Math.ceil(mid + half)];
}

function drawLanes(ctx: CanvasRenderingContext2D, w: number, h: number, range: [number, number],
                   inScale: Set<number> | null) {
  ctx.fillStyle = "#12141a";
  ctx.fillRect(0, 0, w, h);
  const row = (h - RULER) / (range[1] - range[0]);
  for (let m = range[0]; m <= range[1]; m++) {
    const top = yOf(m + 0.5, range, h);
    const pc = ((m % 12) + 12) % 12;
    if (isBlackKey(m)) {
      ctx.fillStyle = "rgba(0, 0, 0, 0.28)";
      ctx.fillRect(GUTTER, top, w - GUTTER, row);
    }
    if (inScale?.has(pc)) {
      ctx.fillStyle = "rgba(79, 209, 197, 0.06)";
      ctx.fillRect(GUTTER, top, w - GUTTER, row);
    }
    if (pc === 0) {
      ctx.fillStyle = "rgba(255, 255, 255, 0.07)";
      ctx.fillRect(GUTTER, top + row - 1, w - GUTTER, 1);
    }
    // keyboard
    ctx.fillStyle = isBlackKey(m) ? "#1b1e26" : "#2a2f3a";
    ctx.fillRect(0, top + 0.5, GUTTER - 2, Math.max(1, row - 1));
    if (inScale?.has(pc)) {
      ctx.fillStyle = "#4fd1c5";
      ctx.fillRect(GUTTER - 4, top + 1, 2, Math.max(1, row - 2));
    }
    if (row >= 11 || pc === 0) {
      ctx.fillStyle = pc === 0 ? "#e6e8ee" : "#8b93a7";
      ctx.font = `${Math.min(11, Math.max(9, row - 2))}px system-ui, sans-serif`;
      ctx.textBaseline = "middle";
      ctx.fillText(noteName(m), 5, top + row / 2);
    }
  }
}

function drawRuler(ctx: CanvasRenderingContext2D, w: number, h: number, view: [number, number], offset: number) {
  const span = view[1] - view[0];
  const steps = [0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120];
  const step = steps.find((s) => (s / span) * (w - GUTTER) >= 70) ?? 300;
  ctx.fillStyle = "#161920";
  ctx.fillRect(GUTTER, 0, w - GUTTER, RULER);
  ctx.font = "10px system-ui, sans-serif";
  ctx.textBaseline = "middle";
  for (let label = Math.ceil((view[0] + offset) / step) * step; label <= view[1] + offset; label += step) {
    const t = label - offset;
    const x = Math.round(xOf(t, view, w));
    ctx.fillStyle = "rgba(255, 255, 255, 0.05)";
    ctx.fillRect(x, RULER, 1, h - RULER);
    ctx.fillStyle = "#8b93a7";
    ctx.fillRect(x, RULER - 5, 1, 5);
    ctx.fillText(fmtTime(label, step < 1 ? (step < 0.1 ? 2 : 1) : 0), x + 3, RULER / 2);
  }
}

function drawCurve(ctx: CanvasRenderingContext2D, curve: Curve, view: [number, number],
                   range: [number, number], plot: { x: number; y: number; w: number; h: number }) {
  const { values, hop } = curve;
  const width = plot.x + plot.w;
  const height = plot.y + plot.h;
  const i0 = Math.max(0, Math.floor(view[0] / hop) - 1);
  const i1 = Math.min(values.length - 1, Math.ceil(view[1] / hop) + 1);
  const perPixel = (i1 - i0) / plot.w;
  const stride = curve.step ? 1 : Math.max(1, Math.floor(perPixel / 2));
  ctx.strokeStyle = curve.color;
  ctx.lineWidth = curve.width ?? 1.5;
  ctx.globalAlpha = curve.alpha ?? 1;
  ctx.lineJoin = "round";
  ctx.lineCap = curve.step ? "butt" : "round";
  ctx.beginPath();
  let pen = false;
  let last = NaN;
  for (let i = i0; i <= i1; i += stride) {
    const v = values[i];
    if (v === null) {
      pen = false;
      continue;
    }
    if (curve.maxJump !== undefined && Math.abs(v - last) > curve.maxJump) pen = false;
    last = v;
    const y = yOf(v, range, height);
    if (curve.step) {
      const x0 = xOf((i - 0.5) * hop, view, width), x1 = xOf((i + 0.5) * hop, view, width);
      if (pen && values[i - 1] === v) ctx.lineTo(x1, y);
      else {
        ctx.moveTo(x0, y);
        ctx.lineTo(x1, y);
      }
    } else {
      const x = xOf(i * hop, view, width);
      if (pen) ctx.lineTo(x, y);
      else ctx.moveTo(x, y);
    }
    pen = true;
  }
  ctx.stroke();
  ctx.globalAlpha = 1;
}
