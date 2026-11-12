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
| `beatbox.py` | beatbox recording → beat: `groove(path)` (onsets, k-means into kick / snare / hat by low-end share and brightness, tempo fit on a 16th grid, dynamic beat tracking anchored on the kicks, bars of 16 steps, bars without a steady kick skipped; the pitched "oscillator" part via HPSS + pyin per 16th: note, level, brightness, and hits in its band reclassified as `"voice"` articulations; `Groove.oscillator` = it sounds through the bars and between the kicks), `loop()` (with a riff: the run of bars where it came through clearly), `events()` (the riff as notes: a new note at a pitch change or an accent, each with gain, start cutoff and how far the filter opens), `pulse()` (the steps where the bwoms recur bar after bar), `riff_events()` (the riff on that pulse: a note per bwom at the pitch sung there, early arrivals snapped, pickups kept), `strudel(style=)` (with a riff, `"synth"` (default): the riff on one of `BASSES` (`bass=`: `saw` = a driven saw plucked by its filter - `lpf` 450-560 with a 2.2-octave `lpenv` opening in 6 ms and falling back in 170 ms (the 6 ms takes the edge off the first periods; nowhere near the rejected 150 ms wow), `lpq` 2, `shape(.2)`, sustain .3 so each bwom hits then sinks (default); `square` the same on a square; `fm`; `synth` = GM Synth Bass 2; `bright` = GM Synth Bass 1; `finger` = GM electric bass; Dirt's `jvbass` is silent in the embedded editor, so it isn't offered), a note per bwom with velocities from the performance's accents, a mild `lpf(perlin.range().slow(4))` drift, dry (no delay under the kick); no filter opening *after* a hit - a saw doing that was tried first and the user called it a fart (measured: brightness rising half an octave right after each hit; a pluck's falls), the pattern cut from the first bwom (`_rotate`) and moved back with `.late()` so the last bwom rings over the bar line; sine sub on orbit 2 (`SYNTH_SUB` .5: the 909 has nothing under 60 Hz, so the sub carries the bottom); the kick = a 909 `bd` stacked with a sine `sbd` in the key sweeping down an octave (`BOOM`, gain .2) for weight, `duckorbit(2)` ducking only the sub; `"mouth"`: supersaw with per-note velocity/lpf/lpenv copied from the mouth, sine sub, `sbd` boom tuned to the root, the performer's own kick layered; without a riff: drum-machine kicks/snares/hats; an open hat and a clap commented in all), `midi_bytes()`/`write_midi()` (drums ch10, riff ch1, sub ch2; same `style`), `pieces()` (the instruments as `Piece(name, about, code, loop, frame)` plus a kick-for-any-pattern function; `strudel()` stacks the `loop` ones and comments the song-only ones (stabs, pad, hook); `frame="riff"` marks what is cut from the first bwom), harmony from the riff: `chord(note, root)` = the triad over a bass note in the natural minor with the Phrygian 2nd (`TRIADS`; passing notes get none), voiced within the octave up from the root at or above F3 (`CHORD_LOW`), `harmony(events, root)` the chords per bar as merged runs (pickups keep the chord before them), `step_chords()` the chord on each 16th; `song()` (the beat as a 112-bar house/techno track at `TEMPO` = 124 BPM (the loop keeps the take's tempo): pieces as `const`s - the beat's plus `stabs` (supersaw chords on the off-beat 8ths, each the chord over the bass note under it, so they never clash with the riff), `pad` (the same chords held, 5 detuned saws, low-passed) and `hook` (the riff two octaves up on supersaw, a dotted-8th delay on orbit 4, `lpf(perlin)`) - variations for the form (lead `Low`/`Mid`/`Rise` opening the filter and gain, `Echo`/`Echo2` a dub echo on orbit 3 opening upward; `padLow`/`padOpen`/`padSwell`; `stabsOpen` (lpf 1500→6000 over 8 bars); `hookAnswer` (bars 7-8 of 8); `padPeak` (gain .28, lpf 900→2200) and lead `Open` (lpf perlin 600-900) for the peak; `hatsBreak` (hats out for bar 8 of 8); `subEnd` (the sub's root on the beat frame, so the final hit lands with the kick - the shifted frame put it 2 16ths late); `boom` (a tuned sine boom under the 909, `BOOM`); `kickHole` (no kick on beat 4 of every 8th bar); `kickReturn` (8th-note roll over the last 2 bars of 8, .35 then .45); `ride` on the bwoms; `shaker` (Dirt `sh` 16ths, off-beat accents, `lpf(7000)`, `degradeBy(.1)` - defined but not in the form: a 10 kHz tick, inaudible under the hats at ×3 gain); `ohat`; `riser` (pink noise, `hpf` 800 sweeping up 2 octaves, gain swelling over the whole 8 bars; white noise read as TV static); `rcrash` (`s("cr").speed(-1)` from 16th 10 so its swell peaks on the downbeat; no forward crash: read as a double hit); `preload` (every sample at gain .001 in bar 1: superdough loads samples lazily and drops their first hit)), `pull`/`end` helpers, and the form as *two* `arrange()`s from the `SONG` plan ((bars, title, layers), `("pull", [...])`/`("end", [...])` groups): the beat frame, and the riff frame (riff, sub, pad, hook and their variations) shifted `.late(2/16)` as a whole - shifting the pieces individually had `arrange` cutting each section's last note at the section line (a 2-16th hole in the bass at every section start); a snare on 2 and 4, tom fills, crashes every 8 bars, a rimshot, a 5/16 delay throw and a static 5th-chord stab (it held A# against the riff's B) were tried and removed. Humanized: hats swung (`SWING` = .006 cycles ≈ 12 ms on the off 16ths), `velocity(perlin)` on hats and stabs, `degradeBy` on hats and shaker. `HATS` (`hats=`: `house` = 909 16ths accented on the off-beat 8ths (.26), then the bwoms (.19), kicks (.15), rest (.1), swung - the default; `pulse` = 808 tick accented on the bwoms; `offbeat` = 909 on the off-beat 8ths) play from the drop on: the user wants nothing ticking and no claps before a drop), `kit_sound()`/`kit()` |
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
**Reverb works; the earlier "never generate `.room()`" finding (2026-10-26: silenced the voice once, then did nothing) was an
artefact of the capture harness.** superdough renders each orbit's impulse response in an `OfflineAudioContext`; the
harness hooked `AudioNode.prototype.connect` into *every* `AudioDestinationNode`, so it tapped (and sometimes picked as
"the" context) that offline render - a few seconds of IR noise, or nothing. With the hook skipping
`OfflineAudioContext` (2026-10-27) a 909 hat with `.room(.6).roomsize(3)` leaves a tail 20 dB under the hit at 150-400 ms
and -51 dB at 400-900 ms (dry: -89 / silence); `.room(.9).roomsize(6).roomfade(2).roomlp(8000)` a long tail (-38 dB at
400-900 ms); a saw the same; the first use works (no lazy-init loss), and `.orbit()` just picks the bus (reverb, like
delay and ducking, is per orbit: `roomsize` is shared by the voices on a bus). The user's browser was never affected.
`distort` (after the filter) was inaudible at .35; `shape` (before the filter) is the drive that works (+5 dB, so lower
`gain` with it). Ducking: one `duckorbit` per sound (the `"2:1:4"` string the docs mention silenced the 909 here - measured, not a theory); several
buses are ducked with silent "ghost" copies of the kick (`gain(.001).duckorbit(n)`), each with its own depth/attack; `duckdepth` d gives a
gain of 1 - sqrt(d) (.6 = -13 dB, .4 = -9, .2 = -5), recovering over `duckattack`. **Orbit 1 is superdough's default bus**: a sound
without `.orbit()` is on it, so `duckorbit(1)` ducks the kick and bass themselves (the 909 dropped 6 dB); the song puts nothing on
orbit 1 on purpose (2 sub, 3 the breakdown echo, 4 stabs+hook, 5 the kit, 6 the pad). Reverb (`room` send per sound; `roomsize`,
`roomlp` per orbit) and delay (`delay` send per sound; time/feedback per orbit) share those buses. Continuous signals
(`saw`, `perlin`, `sine`...) are sampled once at each event's onset: on the riser's single 8-bar event `pan(saw.range(.35, .65).slow(8))`
sat at .35 for all 8 bars (measured L-R +3.6 dB, constant), so it is `pan(.5)`; the filter sweeps work because the riser's
own `hpattack`/`attack` are envelopes, and the riff's `lpf(saw...)` sweeps because the riff has a new event every bwom.
The "mouth" voicing constants (`BRIGHT_SCALE`, `OSC`, `KICK`, gains in `beatbox.py`) were calibrated by
recording what Strudel actually plays (Playwright: hook `AudioNode.prototype.connect` into a
`MediaStreamDestination` + `MediaRecorder`, press Play) and comparing it with the beatbox per 16th: notes,
loudness shape, brightness shape, tone per band, and peak level (keep it below 0 dBFS). The "synth" voicing
(`SYNTH`, `SYNTH_DELAY`, `SYNTH_KICK`, `SYNTH_SUB`, `DUCK`) is an interpretation, not a match, but its timing is measured: with a
12.5 kHz tick added on each bar line, each bwom of the render hits within a 64th of its 16th and fades ~15 dB over
half a bwom, as the beatbox's do (ducking the synth too made the bwom on beat 3 swell in late). Measure levels
from raw samples (a ScriptProcessor tap), not the MediaRecorder's Opus: its decode soft-clips and overshoots.
Peak about -1.3 to -1.7 dBFS.
The song form was checked the same way (sections rendered through the harness with short `SONG` overrides, then the
whole track; grids from the first sound at the exact tempo, since the 12.5 kHz tick drowns under a loud drop): per-bar
RMS per band, the 1-4 kHz level against the kick band, the spectral centroid, per-16th levels (hats absent before the
drop, present from it; the bass ringing over section lines), hat timing against the 16th grid (the swing), the riser's
climb per bar, the peak. `arrange()` loops after the last bar (Strudel patterns always loop).
Rendering a song to a file is done with the Playwright harness `tools/render_strudel.py` (system python + Chromium, not
the uv env; the server must be up): it records Strudel's raw output **in stereo** while the Beat tab plays (the first,
scratchpad-only harness averaged L and R into one channel, so the Tracks files up to v2 on 2026-10-27 were mono while the app
played stereo - measure the peak on each channel). The page runs more than one AudioContext, so the tap records each and keeps
the one that carried the music (otherwise silent 16384-sample blocks interleave with the sound); headless Chromium's
context runs at 48 kHz. `python3 tools/render_strudel.py OUT.wav --seconds 222 --code song.js --bars 112 --bpm 124`, with song.js
= the `code` field of `/api/beat?path=beatbox/bbox.mp3&form=song` (the code generated *with* the kit URL, as the Beat tab's is, so the
performer's own boom `own` is in the render - verified: the `bb_kick` sample loads through `/api/beat/kit` headless; the
CLI's `bbox song.strudel.js` has no kit, since that URL only resolves inside the app), trims to the 112 bars, and the WAV/MP3 go to `recordings/tracks/` (the **Tracks** group; the mp3 in
`mp3/` so the library lists one track). Strudel can't render faster than real time (no OfflineAudioContext), so a 3:36
song takes 3:36 to render. `recordings/tracks/versions/` keeps earlier renders (v1 2026-10-27: the 115.8 BPM version
with the GM bass, rim, throw and static stab, which the user called "fucked up"; its measured faults: 95% of the drop's
energy under 400 Hz (power-weighted centroid 212 Hz in the drop; v2's is 388-484 Hz - amplitude-weighted figures
run ~3.5 kHz and aren't comparable), a stab clashing with the bass once a bar, an inaudible riser, no variation bar
to bar). The saw bass measured alone: 6-8.5 dB bwom-to-floor pump (v1's GM bass: 2 dB; the mouth's: 12), brightness
falling ~0.1 octave after each hit (no wow).
**The mix (v3, 2026-10-27 - glue, not new instruments):** buses as above; the kick's three ghost copies duck the pad (orbit 6) and
stabs+hook (orbit 4) at depth .4 and the kit (5) at .2 (measured on a sustained tone: -4.8, -4.8 and -2 dB averaged over the
first 80 ms; the sub's own duck .6 reads -8), with the kick stack itself unchanged at -4.7 dBFS. Stabs and hook share one
dotted-8th echo (sends .12 / .25, feedback .4) and one room (`room .3`, `roomsize 1.5`, `roomlp 5000`); the pad (`room .2`)
and the open hat, ride and reversed crash (`room .3`) have their own; the kick, sub, bass and 16th hats are dry. The stabs
got a tail (`sustain .08`, `release .12`): between hits they now sit 12-20 dB under the hit instead of digital silence. Drive:
`shape .15` on the stabs, `.2` on the hook, `.1` on the pad, gains scaled down to match. The hook is the bass's own voice two
octaves up (driven saw, filter pluck 1800-2400 Hz with a 2-octave envelope). Filters: stabs `hpf 250`, pad `hpf 320`, hook
`hpf 300`, kit `hpf 250` and `lpf 10000-12000`, riser `lpf 9000`. Stereo: supersaw stabs spread by themselves (L/R
correlation .77), hook `pan .58`, open hat `.4`, ride `.62`, reversed crash `.55`, riser centred; kick, sub, bass
and hats centred (kick correlation .999). Kick `speed .95` (its tail on D#2, a 4th over the A#1 sub), boom `decay .18`, `own`
`lpf 6000`. Mix blocks measured in stereo: drop 1 peaks -3.6 dBFS, drop 2 and the peak block -2.8; stabs 8-10 dB under the
kick per hit; the 60-150 band -19.2, 1-4k -31, 4-12k -32 at the peak.
**v5 (2026-10-29 - the kick):** the user heard the kick as "something rattling on a table". Measured by rendering the layers
alone through the harness: the 909 + sine boom has 1-4 kHz 52 dB under its total; the performer's own boom (`own`, the `bb_kick`
sample cut from the mp3) has it 15 dB under, eight envelope bumps 6-13 dB deep in its first 50 ms, and a second knock 150 ms
after every hit (the 250 ms cut takes in the next mouth sound); stacked, the kick's 1-4 kHz rose 26 dB. Fix: `pieces()` no longer
adds `own` in the synth style (the "mouth" style still layers it; the SONG plan still names it and `resolve` skips what isn't
there). v5's intro kick measures as the 909 + boom alone (1-4 kHz -52 dB, 150-400 -19, 78 ms to -30 dB; v4: -26, -14, 49 ms);
whole-track band levels within 0.2 dB of v4, peak -1.7 dBFS. `versions/v5 2026-10-29/bbox song.strudel.js` is the code as rendered
(with the kit line), not the CLI's. The NM remix v4 instrumental is hardlinked into `recordings/tracks/` so the Track tab can play
it; `?open=<library path>` on the web app selects a file and its tab, `&play` starts it (README).
**Synth stabs (2026-10-29, a trial, not in `beatbox.py`):** the user hears the off-beat chord stabs as "the piano" and asked for a synth.
`recordings/tracks/bbox song (synth stabs, 20s sample).wav` is bars 87-96 (the pull into the peak, and the peak) with the stabs as
`s("supersaw").unison(5).detune(.3).shape(.2)`, `.attack(.015).decay(.3).sustain(.45).release(.22).clip(2.2)`,
`.lpf(600).lpenv(3.2).lpattack(.004).lpdecay(.2).lpsustain(.35).lpq(6)`, gain .3 (same notes, placement, echo, room, bus), and
`stabsOpen = stabs.lpf(saw.range(500, 1600).slow(8))`; the code as rendered sits next to it (`.strudel.js`). Measured against the
same bars with the old stabs: 400-4 kHz on the stab 16ths +0.8 dB, kick band unchanged, peak -2.1 dBFS; alone, the old stab is
20-30 dB down 200 ms after the hit, the synth 9-13 dB. Rendering a section: the song's final `stack(...)` as `const songAll`, then
`arrange([1, preload], [10, songAll.early(86)])` (a silent bar to load the samples, then bars 87-96), recorded raw and cut from the
first loud sample (the kick) - the harness's own trim catches the preload. **While Ardour exports** (the NM session's renders) PipeWire
freewheels and the browser's audio clock runs away: holes of digital silence, or a short file. Launching Chromium with
`--disable-audio-output` (a timer-paced fake output, 44.1 kHz) makes the render immune; the full v5 render was made between exports.
The 20 s sample was also imported into the open NM session (the user asked for it in Ardour while the NM session was in use):
track "bbox synth stabs (sample)" (route 24888), region at 5:00-5:20, after the NM session's end (4:13), with a marker; outside
the song-range exports. Remove it from `nm` when the user is done with it there.
Current beatbox: `recordings/beatbox/bbox.mp3` → 115.8 BPM (the song plays it at 124), A#, a beatboxed oscillator riff
(A# … B G# A C#) with a boom on every beat and bwoms on 16ths 3 6 9 12 15 (3-3-3-3-4), 5 bars kept, loop =
bars 1–2; the "mouth" render matches 94% of its notes. Outputs `bbox beat.strudel.js`, `bbox song.strudel.js`, `bbox beat.mid`, `bbox kit/`.

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
  - `nm`: current, remix v7 (2026-10-29: the piano back as a crossover with the synth, the "stuck / wrong tune" fixes).
  - `nm - remix v6` (v5 polished: mono-safe chorus, the drop the loudest moment).
  - `nm - remix v5` (v4 plus a sustained top line over the builds, the hook doubled, the intro soft).
  - `nm - remix v4` (the piano's part on an Odin2 synth, melodic build-up arpeggios).
  - `nm - remix v3`.
  - `nm - remix v2`.
  - `nm - remix v1`.
  - `nm - duet (transcribed)`.
  - `nm - band version`.
- **Tracks:**
  - `synth`: Odin2, the hook since v4 (patch `PATCH` in `work/synth_patch.py`, applied by parameter label; reverb send -14 dB;
    since v5 gain automation in Play: soft intro/outro, the arp under the voice).
  - `lead` (v5): Odin2 `LEAD`, the sustained top line over the two builds (send -12 dB; v7: Glide 0, release .3, Delay Wet
    automated to 0 through the two pre-drop cuts).
  - `hookL` / `hookR` (v5): Odin2 `DOUBLE_L`/`DOUBLE_R`, the drop hook doubled, detuned -4/+9 cents (v6; v5 -7/+7 beat in mono), panned .2/.8, no delay (send -14).
    Empty and muted in v7 (build flag `DOUBLES=1` brings them back).
  - `lhand` (v5): Odin2 `LHAND`, the piano's left hand, darker and quieter (send -18). Empty and muted in v7 (the left hand is the piano's again).
  - `piano`: Salamander; muted v4-v6, back in v7 as the hook's crossover partner (ACE Fluid Synth Output Level +7, fader +3
    with gain automation in Play, its ACE EQ -6 dB shelf at 2 kHz).
  - `cello`: channel 1 arco (42), channel 2 pizzicato (45).
  - `viola`: channel 1 viola (41), channel 2 tremolo (44).
  - `strings` (49; v7: 50 Synth Strings 1 - one sample zone, no vibrato).
  - `pad` (89).
  - `perc`: channel 10 GM kit, channel 2 timpani (47; no notes since v7), channel 3 reverse cymbal (119).
  - `drums`: Ratchet Kit.
  - `808`: Sub 808 (v7: Punch 2 st, Punch time 40 ms - `nm_session.SUB808_V7`).
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
  - `remix3.py`: v3. Arranged section by section: build-ups, drops, chorus lift, call and response, kick-ducking via CC11, humanized timing.
  - **`remix4.py`**: v4, current. v3 with the piano part renamed `synth` (no sustain-pedal CCs, no timing jitter on the
    riff - the synth's synced delay would flam; the left-hand roots became soft long 808 notes, v44, in 2-5, 54, 59-62,
    78-83, so nothing of the synth sits below the voice) and the build-ups made melodic, to the reviewer's spec:
    `arp_bar()` plays a wave (`WAVE8`/`WAVE16`) through the chord tones (`CHORD`) between a floor of A#4 (the voice is
    F3-F4 in build 1, G2-C#3 in build 2) and a per-bar ceiling: bars 10-11 F#5, 12-13 A#5 (8ths), 14 A#5, 15 D#6 (16ths),
    16 the figure doubled an octave up (top F#6), 17 beats 1-2 up to F#6; 63-64 F#5 (8ths), 65 A#5, 66 beats 1-3 F#6
    (16ths); velocity 46 -> 108 (velocity opens the synth's filter and lifts its level). Quiet hats under the builds (8ths,
    then 16ths). The cello/viola CC11 ramps capped (100/108), the bar-17 cello climb an octave higher (out of the vocal's
    range), the chorus/climax riff keeps v3's octave-up plus the riff itself 16 softer underneath (a layer, not a transposition),
    a real cut at bar 66 beat 4 (`cut_at`: synth, viola, pad), the drop's 808 pattern (three hits a bar) copied under
    67-70 (v3 had one held v60 note a bar there - the "fuller" final had the weaker low end), the CC11 pump extended to the
    builds and to the viola tremolo, the pad's swell capped at CC 100 under the second build. `scan.py` knows the `synth` key.
- **Load into Ardour:**
  - `nm_session.py`: instrument table, `set_params`, `mix`, `clear`.
  - `build_remix3.py`: saves a snapshot, adds the perc track, imports every part with its programs and CCs, sets faders and pans.
  - `build_remix4.py`: snapshot `nm - remix v3`, adds the `synth` track (Odin2 + reverb send) if missing, applies
    `synth_patch.PATCH`, imports every part, mutes the piano, faders/pans.
  - `synth_patch.py`: the Odin2 patch. **Odin2 via LV2 parameters:** all 259 inputs are normalized 0..1
    (`work/odin2_params.tsv`: labels, init values); the oscillator/filter *types*, unison count and mod-matrix routing are
    plugin state, not parameters, so a patch builds on the init patch: Osc1 analog saw -> Filter1 (a lowpass) -> Filter2 -> amp;
    Osc2/Osc3 are "none" (their volume changes nothing). Calibrated by rendering (`probe_synth.py NAME [--set "Label=v;..."]`
    renders a test phrase on the track alone and prints envelope, spectrum, velocity response, echo spacing, register):
    Filter1 Frequency .3 ~ 1 kHz cutoff on a D#5, .5 already open; Envelope Amount is bipolar around .5; Keyboard Follow .5
    keeps D#4..A#6 within 2 dB; Amplifier Velocity 1.0 = 5.4 dB between velocity 60 and 110; Delay Time .25/.30/.35 =
    0.34/0.45/0.57 s (.345 = the dotted 8th at 81 BPM).
  - `render_v4.py [--builds|--full]`: the two builds into their drops (mix and synth alone, per-bar level/centroid/150-400 Hz)
    and the full instrumental to `sessions/nm/export/v4/`.
  - **`remix5.py`** -> `parts_remix5.json`, `build_remix5.py` (snapshot `nm - remix v4`, the four new tracks, patches, parts,
    faders/pans, gain automation on `synth`), `render_v5.py` (-> `export/v5/`, the excerpt solo = synth + lead),
    `check_v4.py RENDER` (per-bar table on the session tempo map) and `check_v5.py RENDER [--ref OTHER[:share]]` (adds the
    vocal headroom per bar from the reviewer's `vocal_bars.json`, the hook's L/R correlation, bars 2-3, the bar-67 landing).
    `probe_synth.py NAME --track T --patch LEAD` auditions any patch dict on any track (it replaces that track's MIDI -
    rebuild afterwards); it also reads the held note's tuning in cents (for the doubles' detune).
  - **One shot for v5:** `uv run python work/remix5.py && uv run python work/scan.py parts_remix5.json && PYTHONPATH=work uv run python work/build_remix5.py`.
- **Check:**
  - `scan.py`: semitone clashes between parts.
  - `check_all.py`: solo renders.
  - `check_music.py`: timing and harmony against the original's stems.
  - `balance.py`: each part relative to the mix, per section.
  - `energy.py`: the loudness, lows and highs arc per section.
- **Share:** `share_remix3.py` etc. render, master and encode into `sessions/nm/export/share/`.
- **One shot for v3:** `bash sessions/nm/work/run_remix3.sh` (generate, scan, load into Ardour, energy arc). Ardour must have `nm` open.
- **One shot for v4:** `bash sessions/nm/work/run_remix4.sh` (same, with `remix4.py`/`build_remix4.py`).

**Latest results (v3)**
- No semitone clashes and no stuck notes.
- Energy arc in LUFS: intro -24, verse 1 -27, build -21, drop -18, chorus -16, interlude -24, final -17, climax -15 (the peak), outro -26.
- Share files: `sessions/nm/export/share/NM (remix v3) - instrumental.{flac,mp3}` and `… - with original vocal (reference)`.

**Latest results (v4, 2026-10-29)** - `sessions/nm/export/v4/NM remix v4 (instrumental).wav` (float, 262 s, peak -1.6 dBFS,
nothing ringing after the end; `check_v4.py RENDER` measures a render per bar on the session tempo map).
- No new semitone rubs (the three cello-A#3 / pad-B2 Bmaj7 rubs in bars 5, 62, 82 are v3's), no stuck notes.
- Build 1 per bar 10-17, LUFS: -24.7 -24.8 -22.2 -21.6 -19.8 -18.8 -15.8 -15.4 (bar 17 = two beats at -19 dB RMS, then
  the cut at -37); 85% roll-off 1660 -> 3809 Hz; >4 kHz -42.7 -> -29.5 dB; the drop (18) -15.5 LUFS. Build 2, 63-66:
  -26.1 -24.5 -19.1 -16.2, roll-off 2219 -> 3114, >4 kHz -43.9 -> -30.5; bar 66 beat 4 is a real cut (-35.8 vs -19.3), the
  final (67) -15.1. Downbeats: first 100 ms -13.6 (18) / -13.2 dB (67), sub 30-120 Hz -15.2 both (v3's final had one held
  808 note a bar; now the drop's pattern).
- Balance (part minus mix, LUFS): synth intro -3.9, verse -0.6 (the build figure), drop -5.6, interlude -3.4, final -6.3,
  outro -4.5; 808 drop -3.8, final -4.2. Synth fader +8 dB, 808 -12 (was -13), piano muted.
- Energy arc: intro -23.4, verse 1 -22.9, build -16.9, drop -16.5, chorus -14.0, interlude -22.0, build 2 -17.8, final -15.6,
  climax -13.6 (the peak), outro -24.7.
- Knowingly short of the reviewer's spec: bar 16's 250 Hz-2 kHz sits 1.7 dB over bar 18's (the octave layer); bar 66's
  150-400 Hz is 10 dB over bar 64's (v3's timpani roll + snare build + cello swell live there); the downbeats are 1.2-1.6 dB
  under its -12/-14 dB marks; the synth sits -5.6/-6.3 under the mix in the drops rather than -3..0 (raising the fader
  moves the mix with it: +1.5 dB fader gave +0.3 relative); the 85% roll-off dips at bars 11, 16 and 65 although >4 kHz rises
  bar over bar; the risers are still v3's 2-3-beat reverse cymbals (the GM sample can't be 2 bars long).
- The Odin2 patch (`synth_patch.py`): analog saw, Filter1 lowpass at .28 (~1 kHz) with the filter envelope fully open on
  the hit (decay .35, sustain .15), velocity -> cutoff 1.0 and -> level 1.0, keyboard follow .5, resonance .3, saturation .2;
  amp A .02 D .5 S .45 R .25; ping-pong delay at the dotted 8th (time .345, feedback .35, wet .3, highpass .35, ducking .3);
  chorus .5; Ardour reverb send -14 dB. Measured alone: D#4..A#6 within 2 dB, 3.9 dB between velocity 60 and 110, the
  pluck's brightness falls 1836 -> 935 Hz over 0.5 s, stabs decay 15 dB within 74 ms, held notes hold at -8 dB.

**v5 (2026-10-29, the reviewer's fix round on v4)** - `sessions/nm/export/v5/NM remix v5 (instrumental).wav` (262 s, peak -1.7 dBFS,
master trimmed -2 dB because the doubles + lead peaked the v4 mix at 0.0; all v5 numbers below are at that master, session origin).
Excerpts next to it: `build_1`/`build_2`/`hook` as `mix` and the synth tracks alone.
- The melodic gap the reviewer found: v4's arp was an arpeggiator wave with no tune. v5 adds `lead` (Odin2 `LEAD`: slow
  attack, sustain .8, the filter opening over each note and with velocity, glide .1, dotted-8th delay) playing the hook an
  octave up, held, over the builds: M's question (A#5 C#6 D#6~ | C#6 A#5 F#5~) then N's answer (A#5 C6 D#6 F#6~ | D#6~) over
  14-17, velocity 84/94/104/116 (the filter opens bar by bar), and N over 65-66 (M's C# would clash with the C chord there);
  cut with everything else at 17 beat 2 / 66 beat 4. Register 13-37 semitones above the voice in build 1, 33-48 in build 2.
- The arp sits under the voice: gain automation on `synth` (Play; dB vs its +8 fader): 2-5 -6, 6-9 -4, 10-13 -7, 14-15 -6,
  16 -3, 17 0, 54-55 -2, 59-62 -3, 63-64 -7, 65 -6, 66 -2, 78-83 -5. Arp fixes: a 3-tone chord (C#) reaches for the next
  tone instead of repeating one (bars 11/64 now top at A#5), Bmaj7 bars start on D#5, the velocity ramp runs over the notes
  actually played, bar 17 one more step (v110-120). Per-bar tops 10-17: F#5 A#5 A#5 B5 C#6 C#6 F#6 F#6.
- The hook doubled in the drops/chorus/final/climax: `hookL`/`hookR` (Odin2 `DOUBLE_L/R`, Osc1 Finetune .43/.57 = -/+7 cents -
  Finetune is linear, 100 cents per unit, measured), panned .2/.8, fader +3 (-5 dB under the synth), no delay, same reverb send.
- The left hand on `lhand` (Odin2 `LHAND`: cutoff .18, gain .45, no delay/chorus, fader -6); M's question in the intro v46,
  roots v38-40. Bar-67 riser 2 beats (was 3: the GM reverse cymbal ended 0.8 s before the downbeat at 74 BPM).
- Measured (v5 | v3 share render where comparable): bars 2-3 LUFS -34.0/-35.5 | -31.5/-33.1 (v4 -26.8/-27.3); 30-120 Hz
  -57.0/-57.3 | -44.9/-53.2 (v4 -40.5/-37.8). Vocal headroom (the original vocal stem's per-bar RMS minus the instrumental's;
  + = voice on top): bars 10-17 +7.4 +7.7 +5.3 +6.7 +2.1 +1.1 -1.4 +0.7; 63-66 +10.7 +9.9 +0.6 -3.0. Arp alone, bars 10-13:
  -39.1 -37.8 -36.2 -34.7 (v4 -32.0 -31.3 -28.7 -27.9). Build 1 LUFS 10-17: -28.9 -28.9 -27.0 -27.0 -21.1 -20.3 -16.9 -14.8
  (17's beats 1-2 -19.3/-18.7 vs bar 16's mean -21.4: +2.4), roll-off 1240 -> 3732 Hz, >4 kHz -47.2 -> -30.4; build 2:
  -32.1 -30.2 -19.8 -16.2. Cuts: 18 -17.3 dB (beat before the cut -18.7 -> -36.0), 67 -13.3. Downbeats first 100 ms -15.4 (18)
  / -15.0 (67), sub -17.8/-17.4; bar 66 beat 4 -34.6, last 0.5 s before 67 -36.4 (v4 -40.5). Hook L/R correlation 300 Hz-4 kHz,
  bars 18-21: .696 (v4 .725; v3's stereo piano .395), synth+doubles alone .687. Energy arc: intro -26.6, build -18.0, drop
  -18.0, chorus -15.3, interlude -24.8, build 2 -17.8, final -17.0, climax -14.6, outro -28.5. No new rubs, no stuck notes.
- Still short of the reviewer's spec: LUFS flat 10->11 and 12->13 and +0.8 at 14->15; roll-off dips at 11 and 15 (the lead's
  sustained mids); the doubles moved the hook's L/R correlation only .725 -> .696; downbeats under its -12/-14 marks; the
  bar-67 riser is louder than v4's but the GM reverse cymbal stays quiet at perc -6.

**v6 (2026-10-29, polish on v5; same scripts - `remix5.py`/`build_remix5.py` carry the v6 values)** -
`sessions/nm/export/v6/NM remix v6 (instrumental).wav` (peak -1.2 dBFS, nothing ringing after the end; session origin,
master -2 dB) and `hook mix/synth.wav` (bars 18-21). Snapshot `nm - remix v5` kept.
- Mono beating of the hook (the reviewer's one artefact: 25 dB at 4 Hz on bar 18's held F#5 in the L+R sum, v5): the cause was
  two things, not one. The symmetric -7/+7-cent doubles null with the centre; de-symmetrizing to -4/+9 (Finetune .46/.59) alone
  changed nothing (31 dB), nor did dropping the doubles 9 dB (bar 20 still 29 dB): the pluck's own chorus at mix .5 notches its
  dry signal in mono (1.8 Hz, up to 25 dB). v6: chorus mix .2 on the synth (lead .25), no chorus on the doubles, doubles at
  -10/-9 dB under the hook (faders -3/-2 vs synth 7), 808 -11. Measured on the hook excerpt (synth tracks alone, L+R, 740 Hz
  band, held F#5): bar 18 11.4 dB, bar 20 12.5 dB peak-to-trough (v5 24.1 / 29.6) - and this metric includes the pluck's
  own ~8 dB decay inside the window. Reviewer's reading with the decay removed (linear fit): bar 18 8.3 dB, bar 20 11.1
  (v5 27.5 / 20.2, v4 7.2) - i.e. back to v4's level, the pluck's residual chorus, movement not tremolo; full-mix mono
  6-9 dB. The price: the hook is narrower (L/R correlation 300 Hz-4 kHz .84; v5 .70, v4 .73), and the narrowing is global
  (builds .89, chorus .83 vs ~.70 in v5) since the chorus cut hits the arp and lead too - width by detune/chorus and mono
  compatibility pull against each other with a centred lead. Reviewer's call: right trade for a track sung over and
  heard on phones. Real width without the null is plugin state, not a parameter: Osc1 unison voices (4-6, small
  spread) set once in the Odin2 GUI on `synth` and saved, then drop or hard-pan hookL/hookR.
- The drop is the loudest moment again: lead 16/17 v98/110, 66 v100 (was 104/116, 112); arp automation 16 -4, 66 -4; synth
  fader 7, lead -1. Bar LUFS: 16 -16.4 < 18 -16.2; 66 -16.4 < 67 -15.8; 17's two beats -17.6/-17.7 RMS. Downbeats
  loudness-matched to -14 LUFS: 18 first 100 ms -11.7 dB, sub -14.7; 67 -11.4, sub -14.2 (raw -13.9/-16.9, -13.6/-16.4).
  Reviewer: margin is only 0.1-0.2 dB because the chorus cut raised the synth/lead's effective level ~1 dB (bars 14-17
  +0.4 to +1.1 raw vs v5); for a real margin take bar 17's arp -2 dB in the automation (bar 17 is still at 0).
- Bar-67 riser 1.8 beats (1.46 s at 74 BPM = the GM reverse cymbal's length): the last 800 ms before the downbeat, per 100 ms,
  -26.3 -30.5 -33.2 -34.9 -36.6 -34.7 -34.9 -34.5 -> downbeat -13.6: no dip in the final 200 ms (v5 died 0.15 s early).
- Unchanged: vocal headroom 10-13 +7.1 +7.4 +5.0 +5.9, 14-17 +0.7 -0.2 -2.2 -0.7, 63-66 +9.8 +8.6 -0.4 -3.2; cuts 18 / 67
  intact (bar 17 beats 3-4 -35/-39, bar 66 beat 4 -31.6); no new rubs.

**v7 (2026-10-29, built - the piano/synth crossover and the reviewer's "stuck / wrong tune" fixes; `remix7.py` ->
`parts_remix7.json`, `build_remix7.py` (FLAGS = v7 as built; snapshot `nm - remix v6` kept), `render_v7.py`, `check_v7.py`)** -
`sessions/nm/export/v7/NM remix v7 (instrumental).wav` (262 s, peak -1.9 dBFS, -16.3 LUFS, nothing ringing after the end at
-161 dBFS; hard-linked into `recordings/tracks/`). Excerpts next to it: `hook` 18-21 as mix / hook (piano + synth) / piano /
synth / rest (the mix without them), `build_1` and `build_2` as mix / hook (+ lead), `intro`/`chorus`/`outro` piano and synth
alone, `808 solo`. Session origin, measured by `check_v7.py` (and `check_v5.py` for the v6 criteria).
- The crossover: the piano plays the hook as the synth's own events (soft parts piano-led, drops piano attack + synth,
  chorus/climax synth an octave up over the piano body). As prepared the piano measured 4-8.5 LU under the synth everywhere.
  Built: ACE Fluid Synth Output Level +7 on the piano (Ardour's fader and gain automation stop at +6.02 dB - the synth's
  "+7" has been +6.02 since v5), fader +3 with its own gain automation (intro -3, drops -1, tail/interlude -2, outro -1);
  synth automation intro -10, drops -2, tail -5, interlude -6, outro -10, bars 16/17/66 -5/-2/-5. Piano minus synth, each
  alone: intro +3.6, drop -0.1, chorus -1.5, outro +3.9 LU. Hook over the rest, 250 Hz-2 kHz, bars 18-21: +4.2 dB (`hook rest`
  rendered directly; mix-minus-hook power estimates are off by ~1 dB because the Odin2's drift makes renders non-coherent).
- Two crossover artefacts found and fixed: the Salamander samples start 13-15 ms before the hammer (a 16 ms flam on every
  shared note) - piano notes are written 15.5 ms early, onset spread now median 0.0 ms, p90 3.4 (drop) / 4.0 ms (chorus);
  and the Salamander sits 5-6 cents flat of A440 on the hook's notes - every Odin2 track is tuned -5 cents to it (Osc1 Finetune
  .45, flag SYNTH_TUNE): synth minus piano now +1.2..+2.6 cents (medians per section).
- The reviewer's seven: (1) lead Glide 0, release .3: 17/19 onsets within +/-10 cents of A440 at 20 ms, 19/19 of the -5-cent
  tuning, no scoops (v5/v6 7/19, scoops up to 97-113 cents over 60-80 ms; read on the fundamental and the 2nd harmonic - a
  40 ms window can't separate a neighbour two semitones away at the fundamental). (2) Sub 808 Punch 2 st / Punch time 40 ms
  (`nm_session.SUB808_V7`; the reviewer's 2/60 would still be +16 cents at 150 ms): +4.5..+5.1 cents at 150 ms (theory +4.7),
  sustain 0.0 +/-0.1. No -10-cent retune: the "+10 at sustain" was the reviewer's 60 ms autocorrelation tracker, which reads
  +9..+21 cents on an exact numpy port of sub808.lua at true 0 (the sine is exactly at pitch; no tune parameter, no pitch
  bend). The session's live 808 was Punch 4 / 80 ms / Drive 1 / Gain 0, not `SUB808` (.75 / -4) - v7 keeps Drive 1 / Gain 0.
  (3) Timpani roll dropped (snare build + riser carry bar 66). (4) `clear_barlines`: cello/viola/strings notes end at beat 3.5
  when the next chord is a semitone away (50 notes); the old note 0.15-0.5 s into the new bar vs its sustain: C#3->C -24.7 ->
  -26.5 dB, C3->B -17.2 -> -22.6, B2->D#m -22.8 -> -25.1 (the rest is reverb and 808 harmonics). (5) The lead ends at bar 17
  beat 2.5 / bar 66 beat 3.5 and its Delay Wet is automated to 0 from each cut to the drop: its note band in the cut's first
  0.2 s -34.2 -> -51.3 dB (17) and -28.2 -> -46.9 (66). (6) The GM viola/violin samples carry a 5-6.5 Hz vibrato of 9-19 cents
  SD (measured on the SF2 sample data) - the "layers at +/-20 cents" are vibrato sidebands; 41 is one sample zone and 40 adds a
  detuned strike layer, so the lines moved instead: lament an octave up, the chorus counter rewritten (unison with the hook 27%
  -> 2%), final/climax viola D#5 F#5 A#5 F#5 | D#6 F#6 A#5, strings on GM 50 Synth Strings 1 (one zone, no vibrato) with a
  moving top A#6 F#6 A#6 B6, the climax an A#6. Beating on the held hook notes they used to double: 17-35 dB at 6-12 Hz ->
  6-9 dB at 3-6 Hz; check_v5's chorus F#6 19.3 dB @ 7.3 Hz -> 5.6 @ 1.8. (7) Tom runs end on 47/47 (B2).
- Held against v6: vocal headroom 10-13 +7.0 +7.3 +5.0 +5.9, 14-17 +0.5 -0.7 -1.9 +0.2, 63-66 +9.2 +8.1 -0.1 -1.9 (bar 66
  +1.3 dB over v6: timpani and the lead's echo gone); cuts intact (the beat before 18 -38.8, before 67 -36.0 dB); the drop is the
  loudest bar (16 -16.7 < 18 -15.8, 66 -17.5 < 67 -15.7 LUFS); downbeats matched to -14 LUFS 18 -12.2 / sub -14.2, 67 -10.9 /
  -14.7 (v6 -11.7/-14.7, -11.4/-14.2); sections intro -26.1, build -17.4, drop -16.5, chorus -14.8, interlude -25.0, final -15.9,
  climax -14.7, outro -27.8; the same three Bmaj7 rubs as v6; hook L/R correlation .34 (v6 .84 - the piano's stereo image).
- `build_remix5.py` now resets what v7 sets beyond its patches (808, Finetune, the lead's Delay Wet and the piano's gain
  automation, the piano's level, lhand/hooks unmuted), so it still rebuilds v6; the `nm - remix v6` snapshot is the exact copy.
  The prepared `PIANO_EQ=0` toggle would have switched the piano off (ACE Fluid Synth has an "Enable" too); fixed.

**Open threads**
- v7 is built and measured (above); the user's listen is next (`/?open=tracks/NM%20remix%20v7%20(instrumental).wav`).
- Reviewer's verdict on v6 (2026-10-29): claims reproduce, 9/11 criteria pass, best of v4-v6, "ship it" for the user's
  listen. v4-v7 instrumentals are hard-linked into `recordings/tracks/` so the web app's Track tab plays them
  (`/?open=tracks/NM%20remix%20v6%20(instrumental).wav`). The user asked for the piano as a synth and a melodic
  build-up "as in Raise Your Weapons" (approach only - nothing of that track is quoted); their listen is next.
- Choices left to them: pluck vs a more sustained lead for the question/answer (one patch now), how early the arp starts
  (bar 10 now; could be 12 or 14), the chorus riff's octave-up over the piano body (v7), whether the hook doubles come back (`DOUBLES=1`), the left-hand roots on the synth
  (the Sub 808 barely follows velocity, so soft 808 roots lifted the intro 5 LUFS and were dropped).
- They called v2 "generic". v3 answers that with arrangement (build-ups, drops, interplay, development).
- The GM string samples are the weakest sound. A next step could be a better orchestral library, such as VSCO 2 CE or Sonatina through sfizz.
- The chorus lift is modest, about 1 dB over verse 2 before the vocal.
- Revised vocals haven't been recorded yet (the `vocal` track is ready on the webcam mic).

## bbox drop (2026-10-29): the bbox song straight into a drop on the NM piano riff, and a keyboard session for it

- **The piece**: 44 bars at 124 BPM, 1:28. 1-4 the boom (909 + the take's `bb_kick`) and the piano asking/answering (NM's M and N);
  5-8 bass in, the riff on the piano opening, a 4-bar riser, out two beats before the drop; 9-24 drop A, the theme = NM's
  RIFF_A (the user's own song, from `sessions/nm/work/remix7.py`) on Strudel's Salamander `piano` + a supersaw layer on
  orbit 7 (its own room/echo, a ghost-kick duck at .25); bass + sub on the bwom rhythm following NM's chords (D#m | F#/C# |
  C | Bmaj7, a bar each; the riff's harmony wins over the bbox's in the drop); 25-40 drop B on RIFF_B; 41-44 out.
- **Code**: `tools/sketches/bbox_drop_sketch.py SONG_JS OUT_JS --piano 1.3 --saw .17 --post .78` (SONG_JS = the song form's code
  from `/api/beat?...&form=song`; `--only drums|bass|chords|theme` for stems, `--align` adds the pre-roll + blip);
  `tools/sketches/bbox_drop_stems.py code|render|trim|verify|midi|all` is the stem pipeline; `tools/strudel_events.mjs` queries
  Strudel's events in Node (the MIDI is built from them). Mix `recordings/tracks/bbox drop.wav` (+ `.strudel.js`); stems + MIDI
  in `recordings/tracks/stems/bbox drop/` (sum of stems = mix within 0.33 dB per bar, offsets <= 3.7 samples).
- **Render Strudel one at a time.** Parallel headless renders made Chromium skip whole 128-frame chunks (everything after a skip
  lands early); per-bar RMS still matched, so only a stem-sum/onset check catches it. The pipeline checks every render.
- **Ardour session `sessions/bbox-drop`**: `bash sessions/bbox-drop/work/run_build.sh [--switch]` (idempotent; `--switch` saves
  the open session and restarts Ardour on bbox-drop), `play.py` rolls the loop (bars 9-41), `keys.py piano|"stab synth"|bass`
  arms what the keys play. Tracks: 4 stems + the mix (muted reference); MIDI tracks piano (ACE Fluid Synth, Salamander),
  stab synth and bass (Odin2 by parameter label; unison voices are plugin state - set once in the Odin2 window), kit (Ratchet
  Kit, channel 10 = the Oxygen's pads; no ride/crash in it). All four on the Oxygen Pro 49; the parts' MIDI regions muted.
  Session auto-input is off, so the armed track plays the keys while rolling.
- Build fixes on the first real run: adding Ratchet Kit took longer than the bridge's 30 s timeout (the track was made anyway;
  a rerun finishes the wiring); the auto-input Lua returned `x and false or nil` (= nil), now returns strings.
