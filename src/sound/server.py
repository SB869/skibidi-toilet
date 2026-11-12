"""Local web app: a JSON API over the engine, plus the built frontend. Run with `sound serve`.

Files live in a workspace folder (default: <project>/recordings):
    ./            your takes
    reference/    reference vocals (e.g. original acapellas)
    tuned/        saved renders
"""

from __future__ import annotations

import re
import tempfile
import threading
import time
import uuid
from urllib.parse import quote
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import analysis, arrangement, audiofile, beatbox, bridge, fx, pitch, timing
from .scales import NOTE_NAMES, SCALES, midi_to_hz, note_name
from .tune import TuneResult, TuneSettings, autotune

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = PROJECT_ROOT / "recordings"
DEFAULT_WEB = PROJECT_ROOT / "web" / "dist"

FOLDERS = {"take": ".", "reference": "reference", "render": "tuned", "beatbox": "beatbox", "track": "tracks"}
AUDIO_SUFFIXES = {".wav", ".flac", ".aif", ".aiff", ".ogg", ".mp3", ".m4a", ".aac", ".opus", ".webm", ".mp4"}
PEAK_BUCKETS = 4000
GUIDE_HOP = 0.02


@dataclass
class Render:
    id: str
    source: Path
    audio: np.ndarray
    sr: int
    result: TuneResult
    settings: TuneSettings
    choice: analysis.KeyChoice
    fx: bool = False                        # went through the vocal chain
    track: pitch.PitchTrack | None = None   # pitch of the rendered audio, measured on demand


class _LRU:
    def __init__(self, size: int):
        self.size, self.items, self.lock = size, OrderedDict(), threading.Lock()

    def get(self, key, make):
        with self.lock:
            if key in self.items:
                self.items.move_to_end(key)
                return self.items[key]
        value = make()  # outside the lock: slow work shouldn't block other requests
        self.put(key, value)
        return value

    def put(self, key, value):
        with self.lock:
            self.items[key] = value
            self.items.move_to_end(key)
            while len(self.items) > self.size:
                self.items.popitem(last=False)

    def find(self, key):
        with self.lock:
            return self.items.get(key)


