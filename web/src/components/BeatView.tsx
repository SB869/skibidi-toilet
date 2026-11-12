import { useEffect, useRef, useState } from "react";
import { api, type AudioFile, type Beat, type BeatBass, type BeatForm, type BeatHats, type BeatStyle, type Lane } from "../api";

const NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const midi = (name: string) => NAMES.indexOf(name.slice(0, -1)) + 12 * (Number(name.slice(-1)) + 1);

const LANES: { lane: Lane; label: string }[] = [
  { lane: "kick", label: "boom" },
  { lane: "snare", label: "mid" },
  { lane: "hat", label: "high" },
];

interface StrudelElement extends HTMLElement {
  editor?: { evaluate: () => void; stop: () => void; setCode: (code: string) => void };
}

/** A beatbox recording turned into a beat: the groove as heard, and Strudel code that plays it as a beat. */
export function BeatView({ file, notify }: { file: AudioFile; notify: (m: string, e?: boolean) => void }) {
  const [beat, setBeat] = useState<Beat | null>(null);
  const [bars, setBars] = useState(4);
  const [style, setStyle] = useState<BeatStyle>("synth");
  const [form, setForm] = useState<BeatForm>("loop");
  const [bass, setBass] = useState<BeatBass>("synth");
  const [hats, setHats] = useState<BeatHats>("pulse");
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);          // the Strudel editor's code is loaded
  const [playing, setPlaying] = useState(false);
  const [version, setVersion] = useState(0);          // bump to put the generated code back
  const editor = useRef<StrudelElement | null>(null);

  useEffect(() => {
    let live = true;
    import("@strudel/repl").then(() => live && setReady(true)).catch((e) => live && setError(`Strudel didn't load: ${e}`));
    return () => { live = false; };
  }, []);

  useEffect(() => {
    let live = true;
    setBeat(null); setError(null);
    api.beat(file.path, bars, style, form, bass, hats).then((b) => live && setBeat(b)).catch((e) => live && setError(String(e.message ?? e)));
    return () => { live = false; };
  }, [file.path, bars, style, form, bass, hats]);

  useEffect(() => {
    const el = editor.current;
    if (!el) return;
    const onUpdate = (e: Event) => setPlaying(Boolean((e as CustomEvent).detail?.started));
    el.addEventListener("update", onUpdate);
    return () => { el.removeEventListener("update", onUpdate); el.editor?.stop(); };
  }, [ready, beat?.code, version]);

  const play = () => {
    if (!editor.current?.editor) { notify("The Strudel editor is still loading", true); return; }
    editor.current.editor.evaluate();
  };
  const stop = () => editor.current?.editor?.stop();
  const pitches = (beat?.riff ?? []).flat().filter((n): n is string => n !== null).map(midi);
  const [lo, hi] = [Math.min(...pitches), Math.max(...pitches)];
  const riffNotes = [...new Set((beat?.loop ?? []).flatMap((i) => beat?.riff?.[i] ?? [])
    .filter((n): n is string => n !== null).map((n) => n.slice(0, -1)))];   // the looped bars' notes, in order

  return (
    <div className="beat-view">
      <section className="card">
        <h3>Beat from {file.name}</h3>
        {error && <p className="error">{error}</p>}
        {!beat && !error && <p className="muted">Listening to the beatbox…</p>}
        {beat && (
          <>
            <dl className="facts">
              <dt>Tempo</dt><dd>{form === "song" && beat.song_bpm !== beat.bpm ? <>{beat.song_bpm} BPM <span className="muted">· your take: {beat.bpm}</span></> : <>{beat.bpm} BPM</>}</dd>
              <dt>Key</dt><dd>{beat.key} <span className="muted">· from where your voice sat</span></dd>
              <dt>Groove</dt>
              <dd>{beat.bars.length} bars with a beat
                {beat.skipped.length > 0 && <span className="muted"> · left out {beat.skipped.map(([a, b]) => `${a.toFixed(1)}–${b.toFixed(1)}s`).join(", ")}</span>}
              </dd>
              {beat.riff && <>
                <dt>Riff</dt>
                <dd>{riffNotes.join(" ")} <span className="muted">· {style === "synth" && beat.pulse.length > 0
                  ? `your oscillator as ${beat.basses[bass].split(":")[0]}, a note on each bwom (16ths ${beat.pulse.map((k) => k + 1).join(" ")}), 8th-note echoes, the sub ducking under the kick; hats: ${beat.hat_styles[hats]}`
                  : "your oscillator on detuned saws, the filter moving like your mouth"}</span></dd>
              </>}
              <dt>Sounds</dt>
              <dd>{beat.hits.kick} booms{beat.hits.snare + beat.hits.hat > 0 && `, ${beat.hits.snare} mid, ${beat.hits.hat} high`}
                <span className="muted"> · {beat.snare_played ? "your snare is the backbeat" : "no backbeat: uncomment the clap in the code to add one"}</span></dd>
            </dl>
            <div className="groove">
              {beat.bars.map((bar, i) => (
                <div key={i} className={beat.loop.includes(i) ? "groove-bar looped" : "groove-bar"}
                     title={beat.loop.includes(i) ? "in the loop" : "not in the loop"}>
                  <span className="groove-n">{i + 1}</span>
                  {beat.riff && (
                    <div className="groove-lane riff" title="riff">
                      {beat.riff[i].map((n, j) => (
                        <i key={j} className={(j % 4 === 0 ? "beat" : "") + (beat.pulse.includes(j) ? " bwom" : "")} title={n ?? "–"}>
                          {n && <b style={{ bottom: `${hi > lo ? ((midi(n) - lo) / (hi - lo)) * 75 : 37}%` }} />}
                        </i>
                      ))}
                    </div>
                  )}
                  {LANES.map(({ lane, label }) => (
                    <div key={lane} className={`groove-lane ${lane}`} title={label}>
                      {bar[lane].split("").map((c, j) => <i key={j} className={(c === "x" ? "on" : "") + (j % 4 === 0 ? " beat" : "")} />)}
                    </div>
                  ))}
                </div>
              ))}
            </div>
            <div className="row">
              <button className={playing ? "" : "primary"} onClick={play} disabled={!ready}>{playing ? "Restart" : "▶ Play"}</button>
              <button onClick={stop} disabled={!playing}>■ Stop</button>
              <label className="row small">Loop
                <select value={bars} onChange={(e) => setBars(Number(e.target.value))}>
                  {[1, 2, 4, 8].map((n) => <option key={n} value={n}>{n} bar{n > 1 ? "s" : ""}</option>)}
                </select>
              </label>
              {beat.riff && (
                <label className="row small" title="synth: the synth your beatbox stands for · mouth: how your mouth shaped each note">Sound
                  <select value={style} onChange={(e) => setStyle(e.target.value as BeatStyle)}>
                    <option value="synth">synth</option>
                    <option value="mouth">mouth</option>
                  </select>
                </label>
              )}
              {beat.riff && style === "synth" && (
                <label className="row small" title={Object.entries(beat.basses).map(([k, v]) => `${k}: ${v}`).join("\n")}>Bass
                  <select value={bass} onChange={(e) => setBass(e.target.value as BeatBass)}>
                    {Object.keys(beat.basses).map((k) => <option key={k} value={k}>{k}</option>)}
                  </select>
                </label>
              )}
              {beat.riff && style === "synth" && (
                <label className="row small" title={Object.entries(beat.hat_styles).map(([k, v]) => `${k}: ${v}`).join("\n")}>Hats
                  <select value={hats} onChange={(e) => setHats(e.target.value as BeatHats)}>
                    {Object.keys(beat.hat_styles).map((k) => <option key={k} value={k}>{k}</option>)}
                  </select>
                </label>
              )}
              <label className="row small" title="loop: the groove, round and round · song: laid out as a track - intro, build, drop, breakdown, drop, outro">Form
                <select value={form} onChange={(e) => setForm(e.target.value as BeatForm)}>
                  <option value="loop">loop</option>
                  <option value="song">song</option>
                </select>
              </label>
              <a href={api.beatMidiUrl(file.path, bars, style)} download><button>MIDI</button></a>
              <button className="link" onClick={() => { stop(); setVersion((v) => v + 1); }} title="Put the generated code back">reset code</button>
              <span className="spacer" />
              <span className="muted small">Edit the code and press <kbd>Ctrl</kbd>+<kbd>Enter</kbd> to hear it · <a href="https://strudel.cc/learn" target="_blank" rel="noreferrer">Strudel docs</a></span>
            </div>
          </>
        )}
      </section>
      {ready && beat && (
        <div className="strudel-host" key={`${beat.code}#${version}`}>
          <strudel-editor ref={editor} code={beat.code} />
        </div>
      )}
    </div>
  );
}
