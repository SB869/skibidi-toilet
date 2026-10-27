"""Ardour, scripted: create sessions and launch straight into them with MCP on, no dialogs.

Ardour runs as a Flatpak (org.ardour.Ardour). Launching it hands-free takes these settings:
  - no-memory-warning      (ui_config) skip the locked-memory warning at startup
  - try-autostart-engine   (config)    start the audio engine from the last setup, no dialog
  - the control surfaces "MCP HTTP Server (Experimental)" and OSC switched on, in Ardour's
    global config and in the session, as <Protocol name=… active="1" config=…/> (without
    config Ardour silently ignores the entry)
The engine setup (which audio system to use) lives in Ardour's config as
<Extra><AudioMIDISetup><EngineStates><State backend="JACK/Pipewire" active="1" …/> (the JACK
backend's name when the JACK library is PipeWire's); with it and
autostart on, Ardour connects to PipeWire's JACK server straight away, sharing the sound card
with every other app, instead of showing the Audio/MIDI dialog.

Ardour rewrites its config files when it quits, so they're only edited while it isn't running.
"""

from __future__ import annotations

import re
import subprocess
import time
import urllib.request
from pathlib import Path

APP = "org.ardour.Ardour"
CONFIG_DIR = Path.home() / ".var/app/org.ardour.Ardour/config/ardour9"
SESSIONS = Path(__file__).resolve().parents[2] / "sessions"
MCP_URL = "http://127.0.0.1:4820/mcp"
SURFACES = ("MCP HTTP Server (Experimental)", "Open Sound Control (OSC)")


def running() -> bool:
    """Ask Flatpak, not pgrep: a pattern search also matches any shell that mentions Ardour."""
    ps = subprocess.run(["flatpak", "ps", "--columns=application"], capture_output=True, text=True)
    return APP in ps.stdout.split()


def session_file(name_or_path: str | Path) -> Path:
    """A session by name (in sessions/) or by path to its folder or .ardour file."""
    p = Path(name_or_path)
    if p.suffix == ".ardour":
        return p.resolve()
    folder = p if p.is_dir() else SESSIONS / str(name_or_path)
    return (folder / f"{folder.name}.ardour").resolve()


def new_session(name: str, sample_rate: int = 48000) -> Path:
    folder = SESSIONS / name
    if folder.exists():
        raise FileExistsError(f"session {name!r} already exists at {folder}")
    SESSIONS.mkdir(parents=True, exist_ok=True)
    subprocess.run(["flatpak", "run", "--user", f"--command=ardour9-new_session", APP,
                    "-s", str(sample_rate), str(folder), name], capture_output=True, text=True)
    for _ in range(40):  # it aborts on exit (after saving); let its sandbox wind down
        if not running():
            break
        time.sleep(0.25)
    path = session_file(name)
    if not path.exists():  # judge by the file, not the exit code
        raise RuntimeError(f"Ardour didn't create the session at {folder}")
    enable_surfaces(path)
    return path


PROTOCOL_TAG = re.compile(r"<Protocol\s[^>]*>")


