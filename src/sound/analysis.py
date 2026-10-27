"""Measuring a vocal performance: tuning, key, notes, and how it lines up with a reference."""

from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np

from .pitch import PitchTrack
from .scales import USED_NOTE_SHARE, estimate_key, hz_to_midi, parse_key, pitch_class_shares
from .tune import TuneSettings, correction

MIN_NOTE_S = 0.08
KERNEL_HARMONICS = 6      # sharpness of the octave-blind pitch match used for alignment
ALIGN_DOWNSAMPLE = 4      # align on ~23 ms frames
MAX_DTW_CELLS = 20_000_000
SILENCE_COST = 0.1        # both silent: cheap, but not free, so silences don't soak up warping
STEADY_TUNING = 0.5       # tuning consistency above which a singer has their own reference pitch
KEY_CONFIDENCE = 0.6      # below this a detected key isn't trusted for tuning
REF_LEAD_IN_S = 5.0
REF_TAIL_S = 10.0


def fold(semitones: np.ndarray) -> np.ndarray:
    """Wrap a pitch difference into -6..+6 semitones, ignoring which octave it's in."""
    return semitones - 12 * np.round(semitones / 12)


@dataclass
class Summary:
    duration: float
    voiced: float              # fraction of frames with a pitch
    low: float                 # 2nd / 98th percentile pitch, MIDI
    high: float
    tuning_cents: float        # average offset from the A4 grid, -50..+50
    tuning_consistency: float  # 0..1; near 0 means no steady offset, just scatter
    key: tuple[int, str, float]
    notes_used: list[int]      # pitch classes with a real share of the held notes, most used first
    in_tune: float             # fraction of voiced frames within 10 cents of a semitone
    median_dev: float          # cents from the nearest semitone


def summarize(track: PitchTrack, a4: float = 440.0) -> Summary:
    midi = hz_to_midi(track.f0[track.voiced], a4)
    duration = float(track.times[-1] + track.hop_s) if len(track.times) else 0.0
    if midi.size == 0:
        return Summary(duration, 0.0, np.nan, np.nan, 0.0, 0.0, (0, "major", 0.0), [], 0.0, np.nan)
    dev = fold(midi - np.round(midi))
    phasor = np.mean(np.exp(2j * np.pi * dev))
    offset = float(np.angle(phasor) / (2 * np.pi))

    # Judge the key from held notes weighted by length, so slides don't count as notes.
    # Measure them against the singer's own reference pitch only if they keep a steady one.
    own = offset if np.abs(phasor) >= STEADY_TUNING else 0.0
    held = notes(track, a4)
    if held:
        pitches = np.array([n.midi for n in held]) - own
        weights = np.array([n.end - n.start for n in held])
    else:
        pitches, weights = midi - own, None
    shares = pitch_class_shares(pitches, weights)
    return Summary(
        duration=duration,
        voiced=float(track.voiced.mean()),
        low=float(np.percentile(midi, 2)),
        high=float(np.percentile(midi, 98)),
        tuning_cents=100 * offset,
        tuning_consistency=float(np.abs(phasor)),
        key=estimate_key(pitches, weights),
        notes_used=[int(pc) for pc in np.argsort(shares)[::-1] if shares[pc] >= USED_NOTE_SHARE],
        in_tune=float(np.mean(np.abs(dev) < 0.1)),
        median_dev=float(100 * np.median(np.abs(dev))),
    )


@dataclass
class Note:
    start: float
    end: float
    midi: float    # median sung pitch


def notes(track: PitchTrack, a4: float = 440.0) -> list[Note]:
    """Held notes: stretches that stay on one semitone for at least MIN_NOTE_S."""
    detected, target, _ = correction(track, TuneSettings(speed_ms=0, a4=a4))
    min_frames = max(1, round(MIN_NOTE_S / track.hop_s))
    out = []
    for a, b in track.segments():
        tgt = target[a:b]
        bounds = np.r_[0, np.flatnonzero(np.diff(tgt)) + 1, len(tgt)]
        for s, e in zip(bounds[:-1], bounds[1:]):
            if e - s >= min_frames:
                out.append(Note(float(track.times[a + s]), float(track.times[a + e - 1] + track.hop_s),
                                float(np.median(detected[a + s:a + e]))))
    return out


