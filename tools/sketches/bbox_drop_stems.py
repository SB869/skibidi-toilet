"""bbox drop: stems, the final mix and MIDI from the Strudel sketch, sample-aligned with each other and with the mix.

    uv run python tools/sketches/bbox_drop_stems.py [all | code | render | trim | verify | midi] [--only mix drums ...]

  code    the song's code from the app (GET /api/beat?path=beatbox/bbox.mp3&form=song: the kit URL included, so the user's
          own boom plays) -> bbox_drop_sketch.py --align: the mix and one variant per stem (--only drums|bass|chords|theme),
          the code as rendered in OUT/code/; the playable code (no pre-roll) as recordings/tracks/bbox drop.strudel.js
  render  each variant through tools/render_strudel.py (system python3 + Chromium, real time, --fake-output so Ardour's
          exports can't punch holes in it), one at a time, untrimmed, into sessions/bbox-drop/work/raw/, each checked for
          skipped render quanta (the mix and drums against their own kick grid, every stem bar by bar against the mix minus
          the stems already clean) and rendered again if it has one (log: sessions/bbox-drop/work/renders.json)
  trim    every render cut at its own blip + exactly one bar (each run starts with a different latency: cutting at each
          stem's first sound would misalign them), 44 bars + TAIL s -> OUT/{drums,bass,chords,theme}.wav and
          sessions/bbox-drop/work/mix.wav
  verify  sum of the stems vs the mix per bar, the lag of each stem in the mix (waveform cross-correlation, whole song and
          bar by bar) and of its onsets against its part in the mix (onset envelopes), peaks, loudness, the mix vs the sketch -> sessions/bbox-drop/work/verify.json; the mix replaces the sketch as
          recordings/tracks/bbox drop.wav only if it passes (peak <= -1 dBFS, no full-scale samples); the sketch's wav/js
          then move to recordings/tracks/versions/bbox drop sketch 2026-10-29/
  midi    the parts as the sketch plays them (its events queried with tools/strudel_events.mjs) -> OUT/theme.mid,
          bass.mid, chords.mid, drums.mid at 124 BPM (sound.midifile)
"""
from __future__ import annotations

import argparse, json, shutil, subprocess, sys, time, urllib.request
from collections import defaultdict
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from sound import midifile  # noqa: E402

OUT = ROOT / "recordings/tracks/stems/bbox drop"
CODE = OUT / "code"
WORK = ROOT / "sessions/bbox-drop/work"
RAW = WORK / "raw"
TRACKS = ROOT / "recordings/tracks"
FINAL = TRACKS / "bbox drop.wav"
SKETCH = [TRACKS / "bbox drop (sketch).wav", TRACKS / "bbox drop (sketch).strudel.js"]
RETIRED = TRACKS / "versions/bbox drop sketch 2026-10-29"
API = "http://127.0.0.1:8765/api/beat?path=beatbox/bbox.mp3&form=song"
GEN = ["python3", str(ROOT / "tools/sketches/bbox_drop_sketch.py")]
HARNESS_PY = "/usr/bin/python3"       # the render harness needs the system python (Playwright), not the uv env's
MIX_ARGS = ["--piano", "1.3", "--saw", ".17", "--post", ".78"]   # the sketch's levels
STEMS = ("drums", "bass", "chords", "theme")
VARIANTS = ("mix",) + STEMS
BPM, BARS, TAIL, SR = 124, 44, 2.5, 48000
BAR = 4 * 60 / BPM                    # seconds
PRE = 3                               # bars of pre-roll in the --align code (the blip on the last one's downbeat)
SECONDS = (PRE + BARS) * BAR + TAIL + 3.0   # what to record: pre-roll, song, tail, and the start-up latency with margin
LENGTH = round((BARS * BAR + TAIL) * SR)     # samples in every output file
BLIP_THRESHOLD = 0.02
KICK_ONSET = 13           # where the 909's attack crosses 0.05 after the scheduled downbeat (a clean render)
BLIP_FLOOR = 0.0005      # -66 dBFS: above the pre-roll, below the blip's first samples


def raw(v): return RAW / f"{v}.wav"
def code(v): return CODE / f"{v}.strudel.js"
def stem(v): return WORK / "mix.wav" if v == "mix" else OUT / f"{v}.wav"


# ---------------------------------------------------------------- code