@dataclass
class Workspace:
    root: Path
    _audio: _LRU = field(default_factory=lambda: _LRU(8))
    _tracks: _LRU = field(default_factory=lambda: _LRU(48))
    _probes: _LRU = field(default_factory=lambda: _LRU(512))
    _references: _LRU = field(default_factory=lambda: _LRU(16))
    _loudness: _LRU = field(default_factory=lambda: _LRU(64))
    _beats: _LRU = field(default_factory=lambda: _LRU(16))
    _grooves: _LRU = field(default_factory=lambda: _LRU(8))
    renders: _LRU = field(default_factory=lambda: _LRU(24))

    def __post_init__(self):
        self.root = self.root.resolve()
        for folder in FOLDERS.values():
            (self.root / folder).mkdir(parents=True, exist_ok=True)

    def resolve(self, rel: str) -> Path:
        path = (self.root / rel).resolve()
        if self.root not in path.parents or not path.is_file():
            raise HTTPException(404, f"no such file: {rel}")
        return path

    def kind(self, path: Path) -> str:
        parent = path.parent
        for kind, folder in FOLDERS.items():
            if parent == (self.root / folder).resolve():
                return kind
        return "take"

    def describe(self, path: Path) -> dict:
        stat = path.stat()
        duration, sr, channels = self._probes.get((path, stat.st_mtime), lambda: audiofile.probe(path))
        return {"path": path.relative_to(self.root).as_posix(), "name": path.name, "kind": self.kind(path),
                "duration": duration, "sample_rate": sr, "channels": channels,
                "size": stat.st_size, "modified": stat.st_mtime}

    def files(self) -> list[dict]:
        out = []
        for folder in FOLDERS.values():
            for path in sorted((self.root / folder).iterdir(), key=lambda p: p.name.lower()):
                if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES:
                    try:
                        out.append(self.describe(path))
                    except RuntimeError:
                        pass  # not decodable; leave it out
        return out

    def audio(self, path: Path) -> tuple[np.ndarray, int]:
        return self._audio.get((path, path.stat().st_mtime), lambda: audiofile.load(path))

    def section(self, path: Path, start: float | None, end: float | None) -> tuple[np.ndarray, int, float, float]:
        audio, sr = self.audio(path)
        duration = len(audio) / sr
        start = max(0.0, start or 0.0)
        end = duration if end is None else min(end, duration)
        if start >= end:
            raise HTTPException(422, "empty section")
        return audio[int(start * sr): int(end * sr)], sr, start, end

    def track(self, path: Path, start: float | None = None, end: float | None = None,
              loose: bool = False) -> tuple[pitch.PitchTrack, np.ndarray, int, float, float]:
        audio, sr, start, end = self.section(path, start, end)
        key = (path, path.stat().st_mtime, round(start, 3), round(end, 3), loose)
        track = self._tracks.get(key, lambda: pitch.track(
            audio.mean(axis=1), sr, min_prob=0.0 if loose else pitch.MIN_VOICED_PROB))
        return track, audio, sr, start, end

    def reference_profile(self, path: Path, start: float | None, end: float | None) -> fx.Profile:
        """The production profile of (a section of) a reference vocal, read loosely like compare does."""
        def make():
            track, audio, sr, *_ = self.track(path, start, end, loose=True)
            return fx.measure(audio, sr, track)
        return self._references.get((path, path.stat().st_mtime, start, end), make)

    def beats(self, path: Path) -> np.ndarray:
        def make():
            audio, sr = self.audio(path)
            return timing.beats(audio.mean(axis=1), sr)
        return self._beats.get((path, path.stat().st_mtime), make)

    def lufs(self, path: Path) -> float:
        def make():
            audio, sr = self.audio(path)
            return fx.measure(audio, sr).lufs
        return self._loudness.get((path, path.stat().st_mtime), make)

    def render(self, rid: str) -> Render:
        render = self.renders.find(rid)
        if render is None:
            raise HTTPException(404, "that render has expired; tune again")
        return render

    def unique(self, folder: str, stem: str, suffix: str) -> Path:
        stem = re.sub(r"[^\w\-.,()# ]+", "_", stem).strip(" .") or "untitled"
        path = self.root / folder / f"{stem}{suffix}"
        n = 2
        while path.exists():
            path = self.root / folder / f"{stem} ({n}){suffix}"
            n += 1
        return path


# ---------------------------------------------------------------- JSON helpers

def _floats(values: np.ndarray, digits: int = 3) -> list[float | None]:
    return [round(float(v), digits) if np.isfinite(v) else None for v in values]


def _midi(track: pitch.PitchTrack, a4: float = 440.0) -> list[float | None]:
    with np.errstate(divide="ignore", invalid="ignore"):
        return _floats(69 + 12 * np.log2(track.f0 / a4))


def _peaks(audio: np.ndarray) -> dict:
    mono = audio.mean(axis=1)
    buckets = max(1, min(PEAK_BUCKETS, len(mono)))
    edges = np.linspace(0, len(mono), buckets + 1).astype(int)[:-1]
    return {"min": _floats(np.minimum.reduceat(mono, edges)), "max": _floats(np.maximum.reduceat(mono, edges))}


def _key(tonic: int, mode: str, confidence: float | None = None) -> dict:
    return {"tonic": tonic, "note": NOTE_NAMES[tonic], "scale": mode,
            "label": "every semitone" if mode == "chromatic" else f"{NOTE_NAMES[tonic]} {mode}",
            "confidence": confidence}


