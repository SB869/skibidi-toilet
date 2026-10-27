import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type Analysis, type AudioFile, type FxParams, type Library, type Render, type Summary, type TuneParams } from "../api";
import type { Player } from "../audio";
import { useStored } from "../hooks";
import { NOTE_NAMES, fmtTime, parseTime } from "../util";
import { Overview } from "./Overview";
import { PitchView, type Curve } from "./PitchView";
import { SoundCard } from "./SoundCard";
import { SummaryCard } from "./SummaryCard";
import { Transport } from "./Transport";

interface Props {
  file: AudioFile;
  library: Library;
  player: Player;
  onLibraryChanged: (select?: string) => void;
  onRender: (render: { id: string; name: string } | null) => void;
  onUseAsReference: (ref: { path: string; start: number; end: number }) => void;
  notify: (message: string, error?: boolean) => void;
}

const DEFAULTS: TuneParams = {
  style: "natural", drift: 0, humanize: 0.25, key: null, scale: null, speed: 25, strength: 1, hysteresis: 0.15, a4: 440,
};
const FX_DEFAULTS: FxParams = {
  enabled: false, reference: null, refStart: "", refEnd: "", compression: 0.45, grit: 0.5, tone: 1,
  deess: 0.5, width: 0.3, doubles: 0.3, space: 0.25, loudness: null,
};
const PRESETS: [string, number][] = [["Robot", 0], ["Tight", 25], ["Natural", 80], ["Gentle", 150]];
const COLORS = { sung: "#a3abbd", target: "#f6ad55", output: "#4fd1c5" };