def make_code():
    CODE.mkdir(parents=True, exist_ok=True)
    song = CODE / "song (from the app).js"
    song.write_text(json.loads(urllib.request.urlopen(API, timeout=60).read())["code"])
    for v in VARIANTS:
        only = [] if v == "mix" else ["--only", v]
        subprocess.run(GEN + [str(song), str(code(v))] + MIX_ARGS + only + ["--align"], check=True, capture_output=True)
        print(f"code: {code(v).relative_to(ROOT)}")
    playable = TRACKS / "bbox drop.strudel.js"
    subprocess.run(GEN + [str(song), str(playable)] + MIX_ARGS, check=True, capture_output=True)
    print(f"code: {playable.relative_to(ROOT)} (the mix as the app plays it, no pre-roll)")


# ---------------------------------------------------------------- render, checked

def load(path):
    x, sr = sf.read(path, dtype="float64", always_2d=True)
    assert sr == SR, f"{path}: {sr} Hz, expected {SR}"
    return x


def find_blip(x):
    """The blip's first sample: the first thing louder than BLIP_THRESHOLD (the pre-roll is at gain .001, ~-84 dBFS just
    before it), then back through its 1 ms attack to where it leaves the floor (the threshold crossing alone sits ~8
    samples into the blip)."""
    m = np.abs(x).max(axis=1)
    i = int(np.argmax(m > BLIP_THRESHOLD))
    if m[i] <= BLIP_THRESHOLD:
        raise SystemExit("no blip found")
    j = i
    while j > i - 48 and m[j - 1] > BLIP_FLOOR:
        j -= 1
    i = j
    # it must stand alone: near-silence before it, and it has decayed before bar 1
    before = m[max(0, i - SR // 2):i - 48].max(initial=0)
    gap = m[i + int(0.1 * SR):i + int(0.9 * BAR * SR)].max(initial=0)
    return i, before, gap


def cut(path):
    """A render cut at its blip + exactly one bar, LENGTH samples."""
    x = load(path)
    b, before, gap = find_blip(x)
    start = b + round(BAR * SR)
    y = x[start:start + LENGTH]
    if len(y) < LENGTH:
        raise SystemExit(f"{path.name}: the render is short: {len(y)} of {LENGTH} samples after the blip - record longer")
    return y, {"blip_sample": b, "blip_s": round(b / SR, 4), "before_blip_dbfs": db(before), "before_bar1_dbfs": db(gap)}


_KICKS = None


def kick_beats():
    """Where the 909 kick plays (in samples from bar 1), from the drums variant's events."""
    global _KICKS
    if _KICKS is None:
        _KICKS = sorted({round((e["b"] - PRE) * BAR * SR) for e in events("drums") if e["v"].get("s") == "bd"})
    return _KICKS


def kick_offsets(y):
    """Each kick's position against where it belongs (samples), by matching the first kick's waveform around each one:
    a skipped render quantum shows as a step of -128 from there on. Also the first kick's onset (should be ~0)."""
    m = y.mean(axis=1)
    onset = int(np.argmax(np.abs(m[:4800]) > 0.05))
    tpl = m[0:2048] - m[0:2048].mean()
    out = []
    for e in kick_beats():
        seg = m[max(0, e - 3000):e + 3000 + len(tpl)]
        c = np.correlate(seg, tpl, mode="valid")
        out.append(int(np.argmax(c)) + max(0, e - 3000) - e)
    return onset, out


def envelope(x, n=48):
    a = np.abs(x)
    return np.convolve(a, np.ones(n) / n, mode="same")


def bar_lags(y, ref):
    """Per bar, the lag (samples) of a stem inside its reference (the mix, minus the stems already clean) and how it was
    found: "wave" = waveform cross-correlation, where the stem's content is deterministic (samples, oscillators: the
    peak is ~ the stem's own energy) - reliable to a sample; "env" = the amplitude envelopes', where it isn't (supersaws
    start at random phases) - only indicative; None where the stem is silent."""
    s1, m1 = y.mean(axis=1), ref.mean(axis=1)
    out = []
    for k in range(BARS):
        i0, i1 = round(k * BAR * SR), round((k + 1) * BAR * SR)
        a, b = m1[i0:i1], s1[i0:i1]
        energy = float(np.dot(b, b))
        if energy / len(b) < 10 ** (-60 / 10):
            out.append((None, None))
            continue
        lag = xcorr_lag(a, b, 3000)
        k0 = int(round(lag))
        peak = float(np.dot(a[max(0, k0):len(a) + min(0, k0)], b[max(0, -k0):len(b) - max(0, k0)]))
        if peak >= 0.5 * energy:
            out.append((round(float(lag), 1), "wave"))
        else:
            ea, eb = envelope(a), envelope(b)
            out.append((round(float(xcorr_lag(ea - ea.mean(), eb - eb.mean(), 3000)), 1), "env"))
    return out


def clean(lags, tol=24):
    """A skipped quantum only ever loses time, so it shows in every later bar: the reliable (waveform) bars decide, and
    the last of them must come after the stem's last envelope-only bar."""
    wave = [i for i, (l, m) in enumerate(lags) if m == "wave"]
    env = [i for i, (l, m) in enumerate(lags) if m == "env"]
    return bool(wave) and all(abs(lags[i][0]) <= tol for i in wave) and (not env or max(wave) > max(env))


def lag_summary(lags, tol=24):
    wave = [l for l, m in lags if m == "wave"]
    env = [l for l, m in lags if m == "env"]
    bad = sorted({l for l in wave if abs(l) > tol})
    return (f"{len(wave)} bars matched by waveform, max |lag| {max(map(abs, wave), default=0):.1f}"
            + (f", OFF: {bad}" if bad else "") + (f"; {len(env)} by envelope only (indicative)" if env else ""))


RENDER_ORDER = ("mix", "drums", "bass", "theme", "chords")   # chords last: checked against the mix minus the other three


def reference(v):
    """What a stem is checked against: the mix minus the other stems already rendered clean (for chords, the last one,
    that is the chords part itself, give or take the random phases of the theme's supersaw)."""
    ref, _ = cut(raw("mix"))
    log = json.loads((WORK / "renders.json").read_text()) if (WORK / "renders.json").exists() else {}
    for other in STEMS:
        if other != v and raw(other).exists() and log.get(other) and log[other][-1].get("clean"):
            ref = ref - cut(raw(other))[0]
    return ref


def render_one(v, out):
    cmd = [HARNESS_PY, str(ROOT / "tools/render_strudel.py"), str(out), "--seconds", f"{SECONDS:.1f}",
           "--code", str(code(v)), "--fake-output", "--latency", "playback"]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"render of {v} failed:\n{r.stdout}\n{r.stderr}")
    return r.stdout.strip().splitlines()[-1]


def render(variants, attempts=4):
    """One render at a time (several at once made Chromium skip render quanta), each checked; a render with a skip is
    done again. The mix is checked against its own kick grid, the drums too, every stem bar by bar against the mix."""
    RAW.mkdir(parents=True, exist_ok=True)
    urllib.request.urlopen("http://127.0.0.1:8765/api/files", timeout=10)   # the app must be up
    log = json.loads((WORK / "renders.json").read_text()) if (WORK / "renders.json").exists() else {}
    order = [v for v in RENDER_ORDER if v in variants]
    for v in order:
        for attempt in range(1, attempts + 1):
            t0 = time.time()
            tmp = RAW / f"{v}.try.wav"
            said = render_one(v, tmp)
            y, info = cut(tmp)
            info["attempt"] = attempt
            if v in ("mix", "drums"):
                onset, offs = kick_offsets(y)
                info["first_kick_onset"] = onset
                info["kick_offsets_nonzero"] = {i: o for i, o in enumerate(offs) if abs(o) > 2}
                ok = abs(onset - KICK_ONSET) <= 8 and not info["kick_offsets_nonzero"]
            else:
                ok = True
            if v != "mix":
                lags = bar_lags(y, reference(v))
                info["bar_lags_vs_mix"] = lags
                ok = ok and clean(lags)
            print(f"render: {v} (try {attempt}, {time.time() - t0:.0f} s): {said.split(': ', 1)[-1]}; "
                  f"{'clean' if ok else 'SKIPPED QUANTA'}: " +
                  (f"first kick {info['first_kick_onset']}, kicks off grid {info['kick_offsets_nonzero'] or 'none'}"
                   if "first_kick_onset" in info else "") +
                  (f" vs the mix: {lag_summary(info['bar_lags_vs_mix'])}" if "bar_lags_vs_mix" in info else ""), flush=True)
            info["clean"] = ok
            log.setdefault(v, []).append(info)
            (WORK / "renders.json").write_text(json.dumps(log, indent=1))
            if ok:
                tmp.replace(raw(v))
                break
            if attempt == attempts:
                tmp.replace(raw(v))
                print(f"render: {v}: no clean render in {attempts} tries - keeping the last; see renders.json")


def trim():
    found = {}
    for v in VARIANTS:
        y, info = cut(raw(v))
        out = stem(v)
        out.parent.mkdir(parents=True, exist_ok=True)
        sf.write(out, y, SR, subtype="PCM_16")
        found[v] = info
        print(f"trim: {v}: blip at {info['blip_s']:.4f} s (pre-roll peak {info['before_blip_dbfs']:.1f} dBFS, before bar 1 "
              f"{info['before_bar1_dbfs']:.1f}) -> {out.relative_to(ROOT)} ({len(y) / SR:.3f} s)")
    return found


# ---------------------------------------------------------------- verify

def db(a): return round(float(20 * np.log10(max(float(a), 1e-12))), 2)
def rms_db(x): return round(float(10 * np.log10(np.mean(np.square(x)) + 1e-20)), 2)


def bars_of(x):
    n = round(BAR * SR)
    return [x[round(k * BAR * SR):round(k * BAR * SR) + n] for k in range(BARS)]


def xcorr_lag(a, b, max_lag):
    """Lag (samples) of b against a that maximizes their cross-correlation, |lag| <= max_lag (b delayed = positive)."""
    n = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    c = np.fft.irfft(np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n)), n)
    lags = np.r_[np.arange(0, max_lag + 1), np.arange(-max_lag, 0)]
    vals = np.r_[c[:max_lag + 1], c[-max_lag:]]
    k = int(np.argmax(vals))
    # parabolic interpolation around the peak
    y0, y1, y2 = (vals[(k - 1) % len(vals)], vals[k], vals[(k + 1) % len(vals)])
    frac = 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2) if (y0 - 2 * y1 + y2) != 0 else 0.0
    return -(lags[k] + frac)   # c[k] = sum a[t+k] b[t]: a peak at k means b leads a by k


