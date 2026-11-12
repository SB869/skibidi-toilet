# sound

Linux audio tools, starting with offline vocal pitch correction (autotune).

## Setup

```sh
uv sync
uv tool install --editable .    # optional: puts `sound` and `autotune` on your PATH
```

Without the tool install, prefix commands with `uv run` from this directory.

| command | does |
|---|---|
| `sound serve` | the web interface (below) |
| `sound tune` (or `autotune`) | pitch-correct recordings |
| `sound fx` | vocal production chain, optionally matching a reference's sound |
| `sound cut` | export a time range as wav |
| `sound analyze` | pitch range, tuning, notes used, key, held-note list |
| `sound compare` | line takes up with a reference vocal and score them |
| `sound beat` | turn a beatbox recording into a beat: Strudel code, MIDI, and a kit of your own sounds |

Times are seconds or `m:ss` (`206`, `3:26`, `3:26.5`).

## Web interface

```sh
uv run sound serve      # opens http://127.0.0.1:8765 (localhost only)
```

- **Library** (left): takes, references and saved renders from `recordings/`. Drop files on
  a group to import them.
- **Record**: pick the part of a reference to sing by dragging across its waveform (or
  typing From/To). The timing is detected: the tempo and beats (tracked over the whole
  song, which is steadier for a vocal-only stem) and where the voice comes in (where the
  sound starts, consonant or breath included), which the take snaps to. A count-in of 2, 4
  or 8 clicks leads you in, a beat apart, with the voice coming in exactly one beat after
  the last click, even when the song's entry is a pickup ahead of the beat; a click track
  through the take, if you want one, follows the song's own beats. Check the tempo with
  Practice and fix it with ½ / 2× / Tap / typing if the clicks don't sit with the song.
  The **Scale** meter is a tuner against the reference's key: its notes lit on a strip,
  a needle for your voice, the reference's current note outlined, a ±50¢ gauge, and how
  much of the last 10 s you sang in key. The reference plays in your headphones while you
  watch three lanes live: the reference's melody (moved to your octave) with
  your pitch on top, its loudness against yours, and its *edge* (upper-harmonic energy:
  support and belt) against yours. Readouts show your note and how many cents you're off,
  your input level, and your edge vs the reference's. Takes are captured uncompressed
  (32-bit float WAV) and scored against the reference the moment they're saved.
- Open a library file straight from a link: `http://127.0.0.1:8765/?open=tracks/song.wav`
  selects it and switches to its tab (Track, Beat, or Tune); add `&play` to start it playing.