export function TuneView({ file, library, player, onLibraryChanged, onRender, onUseAsReference, notify }: Props) {
  const [params, setParams] = useStored<TuneParams>("sound.tune", DEFAULTS);
  const [live, setLive] = useStored("sound.live", { on: true });
  const [sound, setSound] = useStored<FxParams>("sound.fx", FX_DEFAULTS);
  const [matchLevels, setMatchLevels] = useStored("sound.matchLevels", { on: true });
  const [loose, setLoose] = useState(file.kind === "reference");
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [loading, setLoading] = useState(true);
  const [render, setRender] = useState<Render | null>(null);
  const [rendering, setRendering] = useState(false);
  const [view, setView] = useState<[number, number]>([0, file.duration]);
  const [selection, setSelection] = useState<[number, number] | null>(null);
  const [section, setSection] = useState<{ label: string; summary: Summary } | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const seq = useRef(0);
  const inflight = useRef<AbortController | null>(null);

  // The original audio, once per file (this component is re-mounted for each file).
  useEffect(() => {
    const controller = new AbortController();
    player.clear();
    onRender(null);
    player.fetch(api.audioUrl(file.path), controller.signal)
      .then((buffer) => player.set("a", buffer))
      .catch((e: Error) => { if (e.name !== "AbortError") notify(`Couldn't load audio: ${e.message}`, true); });
    return () => { controller.abort(); inflight.current?.abort(); player.clear(); };
  }, [file.path]);

  useEffect(() => {
    let stale = false;
    setLoading(true);
    api.analysis(file.path, { loose })
      .then((a) => { if (!stale) setAnalysis(a); })
      .catch((e: Error) => { if (!stale) notify(`Analysis failed: ${e.message}`, true); })
      .finally(() => { if (!stale) setLoading(false); });
    return () => { stale = true; };
  }, [file.path, loose]);

  const tune = useCallback(async (p: TuneParams, f: FxParams) => {
    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;
    const mine = ++seq.current;
    setRendering(true);
    try {
      const result = await api.tune(file.path, p, chainRequest(f), controller.signal);
      const buffer = await player.fetch(api.renderUrl(result.id), controller.signal);
      if (mine !== seq.current) return;
      const first = !player.has("b");
      setRender(result);
      setSaved(null);
      player.set("b", buffer);
      if (first) player.setActive("b");
      onRender({ id: result.id, name: file.name });
    } catch (e) {
      if ((e as Error).name !== "AbortError" && mine === seq.current) notify(`Tuning failed: ${(e as Error).message}`, true);
    } finally {
      if (mine === seq.current) setRendering(false);
    }
  }, [file.path, file.name, player, onRender, notify]);

  // Live mode re-renders shortly after any change. Long references wait for a first manual
  // render, since tuning a whole song isn't usually what you opened it for.
  const auto = live.on && (file.kind === "take" || render !== null);
  useEffect(() => {
    if (!auto) return;
    const id = setTimeout(() => void tune(params, sound), sound.enabled ? 450 : 250);
    return () => clearTimeout(id);
  }, [params, sound, auto]);

  // Compare at equal loudness: otherwise the louder version always sounds better.
  useEffect(() => {
    player.setTrim("a", matchLevels.on && render ? render.lufs - render.original_lufs : 0);
  }, [player, render, matchLevels.on]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, select, textarea")) return;
      if (e.key === "Escape") setSelection(null);
      if (e.key === "l" || e.key === "L") player.setLoop(player.loop ? null : selection);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [player, selection]);

  const update = (patch: Partial<TuneParams>) => setParams((p) => ({ ...p, ...patch }));

  const inScale = useMemo(() => {
    const key = render?.key;
    if (!key || key.scale === "chromatic") return null;
    return new Set((library.scales[key.scale] ?? []).map((s) => (key.tonic + s) % 12));
  }, [render, library]);

  const curves = useMemo(() => {
    const out: Curve[] = [];
    // Target notes as soft blocks behind the lines, then what you sang, then what comes out.
    if (render) out.push({ values: render.target, hop: render.hop, color: COLORS.target, width: 9, alpha: 0.22, step: true });
    if (analysis) out.push({ values: analysis.pitch, hop: analysis.hop, color: COLORS.sung, width: 1.4, alpha: render ? 0.7 : 1 });
    if (render) out.push({ values: render.corrected, hop: render.hop, color: COLORS.output, width: 1.7 });
    return out;
  }, [analysis, render]);

  async function analyzeSelection() {
    if (!selection) return;
    try {
      const a = await api.analysis(file.path, { start: selection[0], end: selection[1], loose });
      setSection({ label: `Selection ${fmtTime(selection[0])}–${fmtTime(selection[1])}`, summary: a.summary });
    } catch (e) {
      notify(`Analysis failed: ${(e as Error).message}`, true);
    }
  }

  async function exportSelection() {
    if (!selection) return;
    try {
      const out = await api.cut(file.path, selection[0], selection[1]);
      notify(`Saved ${out.name}`);
      onLibraryChanged();
    } catch (e) {
      notify(`Export failed: ${(e as Error).message}`, true);
    }
  }

  async function saveRender() {
    if (!render) return;
    try {
      const out = await api.saveRender(render.id);
      setSaved(out.name);
      notify(`Saved ${out.name}`);
      onLibraryChanged();
    } catch (e) {
      notify(`Save failed: ${(e as Error).message}`, true);
    }
  }

  const keyValue = params.key ?? "none";
  return (
    <div className="workspace">
      <div className="editor">
        <div className="editor-head">
          <h1 title={file.name}>{file.name}</h1>
          <span className="muted small">
            {fmtTime(file.duration, 1)} · {(file.sample_rate / 1000).toFixed(1)} kHz · {file.channels === 1 ? "mono" : "stereo"}
          </span>
          {(loading || rendering) && <span className="busy">{loading ? "analyzing…" : "tuning…"}</span>}
          <span className="spacer" />
          <span className="legend">
            <i style={{ background: COLORS.sung }} /> sung
            <i style={{ background: COLORS.target }} /> target
            <i style={{ background: COLORS.output }} /> output
          </span>
        </div>

        <PitchView duration={file.duration} curves={curves} view={view} onView={setView}
                   selection={selection} onSelect={setSelection} onSeek={(t) => player.seek(t)}
                   player={player} inScale={inScale} />
        {analysis && (
          <Overview peaks={analysis.peaks} duration={file.duration} view={view} onView={setView}
                    selection={selection} onSeek={(t) => player.seek(t)} player={player} />
        )}
        <Transport player={player} selection={selection} labels={["Original", render?.sound ? "Processed" : "Tuned"]}>
          {render && (
            <label className="check small" title="Play the original at the same loudness as the result, for a fair comparison">
              <input type="checkbox" checked={matchLevels.on} onChange={(e) => setMatchLevels({ on: e.target.checked })} />
              Match levels
            </label>
          )}
          {selection && (
            <>
              <button onClick={analyzeSelection}>Analyze selection</button>
              <button onClick={exportSelection}>Export selection</button>
              {file.kind === "reference" && (
                <button className="primary" onClick={() => onUseAsReference({ path: file.path, start: selection[0], end: selection[1] })}>
                  Compare against this →
                </button>
              )}
            </>
          )}
        </Transport>
        <p className="hint">
          Wheel zooms · shift+wheel scrolls · drag to select · double-click to zoom out ·
          <kbd>Space</kbd> play · <kbd>T</kbd> original/tuned · <kbd>L</kbd> loop selection
        </p>
      </div>

      <aside className="inspector">
        <section className="card">
          <h3>Tuning</h3>
          <div className="segmented wide" role="radiogroup" aria-label="Style">
            <button className={params.style === "natural" ? "on" : ""} onClick={() => update({ style: "natural" })}
                    title="Move each whole note into tune; keeps your vibrato, scoops and slides">Natural</button>
            <button className={params.style === "autotune" ? "on" : ""} onClick={() => update({ style: "autotune" })}
                    title="Pull every moment toward the note: the classic Auto-Tune sound">Autotune</button>
          </div>
          <p className="muted small">
            {params.style === "natural"
              ? "Each note is moved as a whole, so it keeps your vibrato and slides and doesn't sound tuned."
              : "Pulls every moment toward the note. Fast speeds give the robotic effect."}
          </p>
          <label className="field">
            <span>Key</span>
            <select value={keyValue} onChange={(e) => update({ key: e.target.value === "none" ? null : e.target.value })}>
              <option value="none">None: nearest semitone</option>
              <option value="auto">Detect automatically</option>
              {NOTE_NAMES.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <label className="field">
            <span>Scale</span>
            <select value={params.scale ?? ""} disabled={params.key === null}
                    onChange={(e) => update({ scale: e.target.value || null })}>
              <option value="">{params.key === "auto" ? "as detected" : "major"}</option>
              {Object.keys(library.scales).filter((s) => s !== "chromatic").map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>

          {params.style === "autotune" && (
            <>
            <label className="field slider">
              <span>Retune speed <b>{params.speed} ms</b></span>
              <input type="range" min={0} max={200} step={1} value={params.speed}
                     onChange={(e) => update({ speed: Number(e.target.value) })} />
            </label>
            <div className="presets">
              {PRESETS.map(([name, speed]) => (
                <button key={name} className={params.speed === speed ? "on" : ""} onClick={() => update({ speed })}>{name}</button>
              ))}
            </div>
            </>
          )}
          <label className="field slider">
            <span>Strength <b>{Math.round(params.strength * 100)}%</b></span>
            <input type="range" min={0} max={1} step={0.01} value={params.strength}
                   onChange={(e) => update({ strength: Number(e.target.value) })} />
          </label>
          {params.style === "autotune" ? (
            <label className="field slider" title="How far past halfway between two notes you must go before it switches note">
              <span>Note hold <b>{params.hysteresis.toFixed(2)} st</b></span>
              <input type="range" min={0} max={0.5} step={0.01} value={params.hysteresis}
                     onChange={(e) => update({ hysteresis: Number(e.target.value) })} />
            </label>
          ) : (
            <>
            <label className="field slider" title="Let each note keep a little of its own lean, so not every note lands dead centre">
              <span>Humanize <b>{Math.round(params.humanize * 100)}%</b></span>
              <input type="range" min={0} max={1} step={0.05} value={params.humanize}
                     onChange={(e) => update({ humanize: Number(e.target.value) })} />
            </label>
            <label className="field slider" title="Straighten slow wander inside long notes. Vibrato and the ends of notes are left alone.">
              <span>Straighten drift <b>{Math.round(params.drift * 100)}%</b></span>
              <input type="range" min={0} max={1} step={0.05} value={params.drift}
                     onChange={(e) => update({ drift: Number(e.target.value) })} />
            </label>
            </>
          )}
          <label className="field inline">
            <span>A4 tuning</span>
            <input type="number" min={400} max={480} step={0.1} value={params.a4}
                   onChange={(e) => update({ a4: Number(e.target.value) || 440 })} /> Hz
          </label>

          <div className="row">
            <label className="check">
              <input type="checkbox" checked={live.on} onChange={(e) => setLive({ on: e.target.checked })} /> Live
            </label>
            <button className="primary" onClick={() => void tune(params, sound)} disabled={rendering}>
              {rendering ? "Tuning…" : "Render"}
            </button>
            <button onClick={() => setParams(DEFAULTS)}>Reset</button>
          </div>
        </section>

        <SoundCard fx={sound} library={library} render={render}
                   onChange={(patch) => setSound((f) => ({ ...f, ...patch }))}
                   onReset={() => setSound((f) => ({ ...FX_DEFAULTS, enabled: true, reference: f.reference,
                                                     refStart: f.refStart, refEnd: f.refEnd }))} />

        {render && (
          <section className="card">
            <h3>Result</h3>
            <p>{describeKey(render)}</p>
            <p className="muted small">
              Moved pitch by {Math.round(render.correction_median)}¢ typically, {Math.round(render.correction_max)}¢ at most.
            </p>
            <div className="row">
              <button onClick={saveRender} disabled={saved !== null}>{saved ? "Saved" : "Save to library"}</button>
            </div>
            {saved && <p className="muted small">→ tuned/{saved}</p>}
          </section>
        )}

        {section ? (
          <SummaryCard title={section.label} summary={section.summary}>
            <button className="link" onClick={() => setSection(null)}>back to whole file</button>
          </SummaryCard>
        ) : analysis && (
          <SummaryCard title="Analysis" summary={analysis.summary}>
            <label className="check small" title="Read pitch even where the voice is unclear: for acapellas pulled from a mix, reverb or bleed">
              <input type="checkbox" checked={loose} onChange={(e) => setLoose(e.target.checked)} /> Loose pitch reading (stems, reverb)
            </label>
          </SummaryCard>
        )}
      </aside>
    </div>
  );
}

/** What the server needs to run the vocal chain, or null when it's off. */
function chainRequest(f: FxParams) {
  if (!f.enabled) return null;
  const start = parseTime(f.refStart), end = parseTime(f.refEnd);
  return {
    compression: f.compression, grit: f.grit, tone: f.tone, deess: f.deess, width: f.width,
    doubles: f.doubles, space: f.space, loudness: f.loudness, reference: f.reference,
    ref_start: start ?? undefined, ref_end: end ?? undefined,
  };
}

function describeKey(render: Render): string {
  const { key, estimate } = render;
  const guess = `${estimate.label}, confidence ${estimate.confidence?.toFixed(2)}`;
  const how = render.style === "natural" ? "Whole notes moved; vibrato and slides kept." : "Every moment pulled to the note.";
  switch (key.source) {
    case "auto": return `Detected ${key.label} (confidence ${estimate.confidence?.toFixed(2)}). ${how}`;
    case "auto-unsure": return `Couldn't pin down a key (best guess ${guess}), so used the nearest semitone. ${how}`;
    case "fixed": return `Tuned to ${key.label}. ${how}`;
    default: return `Nearest semitone. Sounds like ${guess}. ${how}`;
  }
}
