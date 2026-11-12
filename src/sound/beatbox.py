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


def _runs(bars: list[list[tuple[str, int]]]) -> str:
    """(value, length in 16ths) runs of each bar as mini-notation with @lengths, one bar per cycle."""
    out = ["[" + " ".join(f"{v}@{n}" if n > 1 else v for v, n in bar) + "]" for bar in bars]
    return out[0] if len(out) == 1 else "<" + " ".join(out) + ">"


def _weighted(bars: list[list[Event]], value) -> str:
    """Events of each bar as mini-notation with @lengths, one bar per cycle."""
    return _runs([[(str(value(e)), e.length) for e in evs] for evs in bars])


def chord(note: int, root: int) -> tuple[int, ...] | None:
    """The triad over a bass note in the key (TRIADS), voiced close: its notes within the octave up from the
    root at or above CHORD_LOW. None for a passing note."""
    d = (note - root) % 12
    if d not in TRIADS:
        return None
    lo = CHORD_LOW + (root - CHORD_LOW) % 12
    return tuple(sorted(lo + (root + x - lo) % 12 for x in TRIADS[d]))


def _chord_name(c: tuple[int, ...]) -> str:
    return "[" + ",".join(_name(n) for n in c) + "]"


def harmony(bars: list[list[Event]], root: int) -> list[list[tuple[tuple[int, ...], int]]]:
    """Each bar's chords as (chord, length in 16ths) runs: the chord over each note the bass holds, passing notes
    and pickups (shorter than an 8th) keeping the chord before them, equal neighbours merged."""
    out = []
    for evs in bars:
        chords = [chord(e.note, root) if e.note is not None and e.length >= 2 else None for e in evs]
        first = next((c for c in chords if c), chord(root, root))
        row: list[tuple[tuple[int, ...], int]] = []
        for e, c in zip(evs, chords):
            c = c or (row[-1][0] if row else first)
            if row and row[-1][0] == c:
                row[-1] = (c, row[-1][1] + e.length)
            else:
                row.append((c, e.length))
        out.append(row)
    return out


