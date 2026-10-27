import anyio
import numpy as np
import soundfile as sf
from mcp import Client

from sound.mcp_server import create_server, parse_seconds
from test_autotune import SR
from test_cli import IN_TUNE, SUNG, melody


def library(tmp_path):
    (tmp_path / "reference").mkdir()
    sf.write(tmp_path / "take.wav", melody(1.0, SUNG), SR)
    sf.write(tmp_path / "reference" / "ref.wav",
             np.concatenate([np.zeros(10 * SR), melody(0.5, IN_TUNE, transpose=12), np.zeros(2 * SR)]), SR)
    return create_server(tmp_path)


def call(server, name, **arguments):
    async def go():
        async with Client(server) as client:
            return await client.call_tool(name, arguments)
    result = anyio.run(go)
    return result


def test_tools_are_listed(tmp_path):
    async def go():
        async with Client(library(tmp_path)) as client:
            return {t.name for t in (await client.list_tools()).tools}
    assert anyio.run(go) == {"list_library", "analyze", "timing_of", "tune", "produce", "compare",
                             "ardour_open", "ardour_status", "ardour_import", "ardour_lua",
                             "ardour_instruments", "ardour_add_instrument", "ardour_add_reverb", "ardour_add_drums", "ardour_add_guitar",
                             "beat_from_beatbox"}


def test_ardour_tools_say_when_ardour_is_closed(tmp_path, monkeypatch):
    from sound import bridge, daw
    monkeypatch.setattr(daw, "running", lambda: False)
    server = library(tmp_path)
    assert call(server, "ardour_status").structured_content["open"] is False
    result = call(server, "ardour_import", path="take.wav")
    assert result.is_error and "isn't open" in result.content[0].text
    monkeypatch.setattr(bridge, "lua", lambda code: (_ for _ in ()).throw(bridge.BridgeError("boom")))
    result = call(server, "ardour_lua", code="return 1")
    assert result.is_error and "boom" in result.content[0].text


def test_list_and_analyze(tmp_path):
    server = library(tmp_path)
    listed = call(server, "list_library").structured_content
    assert [f["path"] for f in listed["take"]] == ["take.wav"] and listed["reference"][0]["path"] == "reference/ref.wav"
    a = call(server, "analyze", path="reference/ref.wav", start="0:10").structured_content
    assert a["section"][0] == 10 and a["held_notes"] == 4 and "notes_used" in a


def test_tune_produce_compare_round_trip(tmp_path):
    server = library(tmp_path)
    tuned = call(server, "tune", path="take.wav", key="A major", style="natural").structured_content
    assert tuned["saved"] == "tuned/take - A major, natural.wav" and tuned["key_used"] == "A major"
    assert (tmp_path / tuned["saved"]).exists()

    produced = call(server, "produce", path=tuned["saved"], reference="reference/ref.wav",
                    ref_start=10, ref_end=13).structured_content
    assert abs(produced["result"]["lufs"] - produced["reference"]["lufs"]) < 1.0

    scored = call(server, "compare", reference="reference/ref.wav", takes=["take.wav", tuned["saved"]],
                  ref_start=10).structured_content
    raw, fixed = scored["takes"]
    assert raw["octaves_below"] == 1 and abs(raw["first_note_at"] - 10.5) < 0.1
    assert fixed["typical_cents_off"] < 10 < raw["typical_cents_off"]


def test_errors_reach_the_model(tmp_path):
    server = library(tmp_path)
    missing = call(server, "analyze", path="nope.wav")
    assert missing.is_error and "list_library" in missing.content[0].text
    outside = call(server, "analyze", path="../../etc/passwd")
    assert outside.is_error
    bad_key = call(server, "tune", path="take.wav", key="H# lydian")
    assert bad_key.is_error


def test_parse_seconds():
    assert parse_seconds("3:26") == 206 and parse_seconds(12.5) == 12.5 and parse_seconds(None) is None
