"""Pitch correction: decide what note each moment should be, then move it there."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import convolve1d, median_filter
from scipy.signal import lfilter

from . import pitch, psola
from .scales import hz_to_midi, nearest, scale_notes

NOTE_DECISION_FRAMES = 7   # ~40 ms median smoothing before choosing a note
OPENING_FRAMES = 26        # ~150 ms: a full vibrato cycle, or a scoop into the note
STYLES = ("autotune", "natural")

# natural style
NOTE_SMOOTH_S = 0.25       # average vibrato away before deciding where one note ends and the next begins
NOTE_SPLIT_ST = 0.6        # semitones the pitch must move (and stay moved) to count as a new note
MIN_NATURAL_NOTE_S = 0.10  # anything shorter is a slide or a blip, not a note
ORNAMENT_S = 0.15          # notes shorter than this (dips, quick runs) keep their shape relative to their neighbours
NOTE_TRIM = 0.2            # a note's centre ignores its first and last fifth (scoops, releases)
SETTLE_WITHIN_S = 0.35     # a note shorter than this is tuned on its own only if it isn't a movement:
MOVEMENT_ST = 0.5          # travelling (scoop, fall) or dipping by more than this many semitones
EVEN_SHARE = 0.15          # between notes, this much of the change is spread evenly in time, the rest follows the slide
FINAL_SMOOTH_S = 0.03      # rounds off the corners of the correction curve
HUMANIZE_MAX = 0.12        # semitones: the most a note keeps of its own lean
FULL_CORRECTION_S = 0.4    # notes this long or longer get their full correction...
SHORT_NOTE_AMOUNT = 0.8    # ...and the shortest tuned notes this fraction of it
DRIFT_WINDOW_S = 0.5       # wander slower than this counts as drift; vibrato (4-7 Hz) averages out


@dataclass
class TuneSettings:
    tonic: int = 0
    scale: str = "chromatic"
    speed_ms: float = 25.0      # retune speed: 0 = instant (robotic), larger = gentler
    strength: float = 1.0       # 0..1, fraction of the way to the target note
    hysteresis: float = 0.15    # semitones past the midpoint before switching notes
    a4: float = 440.0
    style: str = "autotune"     # "autotune": pull every moment to a note; "natural": move whole notes
    drift: float = 0.0          # natural style: 0..1, how much slow wander within a note to straighten
    humanize: float = 0.25      # natural style: 0..1, how much of each note's own lean to keep (capped)


@dataclass
class TuneResult:
    audio: np.ndarray
    track: pitch.PitchTrack
    detected: np.ndarray    # sung pitch per frame, MIDI, NaN when unvoiced
    target: np.ndarray      # chosen note per frame
    corrected: np.ndarray   # pitch of the output per frame


def correction(track: pitch.PitchTrack, settings: TuneSettings) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-frame (detected, target, shift) in MIDI / semitones; shift is 0 where unvoiced."""
    if settings.style not in STYLES:
        raise ValueError(f"unknown style {settings.style!r}; choose from {', '.join(STYLES)}")
    allowed = scale_notes(settings.tonic, settings.scale)
    detected = hz_to_midi(track.f0, settings.a4)
    target = np.full_like(detected, np.nan)
    shift = np.zeros_like(detected)
    if settings.style == "natural":
        target, shift = _natural(detected, track, allowed, settings)
        return detected, target, shift
    tau = settings.speed_ms / 1000.0
    alpha = float(np.exp(-track.hop_s / tau)) if tau > 0 else 0.0

    for a, b in track.segments():
        sung = detected[a:b]
        smoothed = median_filter(sung, size=NOTE_DECISION_FRAMES, mode="nearest")
        notes = _hold_notes(smoothed, allowed, settings.hysteresis)
        needed = notes - sung
        if alpha > 0:
            # Low-passing the correction means slow drift gets fixed while fast wiggles
            # (vibrato, scoops) survive in proportion to the retune time.
            needed, _ = lfilter([1 - alpha], [1, -alpha], needed, zi=[alpha * needed[0]])
        target[a:b] = notes
        shift[a:b] = settings.strength * needed
    return detected, target, shift


@dataclass
class _Held:
    start: int       # frames within its phrase
    stop: int
    core: slice      # the settled middle, without scoop and release
    centre: float    # MIDI
    target: float
    own: float = 0.0  # semitones of the note's own lean it keeps (humanize)


