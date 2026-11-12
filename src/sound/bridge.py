"""Run things inside a running Ardour that its MCP server can't do: import audio, or any Lua.

`sound daw` installs ardour_bridge.lua as an editor hook (in Ardour's ui_scripts). Every 100 ms
it looks for a request file in BRIDGE_DIR, runs it with Ardour's Lua API and writes a JSON reply.
Requests are Lua table literals, read with no access to anything; replies come back as JSON.

ui_scripts keeps a hook as <LuaCallback signals=… name=… id=…>, base64 of a Lua chunk setting
s = {n = name, s = script, f = factory, a = args}. f is normally bytecode, but Ardour loads it
with load(), so source works too: a chunk that returns the hook's callback. signals is the
std::bitset of LuaSignal values the hook listens to (gtk2_ardour/luasignal_syms.inc.h).
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from . import daw

try:
    import fcntl
except ImportError:  # Windows: no fcntl, and no Ardour bridge either (Ardour runs as a Linux Flatpak)
    fcntl = None

BRIDGE_DIR = daw.CONFIG_DIR / "sound-bridge"
SCRIPT = Path(__file__).with_name("ardour_bridge.lua")
HOOK_NAME = "sound bridge"
HOOK_ID = "7350734000001"
# Ardour 9's LuaSignal enum has 52 values; LuaTimerDS (every 100 ms) is number 49.
SIGNAL_COUNT, LUA_TIMER_DS = 52, 49
SANDBOX_ROOT = Path.home()  # the Flatpak sees the home folder (only), at the same paths


class BridgeError(RuntimeError):
    pass


def lua_string(s: str) -> str:
    out = []
    for ch in s:
        if ch in '\\"':
            out.append("\\" + ch)
        elif ord(ch) < 32 or ord(ch) == 127:
            out.append(f"\\{ord(ch):03d}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def to_lua(value: Any) -> str:
    """A Lua literal for plain data (None, bools, numbers, strings, lists, dicts)."""
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"can't send {value} to Lua")
        return repr(value)
    if isinstance(value, str):
        return lua_string(value)
    if isinstance(value, (list, tuple)):
        return "{" + ", ".join(to_lua(v) for v in value) + "}"
    if isinstance(value, dict):
        return "{" + ", ".join(f"[{lua_string(str(k))}] = {to_lua(v)}" for k, v in value.items()) + "}"
    raise TypeError(f"can't send a {type(value).__name__} to Lua")


def hook_xml() -> str:
    script = SCRIPT.read_text().replace("@BRIDGE_DIR@", lua_string(str(BRIDGE_DIR)))
    factory = f"local function ardour () end\n{script}\nreturn factory (...)"
    state = (f"s = {{}} s['n'] = {lua_string(HOOK_NAME)} s['s'] = {lua_string(script)} "
             f"s['f'] = {lua_string(factory)} s['a'] = {{}} ")
    signals = "".join("1" if bit == LUA_TIMER_DS else "0" for bit in reversed(range(SIGNAL_COUNT)))
    b64 = base64.b64encode(state.encode()).decode()
    return (f'<LuaCallback lua="Lua 5.3" id="{HOOK_ID}" name="{HOOK_NAME}" signals="{signals}">'
            f"{b64}</LuaCallback>")


OLD_HOOK = re.compile(rf'\s*<LuaCallback\s[^>]*name="{re.escape(HOOK_NAME)}"[^>]*>.*?</LuaCallback>', re.S)


def install(ui_scripts: Path | None = None) -> None:
    """Put the current bridge into Ardour's ui_scripts (replacing an older one). Only while
    Ardour is closed: it rewrites the file when it quits."""
    ui_scripts = ui_scripts or daw.CONFIG_DIR / "ui_scripts"
    hook = hook_xml()
    if not ui_scripts.exists():
        ui_scripts.parent.mkdir(parents=True, exist_ok=True)
        ui_scripts.write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<UIScripts>\n  <ActionHooks>\n'
                              f"    {hook}\n  </ActionHooks>\n</UIScripts>\n")
        return
    text = OLD_HOOK.sub("", ui_scripts.read_text())
    if "<ActionHooks/>" in text:
        text = text.replace("<ActionHooks/>", f"<ActionHooks>\n    {hook}\n  </ActionHooks>", 1)
    elif "</ActionHooks>" in text:
        text = text.replace("</ActionHooks>", f"  {hook}\n  </ActionHooks>", 1)
    else:
        text = text.replace("</UIScripts>", f"  <ActionHooks>\n    {hook}\n  </ActionHooks>\n</UIScripts>", 1)
    ui_scripts.write_text(text)


def call(command: str, timeout: float = 30.0, **args: Any) -> Any:
    """Run a bridge command in the open Ardour and return its result."""
    if not daw.running():
        raise BridgeError("Ardour isn't open (start it with `sound daw`)")
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    # one request at a time across processes (the web server, the MCP server, the CLI)
    with open(BRIDGE_DIR / "lock", "w") as lock:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        return _exchange(command, timeout, args)


def _exchange(command: str, timeout: float, args: dict) -> Any:
    request, response = BRIDGE_DIR / "request", BRIDGE_DIR / "response"
    deadline = time.time() + timeout
    while request.exists():  # another request still waiting to be picked up
        if time.time() > deadline:
            request.unlink(missing_ok=True)
            raise BridgeError("Ardour isn't picking up requests: the sound bridge hook isn't loaded "
                              "(close Ardour and reopen it with `sound daw`)")
        time.sleep(0.05)
    rid = uuid.uuid4().hex
    response.unlink(missing_ok=True)
    tmp = BRIDGE_DIR / "request.tmp"
    tmp.write_text(to_lua({"id": rid, "command": command, **args}))
    os.replace(tmp, request)
    while time.time() < deadline:
        if response.exists():
            reply = json.loads(response.read_bytes().decode("utf-8", "replace"))
            if reply.get("id") in (rid, None):
                response.unlink(missing_ok=True)
                if not reply.get("ok"):
                    raise BridgeError(reply.get("error") or "the request failed in Ardour")
                return reply.get("result")
        time.sleep(0.05)
    if request.exists():
        request.unlink(missing_ok=True)
        raise BridgeError("Ardour didn't pick up the request: the sound bridge hook isn't loaded "
                          "(close Ardour and reopen it with `sound daw`)")
    raise BridgeError(f"Ardour took the request but didn't answer within {timeout:.0f} s")


def ping() -> dict:
    return call("ping", timeout=5)


def import_audio(files: list[str | Path], at: int = 0, track: str | None = None, name: str | None = None) -> dict:
    """Import audio files into the open session at a sample position: as new tracks (one per
    file, optionally named) or onto an existing track (by name or id)."""
    paths = [Path(f).expanduser().resolve() for f in files]
    for p in paths:
        if not p.is_file():
            raise BridgeError(f"no such file: {p}")
        if SANDBOX_ROOT not in p.parents:
            raise BridgeError(f"Ardour (a Flatpak) can only see files in {SANDBOX_ROOT}: {p}")
    return call("import", timeout=300, files=[str(p) for p in paths], at=int(at), track=track, name=name)


def lua(code: str, timeout: float = 30.0) -> dict:
    """Run Lua inside Ardour with its scripting API; returns what it returned and printed."""
    return call("lua", timeout=timeout, code=code)
