"""Tempo and entries: where the beats fall, and when the singing comes in."""

from __future__ import annotations

import librosa
import numpy as np

from .pitch import PitchTrack

ANALYSIS_SR = 22050
HOP = 512
MIN_ENTRY_S = 0.15     # a sung run at least this long counts as the voice coming in
ENTRY_QUIET_DB = 20    # and it must be within this much of the loud singing (not reverb or bleed)
ONSET_LOOKBACK_S = 0.25  # how far before the pitch the audible start (a consonant, a breath) may be
ONSET_ABOVE_FLOOR_DB = 12


def beats(mono: np.ndarray, sr: int) -> np.ndarray:
    """Beat times (seconds) through a whole recording.

    Tracked over the whole thing at once: a vocal-only stem gives the tracker little to hold
    on to, and a short window easily locks onto double or half time, while the whole song
    keeps it on one tempo."""
    y = librosa.resample(np.asarray(mono, dtype=np.float32), orig_sr=sr, target_sr=ANALYSIS_SR)
    onset = librosa.onset.onset_strength(y=y, sr=ANALYSIS_SR, hop_length=HOP)
    _, found = librosa.beat.beat_track(onset_envelope=onset, sr=ANALYSIS_SR, hop_length=HOP, units="time")
    return np.asarray(found, dtype=float)


def tempo_near(beat_times: np.ndarray, t: float, window: float = 30.0) -> float | None:
    """BPM from the beat spacing within `window` seconds of t."""
    near = beat_times[np.abs(beat_times - t) < window]
    if len(near) < 3:
        return None
    return float(60.0 / np.median(np.diff(near)))


def entry(mono: np.ndarray, sr: int, track: PitchTrack) -> float | None:
    """When the singing first comes in (seconds from the start of `mono`), ignoring short
    blips and quiet stuff like reverb tails or bleed.

    That's where the sound starts, not where the pitch does: a phrase that opens on a
    consonant or a breath is heard a little before it becomes a note, so the start is
    walked back through any sound leading into the first sung run."""
    hop = track.hop_s
    level = np.array([np.sqrt(np.mean(mono[int(t * sr): int(t * sr) + int(hop * sr)] ** 2) + 1e-12)
                      for t in track.times])
    db = 20 * np.log10(level)
    if not track.voiced.any():
        return None
    loud = np.percentile(db[track.voiced], 95)
    ok = track.voiced & (db > loud - ENTRY_QUIET_DB)
    need = max(1, round(MIN_ENTRY_S / hop))
    run = 0
    for i, v in enumerate(ok):
        run = run + 1 if v else 0
        if run >= need:
            first = i - need + 1
            floor = np.percentile(db, 10)
            back = first
            while back > 0 and first - back < ONSET_LOOKBACK_S / hop and db[back - 1] > floor + ONSET_ABOVE_FLOOR_DB:
                back -= 1
            return float(track.times[back])
    return None