- **Record → Follow Ardour** (or open `http://127.0.0.1:8765/?ardour`): the reference,
  section and a take to compare come from the session open in Ardour, and stay in step with
  it (checked every 2 s while you're not singing). The section is the editor's range
  selection, or else the selected regions, or else the loop range, or else the take under
  the playhead. The take from Ardour is drawn in purple, either **As placed** (where it sits
  on the timeline, so timing problems show) or **Note by note** (lined up with the reference
  by its pitch contour, so only pitch problems show). Its speed relative to the reference is
  shown too. Takes you record go straight into Ardour on a `takes` track, at the spot they
  were sung against, corrected for the browser's audio latency, and layered so the newest
  plays.
- **Tune**: piano-roll pitch view (grey = sung, blocks = target notes, teal = output) with
  live re-rendering as you change key, retune speed, strength, etc. **Original / Tuned**
  switches what you hear without stopping. Drag to select, then loop, analyze, or export it.
- **Sound** (in Tune): the vocal chain on top of the tuning. Pick a reference section to
  sound like, or leave it on the generic heavy-rock tone. With **Match levels** the
  original plays at the same loudness, so the comparison is fair.
- **Compare**: score takes and the current render against a reference section, with the
  reference drawn time-aligned over your pitch.
- **Track**: finished mixes in the library's **Tracks** group (`recordings/tracks/`), with a
  player. A song rendered from the Beat tab lands here.
- **Beat**: pick a recording from the **Beatbox** group (drop one there) and it becomes a beat.
  The booms, mid and high sounds are found and sorted, the tempo followed (beatboxers drift),
  and the groove laid on a 16th grid, bar by bar (shown as a grid; the bars in the loop
  highlighted). If you beatboxed an "oscillator" (a buzzed or hummed synth line), its riff is
  picked up note by note (the top lane of the grid) and leads the beat. **Sound** picks how:
  *synth* (the default) plays the synth your beatbox stands for - the riff snapped to the pulse
  of your "bwom"s (marked under the lane) on a dance bass (**Bass** picks which: a driven saw
  plucked by its filter (the default), a square plucked the same way, an FM bass, two GM synth
  basses or a fingered electric bass), each bwom as loud as you pushed it, the cutoff drifting
  over the bars, the last bwom ringing on over the bar line, hats (**Hats**: 909 16ths accented
  on the off-beats and lightly swung (the default), an 808 tick accented on your bwoms, or
  off-beat 8ths), a 909 kick with a tuned sine boom under it and a sine sub that ducks under
  both; *mouth* copies the recording - the
  loudness and filter moving note by note the way your mouth moved them, and your booms as a
  kick that sweeps down into the riff's root like yours did. Without a riff, your
  kicks, snares and hats play on drum-machine sounds. Either way your own beatbox sounds are
  layered underneath, and an open hat and a clap backbeat are there to uncomment. **Form**
  switches between the groove looped (at your tempo) and the beat laid out as a house/techno
  track at 124 BPM: 112 bars (3:36) in 8-bar phrases with every big change on a 16-bar line -
  intro (your kick alone, then the sub and the riff low and filtered), build (a pad takes up the
  riff's chords, both opening up under a pink-noise whoosh and a reversed crash; kick, sub and
  riff pull out before the drop), a 16-bar drop (the hats and the open hat come in right here;
  then chord stabs on the off-beat 8ths; every 8th bar the kick skips beat 4), breakdown (no
  kick: the pad holds the chords while the riff echoes and opens upward; then the sub returns,
  the pad swells, the whoosh, and the kick rolls back in over two bars), a 32-bar second drop
  that grows every 8 bars (a ride on your bwoms and the hook - the riff two octaves up with its
  own echo - answering in the last two bars of each phrase; then the hook all through, the hats
  dropping out for a bar; then the stabs' filter opening, kick and hats dropping out for two
  beats before the peak; then the pad under it all with the riff's filter open) and a
  16-bar outro ending on one hit, kick and sub together. The harmony is read off the riff: each stab or pad chord is the triad over the bass
  note sounding under it, in the minor the riff spells (here A# minor with a Phrygian B), so
  nothing ever clashes with the bass. The voices are mixed as one record: the kick ducks the sub,
  the chords, the hook and (lightly) the hats so the top breathes with it; the stabs, pad, hook
  and the 909 kit each sit on their own bus with a short room, a quiet echo on the stabs and the
  hook, a little stereo width on the top and the kick, sub and bass dry in the centre; the hook is
  the bass's own voice two octaves up. Every piece is a `const`; the form is two `arrange(...)`
  blocks - the beat's, and the riff's with everything that follows it (sub, pad, hook), shifted
  onto the first bwom as a whole so notes ring over section lines - so adding a part means one
  more line. It plays in an embedded [Strudel](https://strudel.cc) editor: edit the code and
  press Ctrl+Enter; **MIDI** downloads the loop (drums, riff, sub) for a DAW. `sound beat <file>`
  does the same from the command line (`--song` writes the track too).

Keys: <kbd>Space</kbd> play/pause, <kbd>T</kbd> original/tuned, <kbd>L</kbd> loop
selection, <kbd>Esc</kbd> clear selection. Wheel zooms, shift+wheel scrolls.

The frontend (`web/`, React + TypeScript + Vite) must be built once, and again after you
change it:

```sh
cd web && npm install && npm run build
```

For frontend work with hot reload, run `uv run sound serve --no-browser` and, in `web/`,
`npm run dev` (port 5173, proxies `/api` to the server).

The server ([server.py](src/sound/server.py)) is a thin JSON API over the same engine the
CLI uses; it caches decoded audio and pitch tracks, which is what makes re-tuning take a
fraction of a second. The plan is to wrap `web/` plus this server as a desktop app later.

## Ardour and MCP

The DAW side is [Ardour](https://ardour.org) 9.8 (Flatpak: `flatpak install --user flathub
org.ardour.Ardour`), with native plugin packs from Flathub (Surge XT, Odin 2, LSP, x42,
Dragonfly Reverb). Sessions live in `sessions/` (gitignored).

```sh
uv run sound daw                 # open sessions/friday in Ardour (--new to create one)
uv run sound ardour status       # the open session and its tracks
uv run sound ardour import recordings/reference/acapella.mp3 --name reference
uv run sound ardour import "recordings/Friday, 9h17m PM.mp3" \
    --align-to recordings/reference/acapella.mp3 --ref-start 3:26   # lands where it lines up
uv run sound ardour lua 'return Session:name ()'                    # anything Ardour's Lua can do
uv run sound ardour instrument --synth Odin2  # a synth track your MIDI keyboard plays (--list: synths, inputs)
uv run sound ardour reverb "grand piano"      # send a track to a hall reverb bus
uv run sound ardour drums --bpm 72            # AVL drum kit playing an original looped groove
uv run sound ardour guitar --input Unitek     # distorted guitar (Guitarix) from a USB guitar input, live
uv run sound daw sober --buffer 128           # 2.7 ms engine cycles for playing live (kept for next time)
uv run sound ardour import song.mid --track drums   # a MIDI file of your own onto a track
```

To sing against what's in Ardour, with pitch lines and live input, use the web app's
Record tab with **Follow Ardour** (see Web interface).

`sound daw` launches Ardour with no dialogs: the audio engine starts on PipeWire's JACK
(sharing the sound card with other apps), and the MCP server (port 4820), OSC (UDP 3819) and
the **sound bridge** are switched on. It edits Ardour's config only while Ardour is closed,
because Ardour rewrites the config when it quits.

The sound bridge (`src/sound/ardour_bridge.lua`) is an Ardour editor hook. Every 100 ms it
checks `~/.var/app/org.ardour.Ardour/config/ardour9/sound-bridge/` for a request, runs it,
and writes back a JSON reply. Requests can import audio, which Ardour's MCP can't do, or run
any Lua. Track ids are the same ones Ardour's MCP uses.

Two MCP servers are registered for Claude Code in `.mcp.json`:

- **sound** (`sound mcp`, stdio). Library tools: `list_library`, `analyze`, `timing_of`,
  `tune`, `produce`, `compare` (results are saved into the library, so they show up in the
  web app too). Ardour tools, via the bridge: `ardour_open`, `ardour_status`,
  `ardour_import` (optionally lined up with a reference) and `ardour_lua`.
- **ardour** (`http://127.0.0.1:4820/mcp`): Ardour's built-in, experimental MCP server, with
  95 tools for transport, tracks, mixer, plugins, markers, regions and MIDI. Save a snapshot
  before letting an assistant make destructive edits.

Ardour only reads a control surface's `<Protocol>` entry if it has `name`, `active` and
`config` (for a surface that isn't a device, `config` is its name). An entry without `config`
is silently ignored.

Headless Ardour tools also ship in the Flatpak:
`flatpak run --command=ardour9-new_session org.ardour.Ardour …`, `ardour9-lua` (Lua against
a session, no GUI) and `ardour9-export`.

Note: Flatpak apps get a private `/tmp`, and Ardour's sandbox only sees your home folder.
Anything shared with Ardour must live under home.

## autotune

```sh
autotune vocals.wav                       # snap to the nearest semitone
autotune vocals.m4a --key auto            # detect the key, snap to its scale
autotune vocals.wav --key "F# minor"      # explicit key (C, Bb, Am, "D minor", Ebmaj, ...)
autotune vocals.wav --key "F# major" --style natural   # in tune, but doesn't sound tuned
autotune vocals.wav --key Am --speed 0    # hard, robotic snapping (T-Pain)
autotune take*.wav --key C --speed 80 --strength 0.7 -d tuned/   # gentle, batch
autotune vocals.wav --plot                # also writes vocals_tuned.png (pitch before/after)
```

Output is `<name>_tuned.wav` (24-bit, same sample rate and channels) next to the input,
or in `--out-dir`. Input can be anything ffmpeg decodes.

| option | what it does |
|---|---|
| `--key` | tonic plus optional mode; `auto` estimates it from the melody and falls back to chromatic if unsure. Without it every semitone is a valid target. |
| `--scale` | `major`, `minor`, `harmonic-minor`, `dorian`, `mixolydian`, `minor-pentatonic`, `blues`, ... |
| `--style` | `autotune` (default) pulls every moment toward a note: the classic sound. `natural` moves each whole note into tune and keeps its vibrato, scoops and slides, like Melodyne. |
| `--humanize` | natural style, 0-1 (default 0.25): each note keeps part of its own lean, capped at 12¢, so not every note lands dead centre. |
| `--drift` | natural style, 0-1: also straighten slow wander inside long notes (vibrato and note ends are left alone). |
| `--speed` | autotune style: retune speed in ms. `0` jumps straight to the note; 10-40 is tight; 80+ keeps vibrato and scoops. |
| `--strength` | 0-1, how far toward the target to pull. |
| `--hysteresis` | autotune style: semitones past the halfway point before switching notes (stops warbling between two notes). |
| `--a4` | reference tuning, e.g. `432` if the backing track is tuned that way. |
| `--fmin` / `--fmax` | detection range; narrow it (e.g. `--fmin E2 --fmax A4`) if it locks onto the wrong octave. |

Works best on a dry, isolated vocal: one voice, no reverb, no backing track bleeding in.

Keep takes in `recordings/` and references in `recordings/reference/` (all gitignored), e.g.
`autotune recordings/take.m4a -d recordings/tuned`.

## Producing the vocal (`sound fx`)

```sh
sound fx tuned.wav                                       # generic heavy rock-vocal chain
sound fx tuned.wav --ref acapella.mp3 --ref-start 3:26   # sound like that part of a reference
sound fx tuned.wav --ref acapella.mp3 --grit 0.85 --doubles 0.55   # heavier still
```

The chain ([fx.py](src/sound/fx.py)): low cut → tone-matching EQ → compression →
saturation → tone touch-up → de-esser → width → doubles → reverb → loudness/limiter. EQ comes
before compression so the compressor reacts to a balanced voice, not a boomy low end.
Tone matching is register-aware: if you sing lower than the reference, your low harmonics
are compared with theirs (fundamental with fundamental), while presence and air are
compared at the same frequencies. Saturation is what makes it heavy: it builds the upper
harmonics a belted voice has and a soft one lacks, which EQ alone can't create.

Knobs (0-1): `--grit`, `--compression`, `--tone`, `--deess`, `--doubles`, `--space`,
`--width` (0-1.5), plus `--loudness` (LUFS; defaults to the reference's) and `--low-cut`.

## Checking a take against a reference

```sh
sound cut recordings/reference/acapella.mp3 3:26 3:50                 # listen to just that part
sound analyze recordings/reference/acapella.mp3 --start 3:26 --end 3:50 --loose --notes
sound compare take.wav tuned/*.wav --ref recordings/reference/acapella.mp3 --ref-start 3:26 --plot cmp.png
```

`--loose` reads pitch from audio pYIN is never confident about: acapellas pulled out of
a mix, or anything with reverb or bleed. `compare` always reads the reference that way.

`compare` time-warps each take onto the reference, so it copes with you singing faster or
slower than the original, an octave lower, or with an overall tuning offset. Tuned
versions of one recording (same length) share the alignment of the first one listed.
`analyze` shows the notes the reference actually uses: pass a key that holds all of
them to `autotune --key`.

Recording on Linux (PipeWire): `pw-record --channels 1 --rate 48000 take.wav`, Ctrl-C to stop.

## How it works

1. **Pitch tracking** ([pitch.py](src/sound/pitch.py)): pYIN for robust voiced/unvoiced
   decisions and octave choice, refined with YIN's sub-sample lag interpolation (pYIN alone
   is quantised to 10 cents).
2. **Note choice** ([tune.py](src/sound/tune.py)). *Autotune style:* snap the
   median-smoothed pitch to the nearest scale note, with hysteresis; the correction is
   low-passed with a time constant of `--speed` ms, so slow drift is fixed while faster
   movement survives in proportion. *Natural style:* split each phrase into notes where the
   vibrato-averaged pitch moves and stays moved; measure each settled note's centre (ignoring
   its scoop and release) and move the whole note by centre-to-nearest-scale-note, minus a
   little of its own lean (humanize). Short notes are corrected a bit less than held ones;
   anything under 150 ms, and short stretches that travel or dip (scoops, fall-offs, quick
   dips), keep their shape and ride along with their neighbours. Between notes the
   correction changes in step with the slide, where the ear can't hear it.
3. **Pitch shifting** ([psola.py](src/sound/psola.py)): TD-PSOLA. Marks are placed on each
   glottal pulse by cross-correlating successive periods. Two-period grains are then laid
   back down at the corrected spacing. Formants stay put, unvoiced sounds pass through
   untouched, and with no correction the output is bit-identical to the input.

## Tests

```sh
uv run pytest
```

Most use a synthetic vowel (sawtooth through formant filters) to check pitch accuracy
across D2-A5, scale snapping, retune speed versus vibrato, formant preservation, exact
passthrough of noise and of breaths between tuned notes, stereo, and m4a decoding. The
analysis tests cover alignment (offset, octave, tempo drift, quarter-tone offsets), scoring,
key estimation and the CLI end to end.
