// Sing along with a reference and see, live, where your voice lands against it:
// its melody (in your octave), its loudness and its edge, with your own drawn on top.
// Pick the section on the reference's waveform; the timing (tempo, beats, where the voice
// comes in) is detected so a count-in can click you in. Takes are recorded losslessly and
// scored against the reference as soon as they're saved.
// Following Ardour, the section, the reference and a take to compare come from the session
// open there (and keep following it), and new takes go back into it, in place.

import { useEffect, useMemo, useRef, useState } from "react";
import { api, type ArdourLink, type AudioFile, type Guide, type Library, type Series, type TakeCurves, type Timing } from "../api";
import { useFrame, useStored } from "../hooks";
import { LiveSession, encodeWav, type Reading } from "../live";
import { NOTE_NAMES, fmtTime, parseTime, pct } from "../util";
import { LaneView } from "./LaneView";
import { ScaleMeter } from "./ScaleMeter";
import { PitchView, type Curve } from "./PitchView";
import { SliceSelector } from "./SliceSelector";

interface Props {
  library: Library;
  notify: (message: string, error?: boolean) => void;
  onLibraryChanged: () => Promise<void> | void;
  onOpen: (path: string) => void;
}

interface Setup {
  reference: string | null;
  start: string;           // where you picked; the take starts where the voice comes in after it (if snap)
  end: string;
  snap: boolean;
  bpm: number | null;      // null = as detected
  countIn: number;         // clicks before the entry
  keepClicking: boolean;   // a click track through the whole take
  refInCountIn: boolean;   // hear the reference during the count-in too
  register: string;        // "auto" or semitones to move the reference by, e.g. "-12"
  playGuide: boolean;
  guideVolume: number;
  ardour: boolean;         // follow the session open in Ardour
  takeView: "placed" | "aligned";  // a take from Ardour: where it sits, or lined up note by note
}

interface Attempt {
  file: AudioFile;
  offBy?: number;
  notes?: string;
  tempo?: number;
  edge?: number;
  ardour?: string;         // where it went in Ardour
}

const DEFAULT_SECTION_S = 30;
const MAX_LEAD_S = 10;          // the guide is read from this far before the start, to cover any count-in
const FREEFORM_S = 120;
const LIVE_HOP = 0.02;
const LOUD_TARGET_DBFS = -12;   // where your loud singing should land on the input meter
const WINDOW_S = 12;            // how much of the guide is on screen while singing
const COLORS = { reference: "#f6ad55", you: "#4fd1c5", take: "#b794f4" };
const ARDOUR_POLL_MS = 2000;
const ON_BEAT = 0.2;            // an entry within this fraction of a beat from the song's beat counts as on it
const IN_KEY_CENTS = 25;
const IN_KEY_WINDOW_MS = 10000;