def enable_surfaces(file: Path) -> list[str]:
    """Switch on MCP HTTP and OSC in Ardour's global config or a session file. Ardour skips a
    <Protocol> entry unless it has name, active *and* config (ControlProtocolManager::set_state);
    for a surface that isn't a hardware device, config is its own name, as the Preferences
    checkbox sets it. Returns what was changed."""
    if not file.exists():
        return []
    text = file.read_text()
    changed, seen = [], set()

    def switch_on(m: re.Match) -> str:
        tag = m.group(0)
        name = re.search(r'\sname="([^"]*)"', tag)
        if not name or name.group(1) not in SURFACES:
            return tag
        name = name.group(1)
        seen.add(name)
        new = re.sub(r'\sactive="[^"]*"', ' active="1"', tag)
        if ' active="1"' not in new:
            new = new.replace(f'name="{name}"', f'name="{name}" active="1"', 1)
        if not re.search(r'\sconfig="', new):
            new = new.replace(' active="1"', f' active="1" config="{name}"', 1)
        if new != tag:
            changed.append(name)
        return new

    text = PROTOCOL_TAG.sub(switch_on, text)
    missing = "".join(f'\n    <Protocol name="{n}" active="1" config="{n}"/>' for n in SURFACES if n not in seen)
    if missing:
        changed += [n for n in SURFACES if n not in seen]
        root = "</Session>" if "<Session" in text else "</Ardour>"
        if "<ControlProtocols/>" in text:
            text = text.replace("<ControlProtocols/>", f"<ControlProtocols>{missing}\n  </ControlProtocols>", 1)
        elif "<ControlProtocols>" in text:
            text = text.replace("<ControlProtocols>", f"<ControlProtocols>{missing}", 1)
        else:
            text = text.replace(root, f"  <ControlProtocols>{missing}\n  </ControlProtocols>\n{root}", 1)
    if changed:
        file.write_text(text)
    return changed


def set_option(file: Path, section: str, name: str, value: str) -> bool:
    """Set <Option name=... value=...> inside <section> of an Ardour config file."""
    if not file.exists():
        return False
    text = file.read_text()
    pattern = re.compile(rf'<Option name="{re.escape(name)}" value="[^"]*"/>')
    option = f'<Option name="{name}" value="{value}"/>'
    if pattern.search(text):
        new = pattern.sub(option, text)
    elif f"<{section}>" in text:
        new = text.replace(f"<{section}>", f"<{section}>\n    {option}", 1)
    else:
        return False
    if new != text:
        file.write_text(new)
    return True


def engine_saved() -> bool:
    """Has Ardour got a JACK/Pipewire engine setup it can autostart from?"""
    config = CONFIG_DIR / "config"
    return config.exists() and 'backend="JACK/Pipewire"' in config.read_text()


def forget_engine() -> None:
    """Drop any saved engine setup (e.g. one naming a backend Ardour doesn't recognise)."""
    config = CONFIG_DIR / "config"
    if config.exists():
        config.write_text(re.sub(r"<AudioMIDISetup>.*?</AudioMIDISetup>", "", config.read_text(), flags=re.S))


def grabbed_sound_cards() -> list[str]:
    """Sound cards Ardour has opened directly (ALSA), bypassing PipeWire: they go silent
    for every other app while Ardour holds them."""
    ps = subprocess.run(["flatpak", "ps", "--columns=application,child-pid"], capture_output=True, text=True)
    pids = {line.split()[1] for line in ps.stdout.splitlines() if line.startswith(APP) and len(line.split()) > 1}
    if not pids:
        return []
    # the app's real pid is a descendant of the sandbox's child pid; collect the whole tree
    tree = subprocess.run(["ps", "-eo", "pid=,ppid="], capture_output=True, text=True).stdout.split("\n")
    children: dict[str, list[str]] = {}
    for row in tree:
        parts = row.split()
        if len(parts) == 2:
            children.setdefault(parts[1], []).append(parts[0])
    todo, owned = list(pids), set()
    while todo:
        pid = todo.pop()
        if pid not in owned:
            owned.add(pid)
            todo += children.get(pid, [])
    cards = []
    for status in Path("/proc/asound").glob("card*/pcm*/sub*/status"):
        text = status.read_text()
        owner = re.search(r"owner_pid\s*:\s*(\d+)", text)
        if owner and owner.group(1) in owned:
            cards.append(status.parts[3])
    return sorted(set(cards))


JACK_ENGINE = (
    '<AudioMIDISetup><EngineStates><State backend="JACK/Pipewire" driver="" device="" input-device="" output-device="" '
    'sample-rate="48000" buffer-size="1024" n-periods="2" input-latency="0" output-latency="0" lm-input="" '
    'lm-output="" active="1" use-buffered-io="0" midi-option="" lru="{lru}"><MIDIDevices/></State>'
    '</EngineStates></AudioMIDISetup>'
)


