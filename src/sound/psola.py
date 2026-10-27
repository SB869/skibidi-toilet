"""TD-PSOLA pitch shifting with a shift amount that can change every period.

The voice is cut into overlapping grains, each two pitch periods long and centred
on a glottal pulse (a "pitch mark"). Laying those grains back down closer together
raises the pitch; spreading them apart lowers it. Each grain keeps its own waveform,
so the vocal-tract resonances (formants) stay where they were and the voice keeps
its character instead of going chipmunk. Unvoiced sound (s, t, breaths) is passed
through untouched.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np

from .pitch import PitchTrack

UNVOICED_SPACING_S = 0.005
MIN_CORRELATION = 0.3     # below this, trust the pitch track rather than the waveform match
MIN_SEGMENT_CORRELATION = 0.5


def find_marks(mono: np.ndarray, sr: int, track: PitchTrack) -> tuple[np.ndarray, np.ndarray]:
    """Pitch marks covering the whole signal.

    Returns (marks, segment): sample positions starting at 0 and ending at len(mono),
    and for each mark the index of the voiced segment it belongs to (-1 = unvoiced).
    """
    n = len(mono)
    lowest = float(np.nanmin(track.f0)) if track.voiced.any() else 50.0
    pad = int(2 * sr / lowest) + 1  # room for a 1.2-period search plus half-period reference
    x = np.concatenate((np.zeros(pad), np.asarray(mono, dtype=float), np.zeros(pad)))
    half_hop = track.hop_s / 2

    marks, segment = [0], [-1]
    for seg_idx, (a, b) in enumerate(track.segments()):
        times, f0 = track.times[a:b], track.f0[a:b]
        start = max(0, int((times[0] - half_hop) * sr))
        stop = min(n, int((times[-1] + half_hop) * sr))

        def period(i: int) -> float:
            return sr / float(np.interp(i / sr, times, f0))

        voiced = _segment_marks(x, pad, start, stop, period)
        voiced = [m for m in voiced if marks[-1] < m < n]
        if not voiced:
            continue
        _fill_unvoiced(marks, segment, voiced[0], sr)
        marks += voiced
        segment += [seg_idx] * len(voiced)

    _fill_unvoiced(marks, segment, n, sr)
    marks.append(n)
    segment.append(-1)
    return np.array(marks, dtype=np.int64), np.array(segment, dtype=np.int64)


def _segment_marks(x: np.ndarray, pad: int, start: int, stop: int, period) -> list[int]:
    """Glottal-pulse positions inside one voiced run, in unpadded sample coordinates.

    Starts from the strongest peak near the middle and walks outwards one period at a
    time, placing each mark where the waveform best matches the previous period. That
    keeps every mark at the same point in the vocal cycle, which is what makes the
    re-spaced grains line up cleanly.
    """
    mid = (start + stop) // 2
    t = period(mid)
    if stop - start < 3 * t:
        return []
    lo, hi = max(start, mid - int(t)), min(stop, mid + int(t))
    window = x[pad + lo: pad + hi]
    polarity = 1.0 if window.max() >= -window.min() else -1.0
    first = lo + int(np.argmax(polarity * window))

    scores: list[float] = []
    forward = _walk(x, pad, first, stop, period, +1, scores)
    backward = _walk(x, pad, first, start, period, -1, scores)
    if not scores or np.median(scores) < MIN_SEGMENT_CORRELATION:
        return []   # not actually periodic (breath, noise): shifting it would only add buzz
    return backward[::-1] + [first] + forward


def _walk(x: np.ndarray, pad: int, m: int, limit: int, period, direction: int,
          scores_out: list[float]) -> list[int]:
    out = []
    while True:
        t = period(m)
        if (direction > 0 and m + t > limit) or (direction < 0 and m - t < limit):
            return out
        half = max(1, int(t // 2))
        lag_lo, lag_hi = int(0.8 * t), int(np.ceil(1.2 * t))
        c = m + pad
        ref = x[c - half: c + half]
        if direction > 0:
            scores = _ncc(x[c + lag_lo - half: c + lag_hi + half], ref)
            lag = lag_lo + int(np.argmax(scores))
        else:
            scores = _ncc(x[c - lag_hi - half: c - lag_lo + half], ref)
            lag = lag_hi - int(np.argmax(scores))
        best = float(scores.max())
        scores_out.append(best)
        if best < MIN_CORRELATION:
            lag = int(round(t))
        m += direction * lag
        out.append(m)


def _ncc(search: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Normalised cross-correlation of ref against every same-length window of search."""
    num = np.correlate(search, ref, mode="valid")
    energy = np.concatenate(([0.0], np.cumsum(search * search)))
    window_energy = energy[len(ref):] - energy[:-len(ref)]
    return num / (np.sqrt(window_energy * np.dot(ref, ref)) + 1e-12)


