"""Record what Strudel actually plays, in stereo: open the Beat tab in headless Chromium, tap every node that
feeds the audio output into a ScriptProcessor (raw float samples, both channels - not the MediaRecorder, whose
Opus decode overshoots), press Play, record, write a 16-bit stereo WAV.

Runs in the system Python (Playwright + Chromium), not the uv env; the server must be up (uv run sound serve).

usage: python3 tools/render_strudel.py OUT.wav --seconds 220 [--code song.js] [--bars 112 --bpm 124] [--take bbox]
                                       [--fake-output [--rate 48000]]
  --code   replace the editor's code first (e.g. the song form fetched from /api/beat?...&form=song, which has the kit URL)
  --bars   trim from the first sound to exactly this many bars at --bpm (plus --tail seconds), as the Tracks renders are
  --fake-output  Chromium's timer-paced fake audio output instead of the sound card (--disable-audio-output): immune to
           PipeWire freewheeling while Ardour exports (which leaves holes or a short file); its device runs at 44.1 kHz,
           so the page's AudioContext is pinned to --rate (default 48000 with --fake-output) - what is recorded is the
           context's own samples at that rate
  --latency playback   the context's latencyHint. When an audio callback comes late (CPU contention: e.g. several renders
           at once), Chromium skips whole 128-frame render quanta - the recording loses them while every later event keeps
           its time, so the music after a skip sits 2.7 ms early in the file (measured 2026-10-29: 5 renders at once lost
           1-20 quanta each over 85 s). Render one at a time and check (tools/sketches/bbox_drop_stems.py does both)

Strudel can't render faster than real time. superdough builds each bus's reverb impulse in an OfflineAudioContext:
the tap skips offline contexts, otherwise it records that IR render as "the output" (the 2026-10-26 "room is inert" artefact)."""
import argparse, array, base64, json, time, wave
from pathlib import Path
from playwright.sync_api import sync_playwright

TAP = """
(() => {
  const orig = AudioNode.prototype.connect;
  const tapped = new WeakSet();
  window.__ctxs = []; window.__recOn = false;          // one recording per AudioContext: the page runs more than one
  AudioNode.prototype.connect = function (dest, ...rest) {
    const r = orig.call(this, dest, ...rest);
    if (dest instanceof AudioDestinationNode && !(dest.context instanceof OfflineAudioContext) && !tapped.has(this)) {
      tapped.add(this);
      const ctx = dest.context;
      if (!ctx.__tap) {
        const rec = { L: [], R: [], sr: ctx.sampleRate }; window.__ctxs.push(rec);
        ctx.__tap = ctx.createScriptProcessor(16384, 2, 2);
        ctx.__tap.onaudioprocess = (e) => {
          if (!window.__recOn) return;
          rec.L.push(new Float32Array(e.inputBuffer.getChannelData(0)));
          rec.R.push(new Float32Array(e.inputBuffer.getChannelData(1)));
        };
        orig.call(ctx.__tap, dest);
      }
      orig.call(this, ctx.__tap);
    }
    return r;
  };
})();
"""
PACK = """
() => {
  // keep the context that carried the music (the others are silent)
  const energy = (rec) => rec.L.reduce((a, c) => a + c.reduce((s, v) => s + v * v, 0), 0);
  const rec = window.__ctxs.reduce((best, r) => (!best || energy(r) > energy(best)) ? r : best, null);
  window.__sampleRate = rec.sr; window.__nctx = window.__ctxs.length;
  const n = rec.L.reduce((a, c) => a + c.length, 0);
  const out = new Int16Array(n * 2); let i = 0;
  for (let k = 0; k < rec.L.length; k++) {
    const L = rec.L[k], R = rec.R[k];
    for (let j = 0; j < L.length; j++) {
      out[i++] = Math.max(-32768, Math.min(32767, Math.round(L[j] * 32767)));
      out[i++] = Math.max(-32768, Math.min(32767, Math.round(R[j] * 32767)));
    }
  }
  window.__packed = new Uint8Array(out.buffer); return window.__packed.length;
}
"""
SLICE = """
([a, b]) => { let s = ''; const bytes = window.__packed.subarray(a, b);
  for (let i = 0; i < bytes.length; i += 32768) s += String.fromCharCode(...bytes.subarray(i, i + 32768));
  return btoa(s); }
"""


