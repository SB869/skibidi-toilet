import io
import shutil
import subprocess

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from sound.server import create_app
from test_autotune import SR, voice
from test_cli import IN_TUNE, SUNG, melody


@pytest.fixture
def client(tmp_path):
    (tmp_path / "reference").mkdir()
    sf.write(tmp_path / "take.wav", melody(1.0, SUNG), SR)
    sf.write(tmp_path / "reference" / "ref.wav",
             np.concatenate([np.zeros(10 * SR), melody(0.5, IN_TUNE, transpose=12), np.zeros(2 * SR)]), SR)
    return TestClient(create_app(tmp_path, web_dir=None))


def test_lists_files_by_kind(client):
    files = {f["path"]: f for f in client.get("/api/files").json()["files"]}
    assert files["take.wav"]["kind"] == "take"
    assert files["reference/ref.wav"]["kind"] == "reference"
    assert abs(files["take.wav"]["duration"] - len(melody(1.0, SUNG)) / SR) < 0.01


def test_analysis_has_pitch_peaks_and_summary(client):
    data = client.get("/api/analysis", params={"path": "take.wav"}).json()
    assert data["pitch"][0] is None and any(v is not None for v in data["pitch"])
    assert len(data["peaks"]["min"]) == len(data["peaks"]["max"]) > 100
    assert [n["name"] for n in data["notes"]] == ["A3", "C#4", "E4", "B3"]


def test_tune_play_and_save(client, tmp_path):
    r = client.post("/api/tune", json={"path": "take.wav", "key": "A major", "speed": 0})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["key"]["label"] == "A major" and data["key"]["source"] == "fixed"
    assert 20 < data["correction_median"] < 45

    wav = client.get(f"/api/renders/{data['id']}/audio")
    audio, sr = sf.read(io.BytesIO(wav.content))
    assert sr == SR and abs(len(audio) / SR - len(melody(1.0, SUNG)) / SR) < 0.01

    saved = client.post(f"/api/renders/{data['id']}/save", json={}).json()
    assert saved["kind"] == "render" and (tmp_path / saved["path"]).exists()
    again = client.post(f"/api/renders/{data['id']}/save", json={}).json()
    assert again["path"] != saved["path"]   # never overwrites


def test_compare_scores_a_take_and_an_unsaved_render(client):
    rid = client.post("/api/tune", json={"path": "take.wav", "key": "A major", "speed": 0}).json()["id"]
    r = client.post("/api/compare", json={"reference": "reference/ref.wav", "ref_start": 10,
                                          "takes": ["take.wav", f"render:{rid}"]})
    assert r.status_code == 200, r.text
    raw, tuned = r.json()["rows"]
    assert abs(raw["first_note"] - 10.5) < 0.1 and raw["octave"] == 12
    assert tuned["off_by"] < 10 < raw["off_by"]
    assert tuned["notes_matched"] == tuned["notes_compared"] == 4
    assert len(tuned["pitch"]) == len(tuned["reference"])


def test_compare_aligns_from_the_untuned_take_whatever_the_order(client):
    rid = client.post("/api/tune", json={"path": "take.wav", "key": "A major", "speed": 0}).json()["id"]
    body = {"reference": "reference/ref.wav", "ref_start": 10}
    a = client.post("/api/compare", json=body | {"takes": [f"render:{rid}", "take.wav"]}).json()["rows"]
    b = client.post("/api/compare", json=body | {"takes": ["take.wav", f"render:{rid}"]}).json()["rows"]
    assert a[0]["take"] == f"render:{rid}" and b[1]["take"] == f"render:{rid}"
    assert a[0]["off_by"] == b[1]["off_by"] and a[1]["off_by"] == b[0]["off_by"]


def test_cut_saves_next_to_source(client, tmp_path):
    saved = client.post("/api/cut", json={"path": "reference/ref.wav", "start": 10, "end": 12.5}).json()
    assert saved["kind"] == "reference" and abs(saved["duration"] - 2.5) < 0.01


