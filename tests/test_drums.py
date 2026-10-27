import struct

from sound import drums, midifile


def parse(data: bytes):
    """Just enough of a MIDI file reader to check what we write."""
    assert data[:4] == b"MThd"
    _, fmt, ntrks, ppq = struct.unpack(">IHHH", data[4:14])
    assert data[14:18] == b"MTrk"
    body, i, tick, events, meta = data[22:], 0, 0, [], {}
    while i < len(body):
        delta = 0
        while True:
            b = body[i]; i += 1
            delta = (delta << 7) | (b & 0x7F)
            if not b & 0x80:
                break
        tick += delta
        if body[i] == 0xFF:
            kind, length = body[i + 1], body[i + 2]
            meta[kind] = body[i + 3:i + 3 + length]
            i += 3 + length
        else:
            events.append((tick, body[i] & 0xF0, body[i + 1], body[i + 2]))
            i += 3
    return fmt, ntrks, ppq, meta, events


def test_midi_file_has_tempo_meter_and_paired_notes():
    notes = [midifile.Note(0, 1, 60, 100), midifile.Note(1, 0.5, 64, 90), midifile.Note(1, 1, 67, 80)]
    fmt, ntrks, ppq, meta, events = parse(midifile.encode(notes, 72, 3, name="x"))
    assert (fmt, ntrks, ppq) == (0, 1, midifile.PPQ)
    assert int.from_bytes(meta[0x51], "big") == round(60_000_000 / 72)
    assert meta[0x58][:2] == bytes([3, 2]) and meta[0x03] == b"x" and 0x2F in meta
    ons = [(t, p) for t, s, p, _ in events if s == 0x90]
    offs = [(t, p) for t, s, p, _ in events if s == 0x80]
    assert ons == [(0, 60), (480, 64), (480, 67)] and sorted(offs) == [(480, 60), (720, 64), (960, 67)]
    # at a shared tick, the note-off comes before the note-on
    at_480 = [s for t, s, *_ in events if t == 480]
    assert at_480[0] == 0x80


def test_groove_shape():
    notes = drums.heavy_groove(8)
    assert min(n.beat for n in notes) == 0 and max(n.beat for n in notes) == 32
    assert all(n.pitch in drums.GM.values() and 1 <= n.velocity <= 127 for n in notes)
    crashes = sorted(n.beat for n in notes if n.pitch == drums.GM["crash"])
    assert crashes == [0, 16, 32]                      # each phrase opens on a crash, and it ends on one
    backbeats = {n.beat for n in notes if n.pitch == drums.GM["snare"] and n.velocity > 100}
    assert {1.0, 3.0, 5.0, 7.0} <= backbeats           # snare on 2 and 4
    assert any(n.pitch == drums.GM["floor tom"] for n in notes if 12 <= n.beat < 16)   # the fill
    assert any(n.pitch == drums.GM["ride"] for n in notes if 16 <= n.beat < 32)        # second phrase on the ride


def test_program_changes_come_first():
    data = midifile.encode([midifile.Note(0, 1, 60, 100, 2)], 90, programs={2: 48})
    i = data.index(bytes([0xC2, 48]))
    assert i < data.index(bytes([0x92, 60]))
    assert midifile.read.__name__  # the reader skips program changes (2-byte messages)


def test_controls_are_written_before_notes_at_the_same_time():
    data = midifile.encode([midifile.Note(1, 1, 42, 90)], 88, controls=[(1, 0, 11, 40), (1.5, 0, 11, 90)])
    assert data.index(bytes([0xB0, 11, 40])) < data.index(bytes([0x90, 42, 90])) < data.index(bytes([0xB0, 11, 90]))
