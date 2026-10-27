"""sound fx: run the vocal production chain (optionally matching a reference vocal)."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .. import audiofile, fx, pitch
from .common import add_section_args, fmt_time, load_section

EXAMPLES = """\
examples:
  sound fx tuned.wav                                          heavy rock-vocal chain
  sound fx tuned.wav --ref acapella.mp3 --ref-start 3:26      sound like that part of the reference
  sound fx tuned.wav --ref acapella.mp3 --grit 0.8 --space 0  grittier, dry
"""


def add_parser(sub) -> None:
    parser = sub.add_parser("fx", help="vocal production chain: compression, grit, tone, doubles, reverb",
                            description="Make a dry take sound produced: low cut, tone-matching EQ, compression, "
                            "saturation, de-essing, doubles, reverb and loudness.", epilog=EXAMPLES,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path, help="default: <name>_fx.wav next to the input")
    parser.add_argument("-r", "--ref", type=Path, help="reference vocal to match tone and loudness to")
    add_section_args(parser, prefix="ref", what="the reference")
    defaults = fx.Chain()
    for name, help_ in [("compression", "0-1, how even the level gets"), ("grit", "0-1, saturation: upper harmonics, edge"),
                        ("tone", "0-1, how far to move towards the reference's (or a rock vocal's) tonal balance"),
                        ("deess", "0-1, taming of sharp s/t/sh sounds"), ("width", "0-1.5, stereo width of the lead (0 = mono)"),
                        ("doubles", "0-1, two detuned, delayed copies left and right"), ("space", "0-1, reverb")]:
        parser.add_argument(f"--{name}", type=float, default=getattr(defaults, name),
                            help=f"{help_} (default: %(default)s)")
    parser.add_argument("--loudness", type=float, help="target LUFS (default: the reference's, or -12)")
    parser.add_argument("--low-cut", type=float, default=defaults.low_cut, help="Hz (default: %(default)s)")
    parser.set_defaults(run=run)


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    started = time.monotonic()
    out_path = args.output or args.input.with_name(f"{args.input.stem}_fx.wav")
    if out_path.resolve() == args.input.resolve():
        parser.error("refusing to overwrite the input")
    try:
        audio, sr = audiofile.load(args.input)
        reference = None
        if args.ref:
            ref_audio, ref_sr, ref_from, ref_to = load_section(args.ref, args.ref_start, args.ref_end)
            ref_track = pitch.track(ref_audio.mean(axis=1), ref_sr, min_prob=0.0)
            reference = fx.measure(ref_audio, ref_sr, ref_track)
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        print(f"{e}", file=sys.stderr)
        return 1

    track = pitch.track(audio.mean(axis=1), sr)
    loudness = args.loudness if args.loudness is not None else (reference.lufs if reference else -12.0)
    chain = fx.Chain(low_cut=args.low_cut, compression=args.compression, grit=args.grit, tone=args.tone,
                     deess=args.deess, width=args.width, doubles=args.doubles, space=args.space, loudness=loudness)
    out = fx.process(audio, sr, chain, track=track, reference=reference)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    audiofile.save(out_path, out, sr)

    rows = [("input", fx.measure(audio, sr, track)), ("output", fx.measure(out, sr, track))]
    if reference:
        rows.append((f"reference [{fmt_time(ref_from)}–{fmt_time(ref_to)}]", reference))
    print(f"{args.input.name} -> {out_path}  ({time.monotonic() - started:.1f}s)")
    print(f"  {'':30s} {'loudness':>9s} {'dynamics':>9s} {'brightness':>11s} {'width':>8s}")
    for name, p in rows:
        print(f"  {name:30s} {p.lufs:6.1f} LU {p.dynamics:6.1f} dB {p.brightness:8.0f} Hz {p.width:5.0f} dB")
    return 0
