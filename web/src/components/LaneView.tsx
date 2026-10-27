// A value-over-time lane (level, edge) lined up with the pitch view above it.

import { useEffect, useRef } from "react";
import type { Series } from "../api";
import type { Clock } from "../audio";
import { useFrame, useSize } from "../hooks";
import { GUTTER, xOf } from "./PitchView";

export interface LaneSeries {
  values: Series;
  hop: number;
  color: string;
  width?: number;
  alpha?: number;
}

interface Props {
  label: string;
  range: [number, number];
  ticks: number[];
  series: LaneSeries[];
  view: [number, number];
  clock: Clock;
  target?: number;   // a dashed reference line
}

export function LaneView({ label, range, ticks, series, view, clock, target }: Props) {
  const [box, size] = useSize<HTMLDivElement>();
  const base = useRef<HTMLCanvasElement>(null);
  const overlay = useRef<HTMLCanvasElement>(null);
  const yOf = (v: number) => 4 + ((range[1] - v) / (range[1] - range[0])) * (size.height - 8);

  useEffect(() => {
    const canvas = base.current;
    if (!canvas || !size.width) return;
    const ctx = prepare(canvas, size.width, size.height);
    ctx.fillStyle = "#12141a";
    ctx.fillRect(0, 0, size.width, size.height);
    ctx.fillStyle = "#1b1e26";
    ctx.fillRect(0, 0, GUTTER - 2, size.height);
    ctx.font = "10px system-ui, sans-serif";
    ctx.textBaseline = "middle";
    ctx.fillStyle = "#e6e8ee";
    ctx.fillText(label, 5, 10);
    for (const tick of ticks) {
      const y = Math.round(yOf(tick));
      ctx.fillStyle = "rgba(255, 255, 255, 0.06)";
      ctx.fillRect(GUTTER, y, size.width - GUTTER, 1);
      if (y > 20 && y < size.height - 4) {  // leave the top for the lane's name
        ctx.fillStyle = "#8b93a7";
        ctx.fillText(String(tick), 5, y);
      }
    }
    if (target !== undefined) {
      ctx.strokeStyle = "rgba(246, 173, 85, 0.5)";
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(GUTTER, yOf(target));
      ctx.lineTo(size.width, yOf(target));
      ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.save();
    ctx.beginPath();
    ctx.rect(GUTTER, 0, size.width - GUTTER, size.height);
    ctx.clip();
    for (const s of series) {
      ctx.strokeStyle = s.color;
      ctx.lineWidth = s.width ?? 1.5;
      ctx.globalAlpha = s.alpha ?? 1;
      ctx.lineJoin = "round";
      ctx.beginPath();
      let pen = false;
      const i0 = Math.max(0, Math.floor(view[0] / s.hop) - 1), i1 = Math.min(s.values.length - 1, Math.ceil(view[1] / s.hop) + 1);
      for (let i = i0; i <= i1; i++) {
        const v = s.values[i];
        if (v === null || !Number.isFinite(v)) {
          pen = false;
          continue;
        }
        const x = xOf(i * s.hop, view, size.width), y = yOf(Math.min(range[1], Math.max(range[0], v)));
        if (pen) ctx.lineTo(x, y);
        else ctx.moveTo(x, y);
        pen = true;
      }
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    ctx.restore();
  }, [size, series, view, range, ticks, label, target]);

  useFrame(() => {
    const canvas = overlay.current;
    if (!canvas || !size.width) return;
    const ctx = prepare(canvas, size.width, size.height);
    ctx.clearRect(0, 0, size.width, size.height);
    const t = clock.position();
    if (t < view[0] || t > view[1]) return;
    ctx.fillStyle = "#f5f7fa";
    ctx.fillRect(Math.round(xOf(t, view, size.width)), 0, 1.5, size.height);
  });

  return (
    <div className="lane" ref={box}>
      <canvas ref={base} />
      <canvas ref={overlay} className="overlay" />
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
