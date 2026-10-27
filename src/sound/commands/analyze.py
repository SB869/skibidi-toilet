"""sound analyze: pitch range, tuning, key and notes of a recording or a section of one."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import analysis, pitch, plots
from ..scales import NOTE_NAMES, note_name
from .common import add_pitch_args, add_section_args, fmt_time, load_section, section_label

KEY_CONFIDENCE = 0.6


def add_parser(sub) -> None:
    parser = sub.add_parser("analyze", help="pitch range, tuning, key and notes of a recording",
                            description="Report pitch range, tuning, key and (optionally) every held note.")
    parser.add_argument("input", type=Path)
    add_section_args(parser)
    parser.add_argument("--loose", action="store_true",
                        help="read pitch even where the voice is unclear: use for stem-separated "
                        "acapellas, reverb-heavy or mixed audio")
    parser.add_argument("--notes", action="store_true", help="list every held note")
    parser.add_argument("--plot", type=Path, metavar="PNG", help="save the pitch contour as an image")
    add_pitch_args(parser)
    parser.set_defaults(run=run)


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        audio, sr, start, end = load_section(args.input, args.start, args.end)
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        print(f"{args.input}: {e}", file=sys.stderr)
        return 1
    label = section_label(args.input, start, end, args.start is None and args.end is None)
    track = pitch.track(audio.mean(axis=1), sr, fmin=args.fmin, fmax=args.fmax,
                        min_prob=0.0 if args.loose else pitch.MIN_VOICED_PROB)
    s = analysis.summarize(track, args.a4)

    print(f"{label}: {end - start:.1f}s")
    if s.voiced == 0:
        print("  no pitched voice found" + ("" if args.loose else " (try --loose for acapella stems)"))
        return 0
    print(f"  pitched    {s.voiced:.0%} of the time, {note_name(s.low)} to {note_name(s.high)}")
    print(f"  tuning     {s.tuning_cents:+.0f}¢ from A4={args.a4:g}, {_steadiness(s.tuning_consistency)}")
    tonic, mode, r = s.key
    unsure = "" if r >= KEY_CONFIDENCE else ", unsure"
    print(f"  notes used {' '.join(NOTE_NAMES[pc] for pc in s.notes_used)}  (most to least)")
    print(f"  key        {NOTE_NAMES[tonic]} {mode} (confidence {r:.2f}{unsure})")
    print(f"  accuracy   {s.in_tune:.0%} of the time within 10¢ of a note; typically {s.median_dev:.0f}¢ off")
    if not args.loose and s.voiced < 0.4:
        print("  (little pitch found; if this is an extracted acapella or has reverb, try --loose)")

    if args.notes:
        print("  notes:")
        for n in analysis.notes(track, args.a4):
            cents = 100 * (n.midi - round(n.midi))
            print(f"    {fmt_time(start + n.start, 2):>8}  {n.end - n.start:4.2f}s  {note_name(n.midi):4s} {cents:+4.0f}¢")
    if args.plot:
        plots.pitch_plot(track, args.plot, label, start, args.a4)
        print(f"  plot -> {args.plot}")
    return 0


def _steadiness(consistency: float) -> str:
    if consistency >= 0.5:
        return "steady"
    if consistency >= 0.25:
        return "loose"
    return "scattered (no single offset; each note is off its own way)"
