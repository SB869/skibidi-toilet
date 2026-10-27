"""Beatbox -> beat. A beatbox is usually two things at once: percussion (booms, snares, hats) and a pitched
sound - a hummed or buzzed "oscillator" that plays a riff while the mouth shapes it like a filter. This
finds both: the hits (sorted by sound), the tempo (followed through the performer's drift) and the bars on a
16th grid, and the pitched part's notes, loudness and brightness per 16th. It then writes the groove as a
beat - Strudel code that plays the riff on a stack of detuned oscillators whose filter moves the way the
mouth did, with the kicks under it - plus a MIDI file and a kit cut from the recording itself."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

import librosa
import numpy as np
from scipy.cluster.vq import kmeans2

from . import audiofile, midifile
from .scales import NOTE_NAMES

STEPS = 16                       # sixteenths per bar (4/4)
KINDS = ("kick", "snare", "hat")
VOICE_SR = 22050


@dataclass
class Hit:
    time: float
    kind: str                     # kick | snare | hat by sound; "voice" = an articulation of the pitched part
    strength: float               # 0..1, relative to the loudest hit
    centroid: float               # Hz


@dataclass
class Voice:
    """The pitched part ("oscillator") of each kept bar, per 16th step."""
    notes: list[list[int | None]]  # MIDI note, None where it's silent
    level: list[list[float]]       # dB (loudest moment of the step)
    bright: list[list[float]]      # Hz: centroid of the pitched part (how open the mouth/filter is)
    voiced: list[float]            # share of the bar where it sounds
    flutter: float | None          # Hz of a fast wobble in its level (a buzz/trill), if there is one


@dataclass
class Groove:
    bpm: float
    beats: np.ndarray             # beat times (s), following the performer's drift
    start: float                  # time of the first bar line
    bars: list[dict[str, str]]    # per kept bar: kind -> 16 characters, "x" hit, "." none
    accents: list[dict[str, list[float]]]   # per kept bar: kind -> 16 strengths (0 where no hit)
    hits: list[Hit]
    root: int | None              # pitch class the pitched part centres on, if there is one
    snare_played: bool            # a sound sat on the backbeat (2 and 4)
    voice: Voice | None = None    # the pitched part, if the beatbox has one
    carried: bool = False         # the pitched part sounds between the kicks most of the time
    skipped: list[tuple[float, float]] = field(default_factory=list)   # stretches left out (no steady beat)

    @property
    def oscillator(self) -> bool:
        """The pitched part carries the beatbox: it sounds through most of the kept bars, and between the kicks
        too (a tonal boom ringing on is not a riff)."""
        return self.carried and self.voice is not None and bool(self.voice.voiced) and float(np.mean(self.voice.voiced)) >= 0.4


# ---------------------------------------------------------------- hits

def _features(y: np.ndarray, sr: int, at: int) -> tuple[float, float, float]:
    seg = y[at:at + int(0.08 * sr)]
    if len(seg) < 512:
        seg = np.pad(seg, (0, 512 - len(seg)))
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
    f = np.fft.rfftfreq(len(seg), 1 / sr)
    total = spec.sum() + 1e-12
    return spec[f < 150].sum() / total, float((spec * f).sum() / total), float(np.abs(seg).max())


def detect_hits(y: np.ndarray, sr: int) -> list[Hit]:
    """Every percussive sound, sorted into kick / snare / hat by how low and how bright it is."""
    hop = 128
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    onsets = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=hop, units="samples",
                                        backtrack=True, delta=0.12, wait=int(0.06 * sr / hop))
    raw = [_features(y, sr, int(o)) for o in onsets]
    keep = [i for i, r in enumerate(raw) if r[2] > 1e-4]       # an onset on silence (a decay tail) isn't a hit
    onsets = onsets[keep]; raw = [raw[i] for i in keep]
    if len(onsets) == 0:
        return []
    low = np.array([r[0] for r in raw]); cen = np.array([max(r[1], 20.0) for r in raw]); peak = np.array([r[2] for r in raw])
    X = np.column_stack([np.log10(low + 1e-4), np.log10(cen)])
    Z = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-9)
    k = min(3, len(onsets))
    best = None
    for seed in range(8):                                   # k-means, best of a few starts
        c, lab = kmeans2(Z, k, seed=seed, minit="++")
        inertia = sum(((Z[lab == i] - c[i]) ** 2).sum() for i in range(k))
        if best is None or inertia < best[0]:
            best = (inertia, lab)
    lab = best[1]
    # the lowest cluster (most bass) is the kick, the brightest the hat, the one between the snare
    order = sorted(range(k), key=lambda i: (-low[lab == i].mean() if (lab == i).any() else 0))
    rest = sorted(order[1:], key=lambda i: cen[lab == i].mean() if (lab == i).any() else 0)
    names = {order[0]: "kick"}
    if len(rest) == 2:
        names[rest[0]], names[rest[1]] = "snare", "hat"
    elif rest:
        names[rest[0]] = "hat"
    top = peak.max() + 1e-9
    return [Hit(float(o / sr), names[int(l)], float(p / top), float(c)) for o, l, p, c in zip(onsets, lab, peak, cen)]


def _tempo(hits: list[Hit], lo: float = 70, hi: float = 180) -> float:
    """The tempo whose 16th grid the hits sit on best, with kicks preferring the beats."""
    t = np.array([h.time for h in hits])
    kicks = np.array([h.time for h in hits if h.kind == "kick"])
    best = None
    for bpm in np.arange(lo, hi, 0.25):
        beat = 60 / bpm
        for anchor in kicks[:4] if len(kicks) else t[:4]:
            pos = (t - anchor) / (beat / 4)
            err = np.abs(pos - np.round(pos)).mean()
            kpos = (kicks - anchor) / beat
            kerr = np.abs(kpos - np.round(kpos)).mean() if len(kicks) else 0
            score = err + 0.5 * kerr + 0.002 * abs(bpm - 110) / 10          # mild prior toward common tempos
            if best is None or score < best[0]:
                best = (score, bpm)
    return float(best[1])


def _beat_grid(y: np.ndarray, sr: int, hits: list[Hit], bpm: float) -> np.ndarray:
    """Beat times that follow the performer's drift, anchored on the kicks, from the first kick to the end."""
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=256)
    _, frames = librosa.beat.beat_track(onset_envelope=env, sr=sr, hop_length=256, bpm=bpm, tightness=300)
    beats = librosa.frames_to_time(frames, sr=sr, hop_length=256)
    if len(beats) < 8:
        beats = np.arange(hits[0].time, len(y) / sr, 60 / bpm)
    kicks = np.array([h.time for h in hits if h.kind == "kick"])
    sixteenth = 60 / bpm / 4
    # if the tracker locked onto the off-beats (sharp hats or accents can pull it there), shift it onto the kicks
    inside = [(k - beats[i]) / (beats[i + 1] - beats[i]) for k in kicks
              for i in [np.searchsorted(beats, k) - 1] if 0 <= i < len(beats) - 1]
    if inside:
        shift = (np.angle(np.mean(np.exp(2j * np.pi * np.array(inside)))) / (2 * np.pi)) % 1
        if 0.15 < shift < 0.85:
            beats = beats + shift * np.gradient(beats)
    def snap(bs):
        for i, b in enumerate(bs):
            near = kicks[np.abs(kicks - b) < sixteenth * 0.9]
            if len(near):
                bs[i] = near[np.argmin(np.abs(near - b))]
        return np.sort(bs)
    beats = snap(beats)
    step = float(np.median(np.diff(beats[-8:]))) if len(beats) > 2 else 60 / bpm
    while beats[-1] < max(h.time for h in hits) + step:          # the recording can stop before the bar does
        beats = np.append(beats, beats[-1] + step)
    while beats[0] - step > kicks.min() - sixteenth:              # ...and the tracker can start late
        beats = np.insert(beats, 0, beats[0] - step)
    beats = snap(beats)
    first = next((i for i, b in enumerate(beats) if np.abs(kicks - b).min() < 0.03), 0)   # bar 1 = first kick
    return beats[first:]


