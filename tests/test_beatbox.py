import re
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from sound import beatbox, midifile
from sound.server import create_app

SR = 44100
BPM = 116.0


def _kick(rng):
    t = np.arange(int(0.18 * SR)) / SR
    return np.sin(2 * np.pi * (55 + 90 * np.exp(-t * 40)) * t) * np.exp(-t * 18) * 0.9


def _noise(rng, lo, hi, length, level):
    n = rng.standard_normal(int(length * SR))
    spec = np.fft.rfft(n); f = np.fft.rfftfreq(len(n), 1 / SR)
    spec[(f < lo) | (f > hi)] = 0
    out = np.fft.irfft(spec, len(n))
    return out / np.abs(out).max() * level * np.exp(-np.arange(len(n)) / SR * 30)


def beatbox_take(path: Path, bars: int = 4, lead_in: float = 1.0, jitter: float = 0.008) -> Path:
    """A beatboxer at 116 BPM: a boom on every beat, a "t" on every off-beat, a "k" double before beat 4,
    a little human timing wobble, after a second of nothing."""
    rng = np.random.default_rng(3)
    beat = 60 / BPM
    y = np.zeros(int((lead_in + bars * 4 * beat + 1) * SR))
    def put(sound, t):
        a = int((t + rng.uniform(-jitter, jitter)) * SR); y[a:a + len(sound)] += sound[:len(y) - a]
    for b in range(bars * 4):
        t0 = lead_in + b * beat
        put(_kick(rng), t0)
        put(_noise(rng, 5000, 12000, 0.05, 0.45), t0 + beat / 2)                 # "t" on the off-beat
        if b % 4 == 2:
            for k in (0.5, 0.75):                                                 # "k k" before beat 4
                put(_noise(rng, 900, 2500, 0.08, 0.6), t0 + k * beat)
    sf.write(path, y, SR)
    return path


RIFF = [46, 46, 46, 46, 47, 47, 44, 44, 46, 46, 46, 49, 44, 44, 46, 46]   # A# ... B, G#, C#: one bar, per 16th
PULSE = (2, 5, 8, 11, 14)        # where it pumps: dotted-8th "bwom"s, 3-3-3-3-4, like a real take


def _hum(freqs: np.ndarray) -> np.ndarray:
    """A buzzed/hummed tone following a pitch per sample: a band-limited saw."""
    phase = 2 * np.pi * np.cumsum(freqs) / SR
    return sum(np.sin(k * phase) / k for k in range(1, 16))


