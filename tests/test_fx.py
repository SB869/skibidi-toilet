import numpy as np
import pyloudnorm
from scipy.signal import butter, sosfilt, welch

from sound import fx, pitch
from test_autotune import SR, held, voice


def sung(midi=57.0, seconds=2.0, swell=True):
    """A held vowel that gets louder and softer, like singing does."""
    x = voice(held(midi, seconds=seconds, vibrato_cents=20))
    if swell:
        t = np.arange(len(x)) / SR
        x = x * (0.25 + 0.75 * (0.5 + 0.5 * np.sin(2 * np.pi * 0.7 * t)))
    return np.stack([x, x], axis=1)


def band_db(x, lo, hi):
    f, p = welch(x.mean(axis=1), SR, nperseg=4096)
    return 10 * np.log10(p[(f >= lo) & (f < hi)].mean())


def test_compression_narrows_dynamics():
    x = sung()
    assert fx._dynamics(fx.compress(x, SR, 0.8).mean(axis=1), SR) < fx._dynamics(x.mean(axis=1), SR) - 4


def test_saturation_adds_upper_harmonics():
    x = sung(swell=False)
    y = fx.saturate(x, SR, 0.8)
    gain_low = band_db(y, 200, 400) - band_db(x, 200, 400)
    gain_high = band_db(y, 3000, 6000) - band_db(x, 3000, 6000)
    assert gain_high > gain_low + 6


def test_match_tone_moves_towards_the_target():
    x = sung(swell=False)
    target = fx.tone_of(x.mean(axis=1), SR) + np.where(fx.TONE_GRID > 2000, 8.0, 0.0)  # same, but 8 dB brighter
    y = fx.match_tone(x, SR, target, 1.0)
    lift = (band_db(y, 3000, 6000) - band_db(y, 600, 1200)) - (band_db(x, 3000, 6000) - band_db(x, 600, 1200))
    assert 5 < lift < 11


def test_match_tone_keeps_a_lower_voice_its_fundamental():
    # Matching an A2 voice to an A3 reference: compared frequency for frequency, the low A2
    # fundamental looks like excess and gets cut away; compared harmonic for harmonic it stays.
    low, high = sung(45, swell=False), sung(57, swell=False)
    target = fx.tone_of(high.mean(axis=1), SR)
    naive = fx.match_tone(low, SR, target, 1.0, octaves_below=0.0)
    aware = fx.match_tone(low, SR, target, 1.0, octaves_below=1.0)
    weight = lambda y: band_db(y, 90, 140) - band_db(y, 1500, 3000)
    assert weight(aware) > weight(naive) + 6


def test_deess_tames_only_the_sibilance_band():
    rng = np.random.default_rng(0)
    x = sung(swell=False)
    hiss = np.zeros(len(x))
    hiss[SR // 2: SR // 2 + SR // 5] = 0.4 * rng.standard_normal(SR // 5)   # a loud "s"
    x = x + hiss[:, None]
    y = fx.deess(x, SR, 1.0)
    s = slice(SR // 2, SR // 2 + SR // 5)
    assert band_db(y[s], 4500, 10000) < band_db(x[s], 4500, 10000) - 4
    calm = slice(SR + SR // 4, SR + SR // 2)
    np.testing.assert_allclose(y[calm], x[calm], atol=1e-3)


def test_width_zero_is_mono():
    x = np.random.default_rng(1).standard_normal((SR, 2)) * 0.1
    y = fx.set_width(x, 0.0)
    np.testing.assert_allclose(y[:, 0], y[:, 1])


def test_doubles_add_stereo_and_keep_length():
    x = sung(swell=False)
    track = pitch.track(x.mean(axis=1), SR)
    y = fx.double(x, SR, track, 1.0)
    assert y.shape == x.shape
    side = lambda z: np.mean((z[:, 0] - z[:, 1]) ** 2)
    assert side(x) < 1e-12 < side(y)


def test_reverb_leaves_a_tail():
    x = np.zeros((SR * 2, 2))
    x[: SR // 2] = sung(seconds=0.5, swell=False)
    y = fx.reverb(x, SR, 0.8)
    tail = slice(int(0.7 * SR), int(0.9 * SR))
    assert np.sqrt(np.mean(y[tail] ** 2)) > 1e-3 > np.sqrt(np.mean(x[tail] ** 2))


def test_loudness_hits_target_under_the_ceiling():
    y = fx.loudness(sung(), SR, -12.0)
    assert abs(pyloudnorm.Meter(SR).integrated_loudness(y) - (-12.0)) < 0.5
    assert np.abs(y).max() <= 10 ** (fx.CEILING_DB / 20) + 1e-9


def test_full_chain_moves_a_take_towards_the_reference():
    take = sosfilt(butter(2, 1200, fs=SR, output="sos"), sung(57), axis=0)   # dark (think phone mic) and dynamic
    reference = fx.saturate(sung(57, swell=False), SR, 0.6)                   # bright and steady
    ref_profile = fx.measure(reference, SR, pitch.track(reference.mean(axis=1), SR))
    track = pitch.track(take.mean(axis=1), SR)
    out = fx.process(take[:, :1], SR, fx.Chain(loudness=-11.0), track=track, reference=ref_profile)
    assert out.shape == (len(take), 2) and out.dtype == np.float32
    before, after = fx.measure(take, SR, track), fx.measure(out, SR, track)
    assert abs(after.lufs + 11.0) < 0.6
    assert after.dynamics < before.dynamics
    assert after.brightness > before.brightness
