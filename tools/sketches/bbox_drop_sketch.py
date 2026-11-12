"""bbox drop sketch: the bbox song's own pieces (909 + your boom, hats, rooms, ducks) with NM's piano riff as the drop's
theme. Writes the app's Strudel code to OUT_JS.

usage: python3 bbox_drop_sketch.py SONG_JS OUT_JS [--piano G] [--saw G] [--post G] [--only STEM] [--align]

  --only drums|bass|chords|theme   one stem: every other piece becomes `silence`, except the kick's ducking copies -
                                   the audible kick turns into a silent one (gain .001) that still ducks, so every bus
                                   pumps exactly as in the mix (drums = 909 kick, boom, hats, open hat, ride, riser,
                                   reversed crash; bass = bass pluck + sub; chords = stabs + pad; theme = piano +
                                   supersaw layers, incl. the intro/outro question)
  --align                          for renders: two silent bars that load every sample and open every bus, a 1 kHz
                                   blip on the downbeat of the bar before bar 1 (cut the render at the blip + one bar:
                                   every stem and the mix then start on the same sample), and 8 silent bars after the
                                   end so the pattern doesn't loop back into the intro during the tail
"""
import argparse, ast, re
from pathlib import Path

REMIX = Path("/home/cheese/Desktop/Dev/sound/sessions/nm/work/remix7.py")
NAMES = ["c", "c#", "d", "d#", "e", "f", "f#", "g", "g#", "a", "a#", "b"]
STEMS = ("drums", "bass", "chords", "theme")
PRE_BARS = 3   # --align: bars before bar 1 (2 to load, 1 with the blip on its downbeat)


def name(p: int) -> str:
    return f"{NAMES[p % 12]}{p // 12 - 1}"


def table(src: str, var: str):
    m = re.search(rf"^{var}\s*=\s*(\[.*?\]\])", src, re.S | re.M)
    body = re.sub(r"#[^\n]*", "", m.group(1))
    return ast.literal_eval(body)


def bar_mini(notes, shift=0) -> str:
    """One bar of (beat, length, pitch, passing) as 16 steps of mini-notation, rests where nothing plays."""
    out, at = [], 0
    for b, l, p, _ in sorted(notes):
        s, n = round(b * 4), max(1, round(l * 4))
        if s > at:
            out.append(f"~@{s - at}" if s - at > 1 else "~")
        out.append(f"{name(p + shift)}@{n}" if n > 1 else name(p + shift))
        at = s + n
    if at < 16:
        out.append(f"~@{16 - at}" if 16 - at > 1 else "~")
    return "[" + " ".join(out) + "]"