def _natural(detected: np.ndarray, track: pitch.PitchTrack, allowed: np.ndarray,
             settings: TuneSettings) -> tuple[np.ndarray, np.ndarray]:
    """Note-by-note correction: (target, shift) per frame.

    Each note is moved as a whole, by the distance from its centre to the nearest allowed
    note, so its vibrato, scoops and slides keep their exact shape and just land in tune.
    Done the way a careful person tunes by hand:
      - the move is pinned to the settled middle of each note, and the change from one
        note's amount to the next happens along the slide between them, in step with how
        fast the voice is moving, where it can't be heard;
      - with humanize, each note keeps part of its own lean (relative to how the singer
        leans overall), so not every note lands dead centre;
      - short notes are corrected more lightly than held ones;
      - optionally, slow wander inside a note (not its vibrato) is partly straightened.
    """
    hop = track.hop_s
    target = np.full_like(detected, np.nan)
    shift = np.zeros_like(detected)
    phrases = [(a, b, _held_notes(detected[a:b], allowed, hop)) for a, b in track.segments()]

    held = [n for *_, notes in phrases for n in notes]
    if not held:
        return target, shift
    # Humanize: each note keeps part of how it leans compared with the singer's usual lean,
    # re-centred so the take as a whole stays in tune.
    offs = np.array([n.centre - n.target for n in held])
    lengths = np.array([n.stop - n.start for n in held], dtype=float)
    owns = np.clip(settings.humanize * (offs - _weighted_median(offs, lengths)), -HUMANIZE_MAX, HUMANIZE_MAX)
    owns -= np.average(owns, weights=lengths)
    for n, own in zip(held, owns):
        n.own = float(own)

    for a, b, notes in phrases:
        if not notes:
            continue
        sung = detected[a:b]
        anchors = np.full(b - a, np.nan)
        for n in notes:
            anchors[n.core] = _length_weight((n.stop - n.start) * hop) * (n.target + n.own - n.centre)
            target[a + n.start:a + n.stop] = n.target
        moves = _through_slides(anchors, sung)
        if settings.drift > 0:
            moves -= settings.drift * _drift(sung, notes, hop)
        shift[a:b] = settings.strength * _smooth(moves, round(FINAL_SMOOTH_S / hop))
    return target, shift


def _held_notes(sung: np.ndarray, allowed: np.ndarray, hop: float) -> list[_Held]:
    """Notes that settle long enough to tune on their own.

    The rest ride along with their neighbours, keeping their shape: very short notes, and
    short stretches that never settle (a scoop into a note, a fall-off at the end of a
    phrase, a quick dip), which are movements rather than pitches.
    """
    splits = _split_notes(sung, hop)
    if len(splits) == 1:
        s, e = splits[0]
        return [_make_held(sung, s, e, allowed)]
    light = median_filter(sung, size=5, mode="nearest")
    out = []
    for s, e in splits:
        seconds = (e - s) * hop
        if seconds < ORNAMENT_S or (seconds < SETTLE_WITHIN_S and _is_movement(light[s:e])):
            continue
        out.append(_make_held(sung, s, e, allowed))
    return out


def _is_movement(pitch_: np.ndarray) -> bool:
    """A scoop or fall ends somewhere else; a dip comes back but its middle is far from both
    ends. A shaky held note does neither: it wobbles around one place."""
    first, middle, last = (float(np.median(part)) for part in np.array_split(pitch_, 3))
    travels = abs(last - first) > MOVEMENT_ST
    dips = abs(middle - (first + last) / 2) > MOVEMENT_ST
    return travels or dips


def _make_held(sung: np.ndarray, s: int, e: int, allowed: np.ndarray) -> _Held:
    trim = int(NOTE_TRIM * (e - s))
    core = slice(s + trim, e - trim)
    centre = float(np.median(sung[core]))
    return _Held(s, e, core, centre, float(nearest(centre, allowed)))


def _through_slides(anchors: np.ndarray, sung: np.ndarray) -> np.ndarray:
    """Fill the gaps between anchored stretches, changing value as the voice moves.

    Between two notes the correction goes from one amount to the other in proportion to how
    far the pitch has travelled, so the change rides on the slide; a little is spread evenly
    over time too, so a sudden leap doesn't carry the whole change in one frame.
    """
    out = anchors.copy()
    known = np.flatnonzero(np.isfinite(anchors))
    out[:known[0]] = anchors[known[0]]
    out[known[-1] + 1:] = anchors[known[-1]]
    travel = np.abs(np.diff(median_filter(sung, size=5, mode="nearest")))
    for p, q in zip(known[:-1], known[1:]):
        if q - p < 2:
            continue
        steps = travel[p:q]
        evenly = np.full(q - p, 1.0 / (q - p))
        by_motion = steps / steps.sum() if steps.sum() > 1e-6 else evenly
        progress = np.cumsum((1 - EVEN_SHARE) * by_motion + EVEN_SHARE * evenly)[:-1]
        out[p + 1:q] = anchors[p] + (anchors[q] - anchors[p]) * progress
    return out