def test_upload_reference_and_reject_non_audio(client, tmp_path):
    buf = io.BytesIO()
    sf.write(buf, voice(np.full(SR // 2, 220.0)), SR, format="FLAC")
    r = client.post("/api/files", data={"kind": "reference"}, files={"file": ("song.flac", buf.getvalue())})
    assert r.status_code == 200 and r.json()["path"] == "reference/song.flac"

    bad = client.post("/api/files", data={"kind": "take"}, files={"file": ("notes.mp3", b"definitely not audio")})
    assert bad.status_code == 422


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_browser_recording_becomes_a_wav_take(client, tmp_path):
    wav, webm = tmp_path / "src.wav", tmp_path / "rec.webm"
    sf.write(wav, voice(np.full(SR // 2, 220.0)), SR)
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav), "-c:a", "libopus", str(webm)], check=True)
    r = client.post("/api/files", data={"kind": "take", "recorded": "true"},
                    files={"file": ("recording.webm", webm.read_bytes())})
    assert r.status_code == 200, r.text
    assert r.json()["path"].startswith("take ") and r.json()["path"].endswith(".wav")


@pytest.mark.parametrize("path", ["../../etc/passwd", "/etc/passwd", "reference/../../x.wav", "nope.wav"])
def test_paths_outside_workspace_are_refused(client, path):
    assert client.get("/api/audio", params={"path": path}).status_code == 404


def test_natural_style_render_and_save_name(client):
    r = client.post("/api/tune", json={"path": "take.wav", "key": "A major", "style": "natural", "drift": 0.3})
    assert r.status_code == 200, r.text
    saved = client.post(f"/api/renders/{r.json()['id']}/save", json={}).json()
    assert saved["name"] == "take - A major, natural.wav"
    assert client.post("/api/tune", json={"path": "take.wav", "style": "chipmunk"}).status_code == 422


def test_tune_with_vocal_chain_matching_a_reference(client):
    body = {"path": "take.wav", "key": "A major", "style": "natural",
            "fx": {"reference": "reference/ref.wav", "ref_start": 10, "ref_end": 13}}
    r = client.post("/api/tune", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    ref_lufs = data["sound"]["reference"]["lufs"]
    assert abs(data["sound"]["output"]["lufs"] - ref_lufs) < 1.0 and abs(data["lufs"] - ref_lufs) < 1.0
    assert data["original_lufs"] < data["lufs"]
    audio, _ = sf.read(io.BytesIO(client.get(f"/api/renders/{data['id']}/audio").content))
    assert audio.ndim == 2 and audio.shape[1] == 2
    saved = client.post(f"/api/renders/{data['id']}/save", json={}).json()
    assert saved["name"] == "take - A major, natural, produced.wav"


def test_guide_describes_a_reference_section(client):
    r = client.get("/api/guide", params={"path": "reference/ref.wav", "start": 9.5, "end": 13.5})
    assert r.status_code == 200, r.text
    g = r.json()
    assert g["start"] == 9.5 and abs(g["end"] - 13.5) < 1e-6
    assert len(g["level"]) == len(g["edge"]) and abs(len(g["level"]) * g["env_hop"] - 4.0) < 0.1
    sung = [v for v in g["pitch"] if v is not None]
    assert sung and 68 < sorted(sung)[len(sung) // 2] < 77      # the ref melody sits around A4-E5
    assert max(v for v in g["level"]) <= 3 and g["typical_edge"] is not None


def test_overview_and_timing(client):
    o = client.get("/api/overview", params={"path": "reference/ref.wav"}).json()
    assert abs(o["duration"] - 15.5) < 0.1 and len(o["peaks"]["max"]) > 100
    t = client.get("/api/timing", params={"path": "reference/ref.wav", "start": 9.0, "end": 13.5}).json()
    assert abs(t["entry"] - 10.5) < 0.1            # the reference's melody starts 10.5 s in


def test_bad_key_is_a_clear_error(client):
    r = client.post("/api/tune", json={"path": "take.wav", "key": "H# lydian"})
    assert r.status_code == 422 and "key" in r.json()["detail"]