def step_chords(evs: list[Event], root: int) -> list[tuple[int, ...]]:
    """The chord sounding on each 16th of one bar (events in bar time)."""
    return [c for c, n in harmony([evs], root)[0] for _ in range(n)]


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
# as it was pushed (velocity), a 909 kick where the booms were, and a sine sub ducking under it (the bass isn't
# ducked: its bwoms fall between and on the kicks). The sound is one of BASSES, dry (the echoes go on the song's
# hook, not under the kick). The default plucks each note with its filter: the cutoff jumps up and falls back in
# ~140 ms - the hit-then-sink of a bwom. None of them opens the filter *after* the hit: a saw doing that was tried
# first and sounded like a fart (measured: brightness rising half an octave right after each note; a pluck's
# falls). Gains are set so each lands at about the same level under the kick. (Dirt's "jvbass" sample is silent
# in the embedded editor: not offered.) Placeholders: {riff} the notes, {riff_up} an octave up, {gain}, {vel} the
# velocity line, {late} the shift onto the first bwom
BASSES = {
    "saw": dict(about="a driven saw, each note plucked by its filter (the cutoff jumps and falls back): techno's bass", gain=.5,
                code='note("{riff}").s("sawtooth").shape(.2).gain({gain:g}){vel}\n'
                     '    .lpf(perlin.range(450, 560).slow(4)).lpenv(2.2).lpattack(.006).lpdecay(.17).lpsustain(.2).lpq(2)\n'
                     '    .attack(.004).decay(.22).sustain(.3).release(.06){late}'),
    "square": dict(about="a square wave plucked the same way: hollower, rounder", gain=.4,
                   code='note("{riff}").s("square").gain({gain:g}){vel}\n'
                        '    .lpf(perlin.range(350, 480).slow(4)).lpenv(2).lpattack(.001).lpdecay(.1).lpsustain(.1).lpq(.5)\n'
                        '    .attack(.003).decay(.2).sustain(.5).release(.05){late}'),
    "fm": dict(about="an FM bass: a sine with a second sine bending it, hard at the start of each note, then round", gain=.55,
               code='note("{riff}").s("sine").fm(2).fmh(2).fmattack(.001).fmdecay(.12).fmsustain(.15).gain({gain:g}){vel}\n'
                    '    .attack(.005).decay(.25).sustain(.3).release(.05){late}'),
    "synth": dict(about="a synth bass (General MIDI's Synth Bass 2): a filtered saw with a plucky attack, sustained", gain=.45,
                  code='note("{riff}").s("gm_synth_bass_2").gain({gain:g}){vel}\n    .lpf(perlin.range(1200, 2400).slow(4)).release(.1){late}'),
    "bright": dict(about="a brighter synth bass (General MIDI's Synth Bass 1): more edge, cuts through", gain=.6,
                   code='note("{riff}").s("gm_synth_bass_1").gain({gain:g}){vel}\n    .lpf(perlin.range(1500, 3000).slow(4)).release(.1){late}'),
    "finger": dict(about="a fingered electric bass (General MIDI): the house way, warm and played", gain=.55,
                   code='note("{riff}").s("gm_electric_bass_finger").gain({gain:g}){vel}\n    .release(.1){late}'),
}
RISER_CLIP = .97                 # share of its 8 bars the riser sounds: it stops a beat early, the drop lands out of silence
DRUM_BANK = "RolandTR909"       # the kit the song adds (the 909: house and techno's)
OFFBEATS = (2, 6, 10, 14)        # the off-beat 8ths (16ths, from 0): where house puts its open hat and chord stabs
SWING = .006                     # cycles the off 16ths of the hats are pushed late: ~12 ms at 124 BPM, a light swing
# The hats under the drop. {house} and {pulse} are a gain per 16th made from the groove: {house} loudest on the
# off-beat 8ths (the house accent), then the bwoms, then the kicks; {pulse} loudest on the riff's bwoms. {swing}
# pushes every other 16th late
HATS = {
    "house": dict(about="16ths on the 909 hat, accented on the off-beats, lightly swung, no two hits alike (velocity), one in twelve dropped", bank="RolandTR909",
                  code='s("hh*16").bank("RolandTR909").gain("{house}").late("[0 {swing}]*8").velocity(perlin.range(.7, 1).fast(4)).degradeBy(.08)\n'
                       '    .hpf(250).lpf(12000).orbit(5)'),
    "pulse": dict(about="16ths on a short 808 tick, accented on your bwoms", bank="RolandTR808",
                  code='s("hh*16").bank("RolandTR808").gain("{pulse}").hpf(250).orbit(5)'),
    "offbeat": dict(about="a 909 hat on the off-beat 8ths only, the house way", bank="RolandTR909",
                    code='s("[~ hh]*4").bank("RolandTR909").gain(.3).hpf(250).lpf(12000).orbit(5)'),
}
HAT = "house"                    # the default of HATS
SYNTH_KICK = dict(bank="RolandTR909", gain=.46, speed=.95)   # the 909 kick: its knock at 90-200 Hz, the sine boom and sub have the bottom; .95 settles its
                                                              # tail on D#2 (~78 Hz), a 4th over the A#1 sub, instead of between D# and E
SYNTH_SUB = .5                   # the sub has the bottom: the 909 has nothing under 60 Hz
# Sidechain: the kick ducks the sub hard and the pad, stabs, hook and (lightly) the 909 kit, so the whole top breathes with
# it; the bass (orbit 0) is not ducked, its bwoms fall on and between the kicks.
# (depth, attack): the sub (orbit 2) from the kick itself; the pad (orbit 6), stabs and hook (orbit 4) and the 909 kit (orbit 5)
# from silent "ghost" copies of the kick - superdough takes one duckorbit per sound (a "2:1:4" string broke the kick). Orbit 1
# is superdough's default bus, where the kick and bass live: ducking it ducks the kick (measured -6 dB), so nothing goes there
# on purpose. duckdepth d is about 1 - sqrt(d) of gain: .6 = -13 dB, .4 = -9, .2 = -5
DUCK = dict(sub=(.6, .2), pad=(.4, .15), top=(.4, .15), kit=(.2, .1))
# Space: one short room per bus (superdough's reverb is per orbit; its size is shared by the voices on the bus)
ROOM = dict(size=1.5, lp=5000, stabs=.3, pad=.2, hook=.3, ohat=.3, ride=.3, crash=.3)   # a short, dark room; not on the kick, sub, bass or the 16th hats
# buses: 1 (default) kick, boom, own, bass, riser; 2 sub; 3 the bass's breakdown echo; 4 stabs and hook (one echo, one room); 5 the 909 kit; 6 the pad
BOOM = dict(penv=12, pdecay=.15, decay=.18, gain=.2)   # a tuned sine boom stacked under the 909 (the knock from the 909, the weight from this)
STYLES = ("synth", "mouth")     # play what the beatbox stands for, or copy the mouth
BASS = "saw"                    # the default of BASSES
FORMS = ("loop", "song")        # the beat looped, or laid out as a track
TEMPO = 124                      # the song's tempo (the loop plays at the beatbox's own): club house, 7% up from this take

