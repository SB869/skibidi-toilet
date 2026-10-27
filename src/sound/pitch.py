"""Monophonic pitch tracking (vocals, single instruments)."""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import librosa
import numpy as np

ANALYSIS_SR = 22050
HOP = 128           # at ANALYSIS_SR: ~5.8 ms per frame
FRAME = 1024        # ~46 ms: short enough to follow vibrato, long enough for low C2
MIN_RUN_S = 0.04    # voiced blips and unvoiced gaps shorter than this are cleaned up
MIN_VOICED_PROB = 0.1
REFINE_TOLERANCE_CENTS = 30


@dataclass
class PitchTrack:
    times: np.ndarray    # frame centres, seconds
    f0: np.ndarray       # Hz, NaN where unvoiced
    voiced: np.ndarray   # bool per frame

    @property
    def hop_s(self) -> float:
        return float(self.times[1] - self.times[0]) if len(self.times) > 1 else HOP / ANALYSIS_SR

    def segments(self) -> list[tuple[int, int]]:
        """Contiguous voiced runs as [start, stop) frame index pairs."""
        return _runs(self.voiced)


def track(mono: np.ndarray, sr: int, fmin: float = 65.0, fmax: float = 1050.0,
          min_prob: float = MIN_VOICED_PROB) -> PitchTrack:
    """Estimate f0 over time with probabilistic YIN, then tidy up the voicing decisions.

    min_prob is the voicing confidence a frame needs. Keep the default for anything that
    will be pitch-shifted; use 0 to read melodies from reverberant or stem-separated audio
    where pYIN is never confident but its pitch choices are still right.
    """
    y = np.asarray(mono, dtype=np.float32)
    if sr != ANALYSIS_SR:
        y = librosa.resample(y, orig_sr=sr, target_sr=ANALYSIS_SR)
    frame = max(FRAME, 2 * (int(ANALYSIS_SR / fmin) + 2))  # very low fmin needs two periods per frame
    with warnings.catch_warnings():
        # librosa's numba helper casts NaN to int on silent frames; harmless, and noisy
        warnings.filterwarnings("ignore", "invalid value encountered in cast", RuntimeWarning)
        f0, voiced, prob = librosa.pyin(
            y, fmin=fmin, fmax=fmax, sr=ANALYSIS_SR, frame_length=frame, hop_length=HOP,
        )
        # pYIN's answers sit on a 10-cent grid, too coarse for tuning. Plain YIN interpolates
        # between lags, so take its value wherever it agrees with pYIN's more robust choice.
        fine = librosa.yin(y, fmin=fmin, fmax=fmax, sr=ANALYSIS_SR, frame_length=frame, hop_length=HOP)
    times = librosa.times_like(f0, sr=ANALYSIS_SR, hop_length=HOP)
    voiced = voiced & np.isfinite(f0) & (prob >= min_prob)
    with np.errstate(invalid="ignore", divide="ignore"):
        agrees = np.abs(1200 * np.log2(fine / f0)) < REFINE_TOLERANCE_CENTS
    f0 = np.where(voiced & agrees, fine, f0)

    f0, voiced = _clean(f0, voiced, round(MIN_RUN_S * ANALYSIS_SR / HOP))
    return PitchTrack(times=times, f0=f0, voiced=voiced)


def _clean(f0: np.ndarray, voiced: np.ndarray, min_frames: int) -> tuple[np.ndarray, np.ndarray]:
    f0, voiced = f0.copy(), voiced.copy()
    n = len(voiced)

    # Bridge short dropouts inside a note so the shifter doesn't flicker on and off.
    for start, stop in _runs(~voiced):
        if start > 0 and stop < n and stop - start < min_frames:
            lo, hi = np.log2(f0[start - 1]), np.log2(f0[stop])
            f0[start:stop] = 2 ** np.linspace(lo, hi, stop - start + 2)[1:-1]
            voiced[start:stop] = True

    # Drop voiced blips too short to be a sung note.
    for start, stop in _runs(voiced):
        if stop - start < min_frames:
            voiced[start:stop] = False

    f0[~voiced] = np.nan
    return f0, voiced


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    padded = np.concatenate(([False], mask, [False])).astype(np.int8)
    edges = np.flatnonzero(np.diff(padded))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist()))
