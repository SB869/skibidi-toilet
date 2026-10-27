"""Helpers shared by the subcommands."""

from __future__ import annotations

import argparse
from pathlib import Path

import librosa
import numpy as np

from .. import audiofile


def parse_time(text: str) -> float:
    """'206', '3:26', '3:26.5', '1:02:03' -> seconds."""
    parts = text.strip().split(":")
    try:
        values = [float(p) for p in parts]
    except ValueError:
        values = []
    if not 1 <= len(values) <= 3 or any(v < 0 for v in values) or any(v >= 60 for v in values[1:]):
        raise argparse.ArgumentTypeError(f"not a time: {text!r} (use seconds, m:ss or h:mm:ss)")
    seconds = 0.0
    for v in values:
        seconds = seconds * 60 + v
    return seconds


def fmt_time(seconds: float, decimals: int = 1) -> str:
    minutes, secs = divmod(seconds, 60)
    width = 3 + decimals if decimals else 2
    return f"{int(minutes)}:{secs:0{width}.{decimals}f}"


def file_stamp(seconds: float) -> str:
    minutes, secs = divmod(seconds, 60)
    secs_text = f"{secs:g}" if secs != int(secs) else f"{int(secs):02d}"
    return f"{int(minutes)}m{secs_text}s"


def add_section_args(parser: argparse.ArgumentParser, prefix: str = "", what: str = "the input") -> None:
    flag = f"--{prefix}-" if prefix else "--"
    parser.add_argument(f"{flag}start", type=parse_time, metavar="TIME",
                        help=f"start of the section of {what} to use, e.g. 3:26")
    parser.add_argument(f"{flag}end", type=parse_time, metavar="TIME",
                        help=f"end of the section of {what} (default: the end)")


def load_section(path: Path, start: float | None, end: float | None) -> tuple[np.ndarray, int, float, float]:
    """(audio, sample rate, start, end) for the requested stretch of a file."""
    audio, sr = audiofile.load(path)
    duration = len(audio) / sr
    start = start or 0.0
    end = duration if end is None else min(end, duration)
    if start >= duration:
        raise ValueError(f"starts at {fmt_time(start)} but the file is only {fmt_time(duration)} long")
    if end <= start:
        raise ValueError("the end of the section must come after its start")
    return audio[int(start * sr): int(end * sr)], sr, start, end


def section_label(path: Path, start: float, end: float, whole_file: bool) -> str:
    return path.name if whole_file else f"{path.name} [{fmt_time(start)}–{fmt_time(end)}]"


def hz(value: str) -> float:
    try:
        return float(value)
    except ValueError:
        try:
            return float(librosa.note_to_hz(value))
        except Exception:
            raise argparse.ArgumentTypeError(f"not a note or frequency: {value!r}") from None


def add_pitch_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--a4", type=float, default=440.0, help="reference tuning in Hz (default: %(default)s)")
    parser.add_argument("--fmin", type=hz, default="C2", help="lowest note to detect (default: %(default)s)")
    parser.add_argument("--fmax", type=hz, default="C6", help="highest note to detect (default: %(default)s)")