def onset_env(x, hop=12):
    """Onset strength at hop-sample resolution (0.25 ms): the rise of the log energy of the signal's high-passed
    first difference (attacks of every instrument, little of the sustained low end)."""
    m = x.mean(axis=1)
    d = np.diff(m, prepend=0.0)
    e = np.add.reduceat(d * d, np.arange(0, len(d), hop))
    le = np.log10(e + 1e-12)
    return np.maximum(0.0, np.diff(le, prepend=le[0]))


def loudness(x):
    try:
        import pyloudnorm
        return round(float(pyloudnorm.Meter(SR).integrated_loudness(x)), 2)
    except Exception:  # noqa: BLE001 - optional
        return None


def verify(found=None):
    found = found or {}
    x = {v: load(stem(v)) for v in VARIANTS}
    for v in VARIANTS:
        assert len(x[v]) == LENGTH, (v, len(x[v]))
    mix, total = x["mix"], sum(x[s] for s in STEMS)
    report = {"length_samples": LENGTH, "sample_rate": SR, "bars": BARS, "bpm": BPM, "renders": found, "files": {}}

    # levels
    for v in VARIANTS:
        a = x[v]
        report["files"][v] = {"peak_dbfs": db(np.abs(a).max()), "full_scale_samples": int((np.abs(a) >= 32767 / 32768).sum()),
                              "rms_dbfs": rms_db(a), "lufs": loudness(a),
                              "peak_L_dbfs": db(np.abs(a[:, 0]).max()), "peak_R_dbfs": db(np.abs(a[:, 1]).max())}
    report["files"]["sum of stems"] = {"peak_dbfs": db(np.abs(total).max()), "rms_dbfs": rms_db(total), "lufs": loudness(total)}

    # sum of stems vs mix, per bar
    per_bar = []
    for k, (m, s) in enumerate(zip(bars_of(mix), bars_of(total)), 1):
        per_bar.append({"bar": k, "mix_rms": rms_db(m), "sum_rms": rms_db(s), "diff_db": round(rms_db(s) - rms_db(m), 2),
                        "residual_vs_mix_db": round(rms_db(m - s) - rms_db(m), 2)})
    report["per_bar"] = per_bar
    diffs = [abs(b["diff_db"]) for b in per_bar]
    report["sum_vs_mix"] = {"max_abs_bar_diff_db": max(diffs), "mean_abs_bar_diff_db": round(float(np.mean(diffs)), 3),
                            "residual_vs_mix_db_whole": round(rms_db(mix - total) - rms_db(mix), 2)}

    # lag of each stem in the mix: waveform cross-correlation (the stem's deterministic parts - samples, oscillators - are
    # in the mix sample for sample), whole song and per section
    sections = {"1-8": (0, 8), "9-24": (8, 24), "25-40": (24, 40), "41-44": (40, 44)}
    lags = {}
    m1 = mix.mean(axis=1)
    for s in STEMS:
        s1 = x[s].mean(axis=1)
        row = {"whole": round(xcorr_lag(m1, s1, 480), 2)}
        for name, (b0, b1) in sections.items():
            i0, i1 = round(b0 * BAR * SR), round(b1 * BAR * SR)
            if np.abs(s1[i0:i1]).max() > 1e-3:
                row[name] = round(xcorr_lag(m1[i0:i1], s1[i0:i1], 480), 2)
        lags[s] = row
    report["lag_in_mix_samples"] = lags
    report["bar_lags_in_mix_samples"] = {}
    for s_ in STEMS:   # against the mix minus the other three stems
        report["bar_lags_in_mix_samples"][s_] = bar_lags(x[s_], mix - sum(x[o] for o in STEMS if o != s_))

    # onsets: each stem's onset envelope (0.25 ms hops) against its own part in the mix (the mix minus the other three
    # stems), within +-10 ms; between two stems, the difference of their lags. (Cross-correlating two *different* stems'
    # onsets directly isn't an alignment measure where they share no onsets - swung hats vs piano attacks, off-beat
    # stabs - it lands on the musical offsets between them: kept in the JSON as "onset_xcorr_between_stems_raw".)
    onset = {}
    for s_ in STEMS:
        own = mix - sum(x[o] for o in STEMS if o != s_)
        e1, e2 = onset_env(own), onset_env(x[s_])
        onset[s_] = round(xcorr_lag(e1 - e1.mean(), e2 - e2.mean(), 40) * 12 / SR * 1000, 3)   # hops -> ms
    report["onset_lag_in_mix_ms"] = onset
    report["onset_lag_between_stems_ms"] = {f"{p}~{q}": round(onset[q] - onset[p], 3)
                                            for i, p in enumerate(STEMS) for q in STEMS[i + 1:]}
    env = {s_: onset_env(x[s_]) for s_ in STEMS}
    report["onset_xcorr_between_stems_raw_ms"] = {
        f"{p}~{q}": round(xcorr_lag(env[p] - env[p].mean(), env[q] - env[q].mean(), 40) * 12 / SR * 1000, 3)
        for i, p in enumerate(STEMS) for q in STEMS[i + 1:]}
    # the new mix against the sketch it replaces, bar by bar
    sketch = next((f for f in (SKETCH[0], RETIRED / SKETCH[0].name) if f.exists()), None)
    if sketch is not None:
        old = load(sketch)[:LENGTH]
        diffs = [round(rms_db(n) - rms_db(o), 2) for n, o in zip(bars_of(mix), bars_of(old))]
        report["vs_sketch"] = {"file": str(sketch.relative_to(ROOT)), "bar_rms_diff_db": diffs,
                               "max_abs_bar_diff_db_1_43": max(abs(d) for d in diffs[:43]),
                               "sketch_peak_dbfs": db(np.abs(old).max()), "sketch_lufs": loudness(old),
                               "tail_rms_dbfs": rms_db(mix[round(BARS * BAR * SR):]),
                               "sketch_tail_rms_dbfs": rms_db(old[round(BARS * BAR * SR):])}
    (WORK / "verify.json").write_text(json.dumps(report, indent=1))

    # print
    print("\nfile           peak dBFS   L / R          RMS dBFS   LUFS    full-scale")
    for v, f in report["files"].items():
        lr = f"{f.get('peak_L_dbfs', '')!s:>6} / {f.get('peak_R_dbfs', '')!s:<6}" if "peak_L_dbfs" in f else " " * 15
        print(f"{v:14} {f['peak_dbfs']:>8}   {lr} {f['rms_dbfs']:>8}   {f['lufs']!s:>6}  {f.get('full_scale_samples', '')}")
    print("\nsum of stems vs mix, RMS per bar (dB):", " ".join(f"{b['diff_db']:+.2f}" for b in per_bar))
    print("  max |diff| %.2f dB, mean %.3f dB; residual (mix - sum) vs mix: %.1f dB whole song" %
          (report["sum_vs_mix"]["max_abs_bar_diff_db"], report["sum_vs_mix"]["mean_abs_bar_diff_db"],
           report["sum_vs_mix"]["residual_vs_mix_db_whole"]))
    print("  residual per bar:", " ".join(f"{b['residual_vs_mix_db']:.0f}" for b in per_bar))
    print("\nlag of each stem in the mix (samples, waveform xcorr, whole song):",
          "  ".join(f"{s_} {row['whole']:+.2f}" for s_, row in lags.items()))
    print("lag of each stem in the mix minus the other stems, bar by bar (samples):")
    for s_, row in report["bar_lags_in_mix_samples"].items():
        print(f"  {s_:7} {'clean' if clean(row) else 'NOT CLEAN'}: {lag_summary(row)}")
    print("onset lag of each stem against its part in the mix (ms):", "  ".join(f"{k} {v:+.3f}" for k, v in onset.items()))
    print("  between stems (ms):", "  ".join(f"{k} {v:+.3f}" for k, v in report["onset_lag_between_stems_ms"].items()))
    if "vs_sketch" in report:
        v = report["vs_sketch"]
        print(f"\nnew mix vs the sketch ({v['file']}): bars 1-43 max |RMS diff| {v['max_abs_bar_diff_db_1_43']:.2f} dB; "
              f"bar 44 {v['bar_rms_diff_db'][43]:+.2f} dB; the {TAIL} s tail {v['tail_rms_dbfs']} dBFS RMS (the sketch's: "
              f"{v['sketch_tail_rms_dbfs']} - the intro looping back in); "
              f"sketch peak {v['sketch_peak_dbfs']} dBFS, {v['sketch_lufs']} LUFS")
    return report


