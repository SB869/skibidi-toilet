"""Pitch plots (PNG)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .analysis import Alignment, reference_on_take
from .pitch import PitchTrack
from .scales import hz_to_midi, note_name
from .tune import TuneResult


def _pyplot():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def _note_axis(ax, values: np.ndarray) -> None:
    values = values[np.isfinite(values)]
    if values.size == 0:
        return
    lo, hi = int(np.floor(np.percentile(values, 0.5))) - 1, int(np.ceil(np.percentile(values, 99.5))) + 1
    ax.set_ylim(lo, hi)
    step = 1 if hi - lo <= 30 else 2
    ax.set_yticks(range(lo, hi + 1, step))
    ax.set_yticklabels([note_name(m) for m in range(lo, hi + 1, step)], fontsize=7)
    ax.grid(axis="y", color="0.9")


def tune_plot(result: TuneResult, path: Path, a4: float) -> None:
    plt = _pyplot()
    t = result.track.times
    fig, ax = plt.subplots(figsize=(16, 6), dpi=110)
    ax.plot(t, result.detected, color="0.6", lw=1, label="sung")
    ax.plot(t, result.corrected, color="tab:blue", lw=1.4, label="output")
    ax.step(t, result.target, where="mid", color="tab:orange", lw=0.8, ls="--", label="target note")
    _note_axis(ax, result.detected)
    ax.set_xlabel("seconds")
    ax.legend(loc="upper right")
    ax.set_title(f"{path.stem}  (A4 = {a4:g} Hz)")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def pitch_plot(track: PitchTrack, path: Path, title: str, start: float, a4: float) -> None:
    plt = _pyplot()
    midi = hz_to_midi(track.f0, a4)
    fig, ax = plt.subplots(figsize=(16, 6), dpi=110)
    ax.plot(track.times + start, midi, color="tab:blue", lw=1)
    _note_axis(ax, midi)
    ax.set_xlabel("seconds")
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def compare_plot(ref: PitchTrack, takes: list[tuple[str, PitchTrack, Alignment]], path: Path, a4: float) -> None:
    """One row per take: the take's pitch, with the reference laid over it in the take's octave."""
    plt = _pyplot()
    fig, axes = plt.subplots(len(takes), 1, figsize=(16, 3.2 * len(takes)), dpi=100, squeeze=False, sharex=True)
    for ax, (name, take, al) in zip(axes[:, 0], takes):
        midi = hz_to_midi(take.f0, a4)
        theirs = reference_on_take(take, ref, al, a4)
        ax.plot(take.times, theirs, color="tab:orange", lw=2.5, alpha=0.45, label="reference")
        ax.plot(take.times, midi, color="tab:blue", lw=1, label=name)
        _note_axis(ax, midi)
        ax.legend(loc="upper right", fontsize=8)
    axes[-1, 0].set_xlabel("seconds into take")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
