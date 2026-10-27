"""sound tune / autotune: pitch-correct vocal recordings."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

from .. import analysis, audiofile, pitch, plots
from ..scales import NOTE_NAMES, SCALES, parse_key
from ..tune import TuneSettings, autotune
from .common import add_pitch_args, add_section_args, file_stamp, load_section, section_label

EXAMPLES = """\
examples:
  autotune vocals.wav                      snap to the nearest semitone
  autotune vocals.m4a --key auto           detect the key and snap to its scale
  autotune vocals.wav --key "F# minor"     snap to F# natural minor
  autotune vocals.wav --key "F# major" --style natural   in tune, but still sounds like you
  autotune vocals.wav --key Am --speed 0   hard, robotic T-Pain style
  autotune take*.wav --key C --speed 80 --strength 0.7   gentle, natural correction
  autotune long.wav --start 1:10 --end 1:40              tune just that section
"""


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("inputs", nargs="+", type=Path, help="audio files (wav, flac, mp3, m4a, ...)")
    parser.add_argument("-o", "--output", type=Path, help="output path (single input only)")
    parser.add_argument("-d", "--out-dir", type=Path, help="write outputs here instead of next to the inputs")
    parser.add_argument("-k", "--key", help="C, F#, Bb, Am, 'D minor', or 'auto' to detect it "
                        "(default: no key, snap to any semitone)")
    parser.add_argument("-s", "--scale", choices=list(SCALES),
                        help="scale for --key (default: major, or minor if the key says so)")
    parser.add_argument("--style", choices=["autotune", "natural"], default="autotune",
                        help="autotune: pull every moment toward a note (the classic sound); natural: move each "
                        "whole note into tune, keeping its vibrato, scoops and slides (default: %(default)s)")
    parser.add_argument("--drift", type=float, default=0.0,
                        help="natural style: 0-1, how much slow wander inside long notes to straighten "
                        "(default: %(default)s)")
    parser.add_argument("--humanize", type=float, default=0.25,
                        help="natural style: 0-1, how much of each note's own lean to keep, so not every "
                        "note lands dead centre (default: %(default)s)")
    parser.add_argument("--speed", type=float, default=25.0,
                        help="retune speed in ms: 0 = instant/robotic, 10-40 = tight, 80+ = natural "
                        "(default: %(default)s)")
    parser.add_argument("--strength", type=float, default=1.0,
                        help="0-1, how far toward the target note to pull (default: %(default)s)")
    parser.add_argument("--hysteresis", type=float, default=0.15,
                        help="semitones past the midpoint before jumping to the next note (default: %(default)s)")
    add_pitch_args(parser)
    add_section_args(parser)
    parser.add_argument("--plot", action="store_true", help="also save a .png of the pitch before/after")


def add_parser(sub) -> None:
    parser = sub.add_parser("tune", help="pitch-correct vocal recordings (autotune)",
                            description="Pitch-correct vocal recordings.", epilog=EXAMPLES,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
    add_arguments(parser)
    parser.set_defaults(run=run)


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.output and len(args.inputs) > 1:
        parser.error("--output only works with a single input; use --out-dir")
    if not 0.0 <= args.strength <= 1.0:
        parser.error("--strength must be between 0 and 1")
    if args.speed < 0:
        parser.error("--speed must be >= 0")
    if not 0.0 <= args.drift <= 1.0:
        parser.error("--drift must be between 0 and 1")
    if not 0.0 <= args.humanize <= 1.0:
        parser.error("--humanize must be between 0 and 1")
    if args.scale and args.scale != "chromatic" and args.key is None:
        parser.error("--scale needs a --key (or --key auto)")
    try:
        if args.key not in (None, "auto"):
            parse_key(args.key)
    except ValueError as e:
        parser.error(str(e))

    failed = 0
    for path in args.inputs:
        out_path = args.output or _default_output(path, args)
        if out_path.resolve() == path.resolve():
            print(f"{path}: refusing to overwrite the input", file=sys.stderr)
            failed += 1
            continue
        try:
            _process(path, out_path, args)
        except Exception as e:  # keep going through a batch
            print(f"{path}: {e}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


def _process(path: Path, out_path: Path, args: argparse.Namespace) -> None:
    started = time.monotonic()
    audio, sr, start, end = load_section(path, args.start, args.end)
    whole = args.start is None and args.end is None
    print(f"{section_label(path, start, end, whole)}: {end - start:.1f}s, {sr} Hz, {audio.shape[1]} ch")

    track = pitch.track(audio.mean(axis=1), sr, fmin=args.fmin, fmax=args.fmax)
    if not track.voiced.any():
        raise RuntimeError("no pitched voice found; nothing to tune")
    choice = analysis.choose_key(track, args.key, args.scale, args.a4)
    est_tonic, est_mode, est_r = choice.estimate
    guess = f"{NOTE_NAMES[est_tonic]} {est_mode}, confidence {est_r:.2f}"
    tonic, scale = choice.tonic, choice.scale
    if choice.source == "none":
        hint = f"sounds like {guess}; try --key auto" if est_r >= analysis.KEY_CONFIDENCE \
            else f"no clear key: best guess {guess}"
        print(f"  key: none, snapping to every semitone ({hint})")
    elif choice.source == "auto-unsure":
        print(f"  key: unsure (best guess {guess} < {analysis.KEY_CONFIDENCE}), "
              f"snapping to every semitone instead; pass --key to force one")
    elif choice.source == "auto":
        print(f"  key: {NOTE_NAMES[tonic]} {scale} (detected, confidence {est_r:.2f})")
    else:
        print(f"  key: {NOTE_NAMES[tonic]} {scale}")

    settings = TuneSettings(tonic=tonic, scale=scale, speed_ms=args.speed,
                            strength=args.strength, hysteresis=args.hysteresis, a4=args.a4,
                            style=args.style, drift=args.drift, humanize=args.humanize)
    result = autotune(audio, sr, settings, track=track)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    gain = audiofile.save(out_path, result.audio, sr)

    voiced = result.track.voiced
    cents = np.abs(result.corrected - result.detected)[voiced] * 100
    print(f"  voiced {voiced.mean():.0%} of the take; correction median {np.median(cents):.0f}¢, "
          f"max {cents.max():.0f}¢")
    if gain < 1.0:
        print(f"  output turned down {20 * np.log10(gain):.1f} dB to avoid clipping")
    if args.plot:
        plot_path = out_path.with_suffix(".png")
        plots.tune_plot(result, plot_path, args.a4)
        print(f"  plot -> {plot_path}")
    print(f"  -> {out_path}  ({time.monotonic() - started:.1f}s)")


def _default_output(path: Path, args: argparse.Namespace) -> Path:
    section = ""
    if args.start is not None or args.end is not None:
        section = f"_{file_stamp(args.start or 0)}-{file_stamp(args.end) if args.end else 'end'}"
    return (args.out_dir or path.parent) / f"{path.stem}{section}_tuned.wav"