def _drift(sung: np.ndarray, notes: list[_Held], hop: float) -> np.ndarray:
    """Slow wander of each note around its centre, faded out towards the note's ends."""
    out = np.zeros_like(sung)
    for n in notes:
        length, trim = n.stop - n.start, n.core.start - n.start
        taper = np.ones(length)
        if trim:
            taper[:trim] = np.linspace(0, 1, trim)
            taper[-trim:] = np.linspace(1, 0, trim)
        wander = _smooth(sung[n.start:n.stop] - n.centre, round(DRIFT_WINDOW_S / hop))
        out[n.start:n.stop] = taper * wander
    return out


def _length_weight(seconds: float) -> float:
    """How much of its correction a note gets: full for held notes, less for quick ones."""
    span = (seconds - ORNAMENT_S) / (FULL_CORRECTION_S - ORNAMENT_S)
    return float(SHORT_NOTE_AMOUNT + (1 - SHORT_NOTE_AMOUNT) * np.clip(span, 0, 1))


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    cumulative = np.cumsum(weights[order])
    return float(values[order][np.searchsorted(cumulative, cumulative[-1] / 2)])


def _split_notes(sung: np.ndarray, hop: float) -> list[tuple[int, int]]:
    """[start, stop) frames of each note in a voiced run.

    A new note starts when the vibrato-averaged pitch moves more than NOTE_SPLIT_ST away from
    the current note's running centre and stays away for a note's length. That depends on
    movement, not on where the semitone grid lines fall, so a singer hovering between two
    notes still gets one note. Anything too short to be a note (slides, blips) is then folded
    into the neighbour it sounds closest to.
    """
    smooth = _smooth(sung, round(NOTE_SMOOTH_S / hop))
    shortest = max(1, round(MIN_NATURAL_NOTE_S / hop))
    starts, total, count, away = [0], float(smooth[0]), 1, None
    for i in range(1, len(smooth)):
        if abs(smooth[i] - total / count) > NOTE_SPLIT_ST:
            away = i if away is None else away
            if i - away + 1 >= shortest:
                starts.append(away)
                total, count, away = float(smooth[away:i + 1].sum()), i + 1 - away, None
            continue  # an excursion that may not last mustn't drag the centre along
        away = None
        total, count = total + smooth[i], count + 1
    edges = starts + [len(smooth)]
    runs = [[s, e] for s, e in zip(edges[:-1], edges[1:])]
    while len(runs) > 1:
        short = [i for i, (s, e) in enumerate(runs) if e - s < shortest]
        if not short:
            break
        i = min(short, key=lambda k: runs[k][1] - runs[k][0])
        here = np.median(smooth[runs[i][0]:runs[i][1]])
        neighbours = [j for j in (i - 1, i + 1) if 0 <= j < len(runs)]
        j = min(neighbours, key=lambda k: abs(np.median(smooth[runs[k][0]:runs[k][1]]) - here))
        runs[j] = [min(runs[i][0], runs[j][0]), max(runs[i][1], runs[j][1])]
        del runs[i]
    return [(s, e) for s, e in runs]


def _smooth(x: np.ndarray, frames: int) -> np.ndarray:
    """Hann-weighted moving average (edges held), or x unchanged for tiny windows."""
    if frames < 3 or len(x) < 2:
        return x.copy()
    weights = np.hanning(frames + 2)[1:-1]
    return convolve1d(x, weights / weights.sum(), mode="nearest")


def _hold_notes(midi: np.ndarray, allowed: np.ndarray, hysteresis: float) -> np.ndarray:
    candidates = nearest(midi, allowed)
    notes = np.empty_like(midi)
    # Open on where the note settles, not wherever its first wobble happens to land.
    current = nearest(np.median(midi[:OPENING_FRAMES]), allowed)
    for i, (m, cand) in enumerate(zip(midi, candidates)):
        if cand != current and abs(m - cand) + hysteresis < abs(m - current):
            current = cand
        notes[i] = current
    return notes


def autotune(audio: np.ndarray, sr: int, settings: TuneSettings,
             track: pitch.PitchTrack | None = None) -> TuneResult:
    """Pitch-correct audio shaped (n_samples, n_channels)."""
    mono = audio.mean(axis=1)
    if track is None:
        track = pitch.track(mono, sr)
    detected, target, shift = correction(track, settings)

    marks, segment = psola.find_marks(mono, sr, track)
    semitones = np.zeros(len(marks))
    for idx, (a, b) in enumerate(track.segments()):
        sel = segment == idx
        semitones[sel] = np.interp(marks[sel] / sr, track.times[a:b], shift[a:b])
    out = psola.shift(audio, marks, 2.0 ** (semitones / 12.0))

    return TuneResult(audio=out.astype(np.float32), track=track,
                      detected=detected, target=target, corrected=detected + shift)