def publish(report):
    f = report["files"]["mix"]
    ok = f["peak_dbfs"] <= -1.0 and f["full_scale_samples"] == 0
    if not ok:
        print(f"\nthe mix did NOT pass (peak {f['peak_dbfs']} dBFS, {f['full_scale_samples']} full-scale samples): "
              f"left at {stem('mix').relative_to(ROOT)}, the sketch stays")
        return False
    shutil.copyfile(stem("mix"), FINAL)
    print(f"\nthe mix passes (peak {f['peak_dbfs']} dBFS, no full-scale samples) -> {FINAL.relative_to(ROOT)}")
    for p in SKETCH:
        if p.exists():
            RETIRED.mkdir(parents=True, exist_ok=True)
            shutil.move(p, RETIRED / p.name)
            print(f"  retired {p.name} -> {RETIRED.relative_to(ROOT)}/")
    return True


# ---------------------------------------------------------------- midi

PITCH = {n: i for i, n in enumerate(["c", "c#", "d", "d#", "e", "f", "f#", "g", "g#", "a", "a#", "b"])}
FLAT = {"db": "c#", "eb": "d#", "gb": "f#", "ab": "g#", "bb": "a#"}
RESET = [(0.0, ch, cc, 0) for ch in range(16) for cc in (64, 123, 120)]   # as the NM scripts: sustain, notes, sound off
GM = {"bd": 36, "hh": 42, "oh": 46, "rd": 51, "cr": 49}                  # kick, closed hat, open hat, ride, crash