def cycle(bars, shift=0) -> str:
    return "<" + " ".join(bar_mini(b, shift) for b in bars) + ">"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("song"); ap.add_argument("out")
    ap.add_argument("--piano", type=float, default=0.9); ap.add_argument("--saw", type=float, default=0.14)
    ap.add_argument("--post", type=float, default=1.0)
    ap.add_argument("--only", choices=STEMS); ap.add_argument("--align", action="store_true")
    a = ap.parse_args()
    src = REMIX.read_text()
    RIFF_A, RIFF_B, M, N = (table(src, v) for v in ("RIFF_A", "RIFF_B", "M", "N"))

    song = Path(a.song).read_text().splitlines()
    cut = next(i for i, l in enumerate(song) if l.startswith("// the form"))
    head = "\n".join(song[:cut]).replace('// "bbox": your beatbox as a track', '// "bbox drop": your beatbox song, straight into a drop on your NM piano riff')

    # NM's chords, one a bar: D#m | F#/C# | C (F# A#) | Bmaj7 - the riff's harmony wins over the bbox's in the drop
    roots_bass = ["d#3", "c#3", "c3", "b2"]; roots_sub = ["d#2", "c#2", "c2", "b1"]
    up = ["d#4", "c#4", "c4", "b3"]
    chords = ["[f#3,a#3,d#4]", "[f#3,a#3,c#4]", "[f#3,a#3,c4]", "[f#3,a#3,d#4]"]
    bass = "<" + " ".join(f"[~@2 {r}@3 {r}@3 {u}@3 {r}@3 {r}@2]" for r, u in zip(roots_bass, up)) + ">"
    sub = "<" + " ".join(f"[~@2 {r}@3 {r}@3 {r}@3 {r}@3 {r}@2]" for r in roots_sub) + ">"
    stab = "<" + " ".join(f"[~ ~ {c} ~ ~ ~ {c} ~ ~ ~ {c} ~ ~ ~ {c} ~]" for c in chords) + ">"
    pad = "<" + " ".join(f"{c}" for c in chords) + ">"

    theme_fx = (f'.room(.3).roomsize(1.5).roomlp(5000).delay(.14).delaysync(3/16).delayfeedback(.35).orbit(7)')
    piano = lambda pat, g="": f'note("{pat}").s("piano").clip(1).release(.25).velocity(perlin.range(.85, 1).fast(3)).gain({a.piano:g}{g}).pan(.5){theme_fx}'
    saw = lambda pat, g=a.saw: (f'note("{pat}").s("supersaw").unison(3).detune(.15).shape(.1)'
                                f'.attack(.004).decay(.35).sustain(.35).release(.15).lpf(2600).lpq(.7).hpf(250).gain({g:g}){theme_fx}')
    kicks = "<[bd ~ ~ ~ bd ~ ~ ~ bd ~ ~ ~ bd ~ ~ ~] [bd ~ ~ ~ bd ~ ~ ~ bd ~ ~ ~ bd ~ ~ ~]>"

    # the form, one stem or all: pieces outside the stem become silence; the kick (909 + boom) outside the drums
    # becomes a silent copy (gain .001) that still ducks the sub and the buses, so every stem pumps as in the mix
    on = lambda stem: a.only in (None, stem)
    D, B, C, T = ((lambda js, s=s: js if on(s) else "silence") for s in STEMS)
    K = lambda js: js if on("drums") else f"{js}.gain(.001)"

    def st(*parts):
        parts = [p for p in parts if p != "silence"]
        return "silence" if not parts else parts[0] if len(parts) == 1 else "stack(" + ", ".join(parts) + ")"

    form = [
        (4, st(K("kick"), D("boom"), "duck7", T("question"), C("padNM.gain(.05)"), "preload"),
         "1-4: your boom and the piano asking and answering"),
        (4, st("pull4(" + st(K("kick"), D("boom"), "duck7", B("subNM"), B("bassNM.lpf(saw.range(250, 450).slow(4))")) + ")",
               T("teaser"), C("padNM"), D('hats.mask("<0 0 1 1>")'), D("riser4"), D("rcrash4")),
         "5-8: the bass in, the riff on the piano opening up, a riser; out two beats before the drop"),
        (16, st(K("kickHole"), D("boom"), "duck7", D("hats"), D("ohat"), B("subNM"), B("bassNM"), C("stabsNM"), T("themeA")),
         "9-24: the drop - the riff is the theme"),
        (16, st(K("kickHole"), D("boom"), "duck7", D("hatsBreak"), D("ohat"), D("ride"), B("subNM"),
                B("bassNM.lpf(perlin.range(600, 900).slow(4))"), C("stabsNM.lpf(saw.range(1500, 5000).slow(8))"), C("padNM"),
                T("themeB")),
         "25-40: the riff's second time, a ride, everything opening"),
        (4, st("last(" + st(K("kick"), D("boom"), "duck7", B("subNM")) + ")", D('ohat.mask("<1 1 1 0>")'), T("question")),
         "41-44: out on the question and answer, one last hit"),
    ]
    if a.align:
        form = [(PRE_BARS - 1, "warm", "(render) load every sample, open every bus"),
                (1, "blip", "(render) the alignment blip: bar 1 starts exactly one bar after it")] + form + \
               [(8, "silence", "(render) silence, so the tail isn't the intro looping back")]
    sections = "\n".join(f"    [{n}, {code}]{',' if i < len(form) - 1 else ''}   // {about}" for i, (n, code, about) in enumerate(form))

    # (--align) every piano note the theme plays, so the sample files are loaded before they're needed
    keys = sorted({p for bars in (RIFF_A, RIFF_B, M, N) for bar in bars for _, _, p, _ in bar})
    align_code = "" if not a.align else f"""
// alignment, for rendering stems: two near-silent bars that load every sample (909, boom, crash, piano) and open every
// bus (orbits 2 4 5 6 7, so the first ducks land), then a 1 kHz blip on the downbeat of the bar before bar 1, alone
// on orbit 8; the render is cut at the blip + exactly one bar, so every stem and the mix start on the same sample
const warm = stack(preload, s("bb_kick ~ ~ ~").gain(.001),
  note("[{','.join(name(k) for k in keys)}] ~ ~ ~").s("piano").gain(.001){theme_fx},
  note("d#2 ~ ~ ~").s("sine").gain(.001).orbit(2), note("[f#3,a#3,d#4] ~ ~ ~").s("supersaw").gain(.001).room(0.3).roomsize(1.5).roomlp(5000).orbit(4),
  s("pink ~ ~ ~").gain(.001))
const blip = note("c6 ~ ~ ~").s("sine").attack(.001).decay(.03).sustain(0).release(.005).gain(1).orbit(8)
"""
    stem_note = "" if a.only is None else f"\n// stem: {a.only} alone (the other pieces silent; the kick's ducking copies kept, so the buses pump as in the mix)"
    post = "" if a.post == 1 else f".postgain({a.post:g})"

    body = f"""
// the drop's theme: your NM piano riff (the question and answer made rhythmic, 3-3-2), as written in remix7.py, now at
// 124 BPM - a piano for the attack and body with the stabs' supersaw under it for sustain and width, same notes; on its own
// bus (orbit 7) with a short room and a quiet dotted-8th echo, ducked lightly by the kick so it breathes but doesn't pump
const themeA = stack({piano(cycle(RIFF_A))}, {saw(cycle(RIFF_A))})
const themeB = stack({piano(cycle(RIFF_B))}, {saw(cycle(RIFF_B))}, {saw(cycle(RIFF_B, 12), a.saw * .45)})   // second time: pickups, the higher peak, a quiet octave above
const question = {piano(cycle(M + N))}   // the question and answer, piano alone
const teaser = {piano(cycle(RIFF_A), "")}.lpf(saw.range(900, 5000).slow(4))   // the riff on the piano, opening up toward the drop
const duck7 = s("{kicks}").bank("RolandTR909").gain(.001).duckorbit(7).duckattack(0.12).duckdepth(0.25)   // a silent kick that only ducks the theme, -5 dB
const boom = s("{kicks.replace('bd', 'bb_kick')}").gain(.35).lpf(6000)   // your own boom under the 909

// the bass and sub follow NM's chords, one a bar (D#m | F#/C# | C | Bmaj7), on your bwoms (16ths 3 6 9 12 15), the third
// one flicked up an octave; same voices as the song
const bassNM = note("{bass}").s("sawtooth").shape(.2).gain(0.5)
    .lpf(perlin.range(450, 560).slow(4)).lpenv(2.2).lpattack(.006).lpdecay(.17).lpsustain(.2).lpq(2)
    .attack(.004).decay(.22).sustain(.3).release(.06)
const subNM = note("{sub}").s("sine").gain(0.5).release(.05).orbit(2)
// the stabs, lighter and darker than the song's, on the off-beat 8ths under the theme (F# and A# held, the top moving
// D# C# C D#); the pad holds the same chords
const stabsNM = note("{stab}").s("supersaw").unison(3).detune(.15).shape(.15).velocity(perlin.range(.8, 1).fast(2))
    .attack(.003).decay(.12).sustain(.06).release(.1).lpf(1700).lpq(.8).hpf(250).gain(.18)
    .room(0.3).roomsize(1.5).roomlp(5000).orbit(4)
const padNM = note("{pad}").s("sawtooth").vib(.3).vibmod(.12).shape(.1)
    .attack(.2).decay(.2).sustain(.9).release(.35).lpf(900).hpf(320).gain(.07)
    .room(0.2).roomsize(1.5).roomlp(5000).orbit(6)
const riser4 = s("pink").slow(4).clip(0.97).hpf(800).hpenv(2).hpattack(7.1).hpsustain(1).lpf(9000).attack(7.1).gain(.3).pan(.5)   // 4 bars of noise swelling into the drop
const rcrash4 = s("cr").speed(-1).gain(.6).late(9/16).mask("<0 0 0 1>").pan(.55).room(0.3).roomsize(1.5).roomlp(5000).orbit(5)
const pull4 = p => p.lastOf(4, x => x.mask("1 1 0 0"))   // out after two beats in the last bar before the drop
const last = p => p.mask("<1 1 1 [1 0 0 0]>")   // one last hit on the final bar
{align_code}
// the form: 44 bars, 1:25 - a swift intro straight into the drop at bar 9, two 16-bar drops (they loop cleanly), out{stem_note}
stack(
  arrange(
{sections}
  )
){post}
"""
    Path(a.out).write_text(head + "\n" + body)
    print(f"wrote {a.out}: theme A bar 1 = {bar_mini(RIFF_A[0])}")


if __name__ == "__main__":
    main()