def set_engine_buffer(size: int) -> bool:
    """Samples per cycle for the JACK/Pipewire engine. PipeWire runs its whole graph at this
    size while Ardour is open: 128 at 48 kHz is 2.7 ms a cycle, low enough to play an
    instrument through Ardour; bigger buffers cost latency, smaller ones CPU (and risk dropouts)."""
    config = CONFIG_DIR / "config"
    if not config.exists():
        return False
    text = config.read_text()
    new = re.sub(r'(<State backend="JACK/Pipewire"[^>]*?\sbuffer-size=")\d+(")', rf"\g<1>{int(size)}\2", text)
    if new != text:
        config.write_text(new)
    return 'backend="JACK/Pipewire"' in new


def saved_engine_buffer() -> int | None:
    config = CONFIG_DIR / "config"
    found = re.search(r'<State backend="JACK/Pipewire"[^>]*?\sbuffer-size="(\d+)"', config.read_text()) if config.exists() else None
    return int(found.group(1)) if found else None


def apply_engine_buffer(size: int) -> int:
    """Set the running engine's buffer. Joining PipeWire's JACK server, Ardour keeps whatever
    the graph runs at instead of its saved size, so this asks for it (through the bridge);
    PipeWire then runs the whole graph at `size` while Ardour is open. Returns the size in use."""
    from . import bridge  # (it imports this module)
    bridge.lua(f"Session:engine ():set_buffer_size ({int(size)})")
    return bridge.lua("return Session:get_block_size ()")["returned"]


def remember_jack_engine() -> bool:
    """Tell Ardour to use PipeWire's JACK server (if it hasn't saved a setup of its own)."""
    config = CONFIG_DIR / "config"
    if not config.exists() or engine_saved():
        return False
    forget_engine()
    text = config.read_text()
    setup = JACK_ENGINE.format(lru=int(time.time()))
    if "<Extra>" in text:
        text = text.replace("<Extra>", f"<Extra>{setup}", 1)
    else:
        text = text.replace("</Ardour>", f"  <Extra>{setup}</Extra>\n</Ardour>", 1)
    config.write_text(text)
    return True


SCRIPTS = Path(__file__).with_name("ardour_scripts")


def install_scripts() -> list[str]:
    """Copy the project's Ardour Lua scripts (instruments like Sub 808) into Ardour's user
    scripts folder, where it finds them when it starts."""
    target = CONFIG_DIR / "scripts"
    target.mkdir(parents=True, exist_ok=True)
    installed = []
    for script in sorted(SCRIPTS.glob("*.lua")):
        dest = target / script.name
        if not dest.exists() or dest.read_bytes() != script.read_bytes():
            dest.write_bytes(script.read_bytes())
        installed.append(script.name)
    return installed


def prepare(session: Path, buffer: int | None = None) -> list[str]:
    """Everything that makes the launch hands-free. Returns notes on what still needs a person."""
    notes = []
    if running():
        notes.append("Ardour is already running, so its settings weren't touched")

    else:
        set_option(CONFIG_DIR / "ui_config", "UI", "no-memory-warning", "1")
        if not set_option(CONFIG_DIR / "config", "Config", "try-autostart-engine", "1"):
            notes.append("Ardour hasn't been run yet: its config doesn't exist")
        remember_jack_engine()
        if buffer:
            set_engine_buffer(buffer)
        enable_surfaces(CONFIG_DIR / "config")
        from . import bridge  # (it imports this module)
        bridge.install()
        install_scripts()
    enable_surfaces(session)
    if not engine_saved():
        notes.append("no saved engine setup: in Audio/MIDI Setup pick JACK/Pipewire and press Start (once)")
    return notes


LOG = CONFIG_DIR / "sound-daw.log"


