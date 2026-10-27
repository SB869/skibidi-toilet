"""Vocal production: the chain that turns a dry take into a finished-sounding vocal.

    low cut -> tone -> compression -> saturation -> tone touch-up -> de-ess -> width -> doubles
    -> reverb -> loudness

Tone comes before compression so the compressor responds to a balanced voice rather than to
a boomy low end, then is touched up after saturation (which adds upper harmonics).

Every stage is offline and deterministic. Tone can be matched to a reference vocal: its
spectral balance is measured and an EQ curve drawn to move the take towards it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyloudnorm
from numba import njit
from scipy.signal import butter, fftconvolve, firwin2, resample_poly, sosfilt, sosfiltfilt, welch

from . import psola
from .pitch import PitchTrack

# tone matching
TONE_GRID = np.geomspace(60, 16000, 97)   # 1/12-octave steps
TONE_SMOOTH_OCT = 1 / 3
PITCHED_BELOW_HZ = 600                    # below here the balance moves with the voice's pitch (its lowest harmonics)...
FIXED_ABOVE_HZ = 1200                     # ...above here it doesn't (vowel resonances, presence, air); blended between
TONE_MAX_BOOST_DB = 12.0                  # boosts lift hiss too, so keep them modest
TONE_MAX_CUT_DB = 24.0
EQ_TAPS = 2047

# saturation
OVERSAMPLE = 4
SAT_BAND = (180.0, 7000.0)
SAT_BIAS = 0.2                            # a little asymmetry: even harmonics, like a tube

# de-esser
ESS_BAND = (4500.0, 10000.0)
ESS_THRESHOLD_DB = -12.0                  # sibilance band level relative to the whole signal before it's reduced

# doubles
DOUBLE_DETUNE_CENTS = 9.0
DOUBLE_DELAYS_S = (0.019, 0.031)
DOUBLE_PANS = (-0.75, 0.75)

# reverb (Freeverb tunings at 44.1 kHz)
COMB_TUNING = np.array([1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617])
ALLPASS_TUNING = np.array([556, 441, 341, 225])
STEREO_SPREAD = 23
PREDELAY_S = 0.03

# loudness
CEILING_DB = -1.0
LIMITER_LOOKAHEAD_S = 0.003
LIMITER_RELEASE_S = 0.08


@dataclass
class Chain:
    low_cut: float = 90.0            # Hz
    compression: float = 0.45        # 0..1
    grit: float = 0.5                # 0..1 saturation
    tone: float = 1.0                # 0..1 how far to move towards the target tonal balance
    deess: float = 0.5               # 0..1 how hard to tame sharp s/t/sh sounds
    width: float = 0.3               # stereo width of the dry vocal (1 = as recorded, 0 = mono)
    doubles: float = 0.3             # 0..1 level of two detuned, delayed, panned copies
    space: float = 0.25              # 0..1 reverb
    loudness: float | None = -12.0   # integrated LUFS target; None leaves the level alone


@dataclass
class Profile:
    """The production character of a recording."""
    lufs: float
    dynamics: float      # dB between loud (p95) and soft (p10) sung moments, 50 ms windows
    brightness: float    # spectral centroid, Hz
    width: float         # side relative to mid, dB (-inf = mono)
    tone: np.ndarray     # smoothed spectrum on TONE_GRID, dB
    pitch: float         # median sung pitch, Hz (nan if unknown)


def measure(audio: np.ndarray, sr: int, track: PitchTrack | None = None) -> Profile:
    """Profile a recording. With its pitch track, dynamics only count sung moments (so
    breaths and room noise don't read as quiet singing) and the sung pitch is known."""
    audio = _stereo(audio)
    mid, side = audio.mean(axis=1), (audio[:, 0] - audio[:, 1]) / 2
    with np.errstate(divide="ignore"):
        lufs = float(pyloudnorm.Meter(sr).integrated_loudness(audio))
        width = float(10 * np.log10(np.mean(side ** 2) / max(np.mean(mid ** 2), 1e-20)))
    f, p = welch(mid, sr, nperseg=8192)
    sung = track.times[track.voiced] if track is not None else None
    voice_pitch = float(np.median(track.f0[track.voiced])) if track is not None and track.voiced.any() else np.nan
    return Profile(lufs, _dynamics(mid, sr, sung), float((f * p).sum() / max(p.sum(), 1e-20)), width,
                   tone_of(mid, sr), voice_pitch)


def tone_of(mono: np.ndarray, sr: int) -> np.ndarray:
    """Long-term spectrum smoothed to 1/3 octave, in dB, on TONE_GRID."""
    f, p = welch(mono, sr, nperseg=8192)
    lo, hi = TONE_GRID * 2 ** (-TONE_SMOOTH_OCT / 2), TONE_GRID * 2 ** (TONE_SMOOTH_OCT / 2)
    power = np.array([p[(f >= a) & (f < b)].mean() if np.any((f >= a) & (f < b)) else 1e-20 for a, b in zip(lo, hi)])
    return 10 * np.log10(power + 1e-20)


def process(audio: np.ndarray, sr: int, chain: Chain, track: PitchTrack | None = None,
            reference: Profile | None = None) -> np.ndarray:
    """Run the vocal chain.

    track is the pitch of `audio`: it lets doubles keep formants and lets tone matching
    allow for singing in a different register from the reference. reference (a Profile of
    the vocal to sound like) sets the tonal target; without one a forward rock-vocal
    balance is used."""
    x = _stereo(np.asarray(audio, dtype=np.float64))
    if chain.low_cut > 0:
        x = sosfilt(butter(2, chain.low_cut, btype="highpass", fs=sr, output="sos"), x, axis=0)
    target, octaves = HEAVY_TONE, 0.0
    if reference is not None:
        own = float(np.median(track.f0[track.voiced])) if track is not None and track.voiced.any() else np.nan
        target = reference.tone
        octaves = np.log2(reference.pitch / own) if np.isfinite(own) and np.isfinite(reference.pitch) else 0.0
    x = match_tone(x, sr, target, chain.tone, octaves)
    x = compress(x, sr, chain.compression)
    x = saturate(x, sr, chain.grit)
    x = match_tone(x, sr, target, chain.tone, octaves)
    x = deess(x, sr, chain.deess)
    x = set_width(x, chain.width)
    x = double(x, sr, track, chain.doubles)
    x = reverb(x, sr, chain.space)
    if chain.loudness is not None:
        x = loudness(x, sr, chain.loudness)
    return x.astype(np.float32)


# ---------------------------------------------------------------- stages

def compress(x: np.ndarray, sr: int, amount: float) -> np.ndarray:
    """Levelling compressor (stereo-linked RMS detector, soft knee) with automatic make-up.
    The threshold sits low in the singing's own level range, so `amount` behaves the same
    whatever the recording level."""
    if amount <= 0:
        return x
    level = _rms_db(x, sr, 0.005)
    active = level[level > level.max() - 40]
    threshold = float(np.percentile(active, 35))
    ratio = 1 + 7 * amount
    gain_db = _compressor_gain(level, threshold, ratio, 6.0, _coeff(0.008, sr), _coeff(0.12, sr))
    gain_db -= np.percentile(gain_db[level > threshold], 50)  # make-up: typical loud part back to level
    return x * (10 ** (gain_db / 20))[:, None]


def saturate(x: np.ndarray, sr: int, amount: float) -> np.ndarray:
    """Parallel harmonic saturation focused on the vocal band.

    Builds the upper harmonics a belted voice has and a soft one lacks, which is most of
    what makes a vocal sound heavy. Oversampled so the distortion doesn't alias."""
    if amount <= 0:
        return x
    band = sosfilt(butter(2, SAT_BAND, btype="bandpass", fs=sr, output="sos"), x, axis=0)
    band /= np.sqrt(np.mean(band ** 2)) + 1e-12
    drive = 0.6 + 4.4 * amount
    up = resample_poly(band, OVERSAMPLE, 1, axis=0)
    wet = np.tanh(up * drive + SAT_BIAS) - np.tanh(SAT_BIAS)
    wet = resample_poly(wet, 1, OVERSAMPLE, axis=0)[: len(x)]   # its anti-alias filter drops what folds back
    wet = sosfilt(butter(4, 9000, fs=sr, output="sos"), wet, axis=0)
    wet = sosfilt(butter(1, 30, btype="highpass", fs=sr, output="sos"), wet, axis=0)
    wet *= np.sqrt(np.mean(x ** 2)) / (np.sqrt(np.mean(wet ** 2)) + 1e-12)
    return x + 0.9 * amount * wet


def match_tone(x: np.ndarray, sr: int, target: np.ndarray, amount: float, octaves_below: float = 0.0) -> np.ndarray:
    """Linear-phase EQ moving x's tonal balance towards `target` (dB on TONE_GRID).

    octaves_below says how much lower x is sung than the target's voice. The low part of a
    voice's spectrum is its harmonics, which move with pitch, so there x is compared with
    the target that many octaves up: fundamental with fundamental. Presence and air sit at
    fixed frequencies whatever the pitch, so up there they're compared directly."""
    if amount <= 0:
        return x
    diff = _as_sung(target, octaves_below) - tone_of(x.mean(axis=1), sr)
    diff -= np.interp(1000, TONE_GRID, diff)                 # level is loudness's job, not EQ's
    curve = amount * np.clip(diff, -TONE_MAX_CUT_DB, TONE_MAX_BOOST_DB)
    nyquist = sr / 2
    grid = TONE_GRID[TONE_GRID < nyquist * 0.98]
    freqs = np.r_[0, grid, nyquist]
    gains = 10 ** (np.r_[curve[0], curve[: len(grid)], curve[len(grid) - 1]] / 20)
    taps = firwin2(EQ_TAPS, freqs, gains, fs=sr)
    y = fftconvolve(x, taps[:, None], axes=0)
    return y[EQ_TAPS // 2: EQ_TAPS // 2 + len(x)]


def deess(x: np.ndarray, sr: int, amount: float) -> np.ndarray:
    """Turn the sibilance band down only at the moments it stands out from the voice."""
    if amount <= 0:
        return x
    band = sosfiltfilt(butter(3, ESS_BAND, btype="bandpass", fs=sr, output="sos"), x, axis=0)
    rest = x - band  # zero-phase split, so band + rest is exactly x
    excess = _rms_db(band, sr, 0.004) - _rms_db(x, sr, 0.004)
    reduction = _compressor_gain(excess, ESS_THRESHOLD_DB, 1 + 7 * amount, 4.0, _coeff(0.001, sr), _coeff(0.05, sr))
    return rest + band * (10 ** (reduction / 20))[:, None]


def _as_sung(target: np.ndarray, octaves: float) -> np.ndarray:
    """The target balance as it would be for a voice `octaves` lower (see match_tone)."""
    if octaves == 0:
        return target
    grid = np.log2(TONE_GRID)
    moved = np.interp(grid + octaves, grid, target)
    fixed = np.clip((grid - np.log2(PITCHED_BELOW_HZ)) / (np.log2(FIXED_ABOVE_HZ) - np.log2(PITCHED_BELOW_HZ)), 0, 1)
    return (1 - fixed) * moved + fixed * target


def set_width(x: np.ndarray, width: float) -> np.ndarray:
    mid, side = (x[:, 0] + x[:, 1]) / 2, (x[:, 0] - x[:, 1]) / 2 * width
    return np.stack([mid + side, mid - side], axis=1)


def double(x: np.ndarray, sr: int, track: PitchTrack | None, amount: float, seed: int = 7) -> np.ndarray:
    """Two copies with their own slow pitch wander, a few tens of ms late, panned left and
    right: the thickness of a stacked vocal. Pitch is moved with PSOLA so the copies keep
    the voice's formants."""
    if amount <= 0 or track is None or not track.voiced.any():
        return x
    mono = x.mean(axis=1)
    marks, segment = psola.find_marks(mono, sr, track)
    t = marks / sr
    rng = np.random.default_rng(seed)
    level = 10 ** ((-14 + 10 * amount) / 20)
    out = x.copy()
    for delay, pan in zip(DOUBLE_DELAYS_S, DOUBLE_PANS):
        rates, phases = rng.uniform(0.2, 0.9, 3), rng.uniform(0, 2 * np.pi, 3)
        wander = sum(np.sin(2 * np.pi * r * t + p) for r, p in zip(rates, phases)) / 3
        ratios = np.where(segment >= 0, 2 ** (DOUBLE_DETUNE_CENTS * wander / 1200), 1.0)
        copy = psola.shift(mono[:, None], marks, ratios)[:, 0]
        lag = int(delay * sr)
        copy = np.r_[np.zeros(lag), copy[:-lag]]
        angle = (pan + 1) * np.pi / 4
        out[:, 0] += level * np.cos(angle) * copy
        out[:, 1] += level * np.sin(angle) * copy
    return out


def reverb(x: np.ndarray, sr: int, amount: float) -> np.ndarray:
    """Plate-like room (Freeverb) fed from a band-limited send, with a short pre-delay."""
    if amount <= 0:
        return x
    send = sosfilt(butter(2, [250, 7000], btype="bandpass", fs=sr, output="sos"), x.mean(axis=1))
    lag = int(PREDELAY_S * sr)
    send = np.r_[np.zeros(lag), send[:-lag]]
    scale = sr / 44100
    combs = np.maximum(1, (COMB_TUNING * scale).astype(np.int64))
    allpasses = np.maximum(1, (ALLPASS_TUNING * scale).astype(np.int64))
    feedback, damping = 0.80 + 0.12 * amount, 0.35
    left = _freeverb(send, combs, allpasses, feedback, damping)
    right = _freeverb(send, combs + int(STEREO_SPREAD * scale), allpasses + int(STEREO_SPREAD * scale), feedback, damping)
    wet = np.stack([left, right], axis=1)
    wet *= np.sqrt(np.mean(x ** 2)) / (np.sqrt(np.mean(wet ** 2)) + 1e-12)
    return x + 0.55 * amount * wet


def loudness(x: np.ndarray, sr: int, lufs: float) -> np.ndarray:
    """Bring to an integrated loudness, catching peaks with a look-ahead limiter."""
    meter = pyloudnorm.Meter(sr)
    for _ in range(3):  # limiting lowers loudness a little; correct and go again
        now = meter.integrated_loudness(x)
        if not np.isfinite(now):
            return x
        x = limit(x * 10 ** ((lufs - now) / 20), sr)
        if abs(meter.integrated_loudness(x) - lufs) < 0.3:
            break
    return x


def limit(x: np.ndarray, sr: int, ceiling_db: float = CEILING_DB) -> np.ndarray:
    ceiling = 10 ** (ceiling_db / 20)
    peak = np.abs(x).max(axis=1)
    gain = _limiter_gain(peak, ceiling, max(1, int(LIMITER_LOOKAHEAD_S * sr)), _coeff(LIMITER_RELEASE_S, sr))
    return np.clip(x * gain[:, None], -ceiling, ceiling)


# ---------------------------------------------------------------- helpers

# A forward, present rock-vocal balance (dB relative to 1 kHz), used when there's no reference.
HEAVY_TONE = np.interp(np.log2(TONE_GRID), np.log2([60, 150, 300, 600, 1000, 2000, 3000, 5000, 8000, 12000, 16000]),
                       [-16, -6, -2, 0, 0, -2, -2, -12, -14, -21, -26])


def _stereo(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    return np.repeat(x, 2, axis=1) if x.shape[1] == 1 else x[:, :2]


def _coeff(seconds: float, sr: int) -> float:
    return float(np.exp(-1.0 / (seconds * sr)))


def _rms_db(x: np.ndarray, sr: int, window_s: float) -> np.ndarray:
    n = max(1, int(window_s * sr))
    power = np.convolve((x ** 2).mean(axis=1), np.ones(n) / n, mode="same")
    return 10 * np.log10(power + 1e-12)


def _dynamics(mono: np.ndarray, sr: int, sung: np.ndarray | None = None) -> float:
    """Spread of 50 ms levels (p95 - p10) over sung moments, or, without a pitch track, over
    the loud body of the recording (within 20 dB of its p95)."""
    hop = int(0.05 * sr)
    frames = mono[: len(mono) // hop * hop].reshape(-1, hop)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    if sung is not None and len(sung):
        db = db[np.unique(np.clip((sung / 0.05).astype(int), 0, len(db) - 1))]
    else:
        db = db[db > np.percentile(db, 95) - 20]
    return float(np.percentile(db, 95) - np.percentile(db, 10)) if db.size else 0.0


@njit(cache=True)
def _compressor_gain(level_db, threshold, ratio, knee, attack, release):
    out = np.empty_like(level_db)
    slope = 1.0 / ratio - 1.0
    gr = 0.0
    for i in range(len(level_db)):
        over = level_db[i] - threshold
        if 2 * over <= -knee:
            target = 0.0
        elif 2 * abs(over) < knee:
            target = slope * (over + knee / 2) ** 2 / (2 * knee)
        else:
            target = slope * over
        coeff = attack if target < gr else release
        gr = coeff * gr + (1 - coeff) * target
        out[i] = gr
    return out


@njit(cache=True)
def _limiter_gain(peak, ceiling, lookahead, release):
    n = len(peak)
    need = np.empty(n)
    for i in range(n):
        need[i] = min(1.0, ceiling / max(peak[i], 1e-12))
    # the gain each sample must be at or below: the smallest need in the next `lookahead` samples
    window = np.empty(n)
    for i in range(n):
        m = 1.0
        for j in range(i, min(n, i + lookahead)):
            if need[j] < m:
                m = need[j]
        window[i] = m
    # The window starts dropping `lookahead` samples before a peak, so an attack this fast
    # is (almost) fully down by the time it arrives; the final clip catches the rest.
    attack = np.exp(-3.0 / lookahead)
    out = np.empty(n)
    g = 1.0
    for i in range(n):
        target = window[i]
        coeff = attack if target < g else release
        g = coeff * g + (1 - coeff) * target
        out[i] = g
    return out


@njit(cache=True)
def _freeverb(signal, combs, allpasses, feedback, damping):
    n = len(signal)
    out = np.empty(n)
    comb_buf = np.zeros((len(combs), combs.max()))
    comb_pos = np.zeros(len(combs), np.int64)
    comb_store = np.zeros(len(combs))
    all_buf = np.zeros((len(allpasses), allpasses.max()))
    all_pos = np.zeros(len(allpasses), np.int64)
    for i in range(n):
        x = signal[i] * 0.015
        acc = 0.0
        for c in range(len(combs)):
            y = comb_buf[c, comb_pos[c]]
            comb_store[c] = y * (1 - damping) + comb_store[c] * damping
            comb_buf[c, comb_pos[c]] = x + comb_store[c] * feedback
            comb_pos[c] = (comb_pos[c] + 1) % combs[c]
            acc += y
        for a in range(len(allpasses)):
            b = all_buf[a, all_pos[a]]
            all_buf[a, all_pos[a]] = acc + b * 0.5
            all_pos[a] = (all_pos[a] + 1) % allpasses[a]
            acc = b - acc
        out[i] = acc
    return out