RATE = """
(() => {   // pin every (live) AudioContext the page makes to one sample rate (and output buffering)
  const Orig = window.AudioContext, want = %s;
  window.AudioContext = class extends Orig { constructor(o) { super(Object.assign({}, want, o || {})); } };
})();
"""


def record(url, take, code, seconds, fake_output=False, rate=None, latency=None):
    errors = []
    with sync_playwright() as p:
        args = ["--autoplay-policy=no-user-gesture-required"] + (["--disable-audio-output"] if fake_output else [])
        browser = p.chromium.launch(args=args)
        page = browser.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        if rate or latency:
            want = {**({"sampleRate": int(rate)} if rate else {}), **({"latencyHint": latency} if latency else {})}
            page.add_init_script(RATE % json.dumps(want))
        page.add_init_script(TAP)
        page.goto(url, wait_until="networkidle")
        page.get_by_text(take, exact=True).first.click()
        page.wait_for_selector(".strudel-host .cm-editor", timeout=60000)
        time.sleep(2)
        if code is not None:
            page.evaluate("(c) => document.querySelector('strudel-editor').editor.setCode(c)", code)
            time.sleep(0.5)
        page.evaluate("() => { window.__recOn = true; }")
        page.get_by_role("button", name="▶ Play").click()
        time.sleep(seconds)
        page.evaluate("() => { window.__recOn = false; }")
        page.get_by_role("button", name="■ Stop").click()
        total = page.evaluate(PACK)
        sr = page.evaluate("() => window.__sampleRate")
        data = bytearray()
        step = 2 * 1024 * 1024
        for a in range(0, total, step):
            data += base64.b64decode(page.evaluate(SLICE, [a, min(total, a + step)]))
        browser.close()
    return array.array("h", bytes(data)), sr, errors


def trim(samples, sr, bars, bpm, tail, threshold=0.01):
    """Cut from the first sound to exactly `bars` bars (+ tail seconds), like the Tracks renders."""
    lim = int(threshold * 32767)
    first = next((i for i, v in enumerate(samples) if abs(v) > lim), 0) // 2 * 2
    n = int((bars * 4 * 60 / bpm + tail) * sr) * 2
    return samples[first:first + n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out"); ap.add_argument("--seconds", type=float, required=True)
    ap.add_argument("--code"); ap.add_argument("--bars", type=int); ap.add_argument("--bpm", type=float)
    ap.add_argument("--tail", type=float, default=0.0); ap.add_argument("--take", default="bbox")
    ap.add_argument("--url", default="http://127.0.0.1:8765/")
    ap.add_argument("--fake-output", action="store_true"); ap.add_argument("--rate", type=int)
    ap.add_argument("--latency", help="the AudioContext's latencyHint (interactive | balanced | playback): a bigger output "
                                      "buffer gives a late audio callback more slack before Chromium skips render quanta")
    a = ap.parse_args()
    code = Path(a.code).read_text() if a.code else None
    rate = a.rate or (48000 if a.fake_output else None)
    samples, sr, errors = record(a.url, a.take, code, a.seconds, a.fake_output, rate, a.latency)
    if a.bars:
        samples = trim(samples, sr, a.bars, a.bpm, a.tail)
    with wave.open(a.out, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr); w.writeframes(samples.tobytes())
    peak = max(abs(v) for v in samples) / 32767 if samples else 0
    print(f"{a.out}: {len(samples) / 2 / sr:.2f}s stereo @ {sr} Hz, peak {20 * __import__('math').log10(max(peak, 1e-9)):.2f} dBFS; page errors: {len(errors)}")
    for e in errors[:5]: print("  ", e[:200])


if __name__ == "__main__":
    main()