# Harmony, from the riff: a chord on each note the bass plays, in the minor the riff implies - the natural minor
# with the Phrygian 2nd, which a riff walking root, b2, b7 (as this one does) spells. Semitones from the root of
# each degree that gets a chord, and its triad; other notes (the leading tone, the 2nd) are passing notes and keep
# the chord around them. So a stab or pad never clashes with the bass: it moves when the bass does
TRIADS = {0: (0, 3, 7), 1: (1, 5, 8), 3: (3, 7, 10), 5: (5, 8, 0), 7: (7, 10, 2), 8: (8, 0, 3), 10: (10, 1, 5)}
CHORD_LOW = 53                   # chords are voiced within the octave up from the key's root at or above F3


@dataclass
class Piece:
    """One instrument of the beat as Strudel code: a line of the stack in a loop, a `const` in a song."""
    name: str
    about: list[str]              # comment lines above it
    code: str                     # the expression; continuation lines start with 4 spaces
    loop: bool = True             # part of the beat looped; False = a song piece (harmony, hook), a comment in the loop
    frame: str = "beat"           # "riff": follows the riff, cut from its first bwom (the song arranges those apart and shifts them)


def _setup(g: Groove, kit: dict[str, str] | None, bpm: float | None = None) -> list[str]:
    return [f"setcpm({bpm or g.bpm:g}/4)"] + (["samples({ " + ", ".join(f"bb_{k}: ['{u}']" for k, u in kit.items()) + " })"] if kit else [])


