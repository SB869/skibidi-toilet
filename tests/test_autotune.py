import shutil
import subprocess

import numpy as np
import pytest
import soundfile as sf
from scipy.signal import lfilter

from sound import audiofile, pitch, psola
from sound.scales import estimate_key, hz_to_midi, nearest, parse_key, scale_notes
from sound.tune import TuneSettings, autotune

SR = 44100
FORMANTS = ((700, 130), (1220, 70), (2600, 160))   # an "ah" vowel


def voice(f0: np.ndarray, sr: int = SR) -> np.ndarray:
    """Crude sung vowel: band-limited sawtooth glottal source through three formant resonators."""
    phase = 2 * np.pi * np.cumsum(f0) / sr
    source = sum(np.sin(k * phase) / k for k in range(1, int(sr / 2 / f0.max())))
    out = source
    for freq, bw in FORMANTS:
        r = np.exp(-np.pi * bw / sr)
        out = lfilter([1 - r], [1, -2 * r * np.cos(2 * np.pi * freq / sr), r * r], out)
    ramp = np.minimum(1, np.minimum(np.arange(len(f0)), np.arange(len(f0))[::-1]) / (0.02 * sr))
    return 0.5 * out / np.abs(out).max() * ramp


def cents_off(audio: np.ndarray, target_hz: float, sr: int = SR) -> np.ndarray:
    """Per-frame deviation of the measured pitch from target_hz, in cents, voiced frames only."""
    track = pitch.track(audio, sr)
    return 1200 * np.log2(track.f0[track.voiced] / target_hz)


def held(midi: float, seconds: float = 1.5, vibrato_cents: float = 0.0) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return 440 * 2 ** ((midi - 69) / 12 + vibrato_cents / 1200 * np.sin(2 * np.pi * 5.5 * t))


def test_unshifted_resynthesis_is_identical():
    x = voice(held(57.3, vibrato_cents=20)) + 0.01 * np.random.default_rng(0).standard_normal(int(1.5 * SR))
    track = pitch.track(x, SR)
    marks, _ = psola.find_marks(x, SR, track)
    y = psola.shift(x[:, None], marks, np.ones(len(marks)))
    np.testing.assert_allclose(y[:, 0], x, atol=1e-12)


def test_pitch_marks_follow_the_period():
    x = voice(held(57))  # A3, 220 Hz
    track = pitch.track(x, SR)
    marks, segment = psola.find_marks(x, SR, track)
    spacing = np.diff(marks[segment >= 0])
    assert len(spacing) > 200
    np.testing.assert_allclose(np.median(spacing), SR / 220, atol=1)


def test_sharp_note_is_pulled_into_tune():
    x = voice(held(57.35, vibrato_cents=8))       # A3, 35 cents sharp
    assert abs(np.median(cents_off(x, 220)) - 35) < 3

    result = autotune(x[:, None], SR, TuneSettings(speed_ms=0))
    off = cents_off(result.audio[:, 0], 220)
    assert abs(np.median(off)) < 3
    assert np.percentile(np.abs(off), 90) < 8


def test_scale_snaps_to_key_not_nearest_semitone():
    x = voice(held(63.3))                          # D#4 + 30c; not in C major, E4 is closer than D4
    result = autotune(x[:, None], SR, TuneSettings(tonic=0, scale="major", speed_ms=0))
    assert abs(np.median(cents_off(result.audio[:, 0], 440 * 2 ** ((64 - 69) / 12)))) < 5


def test_retune_speed_keeps_vibrato_when_slow():
    x = voice(held(57.2, seconds=2.0, vibrato_cents=40))
    hard = cents_off(autotune(x[:, None], SR, TuneSettings(speed_ms=0)).audio[:, 0], 220)
    soft = cents_off(autotune(x[:, None], SR, TuneSettings(speed_ms=200)).audio[:, 0], 220)
    assert np.std(hard) < 6                          # vibrato flattened
    assert np.std(soft) > 20                         # vibrato survives
    assert abs(np.median(soft)) < 8                  # but the note is still centred


