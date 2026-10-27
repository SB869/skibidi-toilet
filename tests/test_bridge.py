import base64
import re

import pytest

from sound import bridge

UI_SCRIPTS = """<?xml version="1.0" encoding="UTF-8"?>
<UIScripts>
  <ActionScript lua="Lua 5.3">c2NyaXB0cyA9IHt9IA==</ActionScript>
  <ActionHooks/>
</UIScripts>
"""


def test_to_lua_literals():
    assert bridge.to_lua({"id": "x", "n": [1, 2.5, True, None]}) == '{["id"] = "x", ["n"] = {1, 2.5, true, nil}}'
    assert bridge.to_lua('say "hi"\\\n') == r'"say \"hi\"\\\010"'
    assert bridge.to_lua("café") == '"café"'
    with pytest.raises(ValueError):
        bridge.to_lua(float("nan"))


def test_hook_listens_to_the_100ms_timer_and_carries_source():
    xml = bridge.hook_xml()
    signals = re.search(r'signals="([01]+)"', xml).group(1)
    assert len(signals) == bridge.SIGNAL_COUNT and signals.count("1") == 1
    assert signals[::-1].index("1") == bridge.LUA_TIMER_DS        # bitset strings put bit 0 last
    state = base64.b64decode(re.search(r">([^<]+)</LuaCallback>", xml).group(1)).decode()
    assert state.startswith("s = {} s['n'] = \"sound bridge\"")
    assert "@BRIDGE_DIR@" not in state and str(bridge.BRIDGE_DIR) in state
    assert "return factory (...)" in state


def test_install_adds_once_and_keeps_action_scripts(tmp_path):
    ui = tmp_path / "ui_scripts"
    ui.write_text(UI_SCRIPTS)
    bridge.install(ui)
    bridge.install(ui)                                            # replaces, doesn't duplicate
    text = ui.read_text()
    assert text.count("<LuaCallback") == 1 and text.count("</ActionHooks>") == 1
    assert "c2NyaXB0cyA9IHt9IA==" in text
    assert text.index("<ActionHooks>") < text.index("<LuaCallback") < text.index("</ActionHooks>")


def test_install_creates_the_file(tmp_path):
    ui = tmp_path / "ardour9" / "ui_scripts"
    bridge.install(ui)
    assert "<UIScripts>" in ui.read_text() and "<LuaCallback" in ui.read_text()


def test_import_refuses_files_ardour_cannot_see(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "SANDBOX_ROOT", tmp_path / "home")
    f = tmp_path / "a.wav"
    f.write_bytes(b"")
    with pytest.raises(bridge.BridgeError, match="can only see"):
        bridge.import_audio([f])
