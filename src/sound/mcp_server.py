"""The engine as MCP tools, so an assistant (Claude Code, or any MCP client) can drive it.

Run with `sound mcp` (stdio). Paths are library paths, as in the web app: "take.wav",
"reference/acapella.mp3", "tuned/take - F# major, natural.wav". Results are saved into the
library, so they also show up in the web app and can be dragged into Ardour.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import analysis, arrangement, audiofile, beatbox, bridge, daw, fx, pitch, timing
from .scales import NOTE_NAMES, note_name
from .server import DEFAULT_ROOT, FOLDERS, AUDIO_SUFFIXES
from .tune import TuneSettings, autotune

INSTRUCTIONS = """\
Vocal tools for a singer's library of takes, reference vocals and renders.
Typical flow: list_library -> analyze a take (and the reference section it's sung against)
-> tune it (style "natural" keeps the performance; pass key from the reference's analysis)
-> optionally produce it toward the reference's sound -> compare takes against the reference.
Times are seconds or "m:ss" strings. Paths are relative to the library root.
Ardour (the DAW): ardour_open opens a session with everything switched on, ardour_import puts
library audio on tracks (lined up with a reference if asked), ardour_add_instrument makes a
synth track the MIDI keyboard plays, ardour_lua runs Lua with Ardour's scripting API for
anything else. Ardour's own MCP server ("ardour") mixes, edits regions, adds
plugins and drives the transport, using the same track ids.
"""


def parse_seconds(value: float | str | None) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    total = 0.0
    try:
        for part in str(value).strip().split(":"):
            total = total * 60 + float(part)
    except ValueError:
        raise ToolError(f"not a time: {value!r} (use seconds or m:ss)") from None
    return total


class Library:
    """Paths, audio and pitch tracks for the tools, with small caches (pitch tracking is slow)."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        for folder in FOLDERS.values():
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._audio: OrderedDict = OrderedDict()
        self._tracks: OrderedDict = OrderedDict()

    def resolve(self, rel: str) -> Path:
        path = (self.root / rel).resolve()
        if self.root not in path.parents or not path.is_file():
            raise ToolError(f"no such file in the library: {rel} (see list_library)")
        return path

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def audio(self, path: Path) -> tuple[np.ndarray, int]:
        return self._cached(self._audio, (path, path.stat().st_mtime), 6, lambda: audiofile.load(path))

    def section(self, path: Path, start: float | None, end: float | None):
        audio, sr = self.audio(path)
        start = max(0.0, start or 0.0)
        end = len(audio) / sr if end is None else min(end, len(audio) / sr)
        if end <= start:
            raise ToolError("the section is empty: end must come after start")
        return audio[int(start * sr): int(end * sr)], sr, start, end

    def track(self, path: Path, start: float | None = None, end: float | None = None, loose: bool = False):
        audio, sr, start, end = self.section(path, start, end)
        key = (path, path.stat().st_mtime, round(start, 3), round(end, 3), loose)
        track = self._cached(self._tracks, key, 32, lambda: pitch.track(
            audio.mean(axis=1), sr, min_prob=0.0 if loose else pitch.MIN_VOICED_PROB))
        return track, audio, sr, start, end

    def save_render(self, name: str, audio: np.ndarray, sr: int) -> str:
        stem = "".join(c if c.isalnum() or c in " -_.,()#" else "_" for c in name).strip(" .") or "render"
        out = self.root / FOLDERS["render"] / f"{stem}.wav"
        n = 2
        while out.exists():
            out = self.root / FOLDERS["render"] / f"{stem} ({n}).wav"
            n += 1
        audiofile.save(out, audio, sr)
        return self.rel(out)

    def _cached(self, cache: OrderedDict, key, size: int, make):
        with self._lock:
            if key in cache:
                cache.move_to_end(key)
                return cache[key]
        value = make()
        with self._lock:
            cache[key] = value
            while len(cache) > size:
                cache.popitem(last=False)
        return value


def _summary(s: analysis.Summary) -> dict:
    tonic, mode, confidence = s.key
    return {
        "pitched_share": round(s.voiced, 3),
        "range": [note_name(s.low), note_name(s.high)] if np.isfinite(s.low) else None,
        "tuning_offset_cents": round(s.tuning_cents, 1),
        "tuning_steadiness": round(s.tuning_consistency, 2),
        "key": f"{NOTE_NAMES[tonic]} {mode}", "key_confidence": round(confidence, 2),
        "notes_used": [NOTE_NAMES[pc] for pc in s.notes_used],
        "in_tune_share": round(s.in_tune, 3),
        "typical_cents_off": round(s.median_dev, 1) if np.isfinite(s.median_dev) else None,
    }


def _profile(p: fx.Profile) -> dict:
    return {"lufs": round(p.lufs, 1), "dynamics_db": round(p.dynamics, 1), "brightness_hz": round(p.brightness),
            "width_db": round(p.width, 1) if np.isfinite(p.width) else None}


def create_server(root: Path = DEFAULT_ROOT) -> MCPServer:
    lib = Library(root)
    server = MCPServer(name="sound", version="0.1.0", instructions=INSTRUCTIONS)

    @server.tool()
    def list_library() -> dict[str, Any]:
        """Every take, reference vocal and saved render in the library, with durations."""
        out: dict[str, list] = {kind: [] for kind in FOLDERS}
        for kind, folder in FOLDERS.items():
            for path in sorted((lib.root / folder).iterdir(), key=lambda p: p.name.lower()):
                if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES:
                    try:
                        seconds, sr, channels = audiofile.probe(path)
                    except RuntimeError:
                        continue
                    out[kind].append({"path": lib.rel(path), "seconds": round(seconds, 2), "channels": channels})
        return {"root": str(lib.root), **out}

    @server.tool()
    def analyze(path: str, start: float | str | None = None, end: float | str | None = None,
                loose: bool | None = None) -> dict[str, Any]:
        """Pitch range, tuning, key, notes used and accuracy of a recording or a section of it.

        loose reads pitch where the voice is unclear (stem-separated acapellas, reverb); it
        defaults to on for files in reference/."""
        source = lib.resolve(path)
        loose = path.startswith(FOLDERS["reference"] + "/") if loose is None else loose
        track, audio, sr, s, e = lib.track(source, parse_seconds(start), parse_seconds(end), loose)
        held = analysis.notes(track)
        return {"path": path, "section": [round(s, 2), round(e, 2)], **_summary(analysis.summarize(track)),
                "held_notes": len(held), "production": _profile(fx.measure(audio, sr, track))}

    @server.tool()
    def timing_of(reference: str, start: float | str, end: float | str | None = None) -> dict[str, Any]:
        """Where the voice comes in at or after `start` in a reference, the local tempo, and
        whether the entry is on the beat or a pickup."""
        source = lib.resolve(reference)
        s, e = parse_seconds(start), parse_seconds(end)
        audio, sr = lib.audio(source)
        beats = timing.beats(audio.mean(axis=1), sr)
        until = min(len(audio) / sr, e if e is not None else s + 30)
        track, section, sr, look_from, _ = lib.track(source, max(0.0, s - 0.5), until, loose=True)
        found = timing.entry(section.mean(axis=1), sr, track)
        entry = look_from + found if found is not None else None
        tempo = timing.tempo_near(beats, s, 10.0) or timing.tempo_near(beats, s)
        pickup = None
        if entry is not None and len(beats) and tempo:
            off = entry - beats[np.argmin(np.abs(beats - entry))]
            pickup = round(float(off), 3) if abs(off) > 0.2 * 60 / tempo else 0.0
        return {"entry_seconds": round(entry, 3) if entry is not None else None,
                "tempo_bpm": round(tempo, 1) if tempo else None,
                "pickup_seconds": pickup, "note": "pickup < 0 means the voice comes in before the beat"}

    @server.tool()
    def tune(path: str, key: str | None = None, scale: str | None = None, style: str = "natural",
             strength: float = 1.0, humanize: float = 0.25, drift: float = 0.0, speed_ms: float = 25.0,
             name: str | None = None) -> dict[str, Any]:
        """Pitch-correct a take and save the result into tuned/.

        key: None for the nearest semitone, "auto" to detect, or e.g. "F#", "F# major", "Am".
        style: "natural" (moves whole notes, keeps vibrato and slides) or "autotune" (pulls
        every moment to the note; speed_ms 0 is the robotic effect)."""
        if style not in ("natural", "autotune"):
            raise ToolError('style must be "natural" or "autotune"')
        source = lib.resolve(path)
        track, audio, sr, *_ = lib.track(source)
        if not track.voiced.any():
            raise ToolError("no pitched voice found in that file")
        try:
            choice = analysis.choose_key(track, key, scale)
        except ValueError as e:
            raise ToolError(str(e)) from None
        settings = TuneSettings(tonic=choice.tonic, scale=choice.scale, style=style, strength=strength,
                                humanize=humanize, drift=drift, speed_ms=speed_ms)
        result = autotune(audio, sr, settings, track=track)
        label = "chromatic" if choice.scale == "chromatic" else f"{NOTE_NAMES[choice.tonic]} {choice.scale}"
        saved = lib.save_render(name or f"{source.stem} - {label}, {style}", result.audio, sr)
        moved = np.abs(result.corrected - result.detected)[track.voiced] * 100
        return {"saved": saved, "key_used": label, "key_decided_by": choice.source,
                "correction_cents": {"median": round(float(np.median(moved)), 1), "max": round(float(moved.max()), 1)}}

    @server.tool()
    def produce(path: str, reference: str | None = None, ref_start: float | str | None = None,
                ref_end: float | str | None = None, grit: float = 0.5, compression: float = 0.45,
                tone: float = 1.0, deess: float = 0.5, width: float = 0.3, doubles: float = 0.3,
                space: float = 0.25, loudness_lufs: float | None = None, name: str | None = None) -> dict[str, Any]:
        """Run the vocal chain (tone match, compression, saturation, de-ess, doubles, reverb,
        loudness) on a take or render and save it into tuned/. With a reference (and
        section), tone and loudness are matched to it; otherwise a forward rock-vocal sound."""
        source = lib.resolve(path)
        track, audio, sr, *_ = lib.track(source)
        target = None
        if reference:
            ref_track, ref_audio, ref_sr, *_ = lib.track(lib.resolve(reference), parse_seconds(ref_start),
                                                          parse_seconds(ref_end), loose=True)
            target = fx.measure(ref_audio, ref_sr, ref_track)
        loud = loudness_lufs if loudness_lufs is not None else (target.lufs if target else -12.0)
        chain = fx.Chain(grit=grit, compression=compression, tone=tone, deess=deess, width=width,
                         doubles=doubles, space=space, loudness=loud)
        out = fx.process(audio, sr, chain, track=track, reference=target)
        saved = lib.save_render(name or f"{source.stem} - produced", out, sr)
        return {"saved": saved, "result": _profile(fx.measure(out, sr, track)),
                "reference": _profile(target) if target else None}

    @server.tool()
    def compare(reference: str, takes: list[str], ref_start: float | str | None = None,
                ref_end: float | str | None = None) -> dict[str, Any]:
        """Line each take up with a reference section (any tempo, any octave) and score how
        close its pitch is. Versions of one recording share the untuned take's timing."""
        if not takes:
            raise ToolError("give at least one take")
        loaded = []
        for t in takes:
            source = lib.resolve(t)
            track, audio, sr, *_ = lib.track(source)
            loaded.append((t, source, track, len(audio) / sr))
        longest = max(seconds for *_, seconds in loaded)
        window = analysis.reference_window(parse_seconds(ref_start), parse_seconds(ref_end), longest)
        ref, *_, ref_from, ref_to = lib.track(lib.resolve(reference), *window, loose=True)
        order = sorted(range(len(loaded)), key=lambda i: not loaded[i][1].parent == lib.root)  # untuned first
        results = dict(zip(order, analysis.compare(ref, [(loaded[i][0], loaded[i][2]) for i in order])))
        rows = []
        for i, (name, *_rest) in enumerate(loaded):
            res = results[i]
            if res.agreement is None:
                rows.append({"take": name, "note": "no pitched voice found"})
                continue
            ag = res.agreement
            rows.append({"take": name, "first_note_at": round(ref_from + res.first_note, 2),
                         "tempo_vs_reference": round(res.tempo, 3), "octaves_below": res.alignment.octave // 12,
                         "typical_cents_off": round(ag.median_abs, 1), "within_25c": round(ag.within_25, 3),
                         "within_50c": round(ag.within_50, 3), "bias_cents": round(ag.bias, 1),
                         "notes_right": f"{ag.notes_matched}/{ag.notes_compared}"})
        return {"reference_section": [round(ref_from, 2), round(ref_to, 2)],
                "reference": _summary(analysis.summarize(ref)), "takes": rows}

    def _bridge(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except bridge.BridgeError as e:
            raise ToolError(str(e)) from None

    @server.tool()
    def ardour_open(session: str = "friday", new: bool = False, buffer: int | None = None) -> dict[str, Any]:
        """Open Ardour on a session in sessions/ (new=True creates it, 48 kHz), with its MCP
        server, OSC and the sound bridge on and no startup dialogs. buffer sets the engine's
        samples per cycle (128 for playing live through it; kept for next time). Takes up to a
        minute or so."""
        try:
            opened = daw.open_session(session, new=new, buffer=buffer)
        except RuntimeError as e:
            raise ToolError(str(e)) from None
        if opened["outcome"] != "mcp":
            opened["hint"] = f"Ardour didn't finish starting; a dialog may be waiting. Log: {daw.LOG}"
        return opened

    @server.tool()
    def ardour_status() -> dict[str, Any]:
        """Whether Ardour is open, on which session, its tracks (ids as Ardour's MCP uses) and
        their regions: where they sit (seconds) and which library file each came from."""
        if not daw.running():
            return {"open": False, "hint": "open it with ardour_open"}
        info = _bridge(bridge.ping)
        snap = _bridge(arrangement.snapshot)
        regions = {}
        for track, rs in snap.tracks.items():
            regions[track] = []
            for r in rs:
                origin = arrangement.library_file(r, snap.path, lib.root)
                regions[track].append({"id": r.id, "name": r.name, "position": round(r.position, 3),
                                       "length": round(r.length, 3), "muted": r.muted,
                                       "library_file": lib.rel(origin) if origin and lib.root in origin.parents else None})
        return {"open": True, **info, "playhead": round(snap.playhead, 3), "regions": regions,
                "range_selection": snap.time_selection, "loop": snap.loop}

    @server.tool()
    def ardour_import(path: str, at: float | str | None = None, align_to: str | None = None,
                      ref_start: float | str | None = None, ref_end: float | str | None = None,
                      track: str | None = None, name: str | None = None) -> dict[str, Any]:
        """Put a library file into the session open in Ardour: on a new track (called `name`, or
        after the file) or onto an existing `track` (name or id). Place it `at` a time on the
        session timeline, or align_to a reference (library path, plus the section it's sung
        against) to land it where it lines up with that reference starting at 0:00 (import the
        reference itself first, at 0)."""
        source = lib.resolve(path)
        _bridge(bridge.ping)
        seconds = parse_seconds(at) or 0.0
        aligned = None
        if align_to:
            take, audio, sr, *_ = lib.track(source)
            if not take.voiced.any():
                raise ToolError("no pitched voice in that file to line up with the reference")
            window = analysis.reference_window(parse_seconds(ref_start), parse_seconds(ref_end), len(audio) / sr)
            ref, *_, ref_from, _ = lib.track(lib.resolve(align_to), *window, loose=True)
            al = analysis.align(take, ref)
            seconds = ref_from + analysis.placement(take, al)
            aligned = {"reference": align_to, "tempo_vs_reference": round(analysis.tempo(take, al), 3)}
        note = None
        if seconds < 0:
            note = f"the file starts {-seconds:.2f} s before 0:00 on the timeline, so it was placed at 0:00"
            seconds = 0.0
        result = _bridge(arrangement.import_files, [source], at=seconds, track=track, name=name)
        return {"at_seconds": round(seconds, 3), "aligned": aligned, "note": note, **result}

    @server.tool()
    def ardour_instruments() -> dict[str, Any]:
        """Instrument plugins (synths, samplers) Ardour can load, and the MIDI inputs it sees
        (keyboards and controllers)."""
        return {"instruments": _bridge(arrangement.instruments), "midi_inputs": _bridge(arrangement.midi_inputs)}

    @server.tool()
    def ardour_add_instrument(name: str = "keys", synth: str = "Odin2", controller: str = "USB MIDI",
                              preset: str | None = None) -> dict[str, Any]:
        """Add a MIDI track playing `synth` (optionally one of its presets, e.g. synth "ACE
        Fluid Synth" with preset "Salamander Grand Piano" for a grand piano), connected to the
        MIDI inputs whose name contains `controller` and monitoring them, so the keyboard sounds
        straight away."""
        return _bridge(arrangement.add_instrument, name, synth, controller, preset)

    @server.tool()
    def ardour_add_drums(kit: str = "Red Zeppelin Drumkit", bpm: float = 72.0, bars: int = 8,
                         name: str = "drums") -> dict[str, Any]:
        """Add a drum track (a General MIDI drum instrument) with the session set to `bpm` in
        4/4, an original heavy rock groove of `bars` bars from bar 1 and the loop range over it.
        Other parts go onto the track with ardour_import (a .mid file, track=name)."""
        return _bridge(arrangement.add_drums, kit, bpm, bars, name)

    @server.tool()
    def ardour_add_guitar(name: str = "guitar", input: str = "Unitek", reverb_db: float = -20.0) -> dict[str, Any]:
        """Add a distorted guitar track: the audio input whose name contains `input` (a USB guitar
        adapter, an interface's input) through a Tube Screamer and a high-gain Guitarix amp,
        monitored live, with a hall reverb send at reverb_db. Open Ardour with buffer=128 for
        playing live."""
        return _bridge(arrangement.add_guitar, name, input, reverb_db)

    @server.tool()
    def ardour_add_reverb(track: str, db: float = -10.0, bus: str = "reverb") -> dict[str, Any]:
        """Send a track to a hall reverb bus (Dragonfly Hall, made the first time, voiced for
        acoustic instruments) at `db`."""
        return _bridge(arrangement.add_reverb, track, db, bus)

    @server.tool()
    def ardour_lua(code: str) -> dict[str, Any]:
        """Run Lua inside Ardour with its scripting API (Session, Editor, ARDOUR, ...; see
        manual.ardour.org/lua-scripting). Returns what the code returns and prints."""
        return _bridge(bridge.lua, code)

    @server.tool()
    def beat_from_beatbox(path: str, bars: int = 4, style: str = "synth", form: str = "loop", bass: str = beatbox.BASS,
                          hats: str = beatbox.HAT) -> dict[str, Any]:
        """Turn a beatbox recording into a beat: its tempo, the groove as performed (per bar, 16 steps
        per sound: kick, mid, high), the riff of a beatboxed oscillator if there is one (note per 16th),
        and Strudel code that plays it as a beat. style "synth": the riff as the synth it stands for, on
        the pulse of its bwoms, each a filter wow on detuned saws, over a deep kick it ducks under; "mouth":
        the riff shaped like the mouth shaped it. Without a riff, the drums on a drum machine. form "loop"
        is the groove looped; "song" lays it out as a track (intro, build, drop, breakdown, drop, outro).
        bass picks the riff's sound in the synth style (synth, bright, fm, pluck, finger); hats the hats under the
        drop (pulse, straight, offbeat). Paste the code into the web app's Beat tab or strudel.cc."""
        source = lib.resolve(path)
        try:
            g = beatbox.groove(source)
        except ValueError as e:
            raise ToolError(f"{path}: {e}")
        if style not in beatbox.STYLES:
            raise ToolError(f"style is one of {', '.join(beatbox.STYLES)}")
        if form not in beatbox.FORMS:
            raise ToolError(f"form is one of {', '.join(beatbox.FORMS)}")
        if bass not in beatbox.BASSES:
            raise ToolError(f"bass is one of {', '.join(beatbox.BASSES)}")
        if hats not in beatbox.HATS:
            raise ToolError(f"hats is one of {', '.join(beatbox.HATS)}")
        sel = beatbox.loop(g, max(1, min(8, bars)))
        return {"bpm": g.bpm, "key": f"{NOTE_NAMES[g.root]} minor" if g.root is not None else "A minor",
                "bars": g.bars, "loop": sel, "snare_played": g.snare_played,
                "skipped_seconds": [[round(a, 2), round(b, 2)] for a, b in g.skipped],
                "riff": [[NOTE_NAMES[n % 12] if n is not None else None for n in g.voice.notes[i]] for i in sel]
                        if g.oscillator else None,
                "pulse": beatbox.pulse(g, sel) if g.oscillator else [],
                "code": (beatbox.song if form == "song" else beatbox.strudel)(g, source.stem, None, sel, style, bass, hats)}

    return server


def main(root: Path = DEFAULT_ROOT) -> None:
    create_server(root).run(transport="stdio")