def test_natural_style_keeps_vibrato_and_centres_the_note():
    x = voice(held(57.35, seconds=2.0, vibrato_cents=40))
    before = cents_off(x, 220)
    natural = cents_off(autotune(x[:, None], SR, TuneSettings(style="natural")).audio[:, 0], 220)
    assert abs(np.median(natural)) < 4
    assert abs(np.std(natural) - np.std(before)) < 3     # vibrato untouched


def note_sequence(notes, glide=0.08, vibrato_cents=25.0):
    """Sung notes [(midi, seconds)] joined by short slides, with vibrato."""
    f0, prev = [], None
    for midi, seconds in notes:
        t = np.arange(int(seconds * SR)) / SR
        start = midi if prev is None else prev
        pitch_ = start + (midi - start) * np.minimum(1, t / glide)
        pitch_ = pitch_ + vibrato_cents / 100 * np.sin(2 * np.pi * 5.5 * t) * np.clip((t - 0.2) / 0.2, 0, 1)
        f0.append(440 * 2 ** ((pitch_ - 69) / 12))
        prev = midi
    return voice(np.concatenate(f0))


def test_natural_style_glides_between_notes():
    x = note_sequence([(57.4, 0.8), (59.6, 0.8), (61.3, 0.8)])   # A3 sharp, C4 flat... each off its own way
    result = autotune(x[:, None], SR, TuneSettings(style="natural"))
    shift = (result.corrected - result.detected)[result.track.voiced]
    assert np.max(np.abs(np.diff(shift))) < 0.15                # no steps in the correction
    held = [np.unique(result.target[(result.track.times > s + 0.25) & (result.track.times < s + 0.6)])
            for s in (0.0, 0.8, 1.6)]
    assert [list(h) for h in held] == [[57.0], [60.0], [61.0]]


def test_natural_style_makes_one_decision_per_note():
    # Wobbling either side of the A/A# midpoint: autotune flips between the two, natural picks one.
    t = np.arange(int(1.5 * SR)) / SR
    x = voice(440 * 2 ** ((57.5 + 0.3 * np.sin(2 * np.pi * 1.2 * t) - 69) / 12))
    natural = autotune(x[:, None], SR, TuneSettings(style="natural"))
    snapped = autotune(x[:, None], SR, TuneSettings(speed_ms=0, hysteresis=0.05))
    voiced = natural.track.voiced
    assert len(np.unique(natural.target[voiced])) == 1
    assert len(np.unique(snapped.target[voiced])) == 2


def test_natural_drift_straightens_wander_but_not_vibrato():
    t = np.arange(int(2.0 * SR)) / SR
    wander = 0.4 * np.sin(2 * np.pi * 0.5 * t)                  # slow, half a semitone either way
    vibrato = 0.25 * np.sin(2 * np.pi * 5.5 * t)
    x = voice(440 * 2 ** ((57 + wander + vibrato - 69) / 12))
    plain = autotune(x[:, None], SR, TuneSettings(style="natural"))
    straight = autotune(x[:, None], SR, TuneSettings(style="natural", drift=1.0))
    voiced = plain.track.voiced
    slow = lambda r: np.convolve(r.corrected[voiced], np.ones(86) / 86, mode="valid")   # ~0.5 s average
    fast = lambda r: r.corrected[voiced] - np.convolve(r.corrected[voiced], np.ones(86) / 86, mode="same")
    assert np.std(slow(straight)) < 0.4 * np.std(slow(plain))
    assert abs(np.std(fast(straight)[100:-100]) - np.std(fast(plain)[100:-100])) < 0.03


def contour(*parts):
    """Build a pitch contour (MIDI per sample) from (start_midi, end_midi, seconds) ramps."""
    return np.concatenate([np.linspace(a, b, int(s * SR)) for a, b, s in parts])


def shift_at(result, t0, t1):
    sel = (result.track.times >= t0) & (result.track.times < t1) & result.track.voiced
    return 100 * (result.corrected - result.detected)[sel]


