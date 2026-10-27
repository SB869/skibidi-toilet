import argparse

import numpy as np
import pytest

from sound import analysis
from sound.commands.common import file_stamp, fmt_time, parse_time
from sound.pitch import HOP, ANALYSIS_SR, PitchTrack
from sound.scales import midi_to_hz

HOP_S = HOP / ANALYSIS_SR
# (midi, seconds) with a short gap after each note
MELODY = [(66, 0.5), (70, 1.2), (71, 0.3), (70, 0.8), (68, 0.4), (66, 0.9), (61, 0.5), (65, 0.7)]


def track_of(melody, lead_in=0.0, tail=1.0, transpose=0.0, cents=0.0, gap=0.15, jitter=0.0, seed=0):
    rng = np.random.default_rng(seed)
    frames = [(np.nan, round(lead_in / HOP_S))]
    for midi, dur in melody:
        frames += [(midi + transpose + cents / 100, round(dur / HOP_S)), (np.nan, round(gap / HOP_S))]
    frames.append((np.nan, round(tail / HOP_S)))
    midi = np.concatenate([np.full(n, m) for m, n in frames])
    midi = midi + jitter * rng.standard_normal(len(midi))
    voiced = np.isfinite(midi)
    return PitchTrack(times=np.arange(len(midi)) * HOP_S, f0=np.where(voiced, midi_to_hz(midi), np.nan), voiced=voiced)


def onsets(melody, lead_in, stretch=1.0, gap=0.15):
    times, t = [], lead_in
    for _, dur in melody:
        times.append(t)
        t += (dur + gap) * stretch
    return np.array(times)


def assert_onsets_line_up(al, take_onsets, ref_onsets, tolerance=0.1):
    np.testing.assert_allclose(al.ref_time(take_onsets), ref_onsets, atol=tolerance)


def test_align_finds_offset_and_octave():
    ref = track_of(MELODY, lead_in=5.0, tail=6.0)
    take = track_of(MELODY, lead_in=1.2, transpose=-12, cents=-30, jitter=0.1)
    al = analysis.align(take, ref)
    assert_onsets_line_up(al, onsets(MELODY, 1.2), onsets(MELODY, 5.0))
    assert al.octave == 12


def test_placement_is_where_the_take_starts_on_the_reference():
    ref = track_of(MELODY, lead_in=5.0, tail=6.0)
    take = track_of(MELODY, lead_in=1.2, transpose=-12, jitter=0.1)
    assert abs(analysis.placement(take, analysis.align(take, ref)) - (5.0 - 1.2)) < 0.05


def test_align_follows_a_slower_take():
    ref = track_of(MELODY, lead_in=4.0, tail=6.0)
    slow = [(m, d * 1.15) for m, d in MELODY]
    take = track_of(slow, lead_in=0.8, gap=0.15 * 1.15, transpose=-12)
    al = analysis.align(take, ref)
    assert_onsets_line_up(al, onsets(MELODY, 0.8, stretch=1.15), onsets(MELODY, 4.0))
    assert abs(analysis.tempo(take, al) - 1 / 1.15) < 0.03


def test_align_ignores_quarter_tone_offset_and_reference_octave_slips():
    ref_melody = [(m + 12 if i % 3 == 0 else m, d) for i, (m, d) in enumerate(MELODY)]
    ref = track_of(ref_melody, lead_in=3.0)
    take = track_of(MELODY, lead_in=0.5, transpose=-12, cents=48)
    al = analysis.align(take, ref)
    assert_onsets_line_up(al, onsets(MELODY, 0.5), onsets(MELODY, 3.0))


def test_agreement_measures_cents_and_notes():
    ref = track_of(MELODY, lead_in=2.0)
    same = analysis.agreement(track_of(MELODY, lead_in=2.0), ref, analysis.Alignment.shifted(0.0))
    assert same.median_abs < 1 and same.within_25 == 1.0 and same.notes_matched == same.notes_compared == len(MELODY)

    flat = analysis.agreement(track_of(MELODY, lead_in=2.0, cents=-40), ref, analysis.Alignment.shifted(0.0))
    assert flat.within_25 == 0.0 and flat.within_50 == 1.0
    assert abs(flat.bias + 40) < 1

    wrong = [(m + 1 if i % 2 else m, d) for i, (m, d) in enumerate(MELODY)]
    half = analysis.agreement(track_of(wrong, lead_in=2.0), ref, analysis.Alignment.shifted(0.0))
    assert half.notes_matched == len(MELODY) // 2


def test_summarize_reports_offset_and_key():
    s = analysis.summarize(track_of(MELODY, cents=30))
    assert abs(s.tuning_cents - 30) < 1 and s.tuning_consistency > 0.99
    assert s.in_tune == 0.0
    assert s.key[:2] in {(6, "major"), (3, "minor")}   # F# G# A# B C# F: F# major / D# minor
    assert set(s.notes_used) == {6, 8, 10, 11, 1, 5}


def test_notes_are_segmented():
    found = analysis.notes(track_of(MELODY))
    assert [round(n.midi) for n in found] == [m for m, _ in MELODY]
    assert abs((found[1].end - found[1].start) - 1.2) < 2 * HOP_S


@pytest.mark.parametrize("text, seconds", [("206", 206), ("3:26", 206), ("3:26.5", 206.5), ("1:02:03", 3723)])
def test_parse_time(text, seconds):
    assert parse_time(text) == seconds


@pytest.mark.parametrize("text", ["", "abc", "3:75", "-4", "1:2:3:4"])
def test_parse_time_rejects(text):
    with pytest.raises(argparse.ArgumentTypeError):
        parse_time(text)


def test_time_formatting():
    assert fmt_time(206.47, 2) == "3:26.47"
    assert fmt_time(65, 0) == "1:05"
    assert file_stamp(206) == "3m26s" and file_stamp(206.5) == "3m26.5s"


def test_level_and_edge_tell_belted_from_soft():
    from scipy.signal import butter, sosfilt
    from sound import fx
    from test_autotune import SR, held, voice
    belted = fx.saturate(np.stack([voice(held(57, 1.0))] * 2, axis=1), SR, 0.9).mean(axis=1)
    soft = sosfilt(butter(2, 700, fs=SR, output="sos"), voice(held(57, 1.0)))
    track = analysis.PitchTrack(np.arange(0, 1.0, HOP_S), np.full(len(np.arange(0, 1.0, HOP_S)), 220.0),
                                np.ones(len(np.arange(0, 1.0, HOP_S)), bool))
    lb, eb = analysis.level_and_edge(belted, SR, track)
    ls, es = analysis.level_and_edge(soft, SR, track)
    assert np.nanmedian(eb) > np.nanmedian(es) + 4
    assert abs(np.median(lb[5:-5])) < 2 and lb.min() >= -60