def _summary(s: analysis.Summary) -> dict:
    tonic, mode, r = s.key
    finite = np.isfinite(s.low)
    return {"voiced": s.voiced, "low": note_name(s.low) if finite else None,
            "high": note_name(s.high) if finite else None, "tuning_cents": s.tuning_cents,
            "tuning_consistency": s.tuning_consistency, "key": _key(tonic, mode, r),
            "notes_used": [NOTE_NAMES[pc] for pc in s.notes_used], "in_tune": s.in_tune,
            "median_dev": s.median_dev if np.isfinite(s.median_dev) else None}


def _profile(p: fx.Profile) -> dict:
    return {"lufs": p.lufs, "dynamics": p.dynamics, "brightness": p.brightness,
            "width": p.width if np.isfinite(p.width) else None}


def _warp(take: pitch.PitchTrack, ref: pitch.PitchTrack, al: analysis.Alignment) -> np.ndarray:
    """The take's f0 moved onto the reference's frames along the alignment: what was sung against
    each moment of the reference, timing aside (NaN where the take doesn't reach)."""
    inside = (ref.times >= al.ref_t[0]) & (ref.times <= al.ref_t[-1])
    frame = np.clip(np.round(np.interp(ref.times, al.ref_t, al.take_t) / take.hop_s).astype(int), 0, len(take.f0) - 1)
    return np.where(inside, take.f0[frame], np.nan)


def _hz_to_midi(f0: np.ndarray, a4: float = 440.0) -> list[float | None]:
    with np.errstate(divide="ignore", invalid="ignore"):
        return _floats(69 + 12 * np.log2(f0 / a4))


def _notes(track: pitch.PitchTrack, offset: float) -> list[dict]:
    return [{"start": offset + n.start, "end": offset + n.end, "midi": round(n.midi, 3),
             "name": note_name(n.midi), "cents": round(100 * (n.midi - round(n.midi)))}
            for n in analysis.notes(track)]


# ---------------------------------------------------------------- requests

class FxRequest(BaseModel):
    compression: float = Field(0.45, ge=0, le=1)
    grit: float = Field(0.5, ge=0, le=1)
    tone: float = Field(1.0, ge=0, le=1)
    deess: float = Field(0.5, ge=0, le=1)
    width: float = Field(0.3, ge=0, le=1.5)
    doubles: float = Field(0.3, ge=0, le=1)
    space: float = Field(0.25, ge=0, le=1)
    loudness: float | None = Field(None, ge=-30, le=-6)   # None: the reference's, or -12 without one
    reference: str | None = None                          # library path of a vocal to match
    ref_start: float | None = None
    ref_end: float | None = None


class TuneRequest(BaseModel):
    path: str
    key: str | None = None            # None = every semitone, "auto", or e.g. "F#", "Am", "Bb minor"
    scale: str | None = None
    speed: float = Field(25.0, ge=0, le=2000)
    strength: float = Field(1.0, ge=0, le=1)
    hysteresis: float = Field(0.15, ge=0, le=1)
    a4: float = Field(440.0, gt=300, lt=600)
    style: Literal["autotune", "natural"] = "autotune"
    drift: float = Field(0.0, ge=0, le=1)
    humanize: float = Field(0.25, ge=0, le=1)
    fx: FxRequest | None = None                           # run the vocal chain after tuning


class SaveRequest(BaseModel):
    name: str | None = None


class CompareRequest(BaseModel):
    reference: str
    ref_start: float | None = None
    ref_end: float | None = None
    takes: list[str]                  # library paths, or "render:<id>" for an unsaved render


class AddTakeRequest(BaseModel):
    path: str                         # library path of a recorded take
    at: float                         # where it starts on Ardour's timeline, seconds


class CutRequest(BaseModel):
    path: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)


# ---------------------------------------------------------------- app

