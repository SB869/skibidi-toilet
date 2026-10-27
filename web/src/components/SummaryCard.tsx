import type { ReactNode } from "react";
import type { Summary } from "../api";

export function SummaryCard({ title, summary, children }: { title: string; summary: Summary; children?: ReactNode }) {
  const { key } = summary;
  const steadiness = summary.tuning_consistency >= 0.5 ? "steady"
    : summary.tuning_consistency >= 0.25 ? "loose" : "scattered";
  const unsure = (key.confidence ?? 0) < 0.6;
  return (
    <section className="card">
      <h3>{title}</h3>
      <dl className="facts">
        <dt>Pitched</dt>
        <dd>{Math.round(summary.voiced * 100)}% {summary.low && <span className="muted">· {summary.low}–{summary.high}</span>}</dd>
        <dt>Tuning</dt>
        <dd title="Average offset from standard A440 tuning; scattered means each note is off its own way">
          {summary.tuning_cents >= 0 ? "+" : ""}{Math.round(summary.tuning_cents)}¢ <span className="muted">· {steadiness}</span>
        </dd>
        <dt>In tune</dt>
        <dd title="Share of the time within 10 cents of a note">{Math.round(summary.in_tune * 100)}%
          {summary.median_dev !== null && <span className="muted"> · typically {Math.round(summary.median_dev)}¢ off</span>}
        </dd>
        <dt>Notes used</dt>
        <dd className="chips">{summary.notes_used.map((n) => <span key={n} className="chip">{n}</span>)}</dd>
        <dt>Key</dt>
        <dd>{key.label} <span className="muted">· {unsure ? "unsure" : "confidence"} {key.confidence?.toFixed(2)}</span></dd>
      </dl>
      {children}
    </section>
  );
}
