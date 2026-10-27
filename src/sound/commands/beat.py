"""sound beat: turn a beatbox recording into a beat (Strudel code, MIDI, and a kit of your own sounds)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import beatbox
from ..scales import NOTE_NAMES

EXAMPLES = """examples:
  sound beat recordings/beatbox/bbox.mp3            # prints the Strudel code, writes it + MIDI + your kit
  sound beat bbox.mp3 --bars 2                       # loop 2 bars instead of 4
  sound beat bbox.mp3 --style mouth                  # copy the mouth instead of the synth it stands for
  sound beat bbox.mp3 --song                         # also write it laid out as a track
  sound beat bbox.mp3 -o beats/                      # write the files somewhere else
  (then paste the code into the Beat tab of `sound serve`, or strudel.cc)"""


def add_parser(sub) -> None:
    parser = sub.add_parser("beat", help="turn a beatbox recording into a beat (Strudel, MIDI)",
                            description="Find the kicks, snares and hats in a beatbox recording, follow its tempo, "
                                        "pick up the riff of a beatboxed oscillator (its notes, and how the mouth "
                                        "shaped it), and write the groove as a beat: Strudel code to play and edit, "
                                        "a MIDI file, and a kit cut from the recording.",
                            epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path)
    parser.add_argument("--bars", type=int, default=4, help="how many bars of the performance to loop (default 4)")
    parser.add_argument("--style", choices=beatbox.STYLES, default="synth",
                        help="synth: play the riff as the synth it stands for, on the pulse of its bwoms (default); "
                             "mouth: copy how the mouth shaped each note")
    parser.add_argument("--bass", choices=list(beatbox.BASSES), default=beatbox.BASS,
                        help="the riff's sound in the synth style: " + "; ".join(f"{k}: {v['about']}" for k, v in beatbox.BASSES.items()))
    parser.add_argument("--hats", choices=list(beatbox.HATS), default=beatbox.HAT,
                        help="the hats under the drop: " + "; ".join(f"{k}: {v['about']}" for k, v in beatbox.HATS.items()))
    parser.add_argument("--song", action="store_true", help="also write the beat laid out as a track (intro, build, drop, breakdown, drop, outro)")
    parser.add_argument("-o", "--output", type=Path, help="folder for the files (default: next to the input)")
    parser.set_defaults(run=run)


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        g = beatbox.groove(args.input)
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        print(f"{args.input}: {e}", file=sys.stderr)
        return 1
    folder = args.output or args.input.parent
    folder.mkdir(parents=True, exist_ok=True)
    stem = args.input.stem
    bars = beatbox.loop(g, args.bars)
    kit = beatbox.kit(g, args.input, folder / f"{stem} kit")   # for a DAW; browsers can't load local files,
    code = beatbox.strudel(g, stem, None, bars, args.style, args.bass, args.hats)   # so the portable code leaves them out (the web app layers them)
    js = folder / f"{stem} beat.strudel.js"
    js.write_text(code)
    track = folder / f"{stem} song.strudel.js"
    if args.song:
        track.write_text(beatbox.song(g, stem, None, bars, args.style, args.bass, args.hats))
    mid = beatbox.write_midi(g, folder / f"{stem} beat.mid", bars, style=args.style)
    key = f"{NOTE_NAMES[g.root]} minor" if g.root is not None else "A minor (no voiced sound to go by)"
    print(f"{args.input.name}: {g.bpm:g} BPM, {key}, {len(g.bars)} bars with a beat"
          + (f" (left out {len(g.skipped)} without one)" if g.skipped else ""))
    if g.oscillator:
        riff = dict.fromkeys(NOTE_NAMES[n % 12] for i in bars for n in g.voice.notes[i] if n is not None)
        steps = beatbox.pulse(g, bars) if args.style == "synth" else []
        print(f"  the oscillator plays {' '.join(riff)}: it leads the beat, "
              + (f"on {beatbox.BASSES[args.bass]['about'].split(':')[0]}, a note on each bwom (16ths {' '.join(str(k + 1) for k in steps)})"
                 if steps else "on detuned saws, shaped like your mouth shaped it"))
    print(f"  looping bars {', '.join(str(b + 1) for b in bars)}; "
          + ("your snare is the backbeat" if g.snare_played else "no backbeat (the code has a clap to uncomment)"))
    print(f"  -> {js}" + (f"\n  -> {track}" if args.song else "") + f"\n  -> {mid}\n  -> {folder / (stem + ' kit')}/ ({', '.join(kit)})\n")
    print(code)
    return 0