def pieces(g: Groove, kit: dict[str, str] | None, sel: list[int], style: str, bass: str = BASS, hats: str = HAT,
           late: bool = True) -> tuple[list[str], list[Piece], Callable[[str], str]]:
    """The beat's instruments: comment lines about the riff, the pieces in playing order, and a function
    giving the kick for any kick pattern (mini-notation with "x" for a hit; `boom=False` leaves out the
    synth style's sine layer and `gain` is a `.gain(...)` for its 909 layer alone, for a roll), so a song can vary it. `late`:
    move the riff and what follows it (the "riff" frame) onto the first bwom piece by piece; the song does
    that to their arrangement as a whole instead."""
    use = [g.bars[i] for i in sel]
    kicks = _mini([["x" if c == "x" else "~" for c in bar["kick"]] for bar in use])
    head: list[str] = []
    out: list[Piece] = []
    own = Piece("own", ["your own boom, layered under the kick" + (", its very top rolled off" if g.oscillator else "")],
                f's("{kicks.replace("x", "bb_kick")}").gain({{gain}}){{fx}}') if kit and "kick" in kit else None
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
        late_s = f".late({steps[0]}/{STEPS})" if steps[0] and late else ""
        riff = _weighted(evs, lambda e: _name(e.note) if e.note is not None else "~")
        riff_up = _weighted(evs, lambda e: _name(e.note + 12) if e.note is not None else "~")
        sub = _weighted(evs, lambda e: _name(e.note - 12) if e.note is not None else "~")
        velocity = _weighted(evs, lambda e: f"{e.gain:g}")
        root = g.root if g.root is not None else 9
        k_, b_ = SYNTH_KICK, BASSES[bass]
        deep = _name(34 + (root - 34) % 12)                                    # the key's root in octave 1 (A#1 = 58 Hz)
        duck = lambda o, d, a: f'.duckorbit({o}).duckattack({a:g}).duckdepth({d:g})'
        bd = lambda pat: f's("{pat.replace("x", "bd")}").bank("{k_["bank"]}").speed({k_["speed"]:g}).gain({k_["gain"]:g})' + duck(2, *DUCK["sub"])
        ghost = lambda pat, o, d, a: f's("{pat.replace("x", "bd")}").bank("{k_["bank"]}").gain(.001)' + duck(o, d, a)   # silent: only ducks its bus
        ghosts = lambda pat: ", ".join([ghost(pat, 6, *DUCK["pad"]), ghost(pat, 4, *DUCK["top"]), ghost(pat, 5, *DUCK["kit"])])
        kick_of = lambda pat, boom=True, gain="": (                         # `gain`: a .gain(...) for the 909 layer alone (the ghosts stay silent)
            f'stack({bd(pat)}{gain}, s("{pat.replace("x", "sbd")}").note("{deep}").penv({BOOM["penv"]}).pdecay({BOOM["pdecay"]:g}).decay({BOOM["decay"]:g}).gain({BOOM["gain"]:g}), {ghosts(pat)})'
            if boom else f'stack({bd(pat)}{gain}, {ghosts(pat)})')
        out.append(Piece("kick", ["the boom: a 909 kick where you put yours, its tail tuned a 4th over the sub (speed), a short sine in the key sweeping down",
                                  "an octave under it for weight, and three silent copies of it that only duck: the sub ducks hard, the pad, the stabs and",
                                  "the hook by 9 dB, the hats and metal by 5, all breathing with the kick (duckorbit)"], kick_of(kicks)))
        out.append(Piece("riff", [
            f"the riff as the synth you were beatboxing: {b_['about']}. A note on each bwom, as loud as you",
            "pushed it (velocity), the cutoff drifting a little over the bars (perlin). The pattern starts on",
            "your first bwom and is moved there (late), so the last one rings on over the bar line instead of",
            "striking again on the downbeat",
            "- dry: the kick, sub and bass stay out of the rooms"],
            b_["code"].format(riff=riff, riff_up=riff_up, gain=b_["gain"], vel=f'\n    .velocity("{velocity}")', late=late_s), frame="riff"))
        out.append(Piece("sub", ["a sub an octave under it"], f'note("{sub}").s("sine").gain({SYNTH_SUB:g}).release(.05){late_s}.orbit(2)', frame="riff"))
        kick_steps = {k for bar in use for k in range(STEPS) if bar["kick"][k] == "x"}
        house = " ".join(f"{.26 if k in OFFBEATS else .19 if k in steps else .15 if k in kick_steps else .1:g}" for k in range(STEPS))
        pulse_ = " ".join(f"{.5 if k in steps else .3 if k in kick_steps else .17:g}" for k in range(STEPS))   # the 808 tick is a quiet sample
        h = HATS[hats]
        out.append(Piece("hats", [f"the hats: {h['about']}; the 909 kit sits on one bus (orbit 5): high-passed at 250, lightly ducked by the kick; the 16ths dry, centred"], h["code"].format(house=f"[{house}]", pulse=f"[{pulse_}]", swing=f"{SWING:g}")))
        # not `own` here: the mouth boom under the 909 read as a rattle (measured 2026-10-29: a second knock 150 ms after
        # every kick and 26 dB more 1-4 kHz than the 909 + sine boom); the "mouth" style still layers it
        out += [x for x in (others,) if x]
        # the song's harmony and hook, from the riff (not in the loop)
        chords = harmony(evs, root)
        names = sorted({_chord_name(c) for bar in chords for c, _ in bar})
        per_step = [step_chords(riff_events(g, i, steps), root) for i in sel]
        stabs = _mini([[_chord_name(c) if k in OFFBEATS else "~" for k, c in enumerate(row)] for row in per_step])
        hook = _weighted(evs, lambda e: _name(e.note + 24) if e.note is not None else "~")
        out.append(Piece("stabs", [f"chord stabs on the off-beat 8ths, each the chord over the bass note under it ({', '.join(names)}):",
                                   "the harmony moves when the riff does, so they never clash with it. Detuned saws with a little drive, short with a",
                                   "tail, no two hits alike (velocity); on the hook's bus (orbit 4): the same dotted-8th echo, quietly, and a short dark",
                                   "room; ducked by the kick"],
                         f'note("{stabs}").s("supersaw").unison(3).detune(.15).shape(.15).velocity(perlin.range(.8, 1).fast(2))\n'
                         f'    .attack(.003).decay(.14).sustain(.08).release(.12).lpf(2200).lpq(.8).hpf(250).gain(.36)\n'
                         f'    .delay(.12).delaysync(3/16).delayfeedback(.4).room({ROOM["stabs"]:g}).roomsize({ROOM["size"]:g}).roomlp({ROOM["lp"]}).orbit(4)', loop=False))
        out.append(Piece("pad", ["the same chords held: a pad following the riff's harmony - plain saws (not the stabs' supersaw) with a slow vibrato",
                                 "and a little drive, darker, high-passed above the bass's harmonics, a touch of room; on its own bus (orbit 6), ducked by the kick"],
                         f'note("{_runs([[(_chord_name(c), n) for c, n in bar] for bar in chords])}").s("sawtooth").vib(.3).vibmod(.12).shape(.1)\n'
                         f'    .attack(.2).decay(.2).sustain(.9).release(.35).lpf(800).hpf(320).gain(.07)\n'
                         f'    .room({ROOM["pad"]:g}).roomsize({ROOM["size"]:g}).roomlp({ROOM["lp"]}).orbit(6){late_s}', loop=False, frame="riff"))
        out.append(Piece("hook", ["the hook: the riff two octaves up in the bass's own voice - a driven saw plucked by its filter - so it reads as the",
                                  "bass's answer; a dotted-8th echo on its own bus (orbit 4), a short room, a touch right; ducked by the kick"],
                         f'note("{hook}").s("sawtooth").shape(.2).gain(.45)\n    .velocity("{velocity}")\n'
                         f'    .lpf(perlin.range(1800, 2400).slow(3)).lpenv(2).lpattack(.003).lpdecay(.17).lpsustain(.2).lpq(1.5)\n'
                         f'    .attack(.004).decay(.22).sustain(.3).release(.06).hpf(300).pan(.58)\n'
                         f'    .delay(.25).delaysync(3/16).delayfeedback(.4).room({ROOM["hook"]:g}).roomsize({ROOM["size"]:g}).roomlp({ROOM["lp"]}).orbit(4){late_s}', loop=False, frame="riff"))
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
        kick_of = lambda pat, boom_=True, gain="": f's("{pat.replace("x", "sbd")}").note("{boom}").penv({KICK["penv"]}).pdecay({KICK["pdecay"]:g}).decay({KICK["decay"]:g}).gain({KICK_GAIN:g})' + gain
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
        kick_of = lambda pat, boom=True, gain="": f's("{pat.replace("x", "bd")}").gain(1.1)' + gain
        out.append(Piece("kick", ["your booms"], kick_of(kicks)))
        if any(x != "~" for row in snares for x in row):
            out.append(Piece("yourSnares", ["your snares"], f's("{_mini(snares)}").gain(.8)'))
        if any(x != "~" for row in hats for x in row):
            out.append(Piece("yourHats", ["your hats"], f's("{_mini(hats)}").gain(.55)'))
        if own:
            out.append(own)
    for x in out:
        if x.name == "own":
            x.code = x.code.replace("{gain}", ".2" if g.oscillator else ".5").replace("{fx}", ".lpf(6000)" if g.oscillator else "")
    return head, out, kick_of