@dataclass
class Alignment:
    """A time map from the take onto the reference (monotonic, may speed up or slow down)."""
    take_t: np.ndarray   # strictly increasing
    ref_t: np.ndarray
    octave: int          # semitones to add to the take to reach the reference's register

    def ref_time(self, t: np.ndarray) -> np.ndarray:
        return np.interp(np.asarray(t, dtype=float), self.take_t, self.ref_t)

    @classmethod
    def shifted(cls, offset: float, octave: int = 0) -> Alignment:
        """A plain constant offset: ref_t = take_t + offset."""
        return cls(np.array([0.0, 1e6]), np.array([offset, 1e6 + offset]), octave)


def align(take: PitchTrack, ref: PitchTrack, a4: float = 440.0) -> Alignment:
    """Map the take's timeline onto the reference by matching pitch contours.

    Uses subsequence dynamic time warping, so the take may sit anywhere in the reference and
    drift faster or slower than it (anywhere from half to double speed). The match ignores
    octaves (singing the part an octave down is fine, as are octave slips in the reference's
    pitch track) and any constant tuning offset between the two, so a take that is a
    quarter-tone off aligns the same way before and after it is tuned.
    """
    hop = take.hop_s
    step = max(ALIGN_DOWNSAMPLE, int(np.ceil(np.sqrt(len(take.f0) * len(ref.f0) / MAX_DTW_CELLS))))
    tm, rm = hz_to_midi(take.f0, a4)[::step], hz_to_midi(ref.f0, a4)[::step]

    # The take's overall tuning relative to the reference, from the best offset-blind match.
    zt, zr = _phasors(tm), _phasors(rm)
    n = len(tm) + len(rm)
    spectra = [np.fft.ifft(np.fft.fft(r, n) * np.conj(np.fft.fft(t, n))) for t, r in zip(zt, zr)]
    best = int(np.argmax(sum(np.abs(s) for s in spectra)))
    tuning = np.angle(spectra[0][best]) / (2 * np.pi) * 12

    t_voiced, r_voiced = np.isfinite(tm), np.isfinite(rm)
    d = np.nan_to_num(rm)[None, :] - np.nan_to_num(tm)[:, None] - tuning
    match = sum(np.cos(2 * np.pi * h * d / 12) for h in range(1, KERNEL_HARMONICS + 1)) / KERNEL_HARMONICS
    both, either = t_voiced[:, None] & r_voiced[None, :], t_voiced[:, None] | r_voiced[None, :]
    cost = np.where(both, 1 - match, np.where(either, 1.0, SILENCE_COST))
    _, path = librosa.sequence.dtw(C=cost, subseq=True, step_sizes_sigma=np.array([[1, 1], [1, 2], [2, 1]]))
    path = path[::-1]

    al = Alignment(take_t=path[:, 0] * step * hop, ref_t=path[:, 1] * step * ref.hop_s, octave=0)
    diffs = _paired_diffs(take, ref, al, a4, unfolded=True)
    al.octave = -int(12 * np.round(np.median(diffs) / 12)) if diffs.size else 0
    return al


def tempo(take: PitchTrack, al: Alignment) -> float:
    """Reference time covered per second of singing: 1.1 = you're 10% quicker than the reference."""
    sung = take.times[take.voiced]
    if sung.size < 2 or sung[-1] <= sung[0]:
        return 1.0
    return float((al.ref_time(sung[-1]) - al.ref_time(sung[0])) / (sung[-1] - sung[0]))


