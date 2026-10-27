"""sound cut: export a section of a recording."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from .. import audiofile
from .common import file_stamp, fmt_time, load_section, parse_time


def add_parser(sub) -> None:
    parser = sub.add_parser("cut", help="export a section of a recording as wav",
                            description="Export a section of a recording as a 24-bit wav.")
    parser.add_argument("input", type=Path)
    parser.add_argument("start", type=parse_time, help="e.g. 3:26 or 206")
    parser.add_argument("end", type=parse_time, nargs="?", help="default: the end of the file")
    parser.add_argument("-o", "--output", type=Path, help="default: <name>_<start>-<end>.wav next to the input")
    parser.set_defaults(run=run)


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        audio, sr, start, end = load_section(args.input, args.start, args.end)
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        print(f"{args.input}: {e}", file=sys.stderr)
        return 1
    out = args.output or args.input.with_name(f"{args.input.stem}_{file_stamp(start)}-{file_stamp(end)}.wav")
    if out.resolve() == args.input.resolve():
        print(f"{args.input}: refusing to overwrite the input", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    gain = audiofile.save(out, audio, sr)
    print(f"{args.input.name} [{fmt_time(start)}–{fmt_time(end)}], {end - start:.1f}s -> {out}")
    if gain < 1.0:
        print(f"  turned down {20 * np.log10(gain):.1f} dB: the source goes past full scale")
    return 0
