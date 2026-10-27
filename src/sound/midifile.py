"""Standard MIDI files, written directly: type 0, one track, notes plus tempo and meter."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

PPQ = 480  # ticks per quarter note


@dataclass(frozen=True)
class Note:
    beat: float          # start, in quarter notes from the start
    length: float        # in quarter notes
    pitch: int
    velocity: int
    channel: int = 0


def _varlen(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def encode(notes: list[Note], bpm: float, beats_per_bar: int = 4, name: str | None = None,
           programs: dict[int, int] | None = None,
           controls: list[tuple[float, int, int, int]] | None = None) -> bytes:
    """`programs` picks an instrument per channel at the start ({channel: General MIDI program},
    channels and programs counted from 0, e.g. {0: 48} for strings on channel 1). `controls`
    are controller changes (beat, channel, controller, value), e.g. CC11 expression for swells."""
    events: list[tuple[int, int, bytes]] = []   # (tick, note-offs first, bytes)
    for n in notes:
        on = round(n.beat * PPQ)
        off = max(on + 1, round((n.beat + n.length) * PPQ))
        ch = n.channel & 0x0F
        events.append((on, 1, bytes([0x90 | ch, n.pitch & 0x7F, max(1, min(127, n.velocity))])))
        events.append((off, 0, bytes([0x80 | ch, n.pitch & 0x7F, 0])))
    for beat, channel, controller, value in controls or []:   # before notes at the same tick
        events.append((round(beat * PPQ), -1, bytes([0xB0 | (channel & 0x0F), controller & 0x7F, max(0, min(127, int(value)))])))
    events.sort(key=lambda e: (e[0], e[1]))

    track = bytearray()
    if name:
        label = name.encode()
        track += b"\x00\xff\x03" + _varlen(len(label)) + label
    track += b"\x00\xff\x51\x03" + round(60_000_000 / bpm).to_bytes(3, "big")
    track += b"\x00\xff\x58\x04" + bytes([beats_per_bar, 2, 24, 8])   # n/4, metronome every quarter
    for channel, program in (programs or {}).items():
        track += bytes([0x00, 0xC0 | (channel & 0x0F), program & 0x7F])
    tick = 0
    for at, _, data in events:
        track += _varlen(at - tick) + data
        tick = at
    track += b"\x00\xff\x2f\x00"
    return (b"MThd" + struct.pack(">IHHH", 6, 0, 1, PPQ)
            + b"MTrk" + struct.pack(">I", len(track)) + bytes(track))


def write(path: Path, notes: list[Note], bpm: float, beats_per_bar: int = 4, name: str | None = None,
          programs: dict[int, int] | None = None, controls: list[tuple[float, int, int, int]] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode(notes, bpm, beats_per_bar, name, programs, controls))
    return path


# ---------------------------------------------------------------- reading

GM_DRUM_CHANNEL = 9   # "channel 10"


@dataclass
class Song:
    notes: list[tuple[Note, int]]          # with the index of the file track they came from
    tempos: list[tuple[float, float]]      # (beat, quarter notes per minute), from beat 0
    meters: list[tuple[float, int, int]]   # (beat, beats per bar, note value)
    track_names: dict[int, str]

    def seconds_at(self, beat: float) -> float:
        """Where a beat falls in time, following the tempo changes."""
        t, prev_beat, bpm = 0.0, 0.0, self.tempos[0][1]
        for at, new in self.tempos[1:]:
            if at >= beat:
                break
            t += (at - prev_beat) * 60 / bpm
            prev_beat, bpm = at, new
        return t + (beat - prev_beat) * 60 / bpm

    def drums(self) -> list[Note]:
        """The drum part: General MIDI channel 10, or else the notes of tracks named like drums,
        or else everything if the file only has one channel."""
        on_ten = [n for n, _ in self.notes if n.channel == GM_DRUM_CHANNEL]
        if on_ten:
            return on_ten
        named = {i for i, name in self.track_names.items()
                 if any(w in name.lower() for w in ("drum", "kit", "perc"))}
        if named:
            return [n for n, i in self.notes if i in named]
        if len({n.channel for n, _ in self.notes}) == 1:
            return [n for n, _ in self.notes]
        return []


def _read_varlen(data: bytes, i: int) -> tuple[int, int]:
    value = 0
    while True:
        b = data[i]
        i += 1
        value = (value << 7) | (b & 0x7F)
        if not b & 0x80:
            return value, i


def read(path: Path) -> Song:
    """A type 0 or 1 MIDI file (ticks per quarter note timing)."""
    data = Path(path).read_bytes()
    if data[:4] != b"MThd":
        raise ValueError(f"{path}: not a MIDI file")
    length, _fmt, ntracks, division = struct.unpack(">IHHH", data[4:14])
    if division & 0x8000:
        raise ValueError(f"{path}: SMPTE-timed MIDI files aren't supported")
    ppq, i = division, 8 + length
    notes: list[tuple[Note, int]] = []
    tempos: list[tuple[int, float]] = []
    meters: list[tuple[int, int, int]] = []
    names: dict[int, str] = {}
    for track in range(ntracks):
        while data[i:i + 4] != b"MTrk":   # skip unknown chunks
            i += 8 + struct.unpack(">I", data[i + 4:i + 8])[0]
        end = i + 8 + struct.unpack(">I", data[i + 4:i + 8])[0]
        i += 8
        tick, status = 0, 0
        held: dict[tuple[int, int], list[tuple[int, int]]] = {}
        while i < end:
            delta, i = _read_varlen(data, i)
            tick += delta
            if data[i] & 0x80:
                status = data[i]
                i += 1
            if status == 0xFF:
                kind = data[i]
                size, i = _read_varlen(data, i + 1)
                body = data[i:i + size]
                i += size
                if kind == 0x51 and size == 3:
                    tempos.append((tick, 60_000_000 / int.from_bytes(body, "big")))
                elif kind == 0x58 and size >= 2:
                    meters.append((tick, body[0], 2 ** body[1]))
                elif kind == 0x03:
                    names[track] = body.decode("latin-1")
                continue
            if status in (0xF0, 0xF7):
                size, i = _read_varlen(data, i)
                i += size
                continue
            kind, channel = status & 0xF0, status & 0x0F
            if kind in (0xC0, 0xD0):
                i += 1
                continue
            a, b = data[i], data[i + 1]
            i += 2
            if kind == 0x90 and b > 0:
                held.setdefault((channel, a), []).append((tick, b))
            elif kind in (0x80, 0x90) and held.get((channel, a)):
                start, velocity = held[(channel, a)].pop(0)
                notes.append((Note(start / ppq, max(tick - start, 1) / ppq, a, velocity, channel), track))
        i = end
    tempos = sorted(set(tempos)) or [(0, 120.0)]
    if tempos[0][0] > 0:
        tempos.insert(0, (0, tempos[0][1]))
    meters = sorted(set(meters)) or [(0, 4, 4)]
    notes.sort(key=lambda nt: (nt[0].beat, nt[0].pitch))
    return Song(notes, [(t / ppq, bpm) for t, bpm in tempos], [(t / ppq, n, d) for t, n, d in meters], names)
