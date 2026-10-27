import numpy as np
import pytest
import soundfile as sf

from sound.cli import autotune_main, main
from test_autotune import SR, voice

SUNG = [57.3, 61.2, 64.4, 59.1]    # A3 C#4 E4 B3, each a bit off
IN_TUNE = [57, 61, 64, 59]


def melody(lead_in: float, notes: list[float], transpose: float = 0.0) -> np.ndarray:
    parts = [np.zeros(int(lead_in * SR))]
    for m in notes:
        f0 = np.full(int(0.6 * SR), 440 * 2 ** ((m + transpose - 69) / 12))
        parts += [voice(f0), np.zeros(int(0.15 * SR))]
    return np.concatenate(parts)


@pytest.fixture
def files(tmp_path):
    ref, take = tmp_path / "ref.wav", tmp_path / "take.wav"
    sf.write(ref, np.concatenate([np.zeros(10 * SR), melody(0.5, IN_TUNE, transpose=12), np.zeros(2 * SR)]), SR)
    sf.write(take, melody(1.0, SUNG), SR)
    return tmp_path, ref, take


def test_cut_exports_the_section(files):
    tmp, ref, _ = files
    assert main(["cut", str(ref), "0:10", "0:12.5"]) == 0
    data, sr = sf.read(tmp / "ref_0m10s-0m12.5s.wav")
    assert sr == SR and abs(len(data) / SR - 2.5) < 0.001


def test_cut_rejects_start_past_the_end(files, capsys):
    _, ref, _ = files
    assert main(["cut", str(ref), "9:00"]) == 1
    assert "only" in capsys.readouterr().err


def test_analyze_section(files, capsys):
    _, ref, _ = files
    assert main(["analyze", str(ref), "--start", "0:09", "--notes"]) == 0
    out = capsys.readouterr().out
    assert "[0:09.0–" in out and "A4" in out and "C#5" in out


def test_compare_lines_up_and_scores(files, capsys, tmp_path):
    _, ref, take = files
    tuned = tmp_path / "take_tuned.wav"
    assert autotune_main([str(take), "--speed", "0", "-o", str(tuned)]) == 0
    capsys.readouterr()

    assert main(["compare", str(take), str(tuned), "--ref", str(ref), "--ref-start", "0:10",
                 "--plot", str(tmp_path / "cmp.png")]) == 0
    out = capsys.readouterr().out
    rows = {line.split()[0]: line for line in out.splitlines() if line.strip().startswith("take")}
    assert "0:10.5" in rows["take.wav"] and "-1 oct" in rows["take.wav"]   # the ref's melody starts at 10.5s
    assert "flat" not in rows["take_tuned.wav"] and "sharp" not in rows["take_tuned.wav"]
    assert "4/4 (100%)" in rows["take_tuned.wav"]
    assert (tmp_path / "cmp.png").exists()


def test_tune_natural_style(files, capsys):
    tmp, _, take = files
    assert main(["tune", str(take), "--key", "A", "--style", "natural", "--drift", "0.5", "-o", str(tmp / "n.wav")]) == 0
    assert (tmp / "n.wav").exists()
    with pytest.raises(SystemExit):
        main(["tune", str(take), "--drift", "2"])


def test_tune_section_names_output(files):
    tmp, _, take = files
    assert main(["tune", str(take), "--start", "1", "--end", "2"]) == 0
    assert (tmp / "take_0m01s-0m02s_tuned.wav").exists()
