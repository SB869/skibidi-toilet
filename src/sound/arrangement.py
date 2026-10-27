"""The session open in Ardour, in the library's terms: what's on which track and where, which
library file each region came from, and any region's audio.

Ardour copies imported audio into the session (one mono file per channel, e.g. "acapella%L.wav")
and doesn't remember where it came from, so imports through here record it in
<session>/sound-sources.json; anything else is matched to the library by file name.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import audiofile, bridge, daw, drums, midifile

SOURCES_FILE = "sound-sources.json"
TAKES_TRACK = "takes"

# Sent through the bridge: tracks, regions (in samples), the editor selection and the loop range.
SNAPSHOT_LUA = """
local out = { name = Session:name (), path = Session:path (), rate = Session:nominal_sample_rate (),
              playhead = Session:transport_sample (), tracks = {} }
for r in Session:get_routes ():iter () do
  local t = r:to_track ()
  if not t:isnil () then
    local regions = {}
    for reg in t:playlist ():region_list ():iter () do
      local files = {}
      for i = 0, 31 do  -- source(n) falls back to the first one past the last channel
        local fs = reg:source (i):to_filesource ()
        if fs:isnil () or fs:path () == files[1] then break end
        files[#files + 1] = fs:path ()
      end
      regions[#regions + 1] = { id = reg:to_stateful ():id ():to_s (), name = reg:name (),
        position = reg:position ():samples (), start = reg:start ():samples (),
        length = reg:length ():samples (), muted = reg:muted (), files = files }
    end
    out.tracks[#out.tracks + 1] = { id = r:to_stateful ():id ():to_s (), name = r:name (), regions = regions }
  end
end
local sel = Editor:get_selection ()
out.selected_regions = {}
for rv in sel.regions:regionlist ():iter () do
  out.selected_regions[#out.selected_regions + 1] = rv:to_stateful ():id ():to_s ()
end
if not sel.time:empty () then out.time_selection = { sel.time:start_sample (), sel.time:end_sample () } end
local loop = Session:locations ():auto_loop_location ()
if loop then out.loop = { loop:start ():samples (), loop:_end ():samples () } end
return out
"""


@dataclass
class Region:
    id: str
    name: str
    track: str
    position: float      # seconds on the timeline
    start: float         # seconds into its source files
    length: float
    files: list[Path]    # one per channel
    muted: bool = False

    @property
    def end(self) -> float:
        return self.position + self.length


@dataclass
class Snapshot:
    name: str
    path: Path
    rate: int
    playhead: float
    tracks: dict[str, list[Region]]                    # by track name, in Ardour's order
    selected: set[str] = field(default_factory=set)   # region ids
    time_selection: tuple[float, float] | None = None
    loop: tuple[float, float] | None = None

    def regions(self) -> list[Region]:
        return [r for regions in self.tracks.values() for r in regions]


def snapshot() -> Snapshot:
    raw = bridge.lua(SNAPSHOT_LUA)["returned"]
    rate = raw["rate"]
    s = lambda samples: samples / rate  # noqa: E731
    tracks = {t["name"]: [Region(r["id"], r["name"], t["name"], s(r["position"]), s(r["start"]), s(r["length"]),
                                 [Path(f) for f in r["files"]], bool(r.get("muted")))
                          for r in t["regions"]]
              for t in raw["tracks"]}
    pair = lambda v: (s(v[0]), s(v[1])) if v else None  # noqa: E731
    return Snapshot(raw["name"], Path(raw["path"]), rate, s(raw["playhead"]), tracks,
                    set(raw.get("selected_regions") or []), pair(raw.get("time_selection")), pair(raw.get("loop")))


def region_audio(region: Region) -> tuple[np.ndarray, int]:
    """The region's audio as Ardour plays it (before fades and gain), channels side by side."""
    channels, sr = [], None
    for f in region.files:
        audio, sr = audiofile.load(f)
        channels.append(audio.mean(axis=1))
    a, b = int(round(region.start * sr)), int(round((region.start + region.length) * sr))
    return np.stack([c[a:b] for c in channels], axis=1), sr


# ---------------------------------------------------------------- where regions came from

def _sources_file(session: Path) -> Path:
    return session / SOURCES_FILE


def remembered(session: Path) -> dict[str, str]:
    try:
        return json.loads(_sources_file(session).read_text())
    except (OSError, ValueError):
        return {}


def remember(session: Path, mapping: dict[str, str]) -> None:
    known = remembered(session)
    known.update(mapping)
    _sources_file(session).write_text(json.dumps(known, indent=1))


def _bare_stem(path: Path) -> str:
    """The name the file had before Ardour imported it: no %L/%R, no -1 for a second copy."""
    stem = re.sub(r"%[LR]$|-\d+$", "", path.stem)
    return re.sub(r"%[LR]$|-\d+$", "", stem)


def library_file(region: Region, session: Path, root: Path, prefer: str | None = None) -> Path | None:
    """The library file a region was imported from, if it is one."""
    if not region.files:
        return None
    origin = remembered(session).get(str(region.files[0]))
    if origin and Path(origin).is_file():
        return Path(origin)
    stem = _bare_stem(region.files[0])
    matches = [p for p in root.rglob("*") if p.is_file() and p.stem == stem]
    if prefer:
        matches.sort(key=lambda p: p.parent.name != prefer)
    return matches[0] if matches else None


# ---------------------------------------------------------------- importing

def import_files(files: list[str | Path], at: float = 0.0, track: str | None = None,
                 name: str | None = None) -> dict:
    """bridge.import_audio at a time in seconds, remembering which library file each new region
    came from. Returns the bridge's result plus the new regions."""
    before = {r.id for r in snapshot().regions()}
    rate = bridge.ping()["sample_rate"]
    if track:
        # Ardour imports "to track" onto the *selected* track (nowhere, if none is): select it first
        route = bridge.lua(f"local r = Session:route_by_name ({bridge.to_lua(track)}) "
                           "return r:isnil () and '' or r:to_stateful ():id ():to_s ()")["returned"]
        if route:
            daw.mcp_call("track_select", {"id": route})
    result = bridge.import_audio(files, at=round(at * rate), track=track, name=name)
    after = snapshot()
    new = [r for r in after.regions() if r.id not in before]
    if track and any(r.track != track for r in new):
        # Ardour imports "to track" onto the selected track rather than the one asked for:
        # move what landed elsewhere
        target = bridge.lua(f"return Session:route_by_name ({bridge.to_lua(track)}):to_stateful ():id ():to_s ()")["returned"]
        for r in new:
            if r.track != track:
                daw.mcp_call("region_move_to_sample", {"regionId": r.id, "trackId": target,
                                                       "sample": round(r.position * after.rate)})
        after = snapshot()
        new = [r for r in after.regions() if r.id not in before]
    paths = [Path(f).expanduser().resolve() for f in files]
    mapping = {}
    for region in new:
        origin = paths[0] if len(paths) == 1 else next((p for p in paths if p.stem == _bare_stem(region.files[0])), None)
        if origin and region.files:
            mapping[str(region.files[0])] = str(origin)
    if mapping:
        remember(after.path, mapping)
    result["regions"] = [{"id": r.id, "track": r.track, "position": r.position, "length": r.length} for r in new]
    return result


INSTRUMENT_LUA = """
local name, synth, want, preset, mask = {name}, {synth}, {controller}, {preset}, {mask}
local info
for p in ARDOUR.LuaAPI.list_plugins ():iter () do
  if p:is_instrument () and p.name:lower () == synth:lower () then info = p break end
end
if not info then error ("no instrument plugin called " .. synth, 0) end
if preset ~= "" then  -- check before making anything
  local probe = ARDOUR.LuaAPI.new_plugin (Session, info.unique_id, info.type, "")
  local pr = probe:to_insert ():plugin (0):preset_by_label (preset)
  if not pr or not pr.valid then error ("no preset called " .. preset .. " for " .. info.name, 0) end
end
local tl = Session:new_midi_track (ARDOUR.ChanCount (ARDOUR.DataType ("midi"), 1),
  ARDOUR.ChanCount (ARDOUR.DataType ("audio"), 2), true, info, nil, ARDOUR.RouteGroup (), 1, name,
  ARDOUR.PresentationInfo.max_order, ARDOUR.TrackMode.Normal, false, false)
local track = tl:front ()
if preset ~= "" then
  local plug = track:nth_plugin (0):to_insert ():plugin (0)
  plug:load_preset (plug:preset_by_label (preset))
end
local inputs = C.StringVector ()
Session:engine ():get_physical_inputs (ARDOUR.DataType ("midi"), inputs, 0, 0)
local connected = {{}}
for p in inputs:iter () do
  if want ~= "" and p:find (want, 1, true) then track:input ():nth (0):connect (p) connected[#connected + 1] = p end
end
if mask > 0 then  -- only these MIDI channels (e.g. 10: a controller's drum pads)
  track:to_track ():to_midi_track ():set_capture_channel_mode (ARDOUR.ChannelMode.FilterChannels, mask)
end
-- hear the keys and what's recorded on the track, armed or not
track:monitoring_control ():set_value (ARDOUR.MonitorChoice.MonitorCue, PBD.GroupControlDisposition.NoGroup)
return {{ id = track:to_stateful ():id ():to_s (), name = track:name (), synth = info.name, connected = connected,
          preset = preset ~= "" and preset or nil }}
"""

# A send from a track to a "reverb" bus (made the first time): Dragonfly Hall, fully wet, voiced
# for acoustic instruments (32 m hall, 2.2 s, 20 ms predelay keeps attacks clear, low cut 60 Hz,
# a darker tail, little modulation). Parameters are set by name: LuaAPI.set_processor_param
# counts control inputs only, not the plugin's port numbers.
REVERB_LUA = """
local track, db, busname = Session:route_by_name ({track}), {db}, {bus}
if track:isnil () then error ("no track called " .. {track}, 0) end
local bus = Session:route_by_name (busname)
local made = false
if bus:isnil () then
  bus = Session:new_audio_route (2, 2, ARDOUR.RouteGroup (), 1, busname, ARDOUR.PresentationInfo.Flag.AudioBus,
                                 ARDOUR.PresentationInfo.max_order):front ()
  local proc = ARDOUR.LuaAPI.new_plugin (Session, "https://github.com/michaelwillis/dragonfly-reverb", ARDOUR.PluginType.LV2, "")
  if proc:isnil () then error ("Dragonfly Hall Reverb isn't available", 0) end
  bus:add_processor_by_index (proc, 0, nil, true)
  local hall = {{ ["Dry Level"] = 0, ["Early Level"] = 25, ["Late Level"] = 75, ["Size"] = 32, ["Width"] = 100,
                 ["Predelay"] = 20, ["Diffuse"] = 85, ["Low Cut"] = 60, ["Low Cross"] = 500, ["Low Mult"] = 1.1,
                 ["High Cut"] = 9000, ["High Cross"] = 4500, ["High Mult"] = 0.6, ["Spin"] = 2.5, ["Wander"] = 15,
                 ["Decay"] = 2.2, ["Early Send"] = 20, ["Modulation"] = 8 }}
  local plug, k = proc:to_insert ():plugin (0), 0
  while true do
    local port, t = plug:nth_parameter (k, false)
    if not t[2] then break end
    local value = hall[plug:parameter_label (port)]
    if value ~= nil and plug:parameter_is_input (port) then ARDOUR.LuaAPI.set_processor_param (proc, k, value) end
    k = k + 1
  end
  made = true
end
local function sends ()
  local n = 0
  while not track:nth_send (n):isnil () do n = n + 1 end
  return n
end
local before = sends ()
track:add_aux_send (bus, track:main_outs ())
if sends () == before then error ("couldn't add a send from " .. track:name () .. " to " .. busname, 0) end
local send = track:nth_send (before):to_send ()
send:gain_control ():set_value (10 ^ (db / 20), PBD.GroupControlDisposition.NoGroup)
return {{ track = track:name (), bus = busname, bus_created = made, send_db = db }}
"""


def instruments() -> list[str]:
    """Instrument plugins Ardour has found (synths and samplers), by name."""
    code = ("local out, seen = {}, {} for p in ARDOUR.LuaAPI.list_plugins ():iter () do "
            "if p:is_instrument () and not seen[p.name] then seen[p.name] = true out[#out + 1] = p.name end end "
            "table.sort (out) return out")
    return bridge.lua(code)["returned"] or []


def midi_inputs() -> list[str]:
    """MIDI controllers' ports as Ardour sees them (through PipeWire's MIDI bridge)."""
    code = ("local v = C.StringVector () Session:engine ():get_physical_inputs (ARDOUR.DataType ('midi'), v, 0, 0) "
            "local out = {} for p in v:iter () do out[#out + 1] = p end return out")
    return bridge.lua(code)["returned"] or []


def add_instrument(name: str = "keys", synth: str = "Odin2", controller: str = "USB MIDI",
                   preset: str | None = None, channels: list[int] | None = None) -> dict:
    """A MIDI track playing `synth` (with one of its presets, e.g. "Salamander Grand Piano" for
    ACE Fluid Synth), wired to the controller ports whose name contains `controller` (e.g.
    "Oxygen Pro 49 USB MIDI": the keys, pads and knobs, not the DAW-control port), monitoring
    its input so the keys sound straight away."""
    mask = sum(1 << (c - 1) for c in channels or [])   # channels counted from 1, as on the hardware
    code = INSTRUMENT_LUA.format(name=bridge.to_lua(name), synth=bridge.to_lua(synth),
                                 controller=bridge.to_lua(controller), preset=bridge.to_lua(preset or ""),
                                 mask=mask)
    return bridge.lua(code)["returned"]


def add_reverb(track: str, db: float = -10.0, bus: str = "reverb") -> dict:
    """Send `track` to a hall reverb bus (made the first time) at `db`."""
    code = REVERB_LUA.format(track=bridge.to_lua(track), db=bridge.to_lua(float(db)), bus=bridge.to_lua(bus))
    return bridge.lua(code)["returned"]


# A mono track from an audio input through Guitarix: a Tube Screamer as a tight boost into the
# GxAmplifier-X tube amp in high-gain mode (it models its own speaker cabinet, so no separate
# one: two sound muffled), heard live. Control numbers are LuaAPI.set_processor_param's (the
# plugin's control inputs, counted from 0).
GUITAR_LUA = """
local name, want = {name}, {input}
local t = Session:new_audio_track (1, 2, ARDOUR.RouteGroup (), 1, name, ARDOUR.PresentationInfo.max_order,
                                   ARDOUR.TrackMode.Normal, false):front ()
local v, connected = C.StringVector (), {{}}
Session:engine ():get_physical_inputs (ARDOUR.DataType ("audio"), v, 0, 0)
for p in v:iter () do
  if p:find (want, 1, true) then t:input ():nth (0):connect (p) connected[#connected + 1] = p end
end
local chain = {{
  {{ "http://guitarix.sourceforge.net/plugins/gxts9#ts9sim", {{ [0] = -6, [1] = 600, [2] = 0.35 }} }},
  {{ "http://guitarix.sourceforge.net/plugins/gx_amp#GUITARIX",
    {{ [0] = -10, [1] = 3, [2] = 55, [3] = 0.55, [4] = 0.55, [5] = 0.55, [6] = 0.6, [8] = 5, [14] = 1 }} }},
}}
for i, step in ipairs (chain) do
  local proc = ARDOUR.LuaAPI.new_plugin (Session, step[1], ARDOUR.PluginType.LV2, "")
  if proc:isnil () then error ("couldn't load " .. step[1] .. " (is Guitarix installed?)", 0) end
  t:add_processor_by_index (proc, i - 1, nil, true)
  for k, value in pairs (step[2]) do ARDOUR.LuaAPI.set_processor_param (proc, k, value) end
end
t:to_track ():monitoring_control ():set_value (ARDOUR.MonitorChoice.MonitorInput, PBD.GroupControlDisposition.NoGroup)
t:gain_control ():set_value (10 ^ (-6 / 20), PBD.GroupControlDisposition.NoGroup)
return {{ id = t:to_stateful ():id ():to_s (), name = t:name (), connected = connected }}
"""


def audio_inputs() -> list[str]:
    """Audio inputs (sound cards, guitar adapters) as Ardour sees them."""
    code = ("local v = C.StringVector () Session:engine ():get_physical_inputs (ARDOUR.DataType ('audio'), v, 0, 0) "
            "local out = {} for p in v:iter () do out[#out + 1] = p end return out")
    return bridge.lua(code)["returned"] or []


def add_guitar(name: str = "guitar", input: str = "Unitek", reverb_db: float | None = -20.0) -> dict:
    """A distorted guitar track: the audio input whose name contains `input` through a Tube
    Screamer and a high-gain Guitarix amp, monitored live, with a little hall reverb."""
    made = bridge.lua(GUITAR_LUA.format(name=bridge.to_lua(name), input=bridge.to_lua(input)))["returned"]
    if reverb_db is not None:
        add_reverb(made["name"], reverb_db)
    return made


def set_tempo(bpm: float, beats_per_bar: int = 4) -> None:
    """One tempo and meter for the whole session (regions in audio time stay put)."""
    bridge.lua(f"""
local tm = Temporal.TempoMap.write_copy ()
tm:set_tempo (Temporal.Tempo ({float(bpm)}, {float(bpm)}, 4), Temporal.timepos_t (0))
tm:set_meter (Temporal.Meter ({int(beats_per_bar)}, 4), Temporal.timepos_t (0))
Temporal.TempoMap.update (tm)
""")


def set_loop(start: float, end: float) -> None:
    """Loop range in seconds."""
    rate = bridge.ping()["sample_rate"]
    bridge.lua(f"Editor:set_loop_range (Temporal.timepos_t ({round(start * rate)}), "
               f"Temporal.timepos_t ({round(end * rate)}), 'loop')")


def add_drums(kit: str, bpm: float, bars: int = 8, name: str = "drums", controller: str = "") -> dict:
    """A drum track playing `kit` (a General MIDI drum instrument, e.g. AVL Drumkits) with the
    session at `bpm` in 4/4, an original heavy groove of `bars` bars from bar 1, and the loop
    range over it. With no `controller` nothing plays the drums but the groove."""
    set_tempo(bpm)
    made = add_instrument(name, kit, controller)
    snap = snapshot()
    groove = midifile.write(snap.path / "sound-midi" / f"{name} groove.mid", drums.heavy_groove(bars), bpm,
                            name=f"{name} groove")
    imported = import_files([groove], at=0.0, track=made["name"])
    seconds = bars * 4 * 60 / bpm
    set_loop(0.0, seconds)
    return {**made, "bpm": bpm, "bars": bars, "groove": str(groove), "loop": [0.0, seconds],
            "regions": imported["regions"]}


def add_take(file: str | Path, at: float) -> dict:
    """Put a recorded take on the "takes" track (made when first needed), layered over earlier
    ones so the newest plays."""
    exists = TAKES_TRACK in snapshot().tracks
    result = import_files([file], at=max(0.0, at), track=TAKES_TRACK if exists else None,
                          name=None if exists else TAKES_TRACK)
    return {"track": TAKES_TRACK, "at": max(0.0, at), "regions": result["regions"]}