def midi_number(n) -> int:
    if isinstance(n, (int, float)):
        return int(round(n))
    n = str(n).lower()
    i = 2 if len(n) > 1 and n[1] in "#b" else 1
    name, octave = n[:i], int(n[i:])
    name = FLAT.get(name, name)
    return (octave + 1) * 12 + PITCH[name]


def events(v):
    out = subprocess.run(["node", str(ROOT / "tools/strudel_events.mjs"), str(code(v)), str(PRE), str(PRE + BARS)],
                         check=True, capture_output=True, text=True).stdout
    evs = json.loads(out)["events"]
    return [e for e in evs if e["v"].get("gain", 1) > 0.0011]   # leave out the silent ducking kicks and the preload


def untangle(notes):
    """No same-pitch overlaps on one channel (ambiguous note-on pairs: stuck notes in Ardour)."""
    by = defaultdict(list)
    for n in notes:
        by[(n.channel, n.pitch)].append(n)
    out = []
    for ns in by.values():
        ns.sort(key=lambda n: n.beat)
        for a, b in zip(ns, ns[1:] + [None]):
            length = a.length if b is None else min(a.length, b.beat - a.beat)
            out.append(midifile.Note(a.beat, max(length, 1 / 480), a.pitch, a.velocity, a.channel))
    return sorted(out, key=lambda n: (n.beat, n.channel, n.pitch))


