"""sound compare: score takes against a reference vocal (e.g. the original acapella)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import analysis, audiofile, pitch, plots
from ..scales import NOTE_NAMES
from .common import add_pitch_args, add_section_args, fmt_time, load_section

EXAMPLES = """\
examples:
  sound compare take.wav --ref acapella.mp3 --ref-start 3:26
  sound compare take.wav tuned/*.wav --ref acapella.mp3 --ref-start 3:26 --plot compare.png
"""


def add_parser(sub) -> None:
    parser = sub.add_parser("compare", help="score takes against a reference vocal",
                            description="Line each take up with a reference vocal and measure how "
                            "close its pitch is. Octaves are ignored, so singing the part an octave "
                            "down is fine.", epilog=EXAMPLES,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("takes", nargs="+", type=Path, help="your recordings, tuned or not")
    parser.add_argument("-r", "--ref", type=Path, required=True, help="reference vocal, e.g. the original acapella")
    add_section_args(parser, prefix="ref", what="the reference")
    parser.add_argument("--plot", type=Path, metavar="PNG", help="save the takes overlaid on the reference")
    add_pitch_args(parser)
    parser.set_defaults(run=run)


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    takes = []
    for path in args.takes:
        try:
            audio, sr = audiofile.load(path)
        except (FileNotFoundError, RuntimeError) as e:
            print(f"{path}: {e}", file=sys.stderr)
            return 1
        takes.append((path, pitch.track(audio.mean(axis=1), sr, fmin=args.fmin, fmax=args.fmax),
                      len(audio) / sr))

    search_from, search_to = analysis.reference_window(args.ref_start, args.ref_end, max(d for *_, d in takes))
    try:
        ref_audio, sr, search_from, search_to = load_section(args.ref, search_from, search_to)
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        print(f"{args.ref}: {e}", file=sys.stderr)
        return 1
    # Acapellas pulled out of a mix are never "clean" enough for pYIN to be confident,
    # but its pitch choices are still right, so read the reference loosely.
    ref = pitch.track(ref_audio.mean(axis=1), sr, fmin=args.fmin, fmax=args.fmax, min_prob=0.0)
    summary = analysis.summarize(ref, args.a4)
    tonic, mode, r = summary.key
    print(f"reference: {args.ref.name} [{fmt_time(search_from)}–{fmt_time(search_to)}], "
          f"{NOTE_NAMES[tonic]} {mode} (confidence {r:.2f}), {summary.tuning_cents:+.0f}¢ from A4={args.a4:g}")
    print(f"           notes used: {' '.join(NOTE_NAMES[pc] for pc in summary.notes_used)}")
    print()

    names = [p.name if len(p.name) <= 32 else p.name[:31] + "…" for p, *_ in takes]
    width = max(len("take"), *(len(n) for n in names))
    print(f"  {'take':<{width}}  first note at  tempo  octave  off by  within 25¢  within 50¢  bias        notes right")
    names = dict(zip((p for p, *_ in takes), names))
    results = analysis.compare(ref, [(names[p], track) for p, track, _ in takes], args.a4)
    for res in results:
        if res.agreement is None:
            print(f"  {res.name:<{width}}  (no pitched voice found)")
            continue
        ag = res.agreement
        notes = f"{ag.notes_matched}/{ag.notes_compared}"
        if ag.notes_compared:
            notes += f" ({ag.notes_matched / ag.notes_compared:.0%})"
        print(f"  {res.name:<{width}}  {fmt_time(search_from + res.first_note, 1):>13}  {res.tempo:5.0%}  "
              f"{_octave(res.alignment.octave):>6}  {ag.median_abs:5.0f}¢  {ag.within_25:10.0%}  "
              f"{ag.within_50:10.0%}  {_bias(ag.bias):<10}  {notes}")
    rows = [(r.name, r.track, r.alignment) for r in results if r.alignment is not None]
    print()
    print("  first note at: where your first note lands in the reference")
    print("  tempo:         your speed relative to the reference (90% = 10% slower)")
    print("  off by:        typical pitch distance from the reference while you both sing (octave ignored)")
    print("  notes right:   held notes within a quarter-tone of the reference's note at that moment")

    if args.plot and rows:
        plots.compare_plot(ref, rows, args.plot, args.a4)
        print(f"\nplot -> {args.plot}")
    return 0


def _octave(semitones: int) -> str:
    """The take's register relative to the reference: '-1 oct' = an octave below."""
    return "same" if semitones == 0 else f"{-semitones // 12:+d} oct"


def _bias(cents: float) -> str:
    if abs(cents) < 3:
        return "centred"
    return f"{abs(cents):.0f}¢ {'sharp' if cents > 0 else 'flat'}"