def reference_on_take(take: PitchTrack, ref: PitchTrack, al: Alignment, a4: float = 440.0) -> np.ndarray:
    """The reference's pitch at each of the take's frames, moved into the take's octave (for display)."""
    take_midi, ref_midi = hz_to_midi(take.f0, a4), hz_to_midi(ref.f0, a4)
    idx = np.round(al.ref_time(take.times) / ref.hop_s).astype(int)
    inside = (idx >= 0) & (idx < len(ref_midi))
    theirs = np.full_like(take_midi, np.nan)
    theirs[inside] = ref_midi[idx[inside]] - al.octave
    # Sit each reference frame on the take's note where there is one, otherwise near the take's middle.
    anchor = np.where(np.isfinite(take_midi), take_midi, np.nanmedian(take_midi))
    return anchor + fold(theirs - anchor)


def _phasors(midi: np.ndarray) -> list[np.ndarray]:
    voiced = np.isfinite(midi)
    base = np.where(voiced, np.exp(2j * np.pi * np.nan_to_num(midi) / 12), 0)
    return [base ** h for h in range(1, KERNEL_HARMONICS + 1)]


def _paired_diffs(take: PitchTrack, ref: PitchTrack, al: Alignment, a4: float,
                  unfolded: bool = False, frames: slice = slice(None)) -> np.ndarray:
    """Take minus reference, in semitones, for every frame where both are singing."""
    t_midi = hz_to_midi(take.f0, a4)[frames]
    idx = np.round(al.ref_time(take.times[frames]) / ref.hop_s).astype(int)
    inside = (idx >= 0) & (idx < len(ref.f0))
    r_midi = np.full_like(t_midi, np.nan)
    r_midi[inside] = hz_to_midi(ref.f0[idx[inside]], a4)
    d = t_midi - r_midi
    d = d[np.isfinite(d)]
    return d if unfolded else fold(d)


@dataclass
class Agreement:
    frames: int           # frames where both are singing
    median_abs: float     # cents
    within_25: float      # fraction of frames
    within_50: float
    bias: float           # median cents sharp (+) or flat (-) of the reference
    notes_matched: int
    notes_compared: int


def agreement(take: PitchTrack, ref: PitchTrack, al: Alignment, a4: float = 440.0) -> Agreement:
    d = _paired_diffs(take, ref, al, a4)
    matched = compared = 0
    for note in notes(take, a4):
        sel = (take.times >= note.start) & (take.times < note.end)
        frames = slice(int(np.argmax(sel)), int(np.argmax(sel)) + int(sel.sum()))
        nd = _paired_diffs(take, ref, al, a4, frames=frames)
        if nd.size >= 0.5 * sel.sum():
            compared += 1
            matched += abs(np.median(nd)) < 0.5
    if d.size == 0:
        return Agreement(0, np.nan, 0.0, 0.0, np.nan, matched, compared)
    return Agreement(
        frames=int(d.size),
        median_abs=float(100 * np.median(np.abs(d))),
        within_25=float(np.mean(np.abs(d) < 0.25)),
        within_50=float(np.mean(np.abs(d) < 0.5)),
        bias=float(100 * np.median(d)),
        notes_matched=int(matched),
        notes_compared=int(compared),
    )


@dataclass
class KeyChoice:
    """What to tune to, and how that was decided."""
    tonic: int
    scale: str
    source: str                      # "none", "fixed", "auto", or "auto-unsure" (fell back to chromatic)
    estimate: tuple[int, str, float]  # the detector's best guess, whatever was chosen


def choose_key(track: PitchTrack, key: str | None, scale: str | None = None, a4: float = 440.0) -> KeyChoice:
    """key None = every semitone; 'auto' = detect; otherwise a key name like 'F#' or 'Am'.

    An unsure detection falls back to every semitone: a wrong scale drags notes a full
    semitone the wrong way, which snapping to the nearest semitone never does.
    """
    estimate = summarize(track, a4).key if track.voiced.any() else (0, "major", 0.0)
    if key is None:
        return KeyChoice(0, scale or "chromatic", "none", estimate)
    if key == "auto":
        if estimate[2] < KEY_CONFIDENCE:
            return KeyChoice(0, "chromatic", "auto-unsure", estimate)
        return KeyChoice(estimate[0], scale or estimate[1], "auto", estimate)
    tonic, mode = parse_key(key)
    return KeyChoice(tonic, scale or mode or "major", "fixed", estimate)


