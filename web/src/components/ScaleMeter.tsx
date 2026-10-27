// A tuner for singing along: where your voice is right now against the reference's scale.
// A chromatic strip lights up the scale's notes; a needle follows your pitch; the reference's
// current note is outlined; a cents gauge shows how close you are to the note you're aiming for.

import { NOTE_NAMES, isBlackKey, noteName } from "../util";

interface Props {
  midi: number | null;         // your pitch now
  scale: Set<number>;          // pitch classes in the reference's key
  used: Set<number>;           // the ones it actually sings
  keyLabel: string | null;     // e.g. "F# major"
  reference: number | null;    // the reference's note right now, in your octave
  centre: number;              // MIDI note to centre the strip on
  inKey: number | null;        // share of the last 10 s sung within ±25¢ of a scale note
}

const SPAN = 7;                // semitones either side of the centre
const GOOD_CENTS = 15;
const CLOSE_CENTS = 35;

export function ScaleMeter({ midi, scale, used, keyLabel, reference, centre, inKey }: Props) {
  const lo = Math.round(centre) - SPAN, hi = Math.round(centre) + SPAN;
  const cells = Array.from({ length: hi - lo + 1 }, (_, i) => lo + i);
  const scaleNote = midi === null ? null : nearestIn(midi, scale);
  const target = reference ?? scaleNote;
  const cents = midi !== null && target !== null ? Math.round((midi - target) * 100) : null;
  const grade = cents === null ? "" : Math.abs(cents) <= GOOD_CENTS ? "good" : Math.abs(cents) <= CLOSE_CENTS ? "close" : "off";
  const inScale = midi !== null && scaleNote !== null && Math.abs(midi - scaleNote) <= 0.25;
  const at = (m: number) => `${((Math.min(hi + 0.5, Math.max(lo - 0.5, m)) - lo + 0.5) / cells.length) * 100}%`;

  return (
    <section className="card">
      <div className="card-head">
        <h3>Scale{keyLabel ? ` · ${keyLabel}` : ""}</h3>
        {inKey !== null && <span className={`small ${inKey >= 0.75 ? "good-text" : "muted"}`}>{Math.round(inKey * 100)}% in key</span>}
      </div>
      <div className="big-readout">
        <span className="note">{midi === null ? "–" : noteName(midi)}</span>
        <span className={`cents ${grade}`}>
          {cents === null ? "" : cents === 0 ? "on it" : `${cents > 0 ? "♯ " : "♭ "}${Math.abs(cents)}¢`}
        </span>
      </div>
      <div className="scale-strip">
        {cells.map((m) => {
          const pc = ((m % 12) + 12) % 12;
          const classes = ["cell", isBlackKey(m) ? "black" : "white", scale.has(pc) ? "in" : "", used.has(pc) ? "used" : "",
                           reference !== null && Math.round(reference) === m ? "ref" : "",
                           midi !== null && Math.round(midi) === m ? `you ${grade}` : ""].join(" ");
          return (
            <div key={m} className={classes} title={noteName(m)}>
              {scale.has(pc) && <span>{NOTE_NAMES[pc]}</span>}
            </div>
          );
        })}
        {midi !== null && <i className={`needle ${grade}`} style={{ left: at(midi) }} />}
      </div>
      <div className="cents-gauge">
        <div className="zone" />
        <div className="centre" />
        {cents !== null && <i className={`needle ${grade}`} style={{ left: `${50 + Math.max(-50, Math.min(50, cents))}%` }} />}
      </div>
      <p className="muted small">
        {midi === null ? "Sing to see where you land."
          : !inScale && reference === null ? "Between notes or outside the key: slide to a lit note."
          : reference !== null ? `Aim: ${noteName(reference)} (the reference)${nudge(cents ?? 0)}`
          : `On ${noteName(scaleNote!)}, in the key`}
      </p>
    </section>
  );
}

function nudge(cents: number): string {
  if (Math.abs(cents) <= GOOD_CENTS) return "";
  const how = Math.abs(cents) <= CLOSE_CENTS ? " a little" : "";
  return cents > 0 ? `, come down${how}` : `, come up${how}`;
}

function nearestIn(midi: number, scale: Set<number>): number | null {
  if (!scale.size) return Math.round(midi);
  let best: number | null = null;
  for (let m = Math.floor(midi) - 2; m <= Math.ceil(midi) + 2; m++) {
    if (scale.has(((m % 12) + 12) % 12) && (best === null || Math.abs(m - midi) < Math.abs(best - midi))) best = m;
  }
  return best;
}