def oscillator_take(path: Path, bars: int = 4, lead_in: float = 1.0) -> Path:
    """A beatboxer at 116 BPM buzzing a riff (an "oscillator") that pumps on each bwom, a boom on every beat."""
    rng = np.random.default_rng(5)
    beat = 60 / BPM; sixteenth = beat / 4
    y = np.zeros(int((lead_in + bars * 4 * beat + 1) * SR))
    n = int(bars * 16 * sixteenth * SR)
    t = np.arange(n) / SR
    step = (t // sixteenth).astype(int) % 16
    freqs = 440 * 2 ** ((np.array(RIFF)[step] - 69) / 12)
    onsets = np.array([b * 16 + k for b in range(-1, bars) for k in PULSE]) * sixteenth
    pump = 0.35 + 0.65 * np.exp(-(t - onsets[np.searchsorted(onsets, t, side="right") - 1]) * 12)
    a = int(lead_in * SR)
    y[a:a + n] += 0.12 * _hum(freqs) * pump
    for b in range(bars * 4):
        k = _kick(rng); s0 = int((lead_in + b * beat) * SR); y[s0:s0 + len(k)] += k
    sf.write(path, y, SR)
    return path


def test_groove_finds_tempo_bars_and_sounds(tmp_path):
    g = beatbox.groove(beatbox_take(tmp_path / "bb.wav"))
    assert abs(g.bpm - BPM) < 2.0
    assert abs(g.start - 1.0) < 0.03                       # bar 1 starts on the first boom
    assert len(g.bars) >= 3
    for bar in g.bars[:3]:
        assert bar["kick"] == "x...x...x...x..."
        hats = bar["hat"]
        assert sum(hats[i] == "x" for i in (2, 6, 10, 14)) >= 3   # the off-beat "t"s
    assert not g.snare_played                              # nothing on 2 and 4 but booms: the beat adds a clap


def test_strudel_code_plays_the_groove(tmp_path):
    g = beatbox.groove(beatbox_take(tmp_path / "bb.wav"))
    code = beatbox.strudel(g, "bb", {"kick": "/k.wav"})
    assert f"setcpm({g.bpm:g}/4)" in code and code.rstrip().endswith(")")
    assert "stack(" in code and "[bd " in code and " cp " in code   # kicks, and the clap backbeat
    assert "bb_kick: ['/k.wav']" in code                           # the performer's own sounds layered in
    assert code.count("(") == code.count(")")


def test_drums_only_beatbox_has_no_riff(tmp_path):
    g = beatbox.groove(beatbox_take(tmp_path / "bb.wav"))
    assert not g.oscillator                                # the boom's own ring isn't a riff
    song = midifile.read(beatbox.write_midi(g, tmp_path / "beat.mid"))
    notes = [n for n, _ in song.notes]
    assert {n.channel for n in notes} == {9} and any(n.pitch == 36 for n in notes)


def test_oscillator_riff_becomes_the_lead(tmp_path):
    g = beatbox.groove(oscillator_take(tmp_path / "osc.wav"))
    assert g.oscillator and g.root == 46 % 12 and abs(g.bpm - BPM) < 2.0
    bar = beatbox.loop(g)[0]
    assert sum(n is not None and n % 12 == r % 12 for n, r in zip(g.voice.notes[bar], RIFF)) >= 13
    assert all(h.kind in ("kick", "voice") for h in g.hits)  # its articulations aren't hats or snares
    for style, osc, kick in (("synth", 'sawtooth")', '.bank("RolandTR909")'), ("mouth", 'supersaw")', "sbd ~")):
        code = beatbox.strudel(g, "osc", style=style)
        assert f's("{osc}' in code and kick in code                             # the riff on a bass; a 909 kick, or a tuned boom
        assert "a#2" in code and "b2" in code and "g#2" in code
        for line in code.splitlines():
            if line.lstrip().startswith(("s(\"<[bd", "stack(s(\"<[bd", "s(\"<[sbd", "note(\"<[a#2", "note(\"<[a#1")):
                assert ".room(" not in line                                              # kick, bass and sub stay dry
        assert code.count("(") == code.count(")")
    assert 'note("a#2")' in beatbox.strudel(g, "osc", style="mouth")             # a boom where the mouth's was


def test_synth_style_plays_the_riff_on_its_pulse(tmp_path):
    g = beatbox.groove(oscillator_take(tmp_path / "osc.wav"))
    sel = beatbox.loop(g)
    steps = beatbox.pulse(g, sel)
    assert steps == list(PULSE)
    evs = beatbox.riff_events(g, sel[0], steps)
    assert sum(e.length for e in evs) == beatbox.STEPS
    assert {e.step for e in evs if e.length > 1} <= set(steps) | {0}            # notes start on the bwoms
    assert [e.note % 12 for e in evs if e.step == 5][0] == RIFF[5] % 12          # at the pitch sung there
    code = beatbox.strudel(g, "osc")
    assert 'stack(s("<[bd ' in code and '.bank("RolandTR909")' in code and 's("<[sbd ' in code and "duckorbit(2)" in code and code.count("gain(.001).duckorbit(") >= 3   # a 909 kick with a tuned boom under it; the sub ducked from it, three buses from silent copies
    lead = next(line for line in code.splitlines() if 's("sawtooth")' in line)
    assert ".late(2/16)" in code and lead.startswith('  note("<[a#2@3 ')          # the riff starts on its first bwom
    body = code[code.index(lead):code.index("// a sub")]
    assert "perlin.range(" in body and ".lpenv(2.2)" in body and ".lpattack(.006)" in body and ".lpdecay(" in body and ".delay(" not in body   # plucked by its filter, dry
    for bass, sound in (("square", 's("square")'), ("fm", '.fm('), ("synth", 'gm_synth_bass_2'), ("bright", 'gm_synth_bass_1'), ("finger", 'gm_electric_bass_finger')):
        other = beatbox.strudel(g, "osc", bass=bass)
        assert sound in other and other.count("(") == other.count(")")
    assert set(beatbox.BASSES) == {"saw", "square", "fm", "synth", "bright", "finger"}
    hats_line = next(line for line in code.splitlines() if 's("hh*16")' in line)
    assert 'bank("RolandTR909").gain("[' in hats_line and f'.late("[0 {beatbox.SWING:g}]*8")' in hats_line    # 909 16ths, swung
    gains = re.search(r'gain\("\[(.*?)\]"\)', hats_line).group(1).split()
    assert gains.count("0.26") == 4 and gains.count("0.19") == 3                    # accented on the off-beats, then the bwoms (3 aren't off-beats)
    pulse_line = next(line for line in beatbox.strudel(g, "osc", hats="pulse").splitlines() if 's("hh*16")' in line)
    assert 'bank("RolandTR808")' in pulse_line and pulse_line.count("0.5") == 5     # the 808 tick, loud on the bwoms
    assert 's("[~ hh]*4")' in beatbox.strudel(g, "osc", hats="offbeat")
    assert "// , note(" in code and "[a#3,c#4,f4]" in code                       # the song's stabs, pad and hook: commented in the loop
    assert "duckorbit" not in beatbox.strudel(g, "osc", style="mouth")
    song = midifile.read(beatbox.write_midi(g, tmp_path / "beat.mid", repeats=2))
    assert {n.channel for n, _ in song.notes} == {0, 1, 9}
    downbeats = [n for n, _ in song.notes if n.channel == 0 and n.beat % 4 == 0 and n.beat > 0]
    assert not downbeats                                                        # no re-strike on the downbeats


def test_song_lays_the_beat_out_as_a_track(tmp_path):
    g = beatbox.groove(oscillator_take(tmp_path / "osc.wav"))
    for style, lead in (("synth", "riff"), ("mouth", "osc")):
        code = beatbox.song(g, "osc", style=style)
        assert code.count("(") == code.count(")")
        assert f"const {lead} = " in code and "const sub = " in code and "const riser = " in code
        form = code[code.index("arrange("):]
        assert sum(int(n) for n in re.findall(r"^\s*\[(\d+),", form, re.M)) == sum(n for n, _, _ in beatbox.SONG) * form.count("arrange(")
        assert f"{lead}Low" in form and f"{lead}Echo" in form and "pull(stack(" in form and "kickReturn" in form and "end(stack(" in form
        assert "rcrash" in form and "crash" in form and "preload" in form
        assert 's("cp' not in code and "cp*" not in code                                            # no claps anywhere
        assert 's("pink")' in code and 's("white")' not in code                   # the riser: a whoosh, not static
        stacks = re.findall(r"\[\d+, (.*?)\],?\s*//", form)
        if style == "synth":
            assert f"setcpm({beatbox.TEMPO:g}/4)" in code and f"(your take: {g.bpm:g})" in code   # the song at club tempo
            assert code.count("arrange(") == 2 and ").late(2/16)" in form and ".late(2/16)" not in code[:code.index("arrange(")]
            assert "stack(kickHole, hats, ohat)" in form and "stack(kickHole, hats, ohat, stabs)" in form       # the beat's frame...
            assert "stack(sub, riff)" in form and "stack(sub, riff, hookAnswer)" in form and "stack(sub, riffOpen, hook, padPeak)" in form   # ...and the riff's, shifted as a whole
            assert "hatsBreak" in form and "pull(stack(kick, hats))" in form and "end(stack(kick, subEnd))" in form   # drop-outs; the last hit lands together
            assert "stabsOpen, ride)" in form and "shaker" not in form               # the drops build up, piece by piece (the shaker is defined, not used)
            assert "const stabs = note(" in code and "[a#3,c#4,f4]" in code and "[b3,d#4,f#4]" in code   # chords from the riff: i and bII
            assert "const pad = note(" in code and "const hook = note(" in code and "a#4@" in code       # the pad, the hook two octaves up
            blocks = re.findall(r"^const (\w+) = (.*?)(?=^const |^//|^\n)", code, re.M | re.S)   # each const with its continuation lines
            rooms = {name for name, body in blocks if ".room(" in body}
            assert {"stabs", "pad", "hook", "ohat", "ride", "rcrash"} <= rooms and not {"kick", "sub", "riff", "kickReturn", "hats"} & rooms   # rooms on the top, not the low end or the 16ths
            assert ".orbit(6)" in code and ".orbit(4)" in code and ".orbit(5)" in code and "speed(0.95)" in code and "duckorbit(1)" not in code   # orbit 1 is the default bus: the kick
            first = next(x for x in stacks if "hats" in x)
            assert all("hats" not in x and "shaker" not in x and "ride" not in x for x in stacks[:stacks.index(first)])   # nothing ticking before the drop
        else:
            assert f"setcpm({g.bpm:g}/4)" in code and "stack(kickHole, sub, osc, ohat)" in form
        assert 'mask("<0 0 0 0 0 0 0 0 1' not in code and "sd" not in code and "lt mt" not in code   # no crash hits, snare or tom fills: not this track's vibe
        builds = [x for x in stacks if "Low" in x or "Rise" in x or "Echo2" in x]
        assert builds and not any("hats" in x or "roll" in x for x in builds)     # nothing ticking before a drop
    total = sum(n for n, _, _ in beatbox.SONG)
    assert total % 8 == 0 and abs(total * 4 * 60 / beatbox.TEMPO - 210) < 15         # 8-bar phrases, about 3½ minutes
    drums = beatbox.groove(beatbox_take(tmp_path / "bb.wav"))
    code = beatbox.song(drums, "bb")
    stacks = re.findall(r"\[\d+, (.*?)\],?\s*//", code[code.index("arrange("):])       # what plays, without the comments
    assert stacks and not any("riff" in x or "silence" in x for x in stacks) and any("yourHats" in x for x in stacks)


def test_harmony_follows_the_bass():
    root = 10                                                                      # A#: the riff's key
    assert beatbox.chord(46, root) == (58, 61, 65)                                 # A#2 -> A#m, voiced a#3 c#4 f4
    assert beatbox.chord(47, root) == (59, 63, 66) and beatbox.chord(44, root) == (59, 63, 68)   # B -> B major (bII), G# -> G#m (bVII)
    assert beatbox.chord(45, root) is None                                         # A natural: a passing note, no chord
    E = beatbox.Event
    bar = [E(0, 3, 46, 1, 0, 0), E(3, 3, 46, 1, 0, 0), E(6, 3, 47, 1, 0, 0), E(9, 2, 44, 1, 0, 0), E(11, 1, 45, .8, 0, 0), E(12, 4, 46, 1, 0, 0)]
    assert beatbox.harmony([bar], root) == [[((58, 61, 65), 6), ((59, 63, 66), 3), ((59, 63, 68), 3), ((58, 61, 65), 4)]]   # merged; the pickup keeps G#m
    assert beatbox.step_chords(bar, root)[11] == (59, 63, 68) and len(beatbox.step_chords(bar, root)) == 16


def test_rotate_keeps_a_note_ringing_over_the_bar_line():
    E = beatbox.Event
    bars = [[E(0, 2, 46, .5, 0, 0), E(2, 6, 46, 1, 0, 0), E(8, 8, 47, 1, 0, 0)],
            [E(0, 2, 47, .5, 0, 0), E(2, 14, 44, 1, 0, 0)]]
    rot = beatbox._rotate(bars, 2)
    assert [(e.step, e.length, e.note) for e in rot[0]] == [(0, 6, 46), (6, 10, 47)]   # B rings on into bar 2
    assert [(e.step, e.length, e.note) for e in rot[1]] == [(0, 14, 44), (14, 2, 46)]  # A# starts bar 1 again
    assert all(sum(e.length for e in bar) == beatbox.STEPS for bar in rot)


def test_kit_cuts_one_sound_of_each_kind(tmp_path):
    take = beatbox_take(tmp_path / "bb.wav")
    g = beatbox.groove(take)
    files = beatbox.kit(g, take, tmp_path / "kit")
    assert "kick" in files and files["kick"].exists()
    audio, sr = sf.read(files["kick"])
    assert sr == SR and 0.2 < len(audio) / sr <= 0.26


def test_no_beat_is_an_error(tmp_path):
    sf.write(tmp_path / "quiet.wav", np.zeros(SR * 2), SR)
    with pytest.raises(ValueError):
        beatbox.groove(tmp_path / "quiet.wav")


def test_beat_endpoints(tmp_path):
    (tmp_path / "beatbox").mkdir()
    beatbox_take(tmp_path / "beatbox" / "bb.wav")
    client = TestClient(create_app(tmp_path, web_dir=None))
    files = {f["path"]: f for f in client.get("/api/files").json()["files"]}
    assert files["beatbox/bb.wav"]["kind"] == "beatbox"
    beat = client.get("/api/beat", params={"path": "beatbox/bb.wav", "bars": 2}).json()
    assert abs(beat["bpm"] - BPM) < 2.0 and len(beat["loop"]) == 2 and "stack(" in beat["code"]
    assert "/api/beat/kit?path=beatbox/bb.wav&kind=kick" in beat["code"]
    kick = client.get("/api/beat/kit", params={"path": "beatbox/bb.wav", "kind": "kick"})
    assert kick.status_code == 200 and kick.headers["content-type"] == "audio/wav"
    assert client.get("/api/beat", params={"path": "beatbox/bb.wav", "style": "loud"}).status_code == 422
    assert client.get("/api/beat", params={"path": "beatbox/bb.wav", "form": "album"}).status_code == 422
    assert client.get("/api/beat", params={"path": "beatbox/bb.wav", "bass": "tuba"}).status_code == 422
    assert client.get("/api/beat", params={"path": "beatbox/bb.wav", "hats": "ride"}).status_code == 422
    track = client.get("/api/beat", params={"path": "beatbox/bb.wav", "form": "song"}).json()
    assert track["form"] == "song" and "arrange(" in track["code"] and track["song_bpm"] == track["bpm"]   # drums only: the take's tempo
    midi = client.get("/api/beat/midi", params={"path": "beatbox/bb.wav", "style": "mouth"})
    assert midi.status_code == 200 and midi.content[:4] == b"MThd"