# ---------------------------------------------------------------- the pitched part

def _voice_track(y: np.ndarray, sr: int) -> tuple[np.ndarray, ...]:
    """The pitched part over time: frame times, MIDI pitch (nan where unvoiced), level (dB) and brightness
    (spectral centroid, Hz) of the harmonic component. The pitch floor is low (40 Hz): beatboxed bass sits there."""
    y = librosa.resample(y, orig_sr=sr, target_sr=VOICE_SR)
    harmonic, _ = librosa.effects.hpss(y, margin=2.0)
    f0, voiced, _ = librosa.pyin(harmonic, fmin=40, fmax=500, sr=VOICE_SR, frame_length=4096, hop_length=256)
    S = np.abs(librosa.stft(harmonic, n_fft=2048, hop_length=256))
    freqs = librosa.fft_frequencies(sr=VOICE_SR, n_fft=2048)
    centroid = (S * freqs[:, None]).sum(0) / (S.sum(0) + 1e-9)
    level = 20 * np.log10(librosa.feature.rms(y=harmonic, frame_length=2048, hop_length=256)[0] + 1e-9)
    n = min(len(f0), len(centroid), len(level))
    t = np.arange(n) * 256 / VOICE_SR
    midi = np.where(voiced[:n], librosa.hz_to_midi(np.nan_to_num(f0[:n], nan=1.0)), np.nan)
    return t, midi, level[:n], centroid[:n]


def _flutter(t, midi, level, centroid, start: float, end: float) -> float | None:
    """A fast, steady wobble in the pitched part (a lip buzz, a trill) over the beat: its rate, if it's clear.
    Measured on its level and its brightness together."""
    span = (t >= start) & (t <= end)
    v = np.isfinite(midi) & span
    if v.sum() < 0.2 * max(1, span.sum()):
        return None
    def norm(sig):
        x = np.where(v, sig, np.nan)[span]; x = x - np.nanmean(x)
        return np.nan_to_num(x / (np.nanstd(x) + 1e-9))
    fps = 1 / (t[1] - t[0])
    ac = sum(np.correlate(x, x, "full")[len(x) - 1:] for x in (norm(level), norm(centroid)))
    ac /= ac[0] + 1e-12
    lags = np.arange(int(0.065 * fps), int(0.2 * fps))         # 5-15 Hz: a trill or buzz, not a harmonic of one
    peaks = [L for L in lags if ac[L] >= ac[L - 1] and ac[L] >= ac[L + 1]]     # a real peak, not the range's edge
    if not peaks:
        return None
    best = max(peaks, key=lambda L: ac[L])
    return round(float(fps / best), 1) if ac[best] > 0.25 else None


def _clean_notes(notes: list[float | None], level: list[float]) -> list[int | None]:
    """Per-step pitches into notes: fold octave slips, fill short unvoiced gaps while it's still sounding,
    and drop one-step blips that differ from both neighbours."""
    vals = [n for n in notes if n is not None]
    if not vals:
        return [None] * len(notes)
    centre = float(np.median(vals))
    out = []
    for n in notes:
        if n is None:
            out.append(None); continue
        while n - centre > 7: n -= 12
        while centre - n > 7: n += 12
        out.append(int(round(n)))
    loud = max(level)
    for i in range(len(out)):                       # still sounding but unvoiced (a click over it): carry the note
        if out[i] is None and level[i] > loud - 12:
            out[i] = out[i - 1] if i and out[i - 1] is not None else next((x for x in out[i:] if x is not None), None)
    for i in range(1, len(out) - 1):
        a, b, c = out[i - 1], out[i], out[i + 1]
        if a is not None and a == c and b is not None and b != a:
            out[i] = a
    return out


def _away_from_kicks(t: np.ndarray, hits: list[Hit], sixteenth: float) -> np.ndarray:
    """Frames at least two 16ths after the last kick: where a pitched sound is a riff, not the boom ringing."""
    kicks = np.array([h.time for h in hits if h.kind == "kick"])
    since = t - kicks[np.clip(np.searchsorted(kicks, t) - 1, 0, None)]
    return (since >= 2 * sixteenth) | (t < kicks[0])


def _voice_bars(track, beats: np.ndarray, bars: list[int]) -> Voice:
    t, midi, level, centroid = track
    notes, lev, bright, voiced = [], [], [], []
    for b in bars:
        ns, ls, cs, vs = [], [], [], []
        for k in range(STEPS):
            bi = 4 * b + k // 4
            if bi + 1 >= len(beats):
                ns.append(None); ls.append(-90.0); cs.append(0.0); vs.append(0.0); continue
            s0 = beats[bi] + (beats[bi + 1] - beats[bi]) * (k % 4) / 4
            s1 = s0 + (beats[bi + 1] - beats[bi]) / 4
            m = (t >= s0) & (t < s1)
            v = m & np.isfinite(midi)
            share = v.sum() / max(1, m.sum())
            ns.append(float(np.median(midi[v])) if share >= 0.3 else None)
            ls.append(float(level[m].max()) if m.any() else -90.0)
            cs.append(float(np.median(centroid[v])) if v.any() else float(np.median(centroid[m])) if m.any() else 0.0)
            vs.append(share)
        notes.append(_clean_notes(ns, ls)); lev.append(ls); bright.append(cs); voiced.append(float(np.mean(vs)))
    return Voice(notes, lev, bright, voiced, _flutter(t, midi, level, centroid, beats[0], beats[-1]))