def beat(e): return round((e["b"] - PRE) * 4, 6)
def length(e): return round((e["e"] - e["b"]) * 4, 6)
def vel(x): return max(1, min(127, int(round(x))))


def make_midi():
    parts = {}
    # theme: the piano line as played (question/answer, the teaser, riff A, riff B, question/answer); the supersaw doubles
    # these notes (and in drop B a quiet octave above) - not written again
    parts["theme"] = [midifile.Note(beat(e), length(e), midi_number(e["v"]["note"]), vel(100 * e["v"].get("velocity", 1)), 0)
                      for e in events("theme") if e["v"].get("s") == "piano"]
    bass = []
    for e in events("bass"):
        s, orbit = e["v"].get("s"), e["v"].get("orbit")
        if s == "sawtooth":          # the pluck (bassNM), channel 1
            bass.append(midifile.Note(beat(e), length(e), midi_number(e["v"]["note"]), 100, 0))
        elif s == "sine" and orbit == 2:   # the sub an octave under it (subNM), channel 2
            bass.append(midifile.Note(beat(e), length(e), midi_number(e["v"]["note"]), 90, 1))
    parts["bass"] = bass
    chords = []
    for e in events("chords"):
        s = e["v"].get("s")
        if s == "supersaw":          # the off-beat stabs, channel 1
            chords.append(midifile.Note(beat(e), length(e), midi_number(e["v"]["note"]), vel(100 * e["v"].get("velocity", 1)), 0))
        elif s == "sawtooth":        # the pad (held chords), channel 2
            chords.append(midifile.Note(beat(e), length(e), midi_number(e["v"]["note"]), vel(70 * e["v"].get("gain", .07) / .07), 1))
    parts["chords"] = chords
    drums, skipped = [], defaultdict(int)
    for e in events("drums"):
        s = e["v"].get("s")
        if s not in GM:              # the boom (sbd) and your bb_kick are the kick's layers; the riser is noise
            skipped[s] += 1
            continue
        g = e["v"].get("gain", 1) * e["v"].get("velocity", 1)
        v = {"bd": 110, "hh": 127 * min(1.0, g / 0.26), "oh": 90, "rd": 85, "cr": 100}[s]
        n = length(e)
        if s == "cr":                # the reversed crash: from its start to the downbeat it swells into
            n = round(4 * (np.floor(e["b"]) + 1 - e["b"]), 6)
        drums.append(midifile.Note(beat(e), n, GM[s], vel(max(v, 24)), 9))
    parts["drums"] = drums
    OUT.mkdir(parents=True, exist_ok=True)
    for name, notes in parts.items():
        notes = untangle(notes)
        path = midifile.write(OUT / f"{name}.mid", notes, BPM, name=name, controls=RESET)
        chans = sorted({n.channel + 1 for n in notes})
        span = (min(n.beat for n in notes) / 4 + 1, max(n.beat + n.length for n in notes) / 4 + 1)
        print(f"midi: {path.relative_to(ROOT)}: {len(notes)} notes, channels {chans}, "
              f"pitches {min(n.pitch for n in notes)}-{max(n.pitch for n in notes)}, bars {span[0]:.2f}-{span[1]:.2f}")
    print(f"  (drums: not written: {dict(skipped)} - the boom layers sound with each kick; the riser is pink noise)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("step", nargs="?", default="all", choices=["all", "code", "render", "trim", "verify", "midi"])
    ap.add_argument("--attempts", type=int, default=4, help="renders per variant until one has no skipped quanta")
    ap.add_argument("--only", nargs="+", choices=VARIANTS, help="render just these")
    a = ap.parse_args()
    if a.step in ("all", "code"):
        make_code()
    if a.step in ("all", "render"):
        render(a.only or VARIANTS, a.attempts)
    found = None
    if a.step in ("all", "trim"):
        found = trim()
    if a.step in ("all", "trim", "verify"):
        if found is None and (WORK / "verify.json").exists():
            found = json.loads((WORK / "verify.json").read_text()).get("renders")
        publish(verify(found))
    if a.step in ("all", "midi"):
        make_midi()


if __name__ == "__main__":
    main()
