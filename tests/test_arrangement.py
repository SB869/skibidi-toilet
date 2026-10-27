import json

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from sound import arrangement, bridge
from sound.arrangement import Region, Snapshot
from sound.server import create_app
from test_autotune import SR
from test_cli import IN_TUNE, SUNG, melody

REF = np.concatenate([np.zeros(10 * SR), melody(0.5, IN_TUNE, transpose=12), np.zeros(2 * SR)])
TAKE = melody(1.0, SUNG)
REF_AT = 2.0                        # where the reference file's 0:00 sits on Ardour's timeline
TAKE_AT = REF_AT + 10.5 - 1.0       # the take's first note on the reference's first note


@pytest.fixture
def session(tmp_path, monkeypatch):
    lib, sess = tmp_path / "lib", tmp_path / "session"
    (lib / "reference").mkdir(parents=True)
    sess.mkdir()
    sf.write(lib / "reference" / "ref.wav", REF, SR)
    sf.write(sess / "ref%L.wav", REF, SR)       # Ardour's copies of what was imported
    sf.write(sess / "take%L.wav", TAKE, SR)
    snap = Snapshot("s", sess, SR, playhead=12.0, tracks={
        "reference": [Region("1", "ref", "reference", REF_AT, 0.0, len(REF) / SR, [sess / "ref%L.wav"])],
        "vox": [Region("2", "take", "vox", TAKE_AT, 0.0, len(TAKE) / SR, [sess / "take%L.wav"])],
    })
    monkeypatch.setattr(arrangement, "snapshot", lambda: snap)
    return snap, TestClient(create_app(lib, web_dir=None))


def test_link_puts_everything_in_reference_time(session):
    snap, client = session
    link = client.get("/api/ardour").json()
    assert link["reference"] == {"track": "reference", "path": "reference/ref.wav", "offset": REF_AT}
    assert link["span_from"] == "vox"           # no selection or loop: the take under the playhead
    np.testing.assert_allclose(link["span"], [TAKE_AT - REF_AT, TAKE_AT - REF_AT + len(TAKE) / SR])
    assert [t["id"] for t in link["takes"]] == ["2"]
    snap.time_selection = (12.0, 15.0)
    link = client.get("/api/ardour").json()
    assert link["span_from"] == "range selection" and link["span"] == [10.0, 13.0]


def test_take_curves_as_placed_and_lined_up(session):
    _, client = session
    curves = client.get("/api/ardour/take", params={"region": "2"}).json()
    assert curves["placed"]["start"] == pytest.approx(TAKE_AT - REF_AT)
    assert curves["octave"] == 12 and curves["tempo"] == pytest.approx(1.0, abs=0.05)
    a = curves["aligned"]
    first = next(i for i, v in enumerate(a["pitch"]) if v is not None)
    assert a["start"] + first * a["hop"] == pytest.approx(10.5, abs=0.15)   # on the reference's first note
    assert client.get("/api/ardour/take", params={"region": "9"}).status_code == 404


def test_ardour_not_answering_is_a_503(session, monkeypatch):
    _, client = session
    monkeypatch.setattr(arrangement, "snapshot", lambda: (_ for _ in ()).throw(bridge.BridgeError("Ardour isn't open")))
    r = client.get("/api/ardour")
    assert r.status_code == 503 and "isn't open" in r.json()["detail"]


def test_a_take_goes_to_ardour(session, monkeypatch, tmp_path):
    _, client = session
    sent = []
    monkeypatch.setattr(arrangement, "add_take", lambda f, at: sent.append((f, at)) or {"track": "takes", "at": at})
    r = client.post("/api/ardour/takes", json={"path": "reference/ref.wav", "at": 3.25})
    assert r.json() == {"track": "takes", "at": 3.25}
    assert sent == [((tmp_path / "lib" / "reference" / "ref.wav").resolve(), 3.25)]


@pytest.mark.parametrize("name, bare", [("acapella%L.wav", "acapella"), ("Friday, 9h17m PM%R.wav", "Friday, 9h17m PM"),
                                        ("take 00.33.41-2.wav", "take 00.33.41"), ("mono.wav", "mono")])
def test_bare_stem(name, bare, tmp_path):
    assert arrangement._bare_stem(tmp_path / name) == bare


def test_library_file_remembered_then_by_name(tmp_path):
    root, sess = tmp_path / "lib", tmp_path / "sess"
    (root / "reference").mkdir(parents=True)
    sess.mkdir()
    for p in (root / "song.wav", root / "reference" / "song.wav", root / "other.wav"):
        p.write_bytes(b"")
    region = Region("1", "song", "t", 0, 0, 1, [sess / "song%L.wav"])
    assert arrangement.library_file(region, sess, root, prefer="reference") == root / "reference" / "song.wav"
    arrangement.remember(sess, {str(sess / "song%L.wav"): str(root / "other.wav")})
    assert json.loads((sess / arrangement.SOURCES_FILE).read_text())
    assert arrangement.library_file(region, sess, root, prefer="reference") == root / "other.wav"
