// The vocal chain: how produced the tuned take should sound, optionally like a reference.

import type { FxParams, Library, Render, SoundProfile } from "../api";
import { parseTime } from "../util";

interface Props {
  fx: FxParams;
  library: Library;
  render: Render | null;
  onChange: (patch: Partial<FxParams>) => void;
  onReset: () => void;
}

const SLIDERS: [keyof FxParams, string, string, number][] = [
  ["grit", "Grit", "Saturation: builds the upper harmonics that make a vocal sound heavy", 1],
  ["compression", "Compression", "How even the level gets", 1],
  ["tone", "Tone match", "How far to move towards the reference's tonal balance (or a forward rock vocal's)", 1],
  ["deess", "De-ess", "Tames sharp s, t and sh sounds", 1],
  ["doubles", "Doubles", "Two detuned, slightly late copies left and right: a stacked sound", 1],
  ["space", "Space", "Reverb", 1],
  ["width", "Width", "Stereo width of the lead vocal (0 = mono)", 1.5],
];

export function SoundCard({ fx, library, render, onChange, onReset }: Props) {
  const references = library.files.filter((f) => f.kind === "reference");
  const start = parseTime(fx.refStart), end = parseTime(fx.refEnd);
  const sound = render?.sound;
  return (
    <section className="card">
      <div className="card-head">
        <h3>Sound</h3>
        <label className="check small">
          <input type="checkbox" checked={fx.enabled} onChange={(e) => onChange({ enabled: e.target.checked })} />
          Vocal chain
        </label>
      </div>
      {!fx.enabled ? (
        <p className="muted small">Off: you hear the tuned take as recorded. Turn on for a produced, heavier vocal.</p>
      ) : (
        <>
          <label className="field">
            <span>Sound like</span>
            <select value={fx.reference ?? ""} onChange={(e) => onChange({ reference: e.target.value || null })}>
              <option value="">A heavy rock vocal (generic)</option>
              {references.map((f) => <option key={f.path} value={f.path}>{f.name}</option>)}
            </select>
          </label>
          {fx.reference && (
            <div className="row">
              <label className="field time">
                <span>From</span>
                <input value={fx.refStart} placeholder="0:00" className={start === null ? "invalid" : ""}
                       onChange={(e) => onChange({ refStart: e.target.value })} />
              </label>
              <label className="field time">
                <span>To</span>
                <input value={fx.refEnd} placeholder="end" className={end === null ? "invalid" : ""}
                       onChange={(e) => onChange({ refEnd: e.target.value })} />
              </label>
            </div>
          )}
          {SLIDERS.map(([key, label, title, max]) => (
            <label key={key} className="field slider" title={title}>
              <span>{label} <b>{Math.round((fx[key] as number) * 100)}%</b></span>
              <input type="range" min={0} max={max} step={0.05} value={fx[key] as number}
                     onChange={(e) => onChange({ [key]: Number(e.target.value) })} />
            </label>
          ))}
          <label className="field inline" title="Integrated loudness of the result">
            <span>Loudness</span>
            <select value={fx.loudness === null ? "match" : String(fx.loudness)}
                    onChange={(e) => onChange({ loudness: e.target.value === "match" ? null : Number(e.target.value) })}>
              <option value="match">{fx.reference ? "match reference" : "-12 LUFS"}</option>
              {[-8, -10, -12, -14, -16, -18].map((l) => <option key={l} value={l}>{l} LUFS</option>)}
            </select>
          </label>
          <div className="row"><button onClick={onReset}>Reset sound</button></div>
          {sound && <ProfileTable output={sound.output} reference={sound.reference} />}
        </>
      )}
    </section>
  );
}

function ProfileTable({ output, reference }: { output: SoundProfile; reference: SoundProfile | null }) {
  const rows: [string, (p: SoundProfile) => string][] = [
    ["Loudness", (p) => `${p.lufs.toFixed(1)} LUFS`],
    ["Dynamics", (p) => `${p.dynamics.toFixed(1)} dB`],
    ["Brightness", (p) => `${Math.round(p.brightness)} Hz`],
    ["Width", (p) => (p.width === null ? "mono" : `${Math.round(p.width)} dB`)],
  ];
  return (
    <table className="profile">
      <thead><tr><th /><th>Yours</th>{reference && <th>Reference</th>}</tr></thead>
      <tbody>
        {rows.map(([label, fmt]) => (
          <tr key={label}><td>{label}</td><td>{fmt(output)}</td>{reference && <td>{fmt(reference)}</td>}</tr>
        ))}
      </tbody>
    </table>
  );
}
