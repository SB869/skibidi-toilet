import { useEffect, useMemo, useState } from "react";
import { api, type Comparison, type Library } from "../api";
import type { Player } from "../audio";
import { bias, cents, fmtTime, parseTime, pct } from "../util";
import { PitchView, type Curve } from "./PitchView";
import { SummaryCard } from "./SummaryCard";
import { Transport } from "./Transport";

interface Props {
  library: Library;
  player: Player;
  currentRender: { id: string; name: string } | null;
  initialRef: { path: string; start?: number; end?: number } | null;
  notify: (message: string, error?: boolean) => void;
}

export function CompareView({ library, player, currentRender, initialRef, notify }: Props) {
  const references = library.files.filter((f) => f.kind === "reference");
  const candidates = [
    ...library.files.filter((f) => f.kind === "take").map((f) => ({ value: f.path, label: f.name })),
    ...(currentRender ? [{ value: `render:${currentRender.id}`, label: `${currentRender.name} (current render, unsaved)` }] : []),
    ...library.files.filter((f) => f.kind === "render").map((f) => ({ value: f.path, label: f.name })),
  ];

  const [refPath, setRefPath] = useState(initialRef?.path ?? references[0]?.path ?? "");
  const [startText, setStartText] = useState(initialRef?.start !== undefined ? fmtTime(initialRef.start, 1) : "");
  const [endText, setEndText] = useState(initialRef?.end !== undefined ? fmtTime(initialRef.end, 1) : "");
  const [chosen, setChosen] = useState<Set<string>>(() => new Set(currentRender ? [`render:${currentRender.id}`] : []));
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<Comparison | null>(null);
  const [active, setActive] = useState(0);
  const [view, setView] = useState<[number, number]>([0, 1]);
  const [selection, setSelection] = useState<[number, number] | null>(null);

  const start = parseTime(startText);
  const end = parseTime(endText);
  const timesValid = start !== null && end !== null && (start === undefined || end === undefined || end > start);

  async function run() {
    if (!refPath || !chosen.size || !timesValid) return;
    setRunning(true);
    try {
      const takes = candidates.map((c) => c.value).filter((v) => chosen.has(v));
      const comparison = await api.compare(refPath, takes, start ?? undefined, end ?? undefined);
      setResult(comparison);
      const scored = comparison.rows.map((r, i) => [r.off_by ?? Infinity, i] as const);
      setActive(scored.reduce((best, cur) => (cur[0] < best[0] ? cur : best), scored[0])[1]);
    } catch (e) {
      notify(`Compare failed: ${(e as Error).message}`, true);
    } finally {
      setRunning(false);
    }
  }

  const row = result?.rows[active];
  const duration = row?.pitch ? row.pitch.length * row.hop : 1;

  // Load the selected take for playback.
  useEffect(() => {
    if (!row) return;
    const controller = new AbortController();
    player.clear();
    setView([0, duration]);
    setSelection(null);
    const url = row.take.startsWith("render:") ? api.renderUrl(row.take.slice(7)) : api.audioUrl(row.take);
    player.fetch(url, controller.signal)
      .then((buffer) => player.set("a", buffer))
      .catch((e: Error) => { if (e.name !== "AbortError") notify(`Couldn't load ${row.name}: ${e.message}`, true); });
    return () => controller.abort();
  }, [row]); // load whenever the shown row changes

  useEffect(() => () => player.clear(), [player]);

  const curves = useMemo<Curve[]>(() => (row?.pitch && row.reference ? [
    { values: row.reference, hop: row.hop, color: "#f6ad55", width: 6, alpha: 0.5, maxJump: 2.5 },
    { values: row.pitch, hop: row.hop, color: "#4fd1c5", width: 1.6 },
  ] : []), [row]);

  const best = result ? Math.min(...result.rows.map((r) => r.off_by ?? Infinity)) : null;

  return (
    <div className="workspace">
      <div className="editor compare">
        <section className="card setup">
          <div className="setup-row">
            <label className="field">
              <span>Reference</span>
              <select value={refPath} onChange={(e) => setRefPath(e.target.value)}>
                {!references.length && <option value="">import a reference first</option>}
                {references.map((f) => <option key={f.path} value={f.path}>{f.name}</option>)}
              </select>
            </label>
            <label className="field time">
              <span>From</span>
              <input value={startText} placeholder="0:00" onChange={(e) => setStartText(e.target.value)}
                     className={start === null ? "invalid" : ""} />
            </label>
            <label className="field time">
              <span>To</span>
              <input value={endText} placeholder="auto" onChange={(e) => setEndText(e.target.value)}
                     className={end === null ? "invalid" : ""} />
            </label>
            {refPath && timesValid && (
              <audio controls preload="none" src={api.audioUrl(refPath, start ?? undefined, end ?? undefined)} />
            )}
          </div>
          <div className="takes">
            <span className="muted small">Takes to score:</span>
            {candidates.map((c) => (
              <label key={c.value} className={chosen.has(c.value) ? "chip-check on" : "chip-check"}>
                <input type="checkbox" checked={chosen.has(c.value)} onChange={(e) => {
                  const next = new Set(chosen);
                  if (e.target.checked) next.add(c.value);
                  else next.delete(c.value);
                  setChosen(next);
                }} />
                {c.label}
              </label>
            ))}
          </div>
          <div className="row">
            <button className="primary" onClick={run} disabled={running || !refPath || !chosen.size || !timesValid}>
              {running ? "Comparing…" : "Compare"}
            </button>
            <span className="muted small">
              Lines each take up with the reference (any tempo, any octave) and measures how close the pitch is.
            </span>
          </div>
        </section>

        {result && (
          <>
            <table className="results">
              <thead>
                <tr>
                  <th>Take</th><th title="Where your first note lands in the reference">First note at</th>
                  <th title="Your speed relative to the reference">Tempo</th><th>Octave</th>
                  <th title="Typical pitch distance from the reference while you both sing">Off by</th>
                  <th>≤25¢</th><th>≤50¢</th><th>Bias</th>
                  <th title="Held notes within a quarter-tone of the reference's note at that moment">Notes right</th>
                </tr>
              </thead>
              <tbody>
                {result.rows.map((r, i) => (
                  <tr key={r.take} className={i === active ? "active" : ""} onClick={() => setActive(i)}>
                    <td className="name">{r.name}{r.off_by === best && result.rows.length > 1 && <span className="badge">closest</span>}</td>
                    {r.off_by === undefined ? <td colSpan={8} className="muted">no pitched voice found</td> : (
                      <>
                        <td>{fmtTime(r.first_note!, 1)}</td>
                        <td>{pct(r.tempo)}</td>
                        <td>{r.octave === 0 ? "same" : `${-r.octave! / 12 > 0 ? "+" : ""}${-r.octave! / 12} oct`}</td>
                        <td><b>{cents(r.off_by)}</b></td>
                        <td>{pct(r.within_25)}</td>
                        <td>{pct(r.within_50)}</td>
                        <td>{bias(r.bias)}</td>
                        <td>{r.notes_matched}/{r.notes_compared} {r.notes_compared ? `(${pct(r.notes_matched! / r.notes_compared)})` : ""}</td>
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>

            {row?.pitch && (
              <>
                <div className="editor-head">
                  <span className="small">{row.name}</span>
                  <span className="spacer" />
                  <span className="legend">
                    <i style={{ background: "#f6ad55" }} /> reference (in your octave, time-aligned)
                    <i style={{ background: "#4fd1c5" }} /> take
                  </span>
                </div>
                <PitchView duration={duration} curves={curves} view={view} onView={setView}
                           selection={selection} onSelect={setSelection} onSeek={(t) => player.seek(t)} player={player} />
                <Transport player={player} selection={selection} />
              </>
            )}
          </>
        )}
        {!result && !running && (
          <div className="empty-state">
            {references.length
              ? "Pick a reference section and the takes to score, then Compare."
              : "Drop a reference vocal (e.g. the original acapella) on References in the library to get started."}
          </div>
        )}
      </div>

      <aside className="inspector">
        {result && (
          <SummaryCard
            title={`Reference ${fmtTime(result.reference.start, 1)}–${fmtTime(result.reference.end, 1)}`}
            summary={result.reference.summary}>
            <p className="muted small">The section searched includes a few seconds either side of what you entered.</p>
          </SummaryCard>
        )}
      </aside>
    </div>
  );
}
