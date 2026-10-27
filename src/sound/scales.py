"""Keys, scales, note snapping and key estimation."""

from __future__ import annotations

import numpy as np

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_ENHARMONIC = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#",
               "Cb": "B", "Fb": "E", "E#": "F", "B#": "C"}

SCALES: dict[str, tuple[int, ...]] = {
    "chromatic": tuple(range(12)),
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "harmonic-minor": (0, 2, 3, 5, 7, 8, 11),
    "melodic-minor": (0, 2, 3, 5, 7, 9, 11),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "lydian": (0, 2, 4, 6, 7, 9, 11),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "major-pentatonic": (0, 2, 4, 7, 9),
    "minor-pentatonic": (0, 3, 5, 7, 10),
    "blues": (0, 3, 5, 6, 7, 10),
}

USED_NOTE_SHARE = 0.04

# Krumhansl-Kessler key profiles.
_MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def parse_key(text: str) -> tuple[int, str | None]:
    """'F#' -> (6, None), 'Am' / 'A minor' -> (9, 'minor'), 'Bb major' -> (10, 'major')."""
    raw = text.strip().replace(" ", "").replace("♯", "#").replace("♭", "b")
    scale = None
    for suffix, name in (("major", "major"), ("maj", "major"), ("minor", "minor"), ("min", "minor"), ("m", "minor")):
        if len(raw) > len(suffix) and raw.lower().endswith(suffix):
            raw, scale = raw[: -len(suffix)], name
            break
    note = raw[:1].upper() + raw[1:].lower()
    note = _ENHARMONIC.get(note, note)
    if note not in NOTE_NAMES:
        raise ValueError(f"unrecognised key {text!r}")
    return NOTE_NAMES.index(note), scale


def scale_notes(tonic: int, scale: str) -> np.ndarray:
    """Every MIDI note number 0-127 that belongs to the scale."""
    if scale not in SCALES:
        raise ValueError(f"unknown scale {scale!r}; choose from {', '.join(SCALES)}")
    classes = {(tonic + step) % 12 for step in SCALES[scale]}
    return np.array([n for n in range(128) if n % 12 in classes], dtype=float)


def nearest(midi: float | np.ndarray, allowed: np.ndarray) -> np.ndarray:
    """Snap fractional MIDI pitch(es) to the closest allowed note."""
    midi = np.asarray(midi, dtype=float)
    idx = np.clip(np.searchsorted(allowed, midi), 1, len(allowed) - 1)
    below, above = allowed[idx - 1], allowed[idx]
    return np.where(midi - below <= above - midi, below, above)


def pitch_class_shares(midi: np.ndarray, weights: np.ndarray | None = None) -> np.ndarray:
    """Fraction of the singing spent on each of the 12 pitch classes (C=0)."""
    keep = np.isfinite(midi)
    hist = np.bincount(np.round(midi[keep]).astype(int) % 12,
                       weights=None if weights is None else weights[keep], minlength=12).astype(float)
    total = hist.sum()
    return hist / total if total else hist


def estimate_key(midi: np.ndarray, weights: np.ndarray | None = None) -> tuple[int, str, float]:
    """Best-matching (tonic, 'major'|'minor', correlation) for a set of sung pitches.

    Keys that contain every note that is actually used (USED_NOTE_SHARE of the singing or
    more) win over keys that don't, however well the latter fit the profile otherwise. That
    stops a melody that leans on one note from being given a key that lacks its other notes.
    """
    shares = pitch_class_shares(midi, weights)
    if not shares.any():
        return 0, "major", 0.0
    used = {pc for pc in range(12) if shares[pc] >= USED_NOTE_SHARE}
    ranked = []
    for tonic in range(12):
        for name, profile in (("major", _MAJOR_PROFILE), ("minor", _MINOR_PROFILE)):
            r = float(np.corrcoef(shares, np.roll(profile, tonic))[0, 1])
            fits = used <= {(tonic + step) % 12 for step in SCALES[name]}
            ranked.append((fits, r, tonic, name))
    fits, r, tonic, name = max(ranked)
    return tonic, name, r


def note_name(midi: float) -> str:
    n = int(round(midi))
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"


def hz_to_midi(hz: np.ndarray, a4: float = 440.0) -> np.ndarray:
    return 69.0 + 12.0 * np.log2(np.asarray(hz, dtype=float) / a4)


def midi_to_hz(midi: np.ndarray, a4: float = 440.0) -> np.ndarray:
    return a4 * 2.0 ** ((np.asarray(midi, dtype=float) - 69.0) / 12.0)