def test_natural_scoops_and_fall_offs_keep_their_shape():
    # Scoop up a semitone into a held note that's 40c sharp, then fall off at the end.
    midi = contour((56.4, 57.4, 0.25), (57.4, 57.4, 0.9), (57.4, 56.2, 0.25))
    r = autotune(voice(440 * 2 ** ((midi - 69) / 12))[:, None], SR, TuneSettings(style="natural"))
    held = np.median(shift_at(r, 0.5, 1.0))
    assert abs(held + 40) < 5                                       # the note is moved into tune...
    assert np.all(np.abs(shift_at(r, 0.05, 0.25) - held) < 6)       # ...and the scoop moves with it
    assert np.all(np.abs(shift_at(r, 1.2, 1.35) - held) < 6)        # ...and so does the fall-off


def test_natural_changes_correction_during_the_slide_not_the_held_notes():
    # A3 sung 30c sharp, a quick slide, C4 sung 30c flat: the correction must swing by 60c.
    midi = contour((57.3, 57.3, 0.7), (57.3, 59.7, 0.15), (59.7, 59.7, 0.7))
    r = autotune(voice(440 * 2 ** ((midi - 69) / 12))[:, None], SR, TuneSettings(style="natural", humanize=0))
    shift = 100 * (r.corrected - r.detected)
    change = np.abs(np.diff(shift))
    t = r.track.times[1:]
    both = r.track.voiced[1:] & r.track.voiced[:-1]
    in_slide = change[both & (t > 0.68) & (t < 0.87)].sum()
    assert in_slide > 0.85 * change[both].sum()
    assert abs(np.median(shift_at(r, 0.2, 0.5)) + 30) < 4 and abs(np.median(shift_at(r, 1.1, 1.4)) - 30) < 4


def test_natural_humanize_keeps_some_of_each_note():
    # Three held notes leaning different ways; humanize keeps a little of each lean, capped.
    midi = contour((57.25, 57.25, 0.8), (57.25, 59.05, 0.12), (59.05, 59.05, 0.8), (59.05, 60.4, 0.12), (60.4, 60.4, 0.8))
    x = voice(440 * 2 ** ((midi - 69) / 12))[:, None]
    exact = autotune(x, SR, TuneSettings(style="natural", humanize=0))
    human = autotune(x, SR, TuneSettings(style="natural", humanize=1))
    centres = lambda r: [100 * (np.median(r.corrected[(r.track.times > a) & (r.track.times < b)]) - n)
                         for (a, b), n in zip([(0.3, 0.6), (1.2, 1.5), (2.1, 2.4)], [57, 59, 60])]
    assert np.max(np.abs(centres(exact))) < 2
    assert 4 < np.max(np.abs(centres(human))) <= 13


def test_natural_still_tunes_a_shaky_short_note():
    # A 0.3 s note wobbling ±40c around a pitch 45c flat is a note, not a movement.
    t = np.arange(int(0.3 * SR)) / SR
    shaky = 61.55 + 0.4 * np.sin(2 * np.pi * 7 * t)
    midi = np.concatenate([contour((57.0, 57.0, 0.6), (57.0, 61.55, 0.1)), shaky, contour((61.55, 57.0, 0.1), (57.0, 57.0, 0.6))])
    r = autotune(voice(440 * 2 ** ((midi - 69) / 12))[:, None], SR, TuneSettings(style="natural", humanize=0))
    note = (r.track.times > 0.75) & (r.track.times < 0.95)
    assert abs(np.median(r.corrected[note]) - 62) < 0.1


def test_formants_stay_put_when_pitch_moves():
    x = voice(held(57))
    track = pitch.track(x, SR)
    marks, segment = psola.find_marks(x, SR, track)
    ratio = 2 ** (3 / 12)                            # up a minor third
    y = psola.shift(x[:, None], marks, np.where(segment >= 0, ratio, 1.0))[:, 0]

    assert abs(np.median(cents_off(y, 220 * ratio))) < 5

    # Speeding the tape up (the chipmunk way) drags every formant up 19%. PSOLA should
    # leave the spectral envelope far closer to the original than that.
    chipmunk = np.interp(np.arange(len(x)) * ratio, np.arange(len(x)), x, right=0)
    original = _envelope(x)
    assert np.mean(np.abs(_envelope(y) - original)) < 0.5 * np.mean(np.abs(_envelope(chipmunk) - original))


