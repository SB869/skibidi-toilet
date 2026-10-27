"""Reading and writing audio files.

wav, flac, aiff and ogg are read directly with libsndfile; everything else (mp3, m4a/aac
voice memos, opus, video files) is decoded with ffmpeg.
Samples are always returned as float32 shaped (n_samples, n_channels).
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

PCM24_SUFFIXES = {".wav", ".flac", ".aif", ".aiff"}
# Read these with libsndfile; everything else (including mp3, which phones often use as a
# label for AAC) goes through ffmpeg, which sniffs the real format.
SNDFILE_SUFFIXES = PCM24_SUFFIXES | {".ogg"}


def load(path: str | Path) -> tuple[np.ndarray, int]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError("file not found")
    if path.suffix.lower() in SNDFILE_SUFFIXES:
        try:
            data, sr = sf.read(path, dtype="float32", always_2d=True)
            return data, int(sr)
        except sf.LibsndfileError:
            pass  # misnamed file; let ffmpeg sniff it
    return _load_ffmpeg(path)


def _load_ffmpeg(path: Path) -> tuple[np.ndarray, int]:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError(f"{path}: unsupported format and ffmpeg is not installed")

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate,channels", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    streams = json.loads(probe.stdout or "{}").get("streams", [])
    if probe.returncode != 0 or not streams:
        raise RuntimeError(f"{path}: no audio stream found ({probe.stderr.strip()})")
    sr, channels = int(streams[0]["sample_rate"]), int(streams[0]["channels"])

    decoded = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
         "-f", "f32le", "-acodec", "pcm_f32le", "-"],
        capture_output=True,
    )
    if decoded.returncode != 0:
        raise RuntimeError(f"{path}: ffmpeg failed: {decoded.stderr.decode().strip()}")
    data = np.frombuffer(decoded.stdout, dtype="<f4").reshape(-1, channels)
    return data.copy(), sr


def probe(path: str | Path) -> tuple[float, int, int]:
    """(duration in seconds, sample rate, channels) without decoding the whole file."""
    path = Path(path)
    if path.suffix.lower() in SNDFILE_SUFFIXES:
        try:
            info = sf.info(path)
            return float(info.duration), int(info.samplerate), int(info.channels)
        except sf.LibsndfileError:
            pass
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=sample_rate,channels:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    info = json.loads(result.stdout or "{}")
    streams = info.get("streams", [])
    if result.returncode != 0 or not streams:
        raise RuntimeError(f"{path}: no audio stream found")
    return float(info["format"]["duration"]), int(streams[0]["sample_rate"]), int(streams[0]["channels"])


def to_wav_bytes(data: np.ndarray, sr: int) -> bytes:
    """16-bit wav in memory (for streaming to a browser)."""
    buf = io.BytesIO()
    sf.write(buf, np.clip(data, -1.0, 1.0), sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def save(path: str | Path, data: np.ndarray, sr: int) -> float:
    """Write audio, scaling it down if it would clip. Returns the gain applied (1.0 = none)."""
    path = Path(path)
    gain = 1.0
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak > 0.999:
        gain = 0.999 / peak
        data = data * gain
    subtype = "PCM_24" if path.suffix.lower() in PCM24_SUFFIXES else None
    sf.write(path, data, sr, subtype=subtype)
    return gain