# ---------------------------------------------------------------- the groove

def groove(path: Path | str) -> Groove:
    y, sr = audiofile.load(Path(path))
    y = y.mean(axis=1) if y.ndim > 1 else y
    hits = detect_hits(y, sr)
    if len([h for h in hits if h.kind == "kick"]) < 2:
        raise ValueError("couldn't find a steady kick in this recording")
    bpm = _tempo(hits)
    beats = _beat_grid(y, sr, hits, bpm)
    sixteenth = 60 / bpm / 4
    track = _voice_track(y, sr)
    t, midi = track[0], track[1]
    # hits inside the pitched part's own band are its articulations (the "bwom" of a mouth filter), not
    # drums: while it sounds, or anywhere once it carries the beatbox (real hats sit above 3 kHz)
    away = _away_from_kicks(t, hits, sixteenth)
    span = (t >= beats[0]) & (t <= beats[-1]) & away
    carries = span.any() and np.isfinite(midi[span]).mean() >= 0.4
    for h in hits:
        if h.kind != "kick" and h.centroid < 3000:
            m = (t >= h.time - 0.05) & (t <= h.time + 0.05)
            if carries or (m.any() and np.isfinite(midi[m]).mean() > 0.5):
                h.kind = "voice"

    def place(when: float) -> tuple[int, int] | None:
        i = np.searchsorted(beats, when + sixteenth / 2) - 1
        if i < 0 or i >= len(beats) - 1:
            return None
        step = int(round((when - beats[i]) / (beats[i + 1] - beats[i]) * 4))
        return (i + 1, 0) if step == 4 else (i, step)

    n_bars = (len(beats) - 1) // 4
    grid = [{k: ["."] * STEPS for k in KINDS} for _ in range(n_bars)]
    acc = [{k: [0.0] * STEPS for k in KINDS} for _ in range(n_bars)]
    for h in hits:
        p = place(h.time)
        if h.kind == "voice" or p is None or p[0] // 4 >= n_bars:
            continue
        bar, step = p[0] // 4, (p[0] % 4) * 4 + p[1]
        grid[bar][h.kind][step] = "x"
        acc[bar][h.kind][step] = max(acc[bar][h.kind][step], round(h.strength, 2))
    keep, skipped = [], []                                  # the bars with a beat in them (2+ kicks)
    for b in range(n_bars):
        if grid[b]["kick"].count("x") >= 2:
            keep.append(b)
        else:
            skipped.append((float(beats[4 * b]), float(beats[min(len(beats) - 1, 4 * b + 4)])))
    bars = [{k: "".join(grid[b][k]) for k in KINDS} for b in keep]
    voice = _voice_bars(track, beats, keep)
    pcs = [n % 12 for bar in voice.notes for n in bar if n is not None]
    root = int(np.bincount(pcs, minlength=12).argmax()) if pcs else None
    backbeat = sum(bar["snare"][4] == "x" or bar["snare"][12] == "x" for bar in bars)
    return Groove(bpm=round(float(60 / np.median(np.diff(beats[:4 * n_bars + 1]))), 1) if n_bars else bpm,
                  beats=beats, start=float(beats[0]), bars=bars, accents=[acc[b] for b in keep], hits=hits,
                  root=root, snare_played=backbeat >= max(1, len(bars) // 2), voice=voice, carried=bool(carries),
                  skipped=skipped)


def _steadiness(g: Groove, i: int) -> float:
    """How clearly a bar's pitched part was caught: how much of it sounds, and how few wild jumps it has."""
    if g.voice is None:
        return 0.0
    ns = [n for n in g.voice.notes[i] if n is not None]
    jumps = sum(abs(a - b) >= 3 for a, b in zip(ns, ns[1:])) / max(1, len(ns) - 1)
    return g.voice.voiced[i] * (1 - jumps)


def loop(g: Groove, max_bars: int = 4) -> list[int]:
    """The bars to loop: a run of consecutive kept bars, at most `max_bars`. With a pitched part, the run
    where it was caught most clearly (shorter rather than including muddled bars); otherwise the busiest
    run, favouring extra kicks (they're what makes a groove yours)."""
    if not g.bars:
        return []
    n = len(g.bars)
    if g.oscillator:
        steady = [_steadiness(g, i) for i in range(n)]
        good = [s >= 0.8 * max(steady) for s in steady]          # only bars where the riff came through clearly
        runs, i = [], 0
        while i < n:
            if good[i]:
                j = i
                while j < n and good[j] and j - i < max_bars: j += 1
                runs.append(list(range(i, j))); i = j
            else:
                i += 1
        return max(runs, key=lambda r: (len(r), sum(steady[k] for k in r)))
    def score(s):
        run = range(s, min(n, s + max_bars))
        return sum(g.bars[i]["kick"].count("x") + g.bars[i]["hat"].count("x") + g.bars[i]["snare"].count("x")
                   + 6 * max(0, g.bars[i]["kick"].count("x") - 4) for i in run) + 100 * len(run)
    best = max(range(n), key=score)
    return list(range(best, min(n, best + max_bars)))


# ---------------------------------------------------------------- the pitched part as notes

@dataclass
class Event:
    step: int                     # where it starts (16ths into the bar)
    length: int                   # in 16ths
    note: int | None              # None = silence
    gain: float                   # 0..1, how hard the note is pushed
    cutoff: float                 # Hz: the filter where the note starts (the mouth's darkest point)
    opens: float                  # octaves the filter opens after that (to the note's brightest point)


def events(g: Groove, i: int, bright_scale: float = 1.0) -> list[Event]:
    """One bar of the pitched part as notes: a new note where the pitch changes or where the performer
    pushes an accent (a louder moment than both neighbours). Each note keeps how loud it was, how dark the
    mouth was as it started, and how far it opened afterwards (the "bwom")."""
    v = g.voice
    notes, lev, bright = v.notes[i], v.level[i], v.bright[i]
    top = max(lev)
    starts = [0]
    for k in range(1, STEPS):
        accent = lev[k] >= lev[k - 1] + 1.5 and (k == STEPS - 1 or lev[k] >= lev[k + 1])
        if notes[k] != notes[k - 1] or (accent and k - starts[-1] >= 2):
            starts.append(k)
    out = []
    for a, b in zip(starts, starts[1:] + [STEPS]):
        dark = max(bright[a], 1.0) * bright_scale
        widest = max(bright[a:b]) * bright_scale
        out.append(Event(a, b - a, notes[a], round(float(np.clip(10 ** ((max(lev[a:b]) - top) / 20), 0.35, 1.0)), 2),
                         round(float(np.clip(dark, 200, 6000)), -1),
                         round(float(np.clip(np.log2(max(widest, dark) / dark), 0.15, 3.0)), 2)))
    return out


def pulse(g: Groove, sel: list[int]) -> list[int]:
    """The steps where the performer re-articulates the pitched part bar after bar (each "bwom"): accents
    (louder than the step before and after) that recur in at least half the looped bars, give or take a
    16th. [] if there's no steady pattern."""
    count = np.zeros(STEPS)
    for i in sel:
        lev = g.voice.level[i]
        for k in range(STEPS):
            if lev[k] >= lev[k - 1] + 0.5 and lev[k] >= lev[(k + 1) % STEPS]:
                count[k] += 1
    score = count + 0.5 * (np.roll(count, 1) + np.roll(count, -1))
    steps: list[int] = []
    for k in sorted(range(STEPS), key=lambda k: (-score[k], -count[k], k)):
        if score[k] >= len(sel) / 2 and all(min(abs(k - j), STEPS - abs(k - j)) >= 2 for j in steps):
            steps.append(k)
    return sorted(steps) if len(steps) >= 2 else []


def riff_events(g: Groove, i: int, steps: list[int]) -> list[Event]:
    """One bar of the riff as the synth the beatbox stands for would play it: a note on each step of the
    pulse, lasting to the next, at the pitch sung on that step. A different note sung just before a bwom
    that leads somewhere else is kept as a pickup; one that only arrives early is moved onto its bwom. The
    steps before the first bwom are the last one ringing on over the bar line, played quietly. (The filter
    fields are unused here: the basses don't sweep per note.)"""
    notes, lev = g.voice.notes[i], g.voice.level[i]
    top = max(lev)

    def sung(k: int, a: int, b: int) -> int | None:
        vals = [n for n in notes[a:b] if n is not None]
        return notes[k] if notes[k] is not None else max(set(vals), key=vals.count) if vals else None

    out = [Event(0, steps[0], sung(0, 0, steps[0]), 0.5, 0, 0)] if steps[0] > 0 else []
    for a, b in zip(steps, steps[1:] + [STEPS]):
        note, q = sung(a, a, b), b - 1
        after = notes[b] if b < STEPS else notes[steps[0]]
        gain = round(0.6 + 0.4 * 10 ** ((max(lev[a:b]) - top) / 20), 2)       # 4 dB under the loudest -> 0.85
        if q > a and notes[q] is not None and notes[q] not in (note, after):
            out += [Event(a, q - a, note, gain, 0, 0), Event(q, 1, notes[q], 0.8, 0, 0)]
        else:
            out.append(Event(a, b - a, note, gain, 0, 0))
    return out


def _rotate(bars: list[list[Event]], start: int) -> list[list[Event]]:
    """Bars of events re-cut to begin at step `start`, so a note ringing over the bar line stays one note:
    each bar runs from its own `start` to the next bar's (the last wraps round to the first)."""
    out = []
    for j, evs in enumerate(bars):
        body = [replace(e, step=e.step - start) for e in evs if e.step >= start]
        for e in (x for x in bars[(j + 1) % len(bars)] if x.step < start):
            if body and e.note == body[-1].note:
                body[-1] = replace(body[-1], length=body[-1].length + e.length)
            else:
                body.append(replace(e, step=e.step + STEPS - start))
        out.append(body)
    return out


def _name(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12].lower()}{midi // 12 - 1}"


def _mini(steps: list[str]) -> str:
    """16 sound names (or "~") per bar -> a Strudel mini-notation pattern, one bar per cycle."""
    bars = ["[" + " ".join(s) + "]" for s in steps]
    return bars[0] if len(bars) == 1 else "<" + " ".join(bars) + ">"


def _weighted(bars: list[list[Event]], value) -> str:
    """Events of each bar as mini-notation with @lengths, one bar per cycle."""
    out = []
    for evs in bars:
        out.append("[" + " ".join(f"{value(e)}@{e.length}" if e.length > 1 else str(value(e)) for e in evs) + "]")
    return out[0] if len(out) == 1 else "<" + " ".join(out) + ">"


# ---------------------------------------------------------------- Strudel

# The oscillator's voicing, calibrated by recording Strudel's output and comparing it with the beatbox
BRIGHT_SCALE = 2.2               # beatbox brightness (Hz) -> filter cutoff (Hz)
# a pumped envelope (each note hits, then sinks ~9 dB: the beatbox dips between "bwom"s but doesn't stop) and a
# high-pass so the 2nd harmonic is as strong as the fundamental, as in a hummed/buzzed tone; the sub has the low end
OSC = dict(gain=2.2, unison=5, detune=.2, lpq=2, lpattack=.15, attack=.005, decay=.2, sustain=.35, release=.08,
           hpf=160)
KICK = dict(penv=24, pdecay=.05, decay=.2)   # a beatboxed boom: a short tone sweeping down two octaves in ~50 ms
KICK_GAIN, SUB_GAIN = .35, .1

# "synth": what the beatbox stands for - a dance bass playing the riff on the pulse of its bwoms, each one as loud
# as it was pushed (velocity), the cutoff drifting a little over the bars (perlin), an 8th-note echo putting
# ghosts between the bwoms, a deep kick in the riff's key, and the sub ducking under it (the bass isn't ducked:
# its bwoms fall between and on the kicks). The sound is one of BASSES; none of them sweeps a resonant filter on
# each note (a driven saw doing that was tried first and sounded like a fart). Gains are set so each lands at
# about the same level under the kick. (Dirt's "jvbass" sample is silent in the embedded editor: not offered.)
# Placeholders: {riff} the notes, {riff_up} an octave up, {gain}, {vel} the velocity line, {echo} the delay,
# {late} the shift onto the first bwom
BASSES = {
    "synth": dict(about="a synth bass (General MIDI's Synth Bass 2): a filtered saw with a plucky attack", gain=.45,
                  code='note("{riff}").s("gm_synth_bass_2").gain({gain:g}){vel}\n    .lpf(perlin.range(1200, 2400).slow(4)).release(.1){echo}{late}'),
    "bright": dict(about="a brighter synth bass (General MIDI's Synth Bass 1): more edge, cuts through", gain=.6,
                   code='note("{riff}").s("gm_synth_bass_1").gain({gain:g}){vel}\n    .lpf(perlin.range(1500, 3000).slow(4)).release(.1){echo}{late}'),
    "fm": dict(about="an FM bass: a sine with a second sine bending it, hard at the start of each note, then round", gain=.55,
               code='note("{riff}").s("sine").fm(2).fmh(2).fmattack(.001).fmdecay(.12).fmsustain(.15).gain({gain:g}){vel}\n'
                    '    .attack(.005).decay(.25).sustain(.3).release(.05){echo}{late}'),
    "pluck": dict(about="a deep pluck: a triangle wave with a little edge, low-passed, short", gain=.45,
                  code='note("{riff}").s("triangle").shape(.25).gain({gain:g}){vel}\n'
                       '    .lpf(perlin.range(500, 800).slow(4)).attack(.005).decay(.25).sustain(.25).release(.05){echo}{late}'),
    "finger": dict(about="a fingered electric bass (General MIDI): the house way, warm and played", gain=.55,
                   code='note("{riff}").s("gm_electric_bass_finger").gain({gain:g}){vel}\n    .release(.1){echo}{late}'),
}
ECHO = dict(mix=.25, sync="2/16", feedback=.3)            # an 8th note: the echoes fall between the bwoms
RISER_CLIP = .97                 # share of its 8 bars the riser sounds: it stops a beat early, the drop lands out of silence
DRUM_BANK = "RolandTR909"       # the open hat and crash the song adds (the 909: house and techno's kit)
# The hats under the drop. {accents} is a gain per 16th made from the groove: loudest on the riff's bwoms, medium
# on the kicks, soft between - so the hats ride the riff's pulse instead of cutting straight 8ths across it
HATS = {
    "pulse": dict(about="16ths on a short 808 tick, accented on your bwoms", bank="RolandTR808",
                  code='s("hh*16").bank("RolandTR808").gain("{accents}")'),
    "straight": dict(about="16ths on a 909 hat, accented on the 8ths", bank="RolandTR909",
                     code='s("hh*16").bank("RolandTR909").gain("[.24 .13 .18 .13]*4")'),
    "offbeat": dict(about="a 909 hat on the off-beat 8ths only, the house way", bank="RolandTR909",
                    code='s("[~ hh]*4").bank("RolandTR909").gain(.25)'),
}
HAT = "pulse"                    # the default of HATS
SYNTH_KICK = dict(penv=36, pdecay=.06, decay=.35, gain=.6)
SYNTH_SUB, DUCK = .25, dict(attack=.2, depth=.6)
STYLES = ("synth", "mouth")     # play what the beatbox stands for, or copy the mouth
BASS = "synth"                  # the default of BASSES
FORMS = ("loop", "song")        # the beat looped, or laid out as a track


@dataclass
class Piece:
    """One instrument of the beat as Strudel code: a line of the stack in a loop, a `const` in a song."""
    name: str
    about: list[str]              # comment lines above it
    code: str                     # the expression; continuation lines start with 4 spaces


def _setup(g: Groove, kit: dict[str, str] | None) -> list[str]:
    return [f"setcpm({g.bpm:g}/4)"] + (["samples({ " + ", ".join(f"bb_{k}: ['{u}']" for k, u in kit.items()) + " })"] if kit else [])


def pieces(g: Groove, kit: dict[str, str] | None, sel: list[int], style: str, bass: str = BASS, hats: str = HAT) -> tuple[list[str], list[Piece], Callable[[str], str]]:
    """The beat's instruments: comment lines about the riff, the pieces in playing order, and a function
    giving the kick for any kick pattern (mini-notation with "x" for a hit), so a song can vary it."""
    use = [g.bars[i] for i in sel]
    kicks = _mini([["x" if c == "x" else "~" for c in bar["kick"]] for bar in use])
    head: list[str] = []
    out: list[Piece] = []
    own = Piece("own", ["your own boom, layered under the kick"], f's("{kicks.replace("x", "bb_kick")}").gain({{gain}})') if kit and "kick" in kit else None
    drums = [[("sd" if bar["snare"][k] == "x" else "hh" if bar["hat"][k] == "x" else "~") for k in range(STEPS)] for bar in use]
    others = Piece("others", ["the other sounds you made"], f's("{_mini(drums)}").gain(.3)') if any(x != "~" for row in drums for x in row) else None
    steps = pulse(g, sel) if g.oscillator and style == "synth" else []
    if g.oscillator:
        names = [[(NOTE_NAMES[n % 12] if n is not None else "-") for n in g.voice.notes[i]] for i in sel]
        head.append("// the riff you beatboxed, per 16th:")
        head += [f"//   {' '.join(f'{x:>2s}' for x in row)}" for row in names]
    if steps:
        evs = _rotate([riff_events(g, i, steps) for i in sel], steps[0])
        head.append(f"// your bwoms land on 16ths {' '.join(str(k + 1) for k in steps)} of each bar (counting from 1)")
        late = f".late({steps[0]}/{STEPS})" if steps[0] else ""
        riff = _weighted(evs, lambda e: _name(e.note) if e.note is not None else "~")
        riff_up = _weighted(evs, lambda e: _name(e.note + 12) if e.note is not None else "~")
        sub = _weighted(evs, lambda e: _name(e.note - 12) if e.note is not None else "~")
        velocity = _weighted(evs, lambda e: f"{e.gain:g}")
        root = g.root if g.root is not None else 9
        deep = _name(28 + (root - 28) % 12)                                    # E1..D#2, in the riff's key
        k_, b_ = SYNTH_KICK, BASSES[bass]
        kick_of = lambda pat: (f's("{pat.replace("x", "sbd")}").note("{deep}").penv({k_["penv"]}).pdecay({k_["pdecay"]:g}).decay({k_["decay"]:g}).gain({k_["gain"]:g})'
                               f'.duckorbit(2).duckattack({DUCK["attack"]:g}).duckdepth({DUCK["depth"]:g})')
        out.append(Piece("kick", ["the boom: a deep kick in the riff's key, where you put yours. The sub ducks under it (duckorbit)"], kick_of(kicks)))
        out.append(Piece("riff", [
            f"the riff as the synth you were beatboxing: {b_['about']}. A note on each bwom, as loud as you",
            "pushed it (velocity), the cutoff drifting a little over the bars (perlin), and an 8th-note echo",
            "putting ghosts between the bwoms (delay; try delaysync(3/16) for a dub echo onto the next bwom).",
            "The pattern starts on your first bwom and is moved there (late), so the last one rings on over",
            "the bar line instead of striking again on the downbeat",
            "(no reverb: Strudel's room effect does nothing, or silences the voice, in this editor)"],
            b_["code"].format(riff=riff, riff_up=riff_up, gain=b_["gain"], vel=f'\n    .velocity("{velocity}")',
                              echo=f'\n    .delay({ECHO["mix"]:g}).delaysync({ECHO["sync"]}).delayfeedback({ECHO["feedback"]:g})', late=late)))
        out.append(Piece("sub", ["a sub an octave under it"], f'note("{sub}").s("sine").gain({SYNTH_SUB:g}).release(.05){late}.orbit(2)'))
        kick_steps = {k for bar in use for k in range(STEPS) if bar["kick"][k] == "x"}
        accents = " ".join(f"{.5 if k in steps else .3 if k in kick_steps else .17:g}" for k in range(STEPS))   # the 808 tick is a quiet sample
        h = HATS[hats]
        out.append(Piece("hats", [f"the hats: {h['about']}"], h["code"].format(accents=f"[{accents}]")))
        out += [x for x in (others, own) if x]
    elif g.oscillator:
        evs = [events(g, i, BRIGHT_SCALE) for i in sel]
        riff = _weighted(evs, lambda e: _name(e.note) if e.note is not None else "~")
        sub = _weighted(evs, lambda e: _name(e.note - 12) if e.note is not None else "~")
        velocity = _weighted(evs, lambda e: f"{e.gain:g}")
        cutoff = _weighted(evs, lambda e: f"{int(e.cutoff)}")
        opens = _weighted(evs, lambda e: f"{e.opens:g}")
        boom = _name(40 + (g.root - 40) % 12) if g.root is not None else "a#2"   # the boom, tuned to the riff's root
        flutter = g.voice.flutter
        o = OSC
        kick_of = lambda pat: f's("{pat.replace("x", "sbd")}").note("{boom}").penv({KICK["penv"]}).pdecay({KICK["pdecay"]:g}).decay({KICK["decay"]:g}).gain({KICK_GAIN:g})'
        out.append(Piece("kick", ["your booms: a short tone that sweeps down into the riff's root, like the one you made"], kick_of(kicks)))
        out.append(Piece("osc", [
            "the oscillator: your riff on a stack of detuned saws. Each note is as loud as you pushed it",
            "(velocity), starts as dark as your mouth was (lpf) and opens as far as it did (lpenv, octaves)"],
            f'note("{riff}").s("supersaw").unison({o["unison"]}).detune({o["detune"]:g}).gain({o["gain"]:g})\n'
            f'    .velocity("{velocity}")\n'
            f'    .lpf("{cutoff}")\n'
            f'    .lpenv("{opens}").lpattack({o["lpattack"]:g}).lpsustain(1).lpq({o["lpq"]:g}).hpf({o["hpf"]:g})\n'
            f'    .attack({o["attack"]:g}).decay({o["decay"]:g}).sustain({o["sustain"]:g}).release({o["release"]:g})'
            + (f".tremolo({flutter:g}).tremolodepth(.3)" if flutter else "")))
        out.append(Piece("sub", ["a sub an octave under it, for weight"], f'note("{sub}").s("sine").gain({SUB_GAIN:g}).release(.05)'))
        out += [x for x in (others, own) if x]
    else:
        hats = [[("hh" if bar["hat"][k] == "x" else "~") for k in range(STEPS)] for bar in use]
        snares = [[("sd" if bar["snare"][k] == "x" else "~") for k in range(STEPS)] for bar in use]
        kick_of = lambda pat: f's("{pat.replace("x", "bd")}").gain(1.1)'
        out.append(Piece("kick", ["your booms"], kick_of(kicks)))
        if any(x != "~" for row in snares for x in row):
            out.append(Piece("yourSnares", ["your snares"], f's("{_mini(snares)}").gain(.8)'))
        if any(x != "~" for row in hats for x in row):
            out.append(Piece("yourHats", ["your hats"], f's("{_mini(hats)}").gain(.55)'))
        if own:
            out.append(own)
    for x in out:
        if x.name == "own":
            x.code = x.code.replace("{gain}", ".25" if g.oscillator else ".5")
    return head, out, kick_of


def _options(g: Groove, style: str) -> list[str]:
    """Pieces not in the beatbox, as commented lines to uncomment."""
    if g.oscillator and style == "synth":
        return ["// not in your beatbox - uncomment to add a backbeat, or the off-beat open hat:",
                f'// , s("~ cp ~ cp").bank("{DRUM_BANK}").gain(.35)', f'// , s("[~ oh]*4").bank("{DRUM_BANK}").gain(.18)']
    if g.oscillator:
        return ["// not in your beatbox - uncomment to add a backbeat:", '// , s("~ cp ~ cp").gain(.35)']
    if not g.snare_played:
        return ["// no backbeat in your beatbox - uncomment to add one:", '// , s("~ cp ~ cp").gain(.7)']
    return []


def strudel(g: Groove, name: str = "beatbox", kit: dict[str, str] | None = None, bars: list[int] | None = None,
            style: str = "synth", bass: str = BASS, hats: str = HAT) -> str:
    """The groove as a Strudel loop. With a pitched part, in the "synth" style: the riff as the synth it stands
    for, on the pulse of its bwoms, on one of the BASSES, with HATS, over a deep kick the sub ducks under;
    in the "mouth" style: the riff on detuned saws with filter and loudness moving note by note the way the
    mouth moved them, a sub an octave under, and the kicks as booms like the ones made. Without a pitched
    part: the kicks, snares and hats on drum-machine sounds."""
    sel = bars if bars is not None else loop(g)
    head, ps, _ = pieces(g, kit, sel, style, bass, hats)
    lines = [f'// "{name}": your beatbox as a beat', f"// {g.bpm:g} BPM, {len(sel)} bar{'s' if len(sel) > 1 else ''}, looped"]
    lines += head + [""] + _setup(g, kit) + ["", "stack("]
    for x in ps:
        lines += [f"  // {a}" for a in x.about] + ["  " + x.code + ","]
    lines[-1] = lines[-1].rstrip(",")
    lines += ["  " + o for o in _options(g, style)] + [")"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- the song

# The form: (bars, what happens, the pieces that play). "lead" is the riff (or the mouth's oscillator), "drums"
# any other sounds beatboxed; the rest are the song's own pieces (hats, a riser, crashes). ("pull",
# [...]) takes those pieces out after two beats of the section's last bar, ("end", [...]) leaves them one hit
# there. A section nothing of which exists falls back to the drums, then to silence
SONG = [
    (8, "intro: your boom alone (and the samples warming up, silently)", ["kick", "own", "preload"]),
    (8, "intro: the sub joins", ["kick", "own", "sub", "drums"]),
    (8, "build: the riff comes in quiet, low and dry, opening up", ["kick", "own", "sub", "drums", "leadLow"]),
    (8, "build: the riff opens up over a riser and a reverse crash; everything pulls out before the drop",
     [("pull", ["kick", "sub", "drums", "leadRise"]), "riser", "rcrash"]),
    (24, "drop: the hats come in, a rim answers the bass, a rave stab on the off-beats; every 8th bar the kick skips beat 4 and the riff's echo is thrown long",
     ["kickHole", "own", "sub", "drums", "leadThrow", "hats", "ohat", "rim", "stab"]),
    (8, "breakdown: the riff alone, echoing", ["leadEcho"]),
    (8, "breakdown: the sub and a riser come back, the kick rolls in", ["kickReturn", "sub", "drums", "leadEcho2", "riser", "rcrash"]),
    (24, "drop: the same, plus a ride, the stab's filter opening across every 8 bars", ["kickHole", "own", "sub", "drums", "leadThrow", "hats", "ohat", "rim", "ride", "stabSweep"]),
    (8, "outro: the riff and hats out, one last hit", [("end", ["kick", "sub", "drums"])]),
]


def song(g: Groove, name: str = "beatbox", kit: dict[str, str] | None = None, bars: list[int] | None = None,
         style: str = "synth", bass: str = BASS, hats: str = HAT, form: list[tuple[int, str, list[str]]] = SONG) -> str:
    """The beat as a track: the pieces as `const`s, variations of them for the form (the riff filtered and
    opening, echoing alone, the kick pulling out and rolling back in, a noise riser), and the form itself as
    `arrange(...)`, section by section."""
    sel = bars if bars is not None else loop(g)
    head, ps, kick_of = pieces(g, kit, sel, style, bass, hats)
    have = {x.name for x in ps}
    lead = "riff" if "riff" in have else "osc" if "osc" in have else None
    drums = [n for n in ("others", "yourSnares", "yourHats") if n in have]       # the sounds beatboxed, not the song's hats
    bar_s = 4 * 60 / g.bpm
    var = [Piece("kickReturn", ["silent, then a roll of 8ths in the last bar of 8, bringing the beat back"],
                 kick_of("[x ~ x ~ x ~ x ~ x ~ x ~ x ~ x ~]") + '.gain(.45).mask("<0 0 0 0 0 0 0 1>")')]
    steps = pulse(g, sel) if g.oscillator else []
    rims = [k for k in (2, 6, 10, 14) if k not in steps] or [6, 14]        # the off-beat 8ths the bass leaves empty
    var.append(Piece("rim", [f"a rimshot on 16ths {' and '.join(str(k + 1) for k in rims)}: the off-beats between the bwoms, answering the bass"],
                     f's("{_mini([["rim" if k in rims else "~" for k in range(STEPS)]])}").bank("{DRUM_BANK}").gain(.25)'))
    if lead:
        var.append(Piece(f"{lead}Throw", ["the riff; in the last bar of every 8 its last beat goes to its own echo (orbit 3): 5/16ths apart, so the",
                                           "repeats fall between the next bar's bwoms, rolling across it"],
                         f'stack({lead}.mask("<1 1 1 1 1 1 1 [1 1 1 0]>"), {lead}.mask("<0 0 0 0 0 0 0 [0 0 0 1]>").delay(.8).delaysync(5/16).delayfeedback(.8).orbit(3))'))
        r = 48 + (g.root if g.root is not None else 9)                     # the key's root in octave 3
        var += [Piece("stab", ["a rave stab: a 5th chord in your key on detuned saws, short, on the off-beat 8ths"],
                      f'note("[~ [{_name(r)},{_name(r + 7)},{_name(r + 12)}]]*4").s("supersaw").unison(5).detune(.2)'
                      '.attack(.005).decay(.14).sustain(0).release(.04).hpf(200).lpf(2200).lpq(2).gain(.15)'),
                Piece("stabSweep", ["the stab, its filter opening over every 8 bars"], 'stab.lpf(saw.range(1200, 3500).slow(8))')]
        full = BASSES[bass]["gain"] if lead == "riff" else OSC["gain"]
        var += [Piece(f"{lead}Low", ["quieter, low and dry, the filter opening over 8 bars"], f"{lead}.gain({full * .6:.2g}).lpf(saw.range(200, 600).slow(8)).delay(0)"),
                Piece(f"{lead}Rise", ["opening further and getting louder"],
                      f"{lead}.gain(saw.range({full * .6:.2g}, {full:g}).slow(8)).lpf(saw.range(600, 2400).slow(8))"),
                Piece(f"{lead}Echo", ["a little quieter, a dub echo onto the next bwom, nothing under it"], f"{lead}.gain({full * .8:.2g}).delay(.5).delaysync(3/16).delayfeedback(.5).lpf(saw.range(300, 900).slow(8))"),
                Piece(f"{lead}Echo2", ["the echo opening up toward the drop"], f"{lead}.delay(.5).delaysync(3/16).delayfeedback(.5).lpf(saw.range(900, 3000).slow(8))")]
    t = 8 * bar_s * RISER_CLIP
    var += [Piece("riser", ["8 bars of pink noise, a band of it sweeping up (a whoosh, not static) and swelling"
                            + (f", stopping {8 * (1 - RISER_CLIP):.2g} bars early" if RISER_CLIP < 1 else "")],
                  f's("pink").slow(8)' + (f".clip({RISER_CLIP:g})" if RISER_CLIP < 1 else "")
                  + f'.hpf(150).hpenv(3).hpattack({t * .95:.3g}).hpsustain(1).lpf(600).lpenv(3).lpattack({t * .95:.3g}).lpsustain(1).attack({t * .9:.3g}).gain(.18)'),
            Piece("ohat", ["an open hat on the off-beats"], f's("[~ oh]*4").bank("{DRUM_BANK}").gain(.14)'),
            Piece("rcrash", ["a reversed crash swelling into the downbeat after the last bar of 8"], 's("cr").speed(-1).gain(.6).late(9/16).mask("<0 0 0 0 0 0 0 1>")'),
            Piece("kickHole", ["the kick, skipping beat 4 of every 8th bar: a hole, techno's fill"], 'kick.lastOf(8, x => x.mask("1 1 1 0"))'),
            Piece("ride", ["a ride on the off-beat 8ths, quiet"], f's("[~ rd]*4").bank("{DRUM_BANK}").gain(.12)'),
            Piece("preload", ["the samples, triggered silently once so their first real hit isn't lost to loading"],
                  f'stack(s("cr").gain(.001), s("oh rim rd").bank("{DRUM_BANK}").gain(.001), s("hh").bank("{HATS[hats]["bank"]}").gain(.001)).mask("<1 0 0 0 0 0 0 0>")')]
    have |= {x.name for x in var}
    total = sum(n for n, _, _ in form)
    secs = total * bar_s
    lines = [f'// "{name}": your beatbox as a track', f"// {total} bars, {int(secs // 60)}:{int(secs % 60):02d} at {g.bpm:g} BPM - "
             + ", ".join(dict.fromkeys(t.split(":")[0] for _, t, _ in form))] + head + [""] + _setup(g, kit) + ["", "// the pieces"]
    for x in ps:
        lines += [f"// {a}" for a in x.about] + [f"const {x.name} = {x.code}"]
    lines.append("")
    lines.append("// variations of them, for the form")
    for x in var:
        lines.append(f"const {x.name} = {x.code}   // {'; '.join(x.about)}")
    lines += ["", "// in the last bar of 8: out after two beats (before a drop), or one hit (the end)",
              'const pull = p => p.lastOf(8, x => x.mask("1 1 0 0"))', 'const end = p => p.lastOf(8, x => x.mask("1 0 0 0"))',
              "", "// the form: bars, what plays", "arrange("]

    def resolve(layers: list) -> list[str]:
        names = []
        for layer in layers:
            if isinstance(layer, tuple):
                inner = resolve(layer[1])
                names += [f"{layer[0]}(stack({', '.join(inner)}))"] if inner else []
                continue
            layer = layer.replace("lead", lead) if lead and layer.startswith("lead") else layer
            names += drums if layer == "drums" else [layer] if layer in have else []
        return names

    at = 1
    for n, title, layers in form:
        names = resolve(layers) or drums
        expr = f"stack({', '.join(names)})" if names else "silence"
        lines.append(f"  [{n}, {expr}],".ljust(70) + f"// {at}-{at + n - 1}: {title}")
        at += n
    lines[-1] = lines[-1].replace("],", "] ", 1)
    lines.append(")")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- MIDI and the kit

def midi_bytes(g: Groove, bars: list[int] | None = None, repeats: int = 4, style: str = "synth") -> bytes:
    """The same beat as MIDI at the groove's tempo: the riff on channel 1 (a saw lead; in the "synth" style
    on the pulse of its bwoms), its sub on channel 2, and the drums on channel 10 (General MIDI: kick 36,
    snare 38, closed hat 42)."""
    sel = bars if bars is not None else loop(g)
    steps = pulse(g, sel) if g.oscillator and style == "synth" else []
    if steps:          # on the pulse, cut from the first bwom so the last one rings over the bar line
        start = steps[0]
        riff = _rotate([riff_events(g, i, steps) for i in sel], start)
        lead_in = [e for e in riff_events(g, sel[0], steps) if e.step < start]
    else:
        start, riff, lead_in = 0, [events(g, i) for i in sel] if g.oscillator else [[] for _ in sel], []
    notes: list[midifile.Note] = []

    def play(e: Event, at: float) -> None:
        if e.note is not None:
            v = int(50 + 70 * e.gain)
            notes.extend([midifile.Note(at, e.length / 4, e.note, v, 0), midifile.Note(at, e.length / 4, e.note - 12, v, 1)])

    for e in lead_in:
        play(e, e.step / 4)
    for r in range(repeats):
        for j, i in enumerate(sel):
            base, bar = (r * len(sel) + j) * 4, g.bars[i]
            for k in range(STEPS):
                at = base + k / 4
                if bar["kick"][k] == "x": notes.append(midifile.Note(at, 0.25, 36, 110, 9))
                if bar["snare"][k] == "x": notes.append(midifile.Note(at, 0.2, 38, 95, 9))
                if bar["hat"][k] == "x": notes.append(midifile.Note(at, 0.1, 42, 80, 9))
            for e in riff[j]:
                play(e, base + (start + e.step) / 4)
    return midifile.encode(notes, g.bpm, name="beat", programs={0: 81, 1: 38})


def write_midi(g: Groove, path: Path, bars: list[int] | None = None, repeats: int = 4, style: str = "synth") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(midi_bytes(g, bars, repeats, style))
    return path


def kit_sound(g: Groove, y: np.ndarray, sr: int, kind: str) -> np.ndarray | None:
    """The cleanest example of one kind of sound (its strongest hit, 250 ms, faded out), mono."""
    cands = [h for h in g.hits if h.kind == kind]
    if not cands:
        return None
    h = max(cands, key=lambda h: h.strength)
    a = int(h.time * sr)
    seg = y[a:a + int(0.25 * sr)].copy()
    seg *= np.linspace(1, 0, len(seg)) ** 2
    return seg / (np.abs(seg).max() + 1e-9) * 0.9


def kit(g: Groove, path: Path | str, out_dir: Path) -> dict[str, Path]:
    """The performer's own kit: one clean example of each sound, cut from the recording, as wav files."""
    y, sr = audiofile.load(Path(path))
    y = y.mean(axis=1) if y.ndim > 1 else y
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for kind in KINDS:
        seg = kit_sound(g, y, sr, kind)
        if seg is not None:
            files[kind] = out_dir / f"{kind}.wav"
            audiofile.save(files[kind], seg[:, None], sr)
    return files