def create_app(root: Path = DEFAULT_ROOT, web_dir: Path | None = DEFAULT_WEB) -> FastAPI:
    ws = Workspace(root)
    app = FastAPI(title="sound")
    app.state.workspace = ws

    @app.get("/api/files")
    def list_files():
        return {"root": str(ws.root), "files": ws.files(), "notes": NOTE_NAMES,
                "scales": {name: list(steps) for name, steps in SCALES.items()}}

    @app.post("/api/files")
    def upload(file: UploadFile = File(...), kind: str = Form("take"), recorded: bool = Form(False)):
        if kind not in FOLDERS:
            raise HTTPException(422, f"kind must be one of {', '.join(FOLDERS)}")
        name = Path(file.filename or "upload").name
        suffix = Path(name).suffix.lower()
        with tempfile.NamedTemporaryFile(suffix=suffix or ".bin", delete=False) as tmp:
            tmp.write(file.file.read())
        tmp_path = Path(tmp.name)
        try:
            if recorded:
                # Straight from the browser's recorder (webm or ogg): keep it as a plain wav.
                audio, sr = audiofile.load(tmp_path)
                out = ws.unique(FOLDERS[kind], time.strftime("take %Y-%m-%d %H.%M.%S"), ".wav")
                audiofile.save(out, audio, sr)
            else:
                audiofile.probe(tmp_path)  # refuse anything that isn't audio
                out = ws.unique(FOLDERS[kind], Path(name).stem, suffix)
                tmp_path.replace(out)
        except RuntimeError as e:
            raise HTTPException(422, f"{name}: not an audio file I can read ({e})")
        finally:
            tmp_path.unlink(missing_ok=True)
        return ws.describe(out)

    @app.get("/api/audio")
    def get_audio(path: str, start: float | None = None, end: float | None = None):
        audio, sr, *_ = ws.section(ws.resolve(path), start, end)
        return Response(audiofile.to_wav_bytes(audio, sr), media_type="audio/wav")

    @app.get("/api/analysis")
    def get_analysis(path: str, start: float | None = None, end: float | None = None, loose: bool = False):
        source = ws.resolve(path)
        track, audio, sr, start, end = ws.track(source, start, end, loose)
        return {"file": ws.describe(source), "start": start, "end": end, "hop": track.hop_s,
                "pitch": _midi(track), "peaks": _peaks(audio),
                "summary": _summary(analysis.summarize(track)), "notes": _notes(track, start)}

    @app.get("/api/overview")
    def get_overview(path: str):
        """The whole file at a glance, for picking a section by eye."""
        audio, sr = ws.audio(ws.resolve(path))
        return {"duration": len(audio) / sr, "peaks": _peaks(audio)}

    @app.get("/api/timing")
    def get_timing(path: str, start: float = 0.0, end: float | None = None):
        """Beats around a section, its tempo, and when the voice comes in at or after `start`."""
        source = ws.resolve(path)
        beat_times = ws.beats(source)
        audio, sr = ws.audio(source)
        until = min(len(audio) / sr, end if end is not None else start + 30.0)
        look_from = max(0.0, start - 0.5)   # a hair early, in case the voice starts right on `start`
        track, section, sr, look_from, _ = ws.track(source, look_from, until, loose=True)
        found = timing.entry(section.mean(axis=1), sr, track)
        near = beat_times[(beat_times > start - 30) & (beat_times < until + 5)]
        # the tempo right around the entry: songs drift, and the count-in should match this bit
        local = timing.tempo_near(beat_times, start, window=10.0) or timing.tempo_near(beat_times, start)
        return {"tempo": local, "beats": _floats(near, 3),
                "entry": look_from + found if found is not None else None}

    @app.get("/api/guide")
    def get_guide(path: str, start: float | None = None, end: float | None = None):
        """What a reference section does over time, to sing along against: its melody, how
        loud each moment is, and how much edge the voice has."""
        source = ws.resolve(path)
        track, audio, sr, start, end = ws.track(source, start, end, loose=True)
        level, edge = analysis.level_and_edge(audio.mean(axis=1), sr, track, GUIDE_HOP)
        summary = analysis.summarize(track)
        sung_edge = edge[np.isfinite(edge)]
        return {"path": path, "start": start, "end": end, "hop": track.hop_s, "pitch": _midi(track),
                "env_hop": GUIDE_HOP, "level": _floats(level, 1), "edge": _floats(edge, 1),
                "typical_edge": float(np.median(sung_edge)) if sung_edge.size else None,
                "summary": _summary(summary)}

    @app.post("/api/tune")
    def tune(req: TuneRequest):
        source = ws.resolve(req.path)
        track, audio, sr, *_ = ws.track(source)
        if not track.voiced.any():
            raise HTTPException(422, "no pitched voice found; nothing to tune")
        if req.scale is not None and req.scale not in SCALES:
            raise HTTPException(422, f"unknown scale {req.scale!r}")
        try:
            choice = analysis.choose_key(track, req.key, req.scale, req.a4)
        except ValueError as e:
            raise HTTPException(422, str(e))
        settings = TuneSettings(tonic=choice.tonic, scale=choice.scale, speed_ms=req.speed,
                                strength=req.strength, hysteresis=req.hysteresis, a4=req.a4,
                                style=req.style, drift=req.drift, humanize=req.humanize)
        result = autotune(audio, sr, settings, track=track)
        out, sound = result.audio, None
        if req.fx is not None:
            out, sound = _produce(result, track, sr, req)
        render = Render(uuid.uuid4().hex[:12], source, out, sr, result, settings, choice, fx=req.fx is not None)
        ws.renders.put(render.id, render)

        cents = np.abs(result.corrected - result.detected)[track.voiced] * 100
        est = choice.estimate
        return {"id": render.id, "source": req.path, "style": req.style, "hop": track.hop_s,
                "key": _key(choice.tonic, choice.scale) | {"source": choice.source},
                "estimate": _key(*est), "target": _floats(result.target, 2),
                "corrected": _floats(result.corrected), "correction_median": float(np.median(cents)),
                "correction_max": float(cents.max()), "sound": sound,
                "lufs": fx.measure(out, sr).lufs if sound is None else sound["output"]["lufs"],
                "original_lufs": ws.lufs(source)}

    def _produce(result: TuneResult, track: pitch.PitchTrack, sr: int, req: TuneRequest):
        """The vocal chain on a tuned take: (audio, profiles for display)."""
        opts = req.fx
        reference = None
        if opts.reference:
            reference = ws.reference_profile(ws.resolve(opts.reference), opts.ref_start, opts.ref_end)
        loudness = opts.loudness if opts.loudness is not None else (reference.lufs if reference else -12.0)
        chain = fx.Chain(compression=opts.compression, grit=opts.grit, tone=opts.tone, deess=opts.deess,
                         width=opts.width, doubles=opts.doubles, space=opts.space, loudness=loudness)
        # the tuned audio's pitch: the original's, moved by the correction
        sung = pitch.PitchTrack(track.times, np.where(track.voiced, midi_to_hz(result.corrected, req.a4), np.nan),
                                track.voiced)
        out = fx.process(result.audio, sr, chain, track=sung, reference=reference)
        sound = {"output": _profile(fx.measure(out, sr, sung)),
                 "reference": _profile(reference) if reference is not None else None}
        return out, sound

    @app.get("/api/renders/{rid}/audio")
    def render_audio(rid: str):
        render = ws.render(rid)
        return Response(audiofile.to_wav_bytes(render.audio, render.sr), media_type="audio/wav")

    @app.post("/api/renders/{rid}/save")
    def save_render(rid: str, req: SaveRequest):
        render = ws.render(rid)
        s = render.settings
        key = "chromatic" if s.scale == "chromatic" else f"{NOTE_NAMES[s.tonic]} {s.scale}"
        style = "natural" if s.style == "natural" else f"{s.speed_ms:g}ms"
        default = f"{render.source.stem} - {key}, {style}" + (", produced" if render.fx else "")
        out = ws.unique(FOLDERS["render"], Path(req.name).stem if req.name else default, ".wav")
        audiofile.save(out, render.audio, render.sr)
        return ws.describe(out)

    @app.post("/api/compare")
    def compare(req: CompareRequest):
        if not req.takes:
            raise HTTPException(422, "pick at least one take")
        takes, untuned, longest = [], [], 0.0
        for take in req.takes:
            if take.startswith("render:"):
                render = ws.render(take.removeprefix("render:"))
                if render.track is None:
                    render.track = pitch.track(render.audio.mean(axis=1), render.sr)
                takes.append((f"{render.source.name} (current render)", render.track))
                untuned.append(False)
                longest = max(longest, len(render.audio) / render.sr)
            else:
                source = ws.resolve(take)
                track, audio, sr, *_ = ws.track(source)
                takes.append((source.name, track))
                untuned.append(ws.kind(source) == "take")
                longest = max(longest, len(audio) / sr)
        # Versions of one recording share the alignment of whichever is scored first, so put
        # untuned takes first: timing should come from the raw performance, not a tuned copy.
        order = sorted(range(len(takes)), key=lambda i: not untuned[i])

        ref_path = ws.resolve(req.reference)
        window = analysis.reference_window(req.ref_start, req.ref_end, longest)
        ref, _, _, ref_from, ref_to = ws.track(ref_path, *window, loose=True)
        results = dict(zip(order, analysis.compare(ref, [takes[i] for i in order])))
        rows = []
        for take_id, res in zip(req.takes, (results[i] for i in range(len(takes)))):
            row = {"take": take_id, "name": res.name, "hop": res.track.hop_s}
            if res.agreement is not None:
                ag = res.agreement
                row |= {"first_note": ref_from + res.first_note, "tempo": res.tempo,
                        "octave": res.alignment.octave, "off_by": ag.median_abs, "within_25": ag.within_25,
                        "within_50": ag.within_50, "bias": ag.bias, "notes_matched": ag.notes_matched,
                        "notes_compared": ag.notes_compared, "pitch": _midi(res.track),
                        "reference": _floats(analysis.reference_on_take(res.track, ref, res.alignment))}
            rows.append(row)
        return {"reference": {"path": req.reference, "start": ref_from, "end": ref_to,
                              "summary": _summary(analysis.summarize(ref))}, "rows": rows}

    @app.post("/api/cut")
    def cut(req: CutRequest):
        source = ws.resolve(req.path)
        audio, sr, start, end = ws.section(source, req.start, req.end)
        stamp = lambda t: f"{int(t // 60)}m{t % 60:04.1f}s"  # noqa: E731
        out = ws.unique(str(source.parent.relative_to(ws.root)), f"{source.stem} {stamp(start)}-{stamp(end)}", ".wav")
        audiofile.save(out, audio, sr)
        return ws.describe(out)

    # ------------------------------------------------------------ beats: a beatbox recording -> Strudel

    def groove_of(source: Path) -> beatbox.Groove:
        try:
            return ws._grooves.get((source, source.stat().st_mtime), lambda: beatbox.groove(source))
        except ValueError as e:
            raise HTTPException(422, f"{source.name}: {e}")

    def style_of(style: str) -> str:
        if style not in beatbox.STYLES:
            raise HTTPException(422, f"style is one of {', '.join(beatbox.STYLES)}")
        return style

    def form_of(form: str) -> str:
        if form not in beatbox.FORMS:
            raise HTTPException(422, f"form is one of {', '.join(beatbox.FORMS)}")
        return form

    def bass_of(bass: str) -> str:
        if bass not in beatbox.BASSES:
            raise HTTPException(422, f"bass is one of {', '.join(beatbox.BASSES)}")
        return bass

    def hats_of(hats: str) -> str:
        if hats not in beatbox.HATS:
            raise HTTPException(422, f"hats is one of {', '.join(beatbox.HATS)}")
        return hats

    @app.get("/api/beat")
    def get_beat(path: str, bars: int = 4, style: str = "synth", form: str = "loop", bass: str = beatbox.BASS, hats: str = beatbox.HAT):
        """The groove of a beatbox recording and a Strudel beat made from it (with the performer's own
        sounds layered in, served by /api/beat/kit)."""
        source = ws.resolve(path)
        g = groove_of(source)
        sel = beatbox.loop(g, max(1, min(8, bars)))
        kinds = [k for k in beatbox.KINDS if any(h.kind == k for h in g.hits)]
        kit = {k: f"/api/beat/kit?path={quote(path)}&kind={k}" for k in kinds}
        return {"file": ws.describe(source), "bpm": g.bpm, "start": g.start,
                "song_bpm": beatbox.TEMPO if g.oscillator and style_of(style) == "synth" else g.bpm,   # the song form's tempo
                "key": f"{NOTE_NAMES[g.root]} minor" if g.root is not None else "A minor",
                "bars": g.bars, "loop": sel, "snare_played": g.snare_played,
                "skipped": [[round(a, 2), round(b, 2)] for a, b in g.skipped],
                "hits": {k: sum(h.kind == k for h in g.hits) for k in beatbox.KINDS},
                "riff": [[NOTE_NAMES[n % 12] + str(n // 12 - 1) if n is not None else None for n in bar]
                         for bar in g.voice.notes] if g.oscillator else None,
                "pulse": beatbox.pulse(g, sel) if g.oscillator else [],
                "style": style_of(style), "form": form_of(form), "bass": bass_of(bass),
                "basses": {k: v["about"] for k, v in beatbox.BASSES.items()},
                "hats": hats_of(hats), "hat_styles": {k: v["about"] for k, v in beatbox.HATS.items()},
                "code": (beatbox.song if form == "song" else beatbox.strudel)(g, source.stem, kit, sel, style, bass, hats)}

    @app.get("/api/beat/kit")
    def get_kit(path: str, kind: str):
        """One of the performer's own sounds, cut from the recording."""
        source = ws.resolve(path)
        g = groove_of(source)
        audio, sr = ws.audio(source)
        seg = beatbox.kit_sound(g, audio.mean(axis=1), sr, kind)
        if seg is None:
            raise HTTPException(404, f"no {kind} sounds in {source.name}")
        return Response(audiofile.to_wav_bytes(seg[:, None], sr), media_type="audio/wav")

    @app.get("/api/beat/midi")
    def get_beat_midi(path: str, bars: int = 4, style: str = "synth"):
        source = ws.resolve(path)
        g = groove_of(source)
        data = beatbox.midi_bytes(g, beatbox.loop(g, max(1, min(8, bars))), style=style_of(style))
        name = f"{source.stem} beat.mid"
        return Response(data, media_type="audio/midi", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    # ------------------------------------------------------------ Ardour

    def snapshot() -> arrangement.Snapshot:
        try:
            return arrangement.snapshot()
        except bridge.BridgeError as e:
            raise HTTPException(503, str(e))

    def reference_of(snap: arrangement.Snapshot) -> tuple[arrangement.Region, Path]:
        """The reference track's region and its library file: a track called "reference", or
        else the first whose audio came from the library's reference folder."""
        ordered = sorted(snap.tracks.items(), key=lambda kv: kv[0].casefold() != "reference")
        for name, regions in ordered:
            for region in regions:
                found = arrangement.library_file(region, snap.path, ws.root, FOLDERS["reference"])
                if found and (name.casefold() == "reference" or ws.kind(found) == "reference"):
                    return region, found
        raise HTTPException(422, "No reference in the Ardour session: import one from the library onto a "
                                 "track called \"reference\" (e.g. `sound ardour import "
                                 "recordings/reference/acapella.mp3 --name reference`).")

    @app.get("/api/ardour")
    def ardour_link():
        """The session open in Ardour, to sing along with: its reference (as a library file, and
        where that file's 0:00 sits on the timeline), the section to work on (the editor's range
        selection, else the selected regions, else the loop range, else the take under the
        playhead or the newest one) and the takes, all in the reference's time."""
        snap = snapshot()
        ref_region, ref_path = reference_of(snap)
        offset = ref_region.position - ref_region.start
        takes = sorted((r for r in snap.regions() if r.track != ref_region.track), key=lambda r: -int(r.id))
        span, span_from = None, None
        selected = [r for r in snap.regions() if r.id in snap.selected and r.track != ref_region.track]
        if snap.time_selection:
            span, span_from = snap.time_selection, "range selection"
        elif selected:
            span, span_from = (min(r.position for r in selected), max(r.end for r in selected)), "selected region"
        elif snap.loop:
            span, span_from = snap.loop, "loop range"
        elif takes:
            under = [r for r in takes if r.position <= snap.playhead <= r.end]
            take = (under or takes)[0]
            span, span_from = (take.position, take.end), f"{take.track}"
        return {"session": snap.name, "rate": snap.rate,
                "reference": {"track": ref_region.track, "path": ws.describe(ref_path)["path"], "offset": offset},
                "span": [span[0] - offset, span[1] - offset] if span else None, "span_from": span_from,
                "playhead": snap.playhead - offset,
                "takes": [{"id": r.id, "track": r.track, "name": r.name, "start": r.position - offset,
                           "end": r.end - offset, "muted": r.muted} for r in takes]}

    @app.get("/api/ardour/take")
    def ardour_take(region: str):
        """A take's pitch in the reference's time: as placed in Ardour, and lined up with the
        reference note by note (so pitch can be judged apart from timing)."""
        snap = snapshot()
        found = next((r for r in snap.regions() if r.id == region), None)
        if found is None:
            raise HTTPException(404, f"no region {region} in the Ardour session")
        ref_region, ref_path = reference_of(snap)
        offset = ref_region.position - ref_region.start
        key = ("ardour", tuple((f, f.stat().st_mtime) for f in found.files), round(found.start, 4), round(found.length, 4))
        def make():
            audio, sr = arrangement.region_audio(found)
            return pitch.track(audio.mean(axis=1), sr)
        take = ws._tracks.get(key, make)
        placed = found.position - offset
        out = {"hop": take.hop_s, "placed": {"start": placed, "pitch": _midi(take)},
               "aligned": None, "octave": 0, "tempo": None}
        if take.voiced.any():
            window = analysis.reference_window(placed, None, found.length)
            ref, _, _, ref_from, _ = ws.track(ref_path, *window, loose=True)
            al = analysis.align(take, ref)
            out["aligned"] = {"start": ref_from, "hop": ref.hop_s, "pitch": _hz_to_midi(_warp(take, ref, al))}
            out["octave"], out["tempo"] = al.octave, analysis.tempo(take, al)
        return out

    @app.post("/api/ardour/takes")
    def ardour_add_take(req: AddTakeRequest):
        """Put a recorded take into Ardour, on the "takes" track at `at` on the timeline."""
        source = ws.resolve(req.path)
        try:
            return arrangement.add_take(source, req.at)
        except bridge.BridgeError as e:
            raise HTTPException(503, str(e))

    if web_dir is not None and (web_dir / "index.html").exists():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    else:
        @app.get("/", response_class=HTMLResponse)
        def no_frontend():
            return ("<h1>sound</h1><p>The API is running, but the web interface hasn't been built. "
                    "Run <code>npm install && npm run build</code> in <code>web/</code>.</p>")

    return app