def reference_window(ref_start: float | None, ref_end: float | None, longest_take: float) -> tuple[float, float]:
    """The stretch of a reference to search: a little before the given start (in case the
    take begins early) to comfortably past where the longest take could end."""
    start = ref_start or 0.0
    return max(0.0, start - REF_LEAD_IN_S), ref_end or start + longest_take + REF_TAIL_S


@dataclass
class Comparison:
    name: str
    track: PitchTrack
    alignment: Alignment | None     # None when the take has no pitched voice
    agreement: Agreement | None
    first_note: float | None        # where the take's first note lands, seconds into the reference track
    tempo: float | None


def placement(take: PitchTrack, al: Alignment) -> float:
    """Where the take's start sits on the reference's timeline (seconds into the reference
    track): the one shift that fits the alignment best overall (median over the take's voiced
    frames), for laying the two out together in a DAW."""
    t = take.times[take.voiced]
    return float(np.median(al.ref_time(t) - t)) if t.size else float(al.ref_t[0] - al.take_t[0])


def compare(ref: PitchTrack, takes: list[tuple[str, PitchTrack]], a4: float = 440.0) -> list[Comparison]:
    """Line each take up with the reference and score it.

    Tuned versions of one recording have identical timing, so takes of the same length all
    use the alignment of the first one; then no version gets a timing fit flattering its pitch.
    """
    shared: dict[int, Alignment] = {}
    out = []
    for name, track in takes:
        if not track.voiced.any():
            out.append(Comparison(name, track, None, None, None, None))
            continue
        al = shared.get(len(track.f0)) or align(track, ref, a4)
        shared.setdefault(len(track.f0), al)
        first = float(al.ref_time(track.times[track.voiced][0]))
        out.append(Comparison(name, track, al, agreement(track, ref, al, a4), first, tempo(track, al)))
    return out


EDGE_BAND = (1000.0, 4000.0)      # presence: where a belted voice's upper harmonics put their energy
BODY_BAND = (80.0, 1000.0)        # fundamental and low harmonics


def level_and_edge(mono: np.ndarray, sr: int, track: PitchTrack, hop: float = 0.02,
                   window: float = 0.05) -> tuple[np.ndarray, np.ndarray]:
    """Per-hop loudness and edge of a vocal, for showing a performance over time.

    level: dB relative to the loud singing (p95 of sung moments), floored at -60.
    edge:  dB of presence-band energy over body-band energy while singing (NaN otherwise).
    The more support and belt in a voice, the higher its edge; soft or breathy singing
    puts nearly everything in the body band.
    """
    step, size = max(1, int(hop * sr)), int(window * sr)
    starts = np.arange(0, max(1, len(mono) - size + 1), step)
    frames = np.stack([mono[s:s + size] for s in starts]) if len(mono) >= size else mono[None, :size]
    centre = (starts + size / 2) / sr
    sung = np.interp(centre, track.times, track.voiced.astype(float)) > 0.5

    level = 20 * np.log10(np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-9)
    loud = np.percentile(level[sung], 95) if sung.any() else level.max()
    level = np.maximum(level - loud, -60.0)

    spectrum = np.abs(np.fft.rfft(frames * np.hanning(frames.shape[1]), axis=1)) ** 2
    freqs = np.fft.rfftfreq(frames.shape[1], 1 / sr)
    band = lambda lo_hi: spectrum[:, (freqs >= lo_hi[0]) & (freqs < lo_hi[1])].sum(axis=1)
    edge = 10 * np.log10((band(EDGE_BAND) + 1e-12) / (band(BODY_BAND) + 1e-12))
    edge[~sung] = np.nan
    return level, edge