export function RecordView({ library, notify, onLibraryChanged, onOpen }: Props) {
  const references = library.files.filter((f) => f.kind === "reference");
  const [setup, setSetup] = useStored<Setup>("sound.record", {
    reference: references[0]?.path ?? null, start: "", end: "", snap: true, bpm: null, countIn: 4,
    keepClicking: false, refInCountIn: false, register: "auto", playGuide: true, guideVolume: 0.8,
    ardour: false, takeView: "placed",
  });
  const update = (patch: Partial<Setup>) => setSetup((s) => ({ ...s, ...patch }));
  const start = parseTime(setup.start), end = parseTime(setup.end);
  const timesValid = start !== null && end !== null && (start === undefined || end === undefined || end > start);

  const [overview, setOverview] = useState<{ duration: number; peaks: { min: number[]; max: number[] } } | null>(null);
  const [timing, setTiming] = useState<Timing | null>(null);
  const [guide, setGuide] = useState<Guide | null>(null);
  const [loadingGuide, setLoadingGuide] = useState(false);
  const [guideAudio, setGuideAudio] = useState<AudioBuffer | null>(null);
  const [session, setSession] = useState<LiveSession | null>(null);
  const [mode, setMode] = useState<"idle" | "practice" | "recording" | "saving">("idle");
  const [attempts, setAttempts] = useState<Attempt[]>([]);
  const [view, setView] = useState<[number, number]>([0, WINDOW_S]);
  const [reading, setReading] = useState<Reading | null>(null);
  const [autoOffset, setAutoOffset] = useState<number | null>(null);
  const [frame, setFrame] = useState(0);
  const live = useRef(emptyLive(1));
  const recentEdge = useRef<number[]>([]);
  const taps = useRef<number[]>([]);
  const keyLog = useRef<{ at: number; ok: boolean }[]>([]);
  const [inKey, setInKey] = useState<number | null>(null);
  const lastPaint = useRef(0);
  const modeRef = useRef(mode);
  modeRef.current = mode;
  const [link, setLink] = useState<ArdourLink | null>(null);
  const [linkError, setLinkError] = useState<string | null>(null);
  const [shownTake, setShownTake] = useState<string | null>(null);  // region id; null = the newest in the section; "none"
  const [takeCurves, setTakeCurves] = useState<TakeCurves | null>(null);
  const appliedSpan = useRef("");

  // The reference's key (all its notes) and the notes it actually sings.
  const scale = useMemo(() => {
    const key = guide?.summary.key;
    const steps = key ? library.scales[key.scale] : undefined;
    if (key && steps && key.scale !== "chromatic") return new Set(steps.map((st) => (key.tonic + st) % 12));
    return new Set((guide?.summary.notes_used ?? []).map((n) => NOTE_NAMES.indexOf(n)));
  }, [guide, library]);
  const used = useMemo(() => new Set((guide?.summary.notes_used ?? []).map((n) => NOTE_NAMES.indexOf(n))), [guide]);

  // Song-time plan: where the take starts, the count-in, and session time zero (the first click).
  const picked = start ?? 0;
  const entry = setup.snap && timing?.entry !== null && timing?.entry !== undefined ? timing.entry : picked;
  const sectionEnd = Math.min(overview?.duration ?? Infinity, end ?? picked + DEFAULT_SECTION_S);
  const bpm = setup.bpm ?? timing?.tempo ?? 100;
  const plan = useMemo(() => countIn(entry, timing?.beats ?? [], bpm, setup.bpm !== null, setup.countIn, setup.keepClicking, sectionEnd),
                       [entry, timing, bpm, setup.bpm, setup.countIn, setup.keepClicking, sectionEnd]);
  const zero = setup.reference ? plan.first : 0;
  const duration = guide ? sectionEnd - zero : FREEFORM_S;
  const offset = setup.register === "auto" ? autoOffset ?? 0 : Number(setup.register);

  // Follow Ardour: take its reference and section whenever they change there.
  async function syncArdour(report: boolean) {
    try {
      const l = await api.ardour();
      setLink(l);
      setLinkError(null);
      const key = JSON.stringify([l.reference.path, l.span]);
      if (key !== appliedSpan.current && modeRef.current === "idle") {
        appliedSpan.current = key;
        update({ ardour: true, reference: l.reference.path,
                 ...(l.reference.path !== setup.reference ? { bpm: null } : {}),
                 ...(l.span ? { start: fmtTime(l.span[0], 2), end: fmtTime(l.span[1], 2) } : {}) });
      }
    } catch (e) {
      setLink(null);
      setLinkError((e as Error).message);
      if (report) notify(`Ardour: ${(e as Error).message}`, true);
    }
  }

  function followArdour() {
    appliedSpan.current = "";
    update({ ardour: true });
    void syncArdour(true);
  }

  useEffect(() => {
    if (new URLSearchParams(window.location.search).has("ardour")) {
      window.history.replaceState(null, "", window.location.pathname);
      followArdour();
    }
  }, []);

  useEffect(() => {
    if (!setup.ardour || mode !== "idle") return;
    void syncArdour(false);
    const id = setInterval(() => void syncArdour(false), ARDOUR_POLL_MS);
    return () => clearInterval(id);
  }, [setup.ardour, mode]);

  // The take from Ardour to compare: the one picked, else the newest that overlaps the section.
  const shown = useMemo(() => {
    if (!setup.ardour || !link || shownTake === "none") return null;
    return link.takes.find((t) => t.id === shownTake)
      ?? link.takes.find((t) => t.end > picked && t.start < sectionEnd) ?? null;
  }, [setup.ardour, link, shownTake, picked, sectionEnd]);
  const shownKey = shown ? `${shown.id}@${shown.start.toFixed(3)}+${(shown.end - shown.start).toFixed(3)}` : "";

  useEffect(() => {
    setTakeCurves(null);
    if (!shown) return;
    let stale = false;
    api.ardourTake(shown.id)
      .then((c) => {
        if (stale) return;
        setTakeCurves(c);
        setAutoOffset((o) => o ?? -c.octave);  // until you sing, your register is the take's
      })
      .catch((e: Error) => { if (!stale) notify(`Couldn't read the take from Ardour: ${e.message}`, true); });
    return () => { stale = true; };
  }, [shownKey]);

  useEffect(() => {
    setOverview(null);
    if (!setup.reference) return;
    let stale = false;
    api.overview(setup.reference).then((o) => { if (!stale) setOverview(o); }).catch(() => undefined);
    return () => { stale = true; };
  }, [setup.reference]);

  // Tempo, beats and the vocal entry around what you picked.
  useEffect(() => {
    setTiming(null);
    if (!setup.reference || !timesValid) return;
    let stale = false;
    api.timing(setup.reference, picked, end ?? undefined)
      .then((t) => { if (!stale) setTiming(t); })
      .catch((e: Error) => { if (!stale) notify(`Couldn't detect the timing: ${e.message}`, true); });
    return () => { stale = true; };
  }, [setup.reference, setup.start, setup.end]);

  // What the reference does over the section (from a little early, to cover the count-in).
  const guideFrom = Math.max(0, Math.floor(picked - MAX_LEAD_S));
  useEffect(() => {
    setGuide(null);
    setGuideAudio(null);
    if (!setup.reference || !timesValid || !Number.isFinite(sectionEnd)) return;
    let stale = false;
    setLoadingGuide(true);
    api.guide(setup.reference, guideFrom, sectionEnd)
      .then((g) => { if (!stale) setGuide(g); })
      .catch((e: Error) => { if (!stale) notify(`Couldn't read the reference: ${e.message}`, true); })
      .finally(() => { if (!stale) setLoadingGuide(false); });
    return () => { stale = true; };
  }, [setup.reference, guideFrom, sectionEnd]);

  // Its audio, decoded on the session's clock so playback and recording stay in step.
  useEffect(() => {
    if (!session || !guide) return;
    let stale = false;
    fetch(api.audioUrl(guide.path, guide.start, guide.end))
      .then((r) => r.arrayBuffer())
      .then((data) => session.ctx.decodeAudioData(data))
      .then((buffer) => { if (!stale) setGuideAudio(buffer); })
      .catch((e: Error) => notify(`Couldn't load the reference audio: ${e.message}`, true));
    return () => { stale = true; };
  }, [session, guide]);

  useEffect(() => {
    live.current = emptyLive(Math.ceil(duration / LIVE_HOP) + 1);
    setView([0, Math.min(duration, WINDOW_S)]);
  }, [duration]);

  useEffect(() => () => session?.close(), [session]);

  async function enableMic() {
    try {
      setSession(await LiveSession.open());
    } catch (e) {
      const err = e as Error;
      notify(err.name === "NotAllowedError" ? "Microphone access was blocked." : `Microphone: ${err.message}`, true);
    }
  }

  function begin(record: boolean) {
    if (!session) return;
    live.current = emptyLive(Math.ceil(duration / LIVE_HOP) + 1);
    session.setGuideVolume(setup.playGuide ? setup.guideVolume : 0);
    session.start(setup.playGuide ? guideAudio : null, record, {
      clicks: setup.reference ? plan.clicks.map((c) => c - zero) : [],
      guideAt: guide ? Math.max(0, guide.start - zero) : 0,
      guideOffset: guide ? Math.max(0, zero - guide.start) : 0,
      guideAudibleFrom: setup.refInCountIn ? 0 : entry - zero - 0.01,
    });
    setView([0, Math.min(duration, WINDOW_S)]);
    setMode(record ? "recording" : "practice");
  }

  async function stop() {
    if (!session?.playing) return;
    const recording = modeRef.current === "recording";
    const channels = await session.stop();
    if (!recording || !channels || channels[0].length < session.sampleRate * 0.5) {
      setMode("idle");
      return;
    }
    setMode("saving");
    try {
      const file = await api.upload(encodeWav(channels, session.sampleRate), "recording.wav", "take", true);
      await onLibraryChanged();
      const edges = live.current.edge.filter((v): v is number => v !== null);
      const attempt: Attempt = { file, edge: edges.length ? median(edges) : undefined };
      setAttempts((a) => [attempt, ...a]);
      if (setup.ardour && link) {
        // the recording starts at session zero; it runs late by the round trip through the browser
        const at = zero + link.reference.offset - session.latency();
        try {
          const placed = await api.ardourAddTake(file.path, at);
          attempt.ardour = `on "${placed.track}" at ${fmtTime(placed.at, 2)}`;
          setShownTake(null);
          void syncArdour(false);
        } catch (e) {
          notify(`Saved, but couldn't put it in Ardour: ${(e as Error).message}`, true);
        }
      }
      if (guide) {
        const row = (await api.compare(guide.path, [file.path], entry, sectionEnd)).rows[0];
        attempt.offBy = row.off_by;
        attempt.tempo = row.tempo;
        attempt.notes = row.notes_compared ? `${row.notes_matched}/${row.notes_compared}` : undefined;
        setAttempts((a) => a.map((x) => (x.file.path === file.path ? { ...attempt } : x)));
      }
    } catch (e) {
      notify(`Couldn't save the take: ${(e as Error).message}`, true);
    } finally {
      setMode("idle");
    }
  }

  function tap() {
    const now = performance.now();
    taps.current = [...taps.current.filter((t) => now - t < 3000), now];
    if (taps.current.length < 3) return;
    const gaps = taps.current.slice(1).map((t, i) => t - taps.current[i]);
    update({ bpm: Math.round((600000 / median(gaps))) / 10 });
  }

  // The reference's lanes on the session's timeline (zero = the first click).
  const refPitch = useMemo<Series>(() => (guide ? fromZero(guide.pitch.map((v) => (v === null ? null : v + offset)),
                                                           guide.hop, guide.start, zero) : []), [guide, offset, zero]);
  const refLevel = useMemo<Series>(() => (guide ? fromZero(guide.level.map((v) => (v === null ? null : v + LOUD_TARGET_DBFS)),
                                                           guide.env_hop, guide.start, zero) : []), [guide, zero]);
  const refEdge = useMemo<Series>(() => (guide ? fromZero(guide.edge, guide.env_hop, guide.start, zero) : []), [guide, zero]);

  // Every frame: read the mic; while running, write it onto the timeline and keep it in view.
  useFrame(() => {
    if (!session) return;
    const r = session.read();
    const L = live.current;
    if (r.edge !== null) recentEdge.current = [...recentEdge.current.slice(-20), r.edge];
    else if (r.midi === null) recentEdge.current = [];
    const nowMs = performance.now();
    if (r.midi !== null && scale.size) {
      const near = nearestPitch(r.midi, scale);
      keyLog.current.push({ at: nowMs, ok: Math.abs(r.midi - near) * 100 <= IN_KEY_CENTS });
    }
    if (session.playing) {
      if (r.time >= duration) {
        void stop();
        return;
      }
      const i = Math.round(r.time / LIVE_HOP);
      for (let k = Math.max(0, L.last + 1); k <= Math.min(i, L.pitch.length - 1); k++) {
        L.pitch[k] = r.midi;
        L.level[k] = r.level;
        L.edge[k] = r.edge;
      }
      L.last = Math.max(L.last, i);
      if (r.midi !== null && guide) {
        const theirs = refPitch[Math.round(r.time / guide.hop)];
        if (theirs !== null && theirs !== undefined) L.votes.push(r.midi - (theirs - offset));
      }
    }
    const now = performance.now();
    if (now - lastPaint.current < 66) return;  // ~15 fps is plenty for the readouts and curves
    lastPaint.current = now;
    keyLog.current = keyLog.current.filter((e) => now - e.at < IN_KEY_WINDOW_MS);
    setInKey(keyLog.current.length > 15 ? keyLog.current.filter((e) => e.ok).length / keyLog.current.length : null);
    setReading(r);
    setFrame((f) => f + 1);
    if (session.playing) {
      const left = Math.max(0, Math.min(r.time - WINDOW_S * 0.25, duration - WINDOW_S));
      setView([left, Math.min(duration, left + WINDOW_S)]);
      if (L.votes.length > 40) setAutoOffset(12 * Math.round(median(L.votes.slice(-400)) / 12));
    }
  });

  // The Ardour take on the session's timeline: where it sits there, or lined up note by note.
  const aligned = setup.takeView === "aligned" && !!takeCurves?.aligned;
  const takePitch = useMemo<{ values: Series; hop: number } | null>(() => {
    if (!takeCurves) return null;
    if (aligned && takeCurves.aligned) {
      const a = takeCurves.aligned;
      return { values: fromZero(a.pitch, a.hop, a.start, zero), hop: a.hop };
    }
    return { values: fromZero(takeCurves.placed.pitch, takeCurves.hop, takeCurves.placed.start, zero), hop: takeCurves.hop };
  }, [takeCurves, aligned, zero]);

  const curves = useMemo<Curve[]>(() => [
    ...(guide ? [{ values: refPitch, hop: guide.hop, color: COLORS.reference, width: 7, alpha: 0.4, maxJump: 2.5 }] : []),
    ...(takePitch ? [{ values: takePitch.values, hop: takePitch.hop, color: COLORS.take, width: 1.8, alpha: 0.9, maxJump: 3 }] : []),
    { values: [...live.current.pitch], hop: LIVE_HOP, color: COLORS.you, width: 2.2, maxJump: 3 },
  ], [guide, refPitch, takePitch, frame]);
  const range = useMemo<[number, number]>(() => {
    const sung = refPitch.filter((v): v is number => v !== null).sort((a, b) => a - b);
    if (!sung.length) return [45, 72];
    const lo = sung[Math.floor(sung.length * 0.02)], hi = sung[Math.floor(sung.length * 0.98)];
    return [Math.floor(lo) - 5, Math.ceil(hi) + 5];
  }, [refPitch]);

  const levelLanes = useMemo(() => [
    ...(guide ? [{ values: refLevel, hop: guide.env_hop, color: COLORS.reference, width: 5, alpha: 0.4 }] : []),
    { values: [...live.current.level], hop: LIVE_HOP, color: COLORS.you, width: 1.6 },
  ], [guide, refLevel, frame]);
  const edgeLanes = useMemo(() => [
    ...(guide ? [{ values: refEdge, hop: guide.env_hop, color: COLORS.reference, width: 5, alpha: 0.4 }] : []),
    { values: [...live.current.edge], hop: LIVE_HOP, color: COLORS.you, width: 1.6 },
  ], [guide, refEdge, frame]);

  const refNow = referenceNote(reading, guide, refPitch);
  const stripCentre = useMemo(() => {
    const sung = refPitch.filter((v): v is number => v !== null).sort((a, b) => a - b);
    return sung.length ? sung[Math.floor(sung.length / 2)] : reading?.midi ?? 57;
  }, [refPitch]);
  const running = mode === "practice" || mode === "recording";
  const ready = !!guide && (!setup.playGuide || !!guideAudio);
  return (
    <div className="workspace">
      <div className="editor record">
        <section className="card setup">
          <div className="setup-row">
            <label className="field">
              <span>Sing along with</span>
              <select value={setup.reference ?? ""} disabled={running || setup.ardour}
                      onChange={(e) => update({ reference: e.target.value || null, start: "", end: "", bpm: null })}>
                <option value="">Nothing (just record)</option>
                {references.map((f) => <option key={f.path} value={f.path}>{f.name}</option>)}
              </select>
            </label>
            {setup.reference && (
              <>
                <label className="field">
                  <span>Your register</span>
                  <select value={setup.register} onChange={(e) => update({ register: e.target.value })}>
                    <option value="auto">Detect{setup.register === "auto" && autoOffset !== null ? ` (${octaves(autoOffset)})` : ""}</option>
                    <option value="0">Same as the reference</option>
                    <option value="-12">An octave lower</option>
                    <option value="12">An octave higher</option>
                  </select>
                </label>
                <label className="field" title="Play the reference in your headphones while you sing (use headphones, or it bleeds into the take)">
                  <span>Reference in headphones</span>
                  <span className="row">
                    <input type="checkbox" checked={setup.playGuide} onChange={(e) => update({ playGuide: e.target.checked })} />
                    <input type="range" min={0} max={1} step={0.05} value={setup.guideVolume} disabled={!setup.playGuide}
                           onChange={(e) => { update({ guideVolume: Number(e.target.value) }); session?.setGuideVolume(Number(e.target.value)); }} />
                  </span>
                </label>
              </>
            )}
          </div>

          <div className="setup-row ardour-row">
            {!setup.ardour ? (
              <button onClick={followArdour} disabled={running}
                      title="Take the reference, the section and a take to compare from the session open in Ardour, and send new takes back to it">
                ⇄ Follow Ardour
              </button>
            ) : (
              <>
                <span className="small">
                  {link
                    ? <>Following <b>{link.session}</b> in Ardour · section from {link.span_from ?? "the whole reference"} (select a range, a region or the loop there to change it)</>
                    : <span className="error">Ardour isn't answering{linkError ? `: ${linkError}` : ""}</span>}
                </span>
                {link && link.takes.length > 0 && (
                  <label className="field inline">
                    <span>Compare with</span>
                    <select value={shownTake ?? shown?.id ?? "none"} onChange={(e) => setShownTake(e.target.value)}>
                      <option value="none">no take</option>
                      {link.takes.map((tk) => (
                        <option key={tk.id} value={tk.id}>{tk.track}: {tk.name} @ {fmtTime(tk.start + link.reference.offset, 1)}</option>
                      ))}
                    </select>
                  </label>
                )}
                {takeCurves?.aligned && (
                  <span className="segmented" title="As placed: where it sits in Ardour, so timing shows. Note by note: lined up with the reference, so only pitch shows.">
                    <button className={!aligned ? "on" : ""} onClick={() => update({ takeView: "placed" })}>As placed</button>
                    <button className={aligned ? "on" : ""} onClick={() => update({ takeView: "aligned" })}>Note by note</button>
                  </span>
                )}
                <button className="link" disabled={running} onClick={() => update({ ardour: false })}>stop following</button>
              </>
            )}
          </div>

          {setup.reference && overview && (
            <SliceSelector peaks={overview.peaks} duration={overview.duration} selection={[picked, sectionEnd]}
                           onSelect={([a, b]) => !running && update({ start: fmtTime(a, 2), end: fmtTime(b, 2) })}
                           entry={timing ? entry : null} countInFrom={timing && setup.countIn ? plan.first : null}
                           beats={timing?.beats ?? []} clicks={timing ? plan.clicks.slice(0, setup.countIn) : []} />
          )}

          {setup.reference && (
            <div className="setup-row">
              <label className="field time">
                <span>From</span>
                <input value={setup.start} placeholder="0:00" disabled={running} className={start === null ? "invalid" : ""}
                       onChange={(e) => update({ start: e.target.value })} />
              </label>
              <label className="field time">
                <span>To</span>
                <input value={setup.end} placeholder={`+${DEFAULT_SECTION_S}s`} disabled={running} className={end === null ? "invalid" : ""}
                       onChange={(e) => update({ end: e.target.value })} />
              </label>
              <label className="check small" title="Start the take exactly where the reference's voice comes in after From">
                <input type="checkbox" checked={setup.snap} onChange={(e) => update({ snap: e.target.checked })} /> Start at the voice
              </label>
              <label className="field" title="Detected from the reference; check it with Practice and fix it here if the clicks don't sit with it">
                <span>Tempo{setup.bpm === null ? " (detected)" : ""}</span>
                <span className="row tempo">
                  <input type="number" min={30} max={300} step={0.1} value={Math.round(bpm * 10) / 10} disabled={running}
                         onChange={(e) => { const v = Number(e.target.value); if (v >= 30 && v <= 300) update({ bpm: v }); }} />
                  <button disabled={running} onClick={() => update({ bpm: bpm / 2 })} title="Half time">½</button>
                  <button disabled={running} onClick={() => update({ bpm: bpm * 2 })} title="Double time">2×</button>
                  <button disabled={running} onClick={tap} title="Tap along with the reference a few times">Tap</button>
                  {setup.bpm !== null && timing?.tempo && (
                    <button className="link" disabled={running} onClick={() => update({ bpm: null })}>auto</button>
                  )}
                </span>
              </label>
              <label className="field">
                <span>Count-in</span>
                <select value={setup.countIn} disabled={running} onChange={(e) => update({ countIn: Number(e.target.value) })}>
                  {[0, 2, 4, 8].map((n) => <option key={n} value={n}>{n ? `${n} clicks` : "none"}</option>)}
                </select>
              </label>
              <label className="check small">
                <input type="checkbox" checked={setup.keepClicking} disabled={running}
                       onChange={(e) => update({ keepClicking: e.target.checked })} /> Click all the way through
              </label>
              <label className="check small">
                <input type="checkbox" checked={setup.refInCountIn} disabled={running}
                       onChange={(e) => update({ refInCountIn: e.target.checked })} /> Reference during count-in
              </label>
            </div>
          )}

          <div className="row">
            {!session ? (
              <button className="primary" onClick={enableMic}>Enable microphone</button>
            ) : running ? (
              <button className="primary" onClick={() => void stop()}>■ Stop{mode === "recording" ? " & save" : ""}</button>
            ) : (
              <>
                <button className="record" onClick={() => begin(true)} disabled={mode === "saving" || (!!setup.reference && !ready)}>
                  ● Record
                </button>
                <button onClick={() => begin(false)} disabled={mode === "saving" || !ready}>▶ Practice</button>
              </>
            )}
            <span className="muted small">
              {loadingGuide && "Reading the reference…"}
              {mode === "recording" && <span className="rec-dot">● recording </span>}
              {mode === "saving" && "Saving and scoring…"}
              {!loadingGuide && mode !== "saving" && (setup.reference
                ? (timing
                    ? `Voice comes in at ${fmtTime(entry, 2)} (${entryFeel(plan.pickup, plan.period)}) · ${Math.round(bpm * 10) / 10} BPM${setup.countIn ? ` · ${setup.countIn} clicks, the last one a beat before` : ""}. Wear headphones.`
                    : "Detecting the timing…")
                : "Use headphones so nothing bleeds into your take.")}
            </span>
          </div>
        </section>

        <div className="editor-head">
          <span className="legend">
            <i style={{ background: COLORS.reference }} /> reference{offset ? ` (${octaves(offset)})` : ""}
            {shown && takeCurves && (
              <>
                <i style={{ background: COLORS.take }} /> {shown.track} {aligned ? "(lined up note by note)" : "(as placed in Ardour)"}
                {takeCurves.tempo && ` · sung at ${pct(takeCurves.tempo)} of the reference's speed`}
              </>
            )}
            <i style={{ background: COLORS.you }} /> you
          </span>
        </div>
        <PitchView duration={duration} curves={curves} view={view} onView={setView} selection={null}
                   onSelect={() => undefined} onSeek={() => undefined} player={session ?? IDLE_CLOCK} range={range}
                   timeOffset={zero} />
        <LaneView label="Level" range={[-60, 0]} ticks={[-48, -36, -24, -12, 0]} series={levelLanes} view={view}
                  clock={session ?? IDLE_CLOCK} target={LOUD_TARGET_DBFS} />
        <LaneView label="Edge" range={[-40, 10]} ticks={[-30, -20, -10, 0]} series={edgeLanes} view={view}
                  clock={session ?? IDLE_CLOCK} target={guide?.typical_edge ?? undefined} />
        <p className="hint">
          Orange is the reference, teal is you{shown ? ", purple the take from Ardour" : ""}. <b>Level</b>: the orange line is where the reference's loudness would sit if your loud
          singing peaked around {LOUD_TARGET_DBFS} dBFS. <b>Edge</b>: upper-harmonic energy, the sound of support and belt; sing
          brighter and with more power to push the teal up toward the dashed line.
        </p>
      </div>

      <aside className="inspector">
        <ScaleMeter midi={reading?.midi ?? null} scale={scale} used={used} keyLabel={guide?.summary.key.label ?? null}
                    reference={refNow} centre={stripCentre} inKey={inKey} />
        <InputCard reading={reading} />
        <EdgeCard yours={recentEdge.current.length ? median(recentEdge.current) : null} typical={guide?.typical_edge ?? null} />
        <section className="card">
          <h3>Takes this session</h3>
          {attempts.length === 0 && <p className="muted small">Record a take and it'll be scored against the reference here.</p>}
          {attempts.map((a) => (
            <div key={a.file.path} className="attempt">
              <div className="row">
                <b className="name" title={a.file.name}>{a.file.name.replace(/\.wav$/, "")}</b>
                <button className="link" onClick={() => onOpen(a.file.path)}>open in Tune →</button>
              </div>
              <span className="muted small">
                {a.offBy === undefined ? (guide ? "scoring…" : "saved") : `off by ${Math.round(a.offBy)}¢ · notes ${a.notes ?? "–"} · tempo ${pct(a.tempo)}`}
                {a.edge !== undefined && ` · edge ${a.edge.toFixed(0)} dB`}
                {a.ardour && ` · in Ardour ${a.ardour}`}
              </span>
            </div>
          ))}
        </section>
      </aside>
    </div>
  );
}

const IDLE_CLOCK = { position: () => 0, playing: false };

/** Count-in clicks that lead straight into the voice: a beat apart, with the voice coming in
    exactly one beat after the last click ("1, 2, 3, 4, sing"), wherever it falls against the
    song's beat. A click track through the take, if wanted, then follows the song's own beats
    from the entry on, so a pickup stays a pickup. Times are song time; `pickup` is how far
    the entry sits before (-) or after (+) the song's beat, 0 if it's on it. */
function countIn(entry: number, beats: Series, bpm: number, manualTempo: boolean, count: number,
                 keepClicking: boolean, end: number) {
  const period = 60 / bpm;
  const first = count ? entry - count * period : entry - 0.5;
  const clicks = Array.from({ length: count }, (_, k) => first + k * period);
  const grid = beats.filter((b): b is number => b !== null);
  const nearest = grid.reduce<number | null>((best, b) => (best === null || Math.abs(b - entry) < Math.abs(best - entry) ? b : best), null);
  const off = nearest === null ? 0 : entry - nearest;
  const pickup = Math.abs(off) > ON_BEAT * period ? off : 0;
  if (keepClicking) {
    if (!manualTempo && grid.length) clicks.push(...grid.filter((b) => b >= entry - ON_BEAT * period && b < end));
    else {
      const anchor = pickup && nearest !== null ? nearest : entry;
      for (let t = anchor + Math.ceil((entry - ON_BEAT * period - anchor) / period) * period; t < end; t += period) clicks.push(t);
    }
  }
  return { first, clicks, period, pickup };
}

/** A series that starts at song time `start`, re-based so index 0 is song time `zero`. */
function fromZero(values: Series, hop: number, start: number, zero: number): Series {
  const shift = Math.round((start - zero) / hop);
  return shift >= 0 ? [...new Array<number | null>(shift).fill(null), ...values] : values.slice(-shift);
}

function emptyLive(n: number) {
  return { pitch: new Array<number | null>(n).fill(null), level: new Array<number | null>(n).fill(null),
           edge: new Array<number | null>(n).fill(null), last: -1, votes: [] as number[] };
}

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

function octaves(semitones: number): string {
  if (!semitones) return "same octave";
  const n = Math.abs(semitones / 12);
  return `${n} octave${n === 1 ? "" : "s"} ${semitones < 0 ? "lower" : "higher"}`;
}

/** The reference's note at this moment, moved to the octave you're singing in (null when it isn't singing). */
function referenceNote(reading: Reading | null, guide: Guide | null, refPitch: Series): number | null {
  if (!reading || reading.midi === null || !guide) return null;
  const theirs = refPitch[Math.round(reading.time / guide.hop)];
  if (theirs === null || theirs === undefined) return null;
  return Math.round(reading.midi + (((theirs - reading.midi + 6) % 12 + 12) % 12 - 6));
}

function nearestPitch(midi: number, pitchClasses: Set<number>): number {
  let best = Math.round(midi);
  for (let m = Math.floor(midi) - 2; m <= Math.ceil(midi) + 2; m++) {
    if (pitchClasses.has(((m % 12) + 12) % 12) && (!pitchClasses.has(((best % 12) + 12) % 12) || Math.abs(m - midi) < Math.abs(best - midi))) best = m;
  }
  return best;
}

function entryFeel(pickup: number, period: number): string {
  if (!pickup) return "on the beat";
  const beats = Math.abs(pickup) / period;
  const amount = beats > 0.4 && beats < 0.6 ? "half a beat" : `${Math.round(Math.abs(pickup) * 1000)} ms`;
  return pickup < 0 ? `${amount} before the beat, a pickup` : `${amount} after the beat`;
}

function InputCard({ reading }: { reading: Reading | null }) {
  const peak = reading?.peak ?? -90;
  const widthOf = (db: number) => `${Math.max(0, Math.min(100, ((db + 60) / 60) * 100))}%`;
  const advice = peak > -1 ? "Clipping: back off the mic or turn the input down."
    : reading && reading.midi !== null && peak < -30 ? "Quiet: get closer or turn the input up."
    : `Loud parts should peak around ${LOUD_TARGET_DBFS} dBFS.`;
  return (
    <section className="card">
      <h3>Input</h3>
      <div className="meter wide">
        <div className={peak > -1 ? "hot" : ""} style={{ width: widthOf(peak) }} />
        <i style={{ left: widthOf(LOUD_TARGET_DBFS) }} />
      </div>
      <p className={peak > -1 ? "error small" : "muted small"}>{advice}</p>
    </section>
  );
}

function EdgeCard({ yours, typical }: { yours: number | null; typical: number | null }) {
  const position = (db: number) => `${Math.max(0, Math.min(100, ((db + 40) / 50) * 100))}%`;
  const gap = yours !== null && typical !== null ? typical - yours : null;
  return (
    <section className="card">
      <h3>Edge</h3>
      <div className="meter wide edge">
        {yours !== null && <div style={{ width: position(yours) }} />}
        {typical !== null && <i style={{ left: position(typical) }} title="the reference" />}
      </div>
      <p className="muted small">
        {gap === null ? "How much bite your voice has: sing to see it."
          : gap > 6 ? "Darker than the reference: more support, brighter vowels, a bit more belt."
          : gap < -6 ? "Brighter than the reference: you can ease off."
          : "Right around the reference."}
      </p>
    </section>
  );
}
