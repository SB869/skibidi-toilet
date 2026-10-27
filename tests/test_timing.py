import numpy as np

from sound import pitch, timing
from test_autotune import SR, held, voice


def clicks(bpm: float, seconds: float, start: float = 0.37) -> np.ndarray:
    """A click track: short decaying noise bursts on every beat."""
    x = np.zeros(int(seconds * SR))
    rng = np.random.default_rng(0)
    burst = rng.standard_normal(int(0.03 * SR)) * np.exp(-np.linspace(0, 8, int(0.03 * SR)))
    for t in np.arange(start, seconds - 0.05, 60 / bpm):
        x[int(t * SR): int(t * SR) + len(burst)] += burst
    return x


def test_beats_follow_a_steady_pulse():
    found = timing.beats(clicks(112, 30), SR)
    assert abs(timing.tempo_near(found, 15) - 112) < 2
    on_grid = (found - 0.37) / (60 / 112)
    assert np.median(np.abs(on_grid - np.round(on_grid))) < 0.08


def test_entry_finds_where_the_voice_comes_in():
    x = np.concatenate([np.zeros(int(1.3 * SR)), voice(held(57, 1.0))])
    x[: int(1.3 * SR)] += 0.001 * np.random.default_rng(1).standard_normal(int(1.3 * SR))   # room noise
    track = pitch.track(x, SR, min_prob=0.0)
    assert abs(timing.entry(x, SR, track) - 1.3) < 0.05


def test_entry_ignores_a_quiet_blip_before_the_voice():
    blip = 0.01 * voice(held(64, 0.25))
    x = np.concatenate([np.zeros(int(0.5 * SR)), blip, np.zeros(int(0.5 * SR)), voice(held(57, 1.0))])
    track = pitch.track(x, SR, min_prob=0.0)
    assert abs(timing.entry(x, SR, track) - 1.25) < 0.05


def test_entry_starts_at_the_consonant_not_the_pitch():
    # A "s"-like hiss for 80 ms runs straight into the vowel: the singer is heard from the hiss.
    rng = np.random.default_rng(2)
    hiss = 0.05 * rng.standard_normal(int(0.08 * SR))
    x = np.concatenate([np.zeros(int(1.0 * SR)), hiss, voice(held(57, 1.0))])
    track = pitch.track(x, SR, min_prob=0.0)
    assert abs(timing.entry(x, SR, track) - 1.0) < 0.03