def _envelope(x: np.ndarray) -> np.ndarray:
    """Power spectrum smoothed over ~600 Hz (a few harmonics), in dB, 300 Hz - 4 kHz."""
    seg = x[SR // 4: SR // 4 + 8192] * np.hanning(8192)
    power = np.abs(np.fft.rfft(seg)) ** 2
    freqs = np.fft.rfftfreq(8192, 1 / SR)
    width = int(600 / freqs[1])
    smooth = np.convolve(power, np.ones(width) / width, mode="same")
    return 10 * np.log10(smooth[(freqs > 300) & (freqs < 4000)] / smooth.max())


def test_stereo_channels_are_both_processed():
    x = voice(held(57.35))
    stereo = np.stack([x, 0.5 * x], axis=1)
    out = autotune(stereo, SR, TuneSettings(speed_ms=0)).audio
    assert out.shape == stereo.shape
    np.testing.assert_allclose(out[:, 1], 0.5 * out[:, 0], atol=1e-6)


def test_unvoiced_audio_after_a_tuned_note_is_untouched():
    rng = np.random.default_rng(2)
    breath = 0.03 * rng.standard_normal(SR // 2)
    x = np.concatenate([voice(held(57.4, seconds=1.0)), breath, voice(held(60.3, seconds=1.0)), breath])
    out = autotune(x[:, None], SR, TuneSettings(speed_ms=0)).audio[:, 0]
    for start in (SR + SR // 10, 2 * SR + SR // 2 + SR + SR // 10):   # inside each breath, past the crossfade
        np.testing.assert_allclose(out[start:start + SR // 4], x[start:start + SR // 4], atol=1e-6)


def test_silence_and_noise_pass_through():
    noise = 0.05 * np.random.default_rng(1).standard_normal(SR)
    out = autotune(noise[:, None], SR, TuneSettings(speed_ms=0)).audio[:, 0]
    np.testing.assert_allclose(out, noise, atol=1e-6)


@pytest.mark.parametrize("text, expected", [
    ("C", (0, None)), ("f#", (6, None)), ("Bb", (10, None)), ("Am", (9, "minor")),
    ("D minor", (2, "minor")), ("Ebmaj", (3, "major")), ("C#m", (1, "minor")), ("B", (11, None)),
])
def test_parse_key(text, expected):
    assert parse_key(text) == expected


def test_parse_key_rejects_garbage():
    with pytest.raises(ValueError):
        parse_key("H")


def test_nearest_respects_scale():
    c_major = scale_notes(0, "major")
    assert nearest(61.4, c_major) == 62            # C#+40c -> D
    assert nearest(60.4, c_major) == 60            # C+40c -> C
    assert list(nearest(np.array([65.6, 66.6]), c_major)) == [65, 67]


def test_estimate_key_needs_a_key_holding_every_used_note():
    # Leans hard on A#, like the reference acapella: a plain profile match says A# minor,
    # but B is sung a lot and A# minor has no B. F# major / D# minor holds every note.
    melody = np.repeat([70] * 10 + [71] * 3 + [66] * 2 + [68] * 2, 20).astype(float)
    tonic, mode, _ = estimate_key(melody)
    assert (tonic, mode) in {(6, "major"), (3, "minor"), (11, "major"), (8, "minor")}
    assert (tonic, mode) != (10, "minor")


def test_estimate_key_from_melody():
    melody = np.repeat([69, 71, 72, 74, 76, 72, 69, 64, 69, 76, 74, 72, 71, 69], 20).astype(float)
    tonic, mode, _ = estimate_key(melody)
    assert (tonic, mode) == (9, "minor")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_loads_m4a_voice_memo(tmp_path):
    wav, m4a = tmp_path / "memo.wav", tmp_path / "memo.m4a"
    sf.write(wav, voice(held(60, seconds=0.5)), SR)
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav), "-c:a", "aac", str(m4a)], check=True)
    data, sr = audiofile.load(m4a)
    assert sr == SR and data.shape[1] == 1 and abs(len(data) / SR - 0.5) < 0.05


def test_save_prevents_clipping(tmp_path):
    out = tmp_path / "loud.wav"
    gain = audiofile.save(out, np.full((100, 1), 1.5, dtype=np.float32), SR)
    data, _ = sf.read(out)
    assert gain < 1 and np.abs(data).max() <= 1.0


def test_hz_to_midi():
    assert hz_to_midi(440.0) == 69
