from sound import daw

SESSION = """<Session>
  <ControlProtocols>
    <Protocol name="Generic MIDI" active="0"/>
    <Protocol name="MCP HTTP Server (Experimental)" active="0"/>
    <Protocol name="Open Sound Control (OSC)" active="0"/>
  </ControlProtocols>
</Session>"""


def test_enable_surfaces_switches_on_mcp_and_osc_only(tmp_path):
    session = tmp_path / "s.ardour"
    session.write_text(SESSION)
    assert daw.enable_surfaces(session) == list(daw.SURFACES)
    text = session.read_text()
    # Ardour ignores an entry without config; for a non-device surface it's the surface's name
    assert '<Protocol name="MCP HTTP Server (Experimental)" active="1" config="MCP HTTP Server (Experimental)"/>' in text
    assert '<Protocol name="Open Sound Control (OSC)" active="1" config="Open Sound Control (OSC)"/>' in text
    assert '<Protocol name="Generic MIDI" active="0"/>' in text
    assert daw.enable_surfaces(session) == []          # idempotent


def test_enable_surfaces_keeps_saved_state_and_adds_missing(tmp_path):
    # as Ardour saves a surface that was on and is now off: its own settings, then active and config
    session = tmp_path / "s.ardour"
    session.write_text('<Session>\n  <ControlProtocols>\n    <Protocol name="MCP HTTP Server (Experimental)" '
                       'feedback="0" port="4820" debug-level="0" active="0" config="MCP HTTP Server (Experimental)"/>\n'
                       '  </ControlProtocols>\n</Session>')
    assert daw.enable_surfaces(session) == list(daw.SURFACES)
    text = session.read_text()
    assert ('<Protocol name="MCP HTTP Server (Experimental)" feedback="0" port="4820" debug-level="0" '
            'active="1" config="MCP HTTP Server (Experimental)"/>') in text
    assert '<Protocol name="Open Sound Control (OSC)" active="1" config="Open Sound Control (OSC)"/>' in text
    assert text.count("<ControlProtocols>") == 1


def test_enable_surfaces_adds_the_section_if_there_is_none(tmp_path):
    config = tmp_path / "config"
    config.write_text("<Ardour>\n  <Config/>\n</Ardour>\n")
    assert daw.enable_surfaces(config) == list(daw.SURFACES)
    text = config.read_text()
    assert text.index("<ControlProtocols>") < text.index("</ControlProtocols>") < text.index("</Ardour>")
    assert text.count('active="1"') == 2


def test_set_option_adds_then_updates(tmp_path):
    config = tmp_path / "config"
    config.write_text('<Ardour>\n  <Config>\n    <Option name="a" value="1"/>\n  </Config>\n</Ardour>')
    assert daw.set_option(config, "Config", "try-autostart-engine", "1")
    assert '<Option name="try-autostart-engine" value="1"/>' in config.read_text()
    assert daw.set_option(config, "Config", "try-autostart-engine", "0")
    assert config.read_text().count("try-autostart-engine") == 1 and 'value="0"' in config.read_text()
    assert not daw.set_option(tmp_path / "missing", "Config", "x", "1")


def test_session_file_by_name_or_path(tmp_path):
    assert daw.session_file("friday") == (daw.SESSIONS / "friday" / "friday.ardour").resolve()
    folder = tmp_path / "song"
    folder.mkdir()
    assert daw.session_file(folder) == (folder / "song.ardour").resolve()


def test_remember_jack_engine_once(tmp_path, monkeypatch):
    monkeypatch.setattr(daw, "CONFIG_DIR", tmp_path)
    (tmp_path / "config").write_text('<?xml version="1.0"?>\n<Ardour>\n  <Config/>\n</Ardour>\n')
    assert not daw.engine_saved()
    assert daw.remember_jack_engine()
    text = (tmp_path / "config").read_text()
    assert '<Extra><AudioMIDISetup><EngineStates><State backend="JACK/Pipewire"' in text and text.rstrip().endswith("</Ardour>")
    assert daw.engine_saved()
    assert not daw.remember_jack_engine()   # never overwrites a saved setup


def test_a_wrongly_named_engine_setup_is_replaced(tmp_path, monkeypatch):
    monkeypatch.setattr(daw, "CONFIG_DIR", tmp_path)
    (tmp_path / "config").write_text('<Ardour>\n  <Extra><AudioMIDISetup><EngineStates><State backend="JACK"/>'
                                     '</EngineStates></AudioMIDISetup></Extra>\n</Ardour>\n')
    assert not daw.engine_saved()
    assert daw.remember_jack_engine()
    text = (tmp_path / "config").read_text()
    assert text.count("<AudioMIDISetup>") == 1 and 'backend="JACK/Pipewire"' in text


def test_set_engine_buffer_changes_only_the_jack_state(tmp_path, monkeypatch):
    monkeypatch.setattr(daw, "CONFIG_DIR", tmp_path)
    (tmp_path / "config").write_text('<Ardour><Extra><AudioMIDISetup><EngineStates>'
                                     '<State backend="ALSA" buffer-size="512"/>'
                                     '<State backend="JACK/Pipewire" device="x" sample-rate="48000" buffer-size="1024" n-periods="2"/>'
                                     '</EngineStates></AudioMIDISetup></Extra></Ardour>')
    assert daw.set_engine_buffer(128)
    text = (tmp_path / "config").read_text()
    assert 'backend="JACK/Pipewire" device="x" sample-rate="48000" buffer-size="128"' in text
    assert '<State backend="ALSA" buffer-size="512"/>' in text
