"""sound ardour: work in the open Ardour session (via the sound bridge `sound daw` installs)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import analysis, arrangement, audiofile, bridge, pitch
from .common import add_section_args, fmt_time, load_section, parse_time

EXAMPLES = """\
examples:
  sound ardour status
  sound ardour import recordings/reference/acapella.mp3 --name reference
  sound ardour import "recordings/Friday, 9h17m PM.mp3" --align-to recordings/reference/acapella.mp3 --ref-start 3:26
  sound ardour lua 'return Session:name ()'
  sound ardour instrument --synth Odin2          # a MIDI track your keyboard plays
  sound ardour instrument --name "grand piano" --synth "ACE Fluid Synth" --preset "Salamander Grand Piano"
  sound ardour reverb "grand piano"              # send it to a hall reverb bus
  sound ardour drums --bpm 72                    # a drum kit playing a groove, looped
  sound ardour guitar --input Unitek             # a distorted guitar track from a USB guitar input
  sound ardour import song.mid --track drums     # or your own MIDI file onto the drum track
  sound ardour instrument --list                 # synths and MIDI controllers Ardour can see
"""


def add_parser(sub) -> None:
    parser = sub.add_parser("ardour", help="import audio into / run Lua in the open Ardour session",
                            description="Talk to the session open in Ardour (open it with `sound daw`).",
                            epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    actions = parser.add_subparsers(dest="action", required=True, metavar="action")

    actions.add_parser("status", help="the open session and its tracks").set_defaults(run=status)

    imp = actions.add_parser("import", help="put audio files on tracks")
    imp.add_argument("files", nargs="+", type=Path)
    imp.add_argument("--at", type=parse_time, default=0.0, metavar="TIME", help="where on the timeline (default: 0:00)")
    imp.add_argument("--align-to", type=Path, metavar="REF",
                     help="line the file up with this reference (which sits at 0:00 in the session)")
    add_section_args(imp, prefix="ref", what="the reference")
    imp.add_argument("--track", help="import onto this existing track (name or id) instead of new tracks")
    imp.add_argument("--name", help="name for the new track (one file only)")
    imp.set_defaults(run=import_files)

    inst = actions.add_parser("instrument", help="add a MIDI track with a synth, played by your keyboard")
    inst.add_argument("--name", default="keys", help="track name (default: %(default)s)")
    inst.add_argument("--synth", default="Odin2", help="instrument plugin (default: %(default)s; see --list)")
    inst.add_argument("--controller", default="USB MIDI",
                      help="connect the MIDI inputs whose name contains this (default: %(default)s)")
    inst.add_argument("--preset", help="one of the synth's presets, by name")
    inst.add_argument("--list", action="store_true", help="list instrument plugins and MIDI inputs instead")
    inst.set_defaults(run=add_instrument)

    dr = actions.add_parser("drums", help="add a drum kit playing a looped groove, at a tempo")
    dr.add_argument("--kit", default="Red Zeppelin Drumkit", help="drum instrument (default: %(default)s)")
    dr.add_argument("--bpm", type=float, default=72.0, help="session tempo (default: %(default)s)")
    dr.add_argument("--bars", type=int, default=8, help="length of the groove (default: %(default)s)")
    dr.add_argument("--name", default="drums", help="track name (default: %(default)s)")
    dr.add_argument("--controller", default="", help="also play the kit from MIDI inputs containing this")
    dr.set_defaults(run=add_drums)

    gt = actions.add_parser("guitar", help="add a distorted guitar track (Guitarix amp), monitored live")
    gt.add_argument("--name", default="guitar", help="track name (default: %(default)s)")
    gt.add_argument("--input", default="Unitek", help="audio input whose name contains this (default: %(default)s)")
    gt.add_argument("--reverb", type=float, default=-20.0, metavar="DB", help="hall reverb send (default: %(default)s dB)")
    gt.add_argument("--list", action="store_true", help="list audio inputs instead")
    gt.set_defaults(run=add_guitar)

    rev = actions.add_parser("reverb", help="send a track to a hall reverb bus")
    rev.add_argument("track", help="track name")
    rev.add_argument("--db", type=float, default=-10.0, help="send level (default: %(default)s dB)")
    rev.add_argument("--bus", default="reverb", help="reverb bus, made the first time (default: %(default)s)")
    rev.set_defaults(run=add_reverb)

    lua = actions.add_parser("lua", help="run Lua with Ardour's scripting API")
    lua.add_argument("code", help="Lua code, or - to read it from stdin")
    lua.set_defaults(run=run_lua)


def status(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        info = bridge.ping()
    except bridge.BridgeError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"{info['session']} ({info['sample_rate']} Hz) at {info['path']}")
    for t in info["tracks"]:
        print(f"  {t['id']:>6}  {t['name']}")
    return 0


def import_files(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        at = args.at
        if args.align_to:
            if len(args.files) != 1:
                parser.error("--align-to lines up one file at a time")
            audio, sr = audiofile.load(args.files[0])
            take = pitch.track(audio.mean(axis=1), sr)
            if not take.voiced.any():
                print(f"{args.files[0]}: no pitched voice to line up", file=sys.stderr)
                return 1
            window = analysis.reference_window(args.ref_start, args.ref_end, len(audio) / sr)
            ref_audio, ref_sr, ref_from, _ = load_section(args.align_to, *window)
            ref = pitch.track(ref_audio.mean(axis=1), ref_sr, min_prob=0.0)
            al = analysis.align(take, ref)
            at = ref_from + analysis.placement(take, al)
            print(f"lines up at {fmt_time(at, 2)} on the reference (tempo {analysis.tempo(take, al):.3f}×)")
            if at < 0:
                print(f"it starts {-at:.2f} s before 0:00, so it goes at 0:00")
                at = 0.0
        result = arrangement.import_files(args.files, at=at, track=args.track, name=args.name)
    except (bridge.BridgeError, FileNotFoundError, RuntimeError, ValueError) as e:
        print(e, file=sys.stderr)
        return 1
    for t in result["added"]:
        print(f"new track {t['name']} (id {t['id']})")
    if not result["added"]:
        print(f"imported onto {args.track}")
    return 0


def add_instrument(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        if args.list:
            print("instruments:")
            for name in arrangement.instruments():
                print(f"  {name}")
            print("MIDI inputs:")
            for port in arrangement.midi_inputs():
                print(f"  {port}")
            return 0
        made = arrangement.add_instrument(args.name, args.synth, args.controller, args.preset)
    except bridge.BridgeError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"new track {made['name']} (id {made['id']}) playing {made['synth']}"
          + (f" ({made['preset']})" if made.get("preset") else ""))
    for port in made["connected"]:
        print(f"  from {port}")
    if not made["connected"]:
        print(f"  no MIDI input matched {args.controller!r}: see `sound ardour instrument --list`")
    return 0


def add_drums(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        made = arrangement.add_drums(args.kit, args.bpm, args.bars, args.name, args.controller)
    except bridge.BridgeError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"new track {made['name']} (id {made['id']}) playing {made['synth']}: {made['bars']} bars at "
          f"{made['bpm']:g} BPM, looped (press play)")
    return 0


def add_guitar(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        if args.list:
            for port in arrangement.audio_inputs():
                print(port)
            return 0
        made = arrangement.add_guitar(args.name, args.input, args.reverb)
    except bridge.BridgeError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"new track {made['name']} (id {made['id']}): Tube Screamer into a high-gain Guitarix amp, monitored live")
    for port in made["connected"]:
        print(f"  from {port}")
    if not made["connected"]:
        print(f"  no audio input matched {args.input!r}: see `sound ardour guitar --list`")
    return 0


def add_reverb(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        made = arrangement.add_reverb(args.track, args.db, args.bus)
    except bridge.BridgeError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"{made['track']} -> {made['bus']} at {made['send_db']:g} dB"
          + (" (new hall reverb bus)" if made["bus_created"] else ""))
    return 0


def run_lua(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    code = sys.stdin.read() if args.code == "-" else args.code
    try:
        result = bridge.lua(code)
    except bridge.BridgeError as e:
        print(e, file=sys.stderr)
        return 1
    for line in result["printed"]:
        print(line)
    if result["returned"] is not None:
        print(json.dumps(result["returned"], indent=1))
    return 0