def _options(g: Groove, style: str, extras: list[Piece] = ()) -> list[str]:
    """Pieces not in the beatbox, as commented lines to uncomment."""
    if g.oscillator and style == "synth":
        out = ["// not in your beatbox - uncomment to add the off-beat open hat, or a backbeat:",
               f'// , s("[~ oh]*4").bank("{DRUM_BANK}").gain(.2)', f'// , s("~ cp ~ cp").bank("{DRUM_BANK}").gain(.35)']
        for x in extras:
            out += ["// " + x.about[0]] + ["// , " + x.code.replace("\n    ", "")]
        return out
    if g.oscillator:
        return ["// not in your beatbox - uncomment to add a backbeat:", '// , s("~ cp ~ cp").gain(.35)']
    if not g.snare_played:
        return ["// no backbeat in your beatbox - uncomment to add one:", '// , s("~ cp ~ cp").gain(.7)']
    return []


def strudel(g: Groove, name: str = "beatbox", kit: dict[str, str] | None = None, bars: list[int] | None = None,
            style: str = "synth", bass: str = BASS, hats: str = HAT) -> str:
    """The groove as a Strudel loop. With a pitched part, in the "synth" style: the riff as the synth it stands
    for, on the pulse of its bwoms, on one of the BASSES, with HATS, over a 909 kick the sub ducks under (the
    song's chord stabs, pad and hook as commented lines); in the "mouth" style: the riff on detuned saws with
    filter and loudness moving note by note the way the mouth moved them, a sub an octave under, and the kicks
    as booms like the ones made. Without a pitched part: the kicks, snares and hats on drum-machine sounds."""
    sel = bars if bars is not None else loop(g)
    head, ps, _ = pieces(g, kit, sel, style, bass, hats)
    lines = [f'// "{name}": your beatbox as a beat', f"// {g.bpm:g} BPM, {len(sel)} bar{'s' if len(sel) > 1 else ''}, looped"]
    lines += head + [""] + _setup(g, kit) + ["", "stack("]
    for x in ps:
        if x.loop:
            lines += [f"  // {a}" for a in x.about] + ["  " + x.code + ","]
    lines[-1] = lines[-1].rstrip(",")
    lines += ["  " + o for o in _options(g, style, [x for x in ps if not x.loop])] + [")"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- the song

# The form: (bars, what happens, the pieces that play). "lead" is the riff (or the mouth's oscillator), "drums"
# any other sounds beatboxed; the rest are the song's own pieces (hats, harmony, the hook, a riser, crashes).
# ("pull", [...]) takes those pieces out after two beats of the section's last bar, ("end", [...]) leaves them one
# hit there. A section nothing of which exists falls back to the drums, then to silence. 8-bar phrases, something
# new in each; nothing ticking (hats, shaker) before the first drop; no claps
SONG = [                          # 16 | 16 | 16 | 16 | 32 | 16: every big change on a 16-bar line, as a DJ counts
    (8, "intro: your boom alone (and the samples warming up, silently): a DJ's mix-in", ["kick", "own", "preload"]),
    (8, "intro: the sub and the riff join, the riff quiet, low and dry", ["kick", "own", "sub", "drums", "leadLow"]),
    (8, "build: a pad takes up the riff's chords underneath, both opening up", ["kick", "own", "sub", "drums", "leadMid", "padLow"]),
    (8, "build: the riff gets louder and opens up, the pad too, over a riser and a reverse crash; kick, sub and riff pull out before the drop",
     [("pull", ["kick", "sub", "drums", "leadRise"]), "padOpen", "riser", "rcrash"]),
    (8, "drop: the hats and the open hat come in; every 8th bar the kick skips beat 4", ["kickHole", "own", "sub", "drums", "lead", "hats", "ohat"]),
    (8, "drop: chord stabs on the off-beats, following the bass", ["kickHole", "own", "sub", "drums", "lead", "hats", "ohat", "stabs"]),
    (8, "breakdown: no kick - the pad holds the chords, the riff echoes, opening upward", ["padOpen", "leadEcho"]),
    (8, "breakdown: the sub comes back under it, the pad swells, a riser, the kick rolls in over two bars", ["kickReturn", "sub", "drums", "leadEcho2", "padSwell", "riser", "rcrash"]),
    (8, "drop 2: the stabs back, a ride on the bwoms, the hook answering in the last two bars of the phrase", ["kickHole", "own", "sub", "drums", "lead", "hats", "ohat", "stabs", "ride", "hookAnswer"]),
    (8, "drop 2: the hook all the way through; the hats drop out for the last bar", ["kickHole", "own", "sub", "drums", "lead", "hatsBreak", "ohat", "stabs", "ride", "hook"]),
    (8, "drop 2: the stabs' filter opening across the phrase; kick and hats drop out for two beats before the peak",
     [("pull", ["kick", "own", "hats"]), "sub", "drums", "lead", "ohat", "stabsOpen", "ride", "hook"]),
    (8, "drop 2: the pad under it all, the riff's filter open - the peak", ["kickHole", "own", "sub", "drums", "leadOpen", "hats", "ohat", "stabsOpen", "ride", "hook", "padPeak"]),
    (8, "outro: hook, stabs and pad out; the beat and the riff", ["kick", "own", "sub", "drums", "lead", "hats", "ohat"]),
    (8, "outro: the kick and the sub, one last hit together", [("end", ["kick", "subEnd", "drums"])]),
]


def song(g: Groove, name: str = "beatbox", kit: dict[str, str] | None = None, bars: list[int] | None = None,
         style: str = "synth", bass: str = BASS, hats: str = HAT, form: list[tuple[int, str, list[str]]] = SONG) -> str:
    """The beat as a track at TEMPO: the pieces as `const`s - the beat's, plus the harmony the riff implies
    (chord stabs on the off-beats and a pad, each chord the one over the bass note under it) and a hook (the
    riff two octaves up, echoing) - variations of them for the form (the riff and the pad filtered and opening,
    the riff echoing alone, the kick skipping a beat or rolling back in, a noise riser, a reversed crash, a ride
    on the bwoms, a shaker), and the form itself as `arrange(...)`, section by section."""
    sel = bars if bars is not None else loop(g)
    head, ps, kick_of = pieces(g, kit, sel, style, bass, hats, late=False)
    have = {x.name for x in ps}
    lead = "riff" if "riff" in have else "osc" if "osc" in have else None
    drums = [n for n in ("others", "yourSnares", "yourHats") if n in have]       # the sounds beatboxed, not the song's hats
    bpm = TEMPO if style == "synth" and g.oscillator else g.bpm
    bar_s = 4 * 60 / bpm
    steps = pulse(g, sel) if g.oscillator else []
    shift = f"{steps[0]}/{STEPS}" if lead == "riff" and steps and steps[0] else ""   # the riff frame's move onto the first bwom
    fr = "riff" if shift else "beat"
    var = [Piece("kickReturn", ["silent, then a roll of 8ths over the last two bars of 8, getting louder: the beat coming back"],
                 kick_of("[x ~ x ~ x ~ x ~ x ~ x ~ x ~ x ~]", False, '.gain("<.35 .45>")') + '.mask("<0 0 0 0 0 0 1 1>")'),
           Piece("kickHole", ["the kick, skipping beat 4 of every 8th bar: a hole, techno's fill"], 'kick.lastOf(8, x => x.mask("1 1 1 0"))')]
    if lead:
        full = BASSES[bass]["gain"] if lead == "riff" else OSC["gain"]
        var += [Piece(f"{lead}Low", ["quieter, the filter low, opening a little over 8 bars"], f"{lead}.gain({full * .6:.2g}).lpf(saw.range(200, 320).slow(8))", frame=fr),
                Piece(f"{lead}Mid", ["opening further"], f"{lead}.gain({full * .75:.2g}).lpf(saw.range(320, 420).slow(8))", frame=fr),
                Piece(f"{lead}Rise", ["opening to where it plays in the drop, getting louder"],
                      f"{lead}.gain(saw.range({full * .75:.2g}, {full:g}).slow(8)).lpf(saw.range(420, 560).slow(8))", frame=fr),
                Piece(f"{lead}Echo", ["a little quieter, a dub echo onto the next bwom on its own bus (orbit 3), the filter opening upward: air, not mud"],
                      f"{lead}.gain({full * .8:.2g}).delay(.5).delaysync(3/16).delayfeedback(.5).lpf(saw.range(600, 1200).slow(8)).orbit(3)", frame=fr),
                Piece(f"{lead}Echo2", ["the echo opening further toward the drop"], f"{lead}.delay(.5).delaysync(3/16).delayfeedback(.5).lpf(saw.range(1200, 2500).slow(8)).orbit(3)", frame=fr)]
    if "pad" in have:
        var += [Piece("padLow", ["the pad quiet and dark, opening over 8 bars"], "pad.gain(.06).lpf(saw.range(300, 600).slow(8))", frame=fr),
                Piece("padOpen", ["the pad opening up"], "pad.lpf(saw.range(600, 1400).slow(8))", frame=fr),
                Piece("padSwell", ["the pad opening wide and getting louder, toward the drop"], "pad.gain(saw.range(.07, .1).slow(8)).lpf(saw.range(1000, 2600).slow(8))", frame=fr),
                Piece("stabsOpen", ["the stabs, their filter opening over every 8 bars, brighter than before"], "stabs.lpf(saw.range(1500, 6000).slow(8))"),
                Piece("hookAnswer", ["the hook in the last two bars of every 8: an answer to the riff"], 'hook.mask("<0 0 0 0 0 0 1 1>")', frame=fr),
                Piece("padPeak", ["the pad louder and opening wider, under the peak"], "pad.gain(.14).lpf(saw.range(900, 2200).slow(8))", frame=fr),
                Piece(f"{lead}Open", ["the riff's filter opened up, for the peak"], f"{lead}.lpf(perlin.range(600, 900).slow(4))", frame=fr)]
    if lead:
        deep = _name(34 + ((g.root if g.root is not None else 9) - 34) % 12)
        var += [Piece("hatsBreak", ["the hats, out for the last bar of 8"], 'hats.mask("<1 1 1 1 1 1 1 0>")'),
                Piece("subEnd", ["the sub's root on the beat frame, so the last hit lands with the kick"],
                      f'note("{deep}").s("sine").gain({SYNTH_SUB:g}).attack(.001).decay(.8).sustain(0).release(.1).orbit(2)' if lead == "riff" else "sub")]
    t = 8 * bar_s * RISER_CLIP
    ride = _mini([["rd" if k in steps else "~" for k in range(STEPS)]]) if steps else '[~ rd]*4'
    shaker = " ".join(f"{.6 if k in OFFBEATS else .36 if k % 2 else .24:g}" for k in range(STEPS))
    var += [Piece("riser", ["8 bars of pink noise swelling the whole way, its low edge sweeping up from 800 Hz (a whoosh, not static), centred: one 8-bar event,",
                            "so a patterned pan would be sampled once at its onset"
                            + (f", stopping {8 * (1 - RISER_CLIP):.2g} bars early" if RISER_CLIP < 1 else "")],
                  f's("pink").slow(8)' + (f".clip({RISER_CLIP:g})" if RISER_CLIP < 1 else "")
                  + f'.hpf(800).hpenv(2).hpattack({t * .95:.3g}).hpsustain(1).lpf(9000).attack({t * .95:.3g}).gain(.3).pan(.5)'),
            Piece("ohat", ["an open hat on the off-beat 8ths: house's backbone with the kick; a touch left, in the kit's room"],
                  f's("[~ oh]*4").bank("{DRUM_BANK}").gain(.13).hpf(250).lpf(10000).pan(.4).room({ROOM["ohat"]:g}).roomsize({ROOM["size"]:g}).roomlp({ROOM["lp"]}).orbit(5)'),
            Piece("rcrash", ["a reversed crash swelling into the downbeat after the last bar of 8, in the kit's room"],
                  f's("cr").speed(-1).gain(.6).late(9/16).mask("<0 0 0 0 0 0 0 1>").pan(.55).room({ROOM["crash"]:g}).roomsize({ROOM["size"]:g}).roomlp({ROOM["lp"]}).orbit(5)'),
            Piece("ride", ["a ride on the bwoms, quiet: the riff's pulse on metal; a touch right, in the kit's room"],
                  f's("{ride}").bank("{DRUM_BANK}").gain(.12).hpf(250).lpf(10000).pan(.62).room({ROOM["ride"]:g}).roomsize({ROOM["size"]:g}).roomlp({ROOM["lp"]}).orbit(5)'),
            Piece("shaker", ["a shaker in 16ths, accented on the off-beats, swung with the hats, its top rolled off; not in the form (Dirt's shaker is a 10 kHz tick, inaudible under the hats at any sane level) - add it to a section if you want it"],
                  f's("sh*16").gain("[{shaker}]").late("[0 {SWING:g}]*8").lpf(7000).degradeBy(.1).orbit(5)'),
            Piece("preload", ["the samples and the rooms, triggered silently once so their first real hit isn't lost to loading"],
                  f'stack(s("cr sh").gain(.001), s("bd oh rd hh").bank("{DRUM_BANK}").gain(.001).room({ROOM["ohat"]:g}).roomsize({ROOM["size"]:g}).roomlp({ROOM["lp"]}).orbit(5)'
                  + (f', s("hh").bank("{HATS[hats]["bank"]}").gain(.001)' if HATS[hats]["bank"] != DRUM_BANK else "")
                  + f', note("a#3 a#3").s("sawtooth").gain(.001).room({ROOM["stabs"]:g}).roomsize({ROOM["size"]:g}).roomlp({ROOM["lp"]}).orbit("6 4")).mask("<1 0 0 0 0 0 0 0>")')]
    have |= {x.name for x in var}
    total = sum(n for n, _, _ in form)
    secs = total * bar_s
    lines = [f'// "{name}": your beatbox as a track', f"// {total} bars, {int(secs // 60)}:{int(secs % 60):02d} at {bpm:g} BPM"
             + (f" (your take: {g.bpm:g})" if bpm != g.bpm else "") + " - " + ", ".join(dict.fromkeys(t.split(":")[0] for _, t, _ in form))]
    lines += head + [""] + _setup(g, kit, bpm) + ["", "// the pieces"]
    for x in ps:
        lines += [f"// {a}" for a in x.about] + [f"const {x.name} = {x.code}"]
    lines.append("")
    lines.append("// variations of them, for the form")
    for x in var:
        lines.append(f"const {x.name} = {x.code}   // {'; '.join(x.about)}")
    lines += ["", "// in the last bar of 8: out after two beats (before a drop), or one hit (the end)",
              'const pull = p => p.lastOf(8, x => x.mask("1 1 0 0"))', 'const end = p => p.lastOf(8, x => x.mask("1 0 0 0"))', ""]
    frames = {x.name: x.frame for x in ps + var}

    def resolve(layers: list, frame: str) -> list[str]:
        names = []
        for layer in layers:
            if isinstance(layer, tuple):
                inner = resolve(layer[1], frame)
                names += [f"{layer[0]}(stack({', '.join(inner)}))"] if inner else []
                continue
            layer = layer.replace("lead", lead) if lead and layer.startswith("lead") else layer
            names += [n for n in (drums if layer == "drums" else [layer] if layer in have else []) if frames[n] == frame]
        return names

    def rows(frame: str, indent: str) -> list[str]:
        out, at = [], 1
        for n, title, layers in form:
            names = resolve(layers, frame) or (drums if frame == "beat" else [])
            expr = f"stack({', '.join(names)})" if len(names) > 1 else names[0] if names else "silence"
            out.append(f"{indent}[{n}, {expr}],".ljust(70 + len(indent)) + f"// {at}-{at + n - 1}" + (f": {title}" if frame == "beat" else ""))
            at += n
        out[-1] = out[-1].replace("],", "] ", 1)
        return out

    if shift:
        lines += ["// the form: bars, what plays. The riff and what follows it (sub, pad, hook) are arranged apart and moved onto the",
                  "// first bwom as a whole (late), so a note ringing over a bar line isn't cut off where a section begins",
                  "stack(", "  arrange("] + rows("beat", "    ") + ["  ),", "  arrange("] + rows("riff", "    ") + [f"  ).late({shift})", ")"]
    else:
        lines += ["// the form: bars, what plays", "arrange("] + rows("beat", "  ") + [")"]
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