def _fill_unvoiced(marks: list[int], segment: list[int], until: int, sr: int) -> None:
    """Evenly spaced marks between the last mark and `until` (exclusive)."""
    base, gap = marks[-1], until - marks[-1]
    count = max(1, round(gap / (UNVOICED_SPACING_S * sr)))
    fill = [base + round(gap * i / count) for i in range(1, count)]
    marks += fill
    segment += [-1] * len(fill)


def shift(audio: np.ndarray, marks: np.ndarray, ratios: np.ndarray) -> np.ndarray:
    """Overlap-add resynthesis with the pitch at each mark multiplied by ratios[mark].

    audio is (n_samples, n_channels); marks come from find_marks. The timing of the
    performance is unchanged. With every ratio at 1 the output equals the input exactly.
    """
    audio = np.asarray(audio, dtype=float)
    n = len(audio)
    a = marks.astype(float)
    last = len(a) - 1

    # Synthesis marks: step through output time at the new (shifted) period, and at
    # each step borrow the grain from whichever analysis mark is nearest in time.
    synth, source = [0.0], [0]
    s, j = 0.0, 0
    while True:
        s += (a[j + 1] - a[j]) / ratios[j]
        if s >= a[last]:
            break
        k = int(np.searchsorted(a, s))
        j = k if a[k] - s < s - a[k - 1] else k - 1
        if j >= last:
            break
        if ratios[j] == 1.0 and a[j] > synth[-1]:
            # Back in unshifted territory: land exactly on the analysis mark so the output
            # re-syncs with the original and breaths/consonants pass through bit-identical.
            s = a[j]
        synth.append(s)
        source.append(j)
    synth.append(a[last])
    source.append(last)

    centres = np.round(synth).astype(np.int64)
    out = np.zeros_like(audio)
    for k in range(len(centres)):
        c, src = int(centres[k]), int(marks[source[k]])
        left = c - int(centres[k - 1]) if k > 0 else 0
        right = int(centres[k + 1]) - c if k < len(centres) - 1 else 0
        if left + right == 0:
            continue
        # Each grain's window spans exactly from the previous synthesis mark to the next,
        # so neighbouring windows always sum to 1: no gain wobble, no normalisation needed.
        window = np.concatenate((_rise(left), _fall(right)))
        lo, offset = c - left, src - c
        start, stop = max(lo, 0, -offset), min(c + right, n, n - offset)
        if stop > start:
            out[start:stop] += window[start - lo: stop - lo, None] * audio[start + offset: stop + offset]
    return out


@lru_cache(maxsize=4096)
def _rise(length: int) -> np.ndarray:
    return 0.5 - 0.5 * np.cos(np.pi * np.arange(length) / length) if length else np.zeros(0)


@lru_cache(maxsize=4096)
def _fall(length: int) -> np.ndarray:
    return 0.5 + 0.5 * np.cos(np.pi * np.arange(length) / length) if length else np.zeros(0)
