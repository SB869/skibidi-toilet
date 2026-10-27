# Handoff: the `sound` repo

A Linux music workstation built around the user's own tools, replacing FL Studio. Three parts:
**vocal tools** (pitch correction, analysis, take scoring; CLI + web app), **Ardour automation**
(Ardour 9.8 Flatpak driven through its MCP server plus a Lua bridge, with custom Lua instruments),
and **song projects** in `sessions/` (the current one is a remix of the user's song "NM").

## State of the repo

- Nothing is committed yet: branch `master` has no commits; the intended main branch is `main`.
- `uv run pytest -q` → 129 tests pass.
- Gitignored and personal: `recordings/` (takes, references, `beatbox/`), `sessions/` (Ardour sessions,
  exports, project work folders), `instruments/` (SoundFonts).
- Python ≥ 3.12 via `uv`. Frontend: `web/` (React + TypeScript + Vite; build with
  `cd web && npm install && npm run build`).

## Working with this user

- Automate everything (scripts, MCP, the bridge). Don't give click-by-click instructions.
- When revising music, change only what was asked, and keep everything else the same.
- Don't transcribe or recreate copyrighted songs. Originals, or the user's own songs (NM is
  theirs), only.
- You can't hear audio. Verify by rendering and measuring: stuck-note probes, clash scans,
  loudness per section, spectra against a reference. Report numbers.
- Their email is for identity only.

## Layout

| path | what it is |
|---|---|
| `src/sound/cli.py`, `commands/` | `sound` CLI: `tune`, `fx`, `cut`, `analyze`, `compare`, `beat`, `serve`, `mcp`, `daw`, `ardour` (`status`, `import`, `instrument`, `drums`, `guitar`, `reverb`, `lua`) |
| `audiofile.py` | load and save audio |
| `pitch.py` | pYIN pitch tracking (`PitchTrack`) |
| `scales.py` | keys, scales, MIDI ↔ Hz |
| `tune.py`, `psola.py` | offline autotune (`TuneSettings`, `autotune`), PSOLA pitch shifting |
| `fx.py` | vocal production chain (compression, grit, tone, doubles, reverb), optionally matched to a reference |
| `analysis.py` | pitch range, tuning, key, notes and held notes; take-vs-reference scoring |
| `timing.py` | tempo, beats and vocal entry detection |
| `plots.py` | pitch plots |
| `beatbox.py` | beatbox recording → beat: `groove(path)` (onsets, k-means into kick / snare / hat by low-end share and brightness, tempo fit on a 16th grid, dynamic beat tracking anchored on the kicks, bars of 16 steps, bars without a steady kick skipped; the pitched "oscillator" part via HPSS + pyin per 16th: note, level, brightness, and hits in its band reclassified as `"voice"` articulations; `Groove.oscillator` = it sounds through the bars and between the kicks), `loop()` (with a riff: the run of bars where it came through clearly), `events()` (the riff as notes: a new note at a pitch change or an accent, each with gain, start cutoff and how far the filter opens), `pulse()` (the steps where the bwoms recur bar after bar), `riff_events()` (the riff on that pulse: a note per bwom at the pitch sung there, early arrivals snapped, pickups kept), `strudel(style=)` (with a riff, `"synth"` (default): the riff on one of `BASSES` (`bass=`: `synth` = GM Synth Bass 2 soundfont (default), `bright` = GM Synth Bass 1, `fm`, `pluck`, `finger` = GM electric bass; Dirt's `jvbass` sample is silent in the embedded editor, so it isn't offered), a note per bwom with velocities from the performance's accents, a mild `lpf(perlin.range().slow(4))` drift, an 8th-note `delay`; no per-note resonant filter sweep - a driven saw with a "wow" per bwom was tried first and the user called it a fart (measured: its brightness rose half an octave right after each hit; the kept basses are steady or punchy), the pattern cut from the first bwom (`_rotate`) and moved back with `.late()` so the last bwom rings over the bar line; sine sub on orbit 2; deep `sbd` kick in the key with `duckorbit(2)` ducking only the sub; `"mouth"`: supersaw with per-note velocity/lpf/lpenv copied from the mouth, sine sub, `sbd` boom tuned to the root, the performer's own kick layered; without a riff: drum-machine kicks/snares/hats; a commented clap in all), `midi_bytes()`/`write_midi()` (drums ch10, riff ch1, sub ch2; same `style`), `pieces()` (the instruments as `Piece(name, about, code)` plus a kick-for-any-pattern function; `strudel()` stacks them), `song()` (the beat as a 104-bar track: pieces as `const`s, variations for the form - the lead quiet/filtered/opening, echoing alone, a kick roll - plus the song's own pieces: `ohat`, `rim` (909 rimshot on the off-beat 8ths that aren't bwoms), `ride` (drop 2), `kickHole` (the kick skipping beat 4 of every 8th bar), `leadThrow` (the riff with its last beat of every 8th bar on orbit 3 with a long-feedback delay - superdough's delay params are per orbit, so a throw needs its own orbit), `stab` (a 5th chord in the key on supersaw, off-beat 8ths) and `stabSweep` (its lpf opening over 8 bars, drop 2); a snare on 2 and 4, tom fills and crashes every 8 bars were tried and rejected as a rock/EDM kit on a techno groove; a `riser` of pink noise with hp+lp envelopes sweeping a band up (white noise with the filter wide open read as TV static), `rcrash` (`s("cr").speed(-1)`, started at 16th 10 so its 7-16th swell peaks on the downbeat), (no forward crash: one on the drop's downbeat right after the reversed one read as a double hit), and `preload` (every sample triggered at gain .001 in bar 1: superdough loads samples lazily and drops their first hit) - `pull`/`end` helpers. The hats are a loop piece (`HATS`, `hats=`: `pulse` = 808 tick 16ths with a gain per 16th from the groove - .3 on the bwoms, .18 on the kicks, .1 between - the default; `straight` = 909 16ths accented on 8ths; `offbeat` = 909 on the off-beat 8ths) and only play from the drops on: the user wants nothing ticking and no claps before a drop (a clap roll and build-up hats were tried and rejected), and `arrange(...)` from the `SONG` plan: (bars, title, layers), `("pull", [...])`/`("end", [...])` groups), `kit_sound()`/`kit()` |
| `server.py` | FastAPI JSON API for the web app (`/api/files`, `audio`, `analysis`, `overview`, `timing`, `guide`, `tune`, `renders/{rid}`, `compare`, `cut`, `beat`, `beat/kit`, `beat/midi`, `ardour`, `ardour/take`, `ardour/takes`). Caches decoded audio, pitch tracks and grooves. Library kinds = folders: take `.`, reference, render `tuned`, beatbox, track `tracks` (finished mixes; `TrackView.tsx` plays them) |
| `web/src/` | the web app: Library, Record (live pitch, scale meter, count-in, **Follow Ardour**), Tune (piano roll, live re-render), Sound, Compare, **Beat** (`BeatView.tsx`: groove grid with a riff lane and the bwom pulse, **Sound** synth/mouth, **Bass**, **Hats**, **Form** loop/song switches, + an embedded Strudel editor from `@strudel/repl`, lazy-loaded; `<strudel-editor code=…>`, its `.editor.evaluate()/stop()`) |
| `mcp_server.py` | the `sound` MCP server (stdio): `list_library`, `analyze`, `tune`, `produce`, `compare`, `timing_of`, `ardour_open`, `ardour_status`, `ardour_import`, `ardour_lua`, `ardour_instruments`, `ardour_add_instrument`, `ardour_add_reverb`, `ardour_add_drums`, `ardour_add_guitar`, `beat_from_beatbox` |
| `daw.py` | Ardour Flatpak launcher and config: `open_session`, `enable_surfaces`, engine buffer (`set_engine_buffer`, `apply_engine_buffer`), `install_scripts`, `mcp_call(tool, args)` (HTTP JSON-RPC to Ardour's MCP) |
| `bridge.py`, `ardour_bridge.lua` | file-based request/reply bridge into Ardour's Lua (below) |
| `arrangement.py` | session helpers: `snapshot()` (regions), `import_files`, `add_instrument`, `add_reverb`, `add_guitar`, `audio_inputs`, `midi_inputs`, `set_tempo`, `set_loop`, `add_drums`, `add_take` |
| `midifile.py` | minimal SMF writer and reader: `Note(beat, length, pitch, velocity, channel)`; `write(path, notes, bpm, name, programs={ch: prog}, controls=[(beat, ch, cc, value)])` |
| `drums.py` | GM drum map and an original groove |
| `ardour_scripts/*.lua` | custom Lua DSP instruments, installed into Ardour by `daw.install_scripts()` (see below) |
| `.mcp.json` | registers two MCP servers for Claude Code: `sound` (stdio) and `ardour` (http://127.0.0.1:4820/mcp, only up while Ardour runs) |

## Ardour: how it's driven

**Launch.** `uv run sound daw <session-name | path/to/snapshot.ardour> [--new] [--buffer 256]`.
It edits Ardour's config only while Ardour is closed, then starts it with no dialogs. The engine
runs on PipeWire's JACK, sharing the card with other apps. It also switches on the MCP server
(port 4820), OSC (UDP 3819) and the bridge, and installs the bridge and the Lua instruments.

`open_session` does **not** switch sessions if Ardour is already running. To switch:
`daw.mcp_call("session_save")`, then `flatpak kill org.ardour.Ardour`, then launch.

**The Flatpak sees only `$HOME`.** Files to import must live under home, not `/tmp`.

**MCP** (`daw.mcp_call`). Tools in use:
- `transport_stop` / `transport_play` / `transport_locate_sample`
- `transport_loop_toggle`: errors if no loop range is set.
- `session_save {snapshotName, switchToSnapshot}`
- `track_select {id}`
- `region_move_to_sample`
- `region_resize_samples`: can shorten an end, can't extend it.
- `markers_set_auto_loop_samples`
- `track_set_pan`: 0 = right, 1 = left.

**Bridge** (`bridge.lua(code)` → `{"returned", "printed"}`). An editor hook polls
`~/.var/app/org.ardour.Ardour/config/ardour9/sound-bridge/` every 100 ms. It runs `ping`,
`import` (Editor:do_import, which MCP can't do) or arbitrary sandboxed `lua`.

Lua API facts learned the hard way:
- **Snapshot name:** `Session:snap_name()`, not `snapshot_name`. `Session:dirty()` isn't bound.
- **Tempo map:** `Temporal.TempoMap.write_copy()`, then `tm:set_tempo(Temporal.Tempo(bpm, bpm, 4), Temporal.timepos_t(sample))`, then `tm:set_meter(Temporal.Meter(n, 4), …)`, then `Temporal.TempoMap.update(tm)`.
- **Plugin parameters:** `ARDOUR.LuaAPI.set_processor_param(proc, k, v)` indexes control *inputs*, not port numbers. Set by label instead: iterate `plug:nth_parameter(k)` and `plug:parameter_label(port)`. See `set_params` in `sessions/nm/work/nm_session.py`.
- **Hiding tracks:** you can't call `set_hidden` on a track. Put tracks in a route group and use `RouteGroup:set_hidden(true, nil)` with `g:set_active(false, nil)`.
- **Deactivating a track:** `route:set_active(false, nil)`.
- **Panning:** `route:pan_azimuth_control():set_value(x)`, with 0 = left and 1 = right (the inverse of MCP).
- **Port connections:** not readable from Lua. Use `pw-link -l` on the host.

**Importing.** "Import to track" lands on the *selected* track, so `arrangement.import_files`
selects the track through MCP first and moves regions that land elsewhere. MIDI regions are in
beat time: notes keep their beats across tempo changes. CCs in an imported SMF become automation
lanes that interpolate between points, so use hold points for steps.

**Rendering.** Export through `Session:simple_export()` with a preset. The 32-bit float,
unnormalised preset is `b0d5c3a1-7e2f-4c9a-9a51-5f0a3c8e1d42`. Use
`sessions/nm/work/renderlib.py`:
- `render(folder, name, start, end)`: release-all, a 1 s warm-up export (loads the MIDI), release-all, the export, release-all.
- `export(...)` parks the playhead at `start` first.
- `leftover(folder)` renders 2 s past the end of every region. It should come back around -150 dBFS; anything louder means a voice is stuck.
- Share files are mastered by the `share*.py` scripts: -14 LUFS, true peak at -1 dBTP, 24-bit FLAC plus 320k MP3.

### Stuck MIDI notes: four causes, all handled

1. `MidiTrack:write_immediate_event` (used for panic and program changes) is only delivered
   **while the transport rolls**. `renderlib.deliver()` mutes the master, rolls past the end of
   the song, sends, stops and unmutes.
2. Ardour **chases a note the playhead is parked inside**. An export that starts elsewhere then
   begins with that note stuck on. Park the playhead at the export start, which `export()` does.
3. **Same-pitch overlapping notes** on one channel make ambiguous note-on pairs. Never write them;
   `untangle()` in the generator scripts trims them.
4. Every generated MIDI file starts with CC64/123/120 resets on all 16 channels.

Also: `ARDOUR.config():set_midi_input_follows_selection(false)` is set, so the Oxygen keyboard
reaches only the track it's wired to (the piano). With it on, selecting tracks while importing
fed a held key into every track.

## Instruments and sounds

- **ACE Fluid Synth** plus SoundFonts in `instruments/`, with LV2 presets in `~/.lv2/sound-*.lv2`:
  - **"Salamander Grand Piano"**.
  - **"GeneralUser GS"**: the full GM set, chosen by program change in the MIDI file. Examples: 41 viola, 42 cello, 44 tremolo strings, 45 pizzicato, 47 timpani, 48/49 strings, 52 choir, 89 warm pad, 119 reverse cymbal. The GM kit is on channel 10.
- **Lua DSP instruments** (`src/sound/ardour_scripts/`). Ardour scans them only at startup:
  - **Sub 808:** a mono sine with saturation. A note that overlaps the previous one slides into it (the 808 glide). Channel 10 pads map to a scale. Parameters: Root, Scale, First pad, Decay (up to 30 s), Release, Punch, Glide, Drive, Gain, and **Punch time**, added last so old sessions keep their parameter indices.
  - **Ratchet Kit:** a synthesized kit on GM notes: 36 kick, 37 snap, 38 snare, 39 clap, 42 closed hat, 44 pedal hat, 46 open hat.
  - **Trance Synth** and **Wub Bass.**
- **Plugins:** Dragonfly Hall reverb (the "reverb" bus that `add_reverb` sends to), ACE EQ
  (`urn:ardour:a-eq`, with labels like "Frequency H" and "Gain H"), Guitarix, x42 (tuner, limiter,
  EQ), AVLDrums, Surge XT, Odin 2.
- **Hardware:**
  - **MIDI keyboard:** M-Audio Oxygen Pro 49 ("Oxygen Pro 49 USB MIDI").
  - **USB guitar adapter:** Unitek/C-Media.
  - **Vocal mic:** Logitech C270 webcam ("Webcam C270 Mono:capture_MONO").

## Strudel

[Strudel](https://strudel.cc) (live-coded patterns, a JavaScript TidalCycles) is embedded in the web app via
`@strudel/repl` 1.3 (licence AGPL-3.0-or-later: fine for local use; matters if the app is ever distributed).
Its editor preloads the standard samples (Dirt-Samples: `bd sd cp hh oh`…, the drum machines, soundfonts), so
generated code needs no `samples()` call except for the performer's own kit, which the server serves at
`/api/beat/kit` (relative URLs, so it works through the Vite dev proxy too). Verified in headless Chromium
(Playwright, in the system Python): the tab loads, code appears, Play starts, no page errors.
**Never generate `.room()`**: in the embedded editor Strudel's reverb silenced the voice once and, measured again on
2026-10-26, did nothing at all. `distort` (after the filter) was inaudible at .35; `shape` (before the filter) is the
drive that works (+5 dB, so lower `gain` with it).
The "mouth" voicing constants (`BRIGHT_SCALE`, `OSC`, `KICK`, gains in `beatbox.py`) were calibrated by
recording what Strudel actually plays (Playwright: hook `AudioNode.prototype.connect` into a
`MediaStreamDestination` + `MediaRecorder`, press Play) and comparing it with the beatbox per 16th: notes,
loudness shape, brightness shape, tone per band, and peak level (keep it below 0 dBFS). The "synth" voicing
(`SYNTH`, `SYNTH_DELAY`, `SYNTH_KICK`, `SYNTH_SUB`, `DUCK`) is an interpretation, not a match, but its timing is measured: with a
12.5 kHz tick added on each bar line, each bwom of the render hits within a 64th of its 16th and fades ~15 dB over
half a bwom, as the beatbox's do (ducking the synth too made the bwom on beat 3 swell in late). Measure levels
from raw samples (a ScriptProcessor tap), not the MediaRecorder's Opus: its decode soft-clips and overshoots.
Peak about -1.3 to -1.7 dBFS.
The song form was checked the same way (a tick on every bar line, `form=song`, a 222 s raw capture): per-bar RMS of
the kick (40-90 Hz), riff (150-3000) and top (3-12 kHz) bands follow the plan - kick gone in the breakdown, risers
rising, both drops at full level, the pull and the one-hit ending where they should be. `arrange()` loops after the
last bar (Strudel patterns always loop). Known nit: the riff's `.late(2/16)` note crossing a section boundary is
dropped, a 0.26 s gap in the riff at the start of each section.
Rendering a song to a file is done with the Playwright harness (system python + Chromium, not in the uv env): it
records Strudel's raw output while the Beat tab plays, trims to the 104 bars, and the WAV/MP3 go to
`recordings/tracks/` (the **Tracks** group). Strudel can't render faster than real time (no OfflineAudioContext),
so a 3:35 song takes 3:35 to render.
Current beatbox: `recordings/beatbox/bbox.mp3` → 115.8 BPM, A#, a beatboxed oscillator riff
(A# … B G# A C#) with a boom on every beat and bwoms on 16ths 3 6 9 12 15 (3-3-3-3-4), 5 bars kept, loop =
bars 1–2; the "mouth" render matches 94% of its notes. Outputs `bbox beat.strudel.js`, `bbox beat.mid`, `bbox kit/`.

## Sessions

`sessions/<name>/` holds the Ardour session, its snapshots and an `export/` folder.

- `cinematic`: a game main-menu theme. Snapshots `menu loop`, `menu loop A (fixes)`,
  `menu loop B (hook first)`. Exports in `export/menu` (intro plus a seamless loop, with
  phone-speaker bass harmonics added), `export/share` and `export/compare`.
- `friday`, `mustard`, `sober`, `trance`: earlier sketches (vocal takes, 808 pads, a drum
  groove, trance).
- **`nm`**: the current project, below.

## Current project: NM remix

The user's own song, `/home/cheese/Downloads/NM.mp3`, rebuilt as a remix in Ardour for them to
sing revised lyrics over.

**Analysis**
- **Key and loop:** D♯ minor. A four-chord loop, one chord per bar: **D♯m | C♯ (voiced F♯/C♯) | C (with F♯, A♯) | Bmaj7**. It starts at session bar 2, and again at bar 59.
- **Tempo:** 81.000 BPM in 4/4 up to the break (bar 1 at 0.250 s), then 74.005 BPM from 166.779 s.
- **Drums:** kick on 1, the "and" of 3 and the "e" of 4; snare on 2 and 4 with a ghost note.
- **808:** re-hits with the kick and bends down about 2 semitones on each note (Sub 808: Punch 3–4, Punch time 80–95 ms).

**Session tempo map**
- Bar 1 is a count-in.
- 81 BPM until session beat 228.
- Bar 58 is a single 1/4 bar at 99.52 BPM, covering the break gap.
- 74.005 BPM from bar 59 (session beat 229).
- The original audio sits at 2.713 s and lines up throughout.

**Form** (session bars):
- intro 2–5
- verse 1: 6–17 (soft 6–9, build 14–17)
- verse 2 / drop: 18–33 (the 808 enters at 18)
- chorus: 34–53 (the vocal is higher and louder)
- tail 54–55, then silence
- interlude 59–66 (the vocal returns at 63)
- final 67–74, climax 75–77
- outro 78–83

**Ardour (`sessions/nm`)**
- **Snapshots:**
  - `nm`: current, remix v3.
  - `nm - remix v2`.
  - `nm - remix v1`.
  - `nm - duet (transcribed)`.
  - `nm - band version`.
- **Tracks:**
  - `piano`.
  - `cello`: channel 1 arco (42), channel 2 pizzicato (45).
  - `viola`: channel 1 viola (41), channel 2 tremolo (44).
  - `strings` (49).
  - `pad` (89).
  - `perc`: channel 10 GM kit, channel 2 timpani (47), channel 3 reverse cymbal (119).
  - `drums`: Ratchet Kit.
  - `808`: Sub 808.
  - `vocal`: audio, on the webcam mic, empty.
  - `reverb` bus.
  - **Guides:** `original vocal`, `original mix` and `melody guide` are deactivated and hidden in route group "not in this mix". The `share*.py` scripts reactivate `original vocal` for the reference render.

**Work folder: `sessions/nm/work/`** (plain scripts; run from the repo root with `PYTHONPATH=sessions/nm/work`)
- **Grid and time:** `tmap.py` converts original time ↔ session beats (`beat()`, `secs()`, `bar_of()`, `bar_start()`, `TEMPI`, `OFFSET`). Inputs: `drums.json`, `bars.json`, `sectionB.json`.
- **Transcription** (done; outputs saved):
  - `separate.py`: Demucs → `stems/`.
  - `transcribe.py`: Basic Pitch → `notes-*.json`.
  - `drum_nmf.py`, `drum_hits.py`: drum hits from NMF.
  - `parts.py`: builds `parts.json` (piano, pad, 808, drums, melody guide in session beats).
  - Both separation and transcription need an ML virtualenv:
    `uv venv --python 3.11 mlenv && VIRTUAL_ENV=mlenv uv pip install demucs "basic-pitch[onnx]" "setuptools<80"`,
    then uninstall tensorflow so Basic Pitch uses ONNX.
- **Arrangements**, each writing a `parts_*.json`:
  - `interplay.py`: band version.
  - `duet.py`: transcribed duet.
  - `remix.py`: v1.
  - `remix2.py`: v2.
  - **`remix3.py`**: v3, current. Arranged section by section: build-ups, drops, chorus lift, call and response, kick-ducking via CC11, humanized timing.
- **Load into Ardour:**
  - `nm_session.py`: instrument table, `set_params`, `mix`, `clear`.
  - `build_remix3.py`: saves a snapshot, adds the perc track, imports every part with its programs and CCs, sets faders and pans.
- **Check:**
  - `scan.py`: semitone clashes between parts.
  - `check_all.py`: solo renders.
  - `check_music.py`: timing and harmony against the original's stems.
  - `balance.py`: each part relative to the mix, per section.
  - `energy.py`: the loudness, lows and highs arc per section.
- **Share:** `share_remix3.py` etc. render, master and encode into `sessions/nm/export/share/`.
- **One shot for v3:** `bash sessions/nm/work/run_remix3.sh` (generate, scan, load into Ardour, energy arc). Ardour must have `nm` open.

**Latest results (v3)**
- No semitone clashes and no stuck notes.
- Energy arc in LUFS: intro -24, verse 1 -27, build -21, drop -18, chorus -16, interlude -24, final -17, climax -15 (the peak), outro -26.
- Share files: `sessions/nm/export/share/NM (remix v3) - instrumental.{flac,mp3}` and `… - with original vocal (reference)`.

**Open threads**
- The user hasn't heard v3 yet.
- They called v2 "generic". v3 answers that with arrangement (build-ups, drops, interplay, development).
- The GM string samples are the weakest sound. A next step could be a better orchestral library, such as VSCO 2 CE or Sonatina through sfizz.
- The chorus lift is modest, about 1 dB over verse 2 before the vocal.
- Revised vocals haven't been recorded yet (the `vocal` track is ready on the webcam mic).