def launch(session: Path) -> None:
    """Start Ardour detached, its output going to LOG (handy when something needs a look)."""
    with open(LOG, "w") as log:
        subprocess.Popen(["flatpak", "run", "--user", APP, str(session)], stdin=subprocess.DEVNULL,
                         stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def mcp_up() -> bool:
    """Is Ardour's MCP endpoint answering? (It starts once the session is loaded.)"""
    try:
        req = urllib.request.Request(MCP_URL, data=b'{"jsonrpc":"2.0","id":0,"method":"ping"}',
                                     headers={"Content-Type": "application/json",
                                              "Accept": "application/json, text/event-stream"})
        urllib.request.urlopen(req, timeout=2)
        return True
    except OSError as e:
        return bool(getattr(e, "code", None))  # an HTTP error still means something is listening


OSC_PORT = 3819


def osc_up() -> bool:
    """Is something listening for OSC on UDP 3819? (Ardour shares the host's network.)"""
    for table in ("/proc/net/udp", "/proc/net/udp6"):
        try:
            rows = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        if any(row.split()[1].endswith(f":{OSC_PORT:04X}") for row in rows if len(row.split()) > 1):
            return True
    return False


def mcp_call(tool: str, arguments: dict | None = None) -> dict:
    """Call one of Ardour's own MCP tools (its HTTP server answers plain JSON-RPC)."""
    import json
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": arguments or {}}}).encode()
    req = urllib.request.Request(MCP_URL, data=body, headers={"Content-Type": "application/json",
                                                              "Accept": "application/json, text/event-stream"})
    reply = json.loads(urllib.request.urlopen(req, timeout=30).read())
    if "error" in reply:
        raise RuntimeError(f"Ardour MCP {tool}: {reply['error']}")
    result = reply.get("result", {})
    if result.get("isError"):
        raise RuntimeError(f"Ardour MCP {tool}: {result.get('content')}")
    return result.get("structuredContent") or {}


def watch_startup(timeout: float = 90.0) -> str:
    """Follow a launch: "mcp" when the MCP server is up, "grabbed" if Ardour takes a sound card
    away from PipeWire (checked every second, so other apps go silent for a moment at most),
    or "waiting" if neither happened in time."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if grabbed_sound_cards():
            return "grabbed"
        if mcp_up():
            return "mcp"
        time.sleep(1)
    return "waiting"


def open_session(name: str = "friday", new: bool = False, rate: int = 48000, wait: bool = True,
                 buffer: int | None = None) -> dict:
    """Everything `sound daw` does: (create,) prepare and launch, then follow the startup.
    Returns {"session", "launched", "outcome" ("mcp" | "waiting" | None if not waited), "osc",
    "notes"}. Raises RuntimeError when it can't, including when Ardour grabbed a sound card
    (it's closed again: every other app would go silent)."""
    try:
        session = new_session(name, rate) if new else session_file(name)
    except FileExistsError as e:
        raise RuntimeError(str(e)) from None
    if not session.exists():
        raise RuntimeError(f"no session at {session} (create it with new)")
    notes = prepare(session, buffer)
    launched = not running()
    if launched:
        launch(session)
    outcome = watch_startup() if wait else None
    in_use = None
    target = buffer or saved_engine_buffer()
    if target and (outcome == "mcp" or not launched):
        if buffer and not launched:
            set_engine_buffer(buffer)  # Ardour may overwrite this when it quits; it's applied below anyway
        from . import bridge
        try:
            in_use = apply_engine_buffer(target)
        except bridge.BridgeError as e:
            notes.append(f"couldn't set the buffer size: {e}")
    if outcome == "grabbed":
        cards = ", ".join(grabbed_sound_cards()) or "a sound card"
        subprocess.run(["flatpak", "kill", APP])
        raise RuntimeError(f"Ardour opened {cards} directly instead of going through PipeWire, which "
                           f"silences every other app, so it was closed. Log: {LOG}")
    return {"session": session.parent.name, "launched": launched, "outcome": outcome,
            "osc": osc_up() if outcome == "mcp" else None, "buffer": in_use, "notes": notes}
