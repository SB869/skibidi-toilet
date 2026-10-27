"""Drum parts as MIDI on the General MIDI drum map, which AVL Drumkits and most drum samplers
follow."""

from __future__ import annotations

from .midifile import Note

GM = {"kick": 36, "rim": 37, "snare": 38, "hat": 42, "pedal hat": 44, "open hat": 46, "crash": 49,
      "ride": 51, "ride bell": 53, "high tom": 50, "mid tom": 47, "low tom": 45, "floor tom": 43}

HIT = 0.25   # drum notes are triggers: a 16th is plenty


def _hit(beat: float, drum: str, velocity: int) -> Note:
    return Note(beat, HIT, GM[drum], velocity)


def heavy_groove(bars: int = 8) -> list[Note]:
    """A slow, heavy rock groove, in four-bar phrases: two kick patterns under eighth-note
    cymbals (hi-hat in the first phrase, ride in the next), snare on 2 and 4 with ghost notes,
    and a sixteenth-note snare-and-tom fill in each phrase's last bar, crashing into the next.
    Ends on a crash."""
    notes: list[Note] = []
    for bar in range(bars):
        at = bar * 4.0
        phrase_bar = bar % 4
        cymbal = "hat" if (bar // 4) % 2 == 0 else "ride"
        fill = phrase_bar == 3 or bar == bars - 1
        # cymbal eighths (beat accents), a crash to open each phrase
        for k in range(4 if fill else 8):
            beat = at + k * 0.5
            if k == 0 and phrase_bar == 0:
                notes.append(_hit(beat, "crash", 112))
            else:
                notes.append(_hit(beat, cymbal, 92 if k % 2 == 0 else 64))
        # kick: a plain bar, then a pushed one
        kicks = (0, 1.5, 2.5) if phrase_bar % 2 == 0 else (0, 0.75, 2.5, 3.25)
        for b in kicks:
            if not (fill and b >= 2):
                notes.append(_hit(at + b, "kick", 108))
        # snare backbeat on 2 and 4 (beats 1 and 3 counting from 0), ghosts in the pushed bar
        notes.append(_hit(at + 1, "snare", 112))
        if not fill:
            notes.append(_hit(at + 3, "snare", 116))
            if phrase_bar % 2 == 1:
                notes += [_hit(at + 1.75, "snare", 34), _hit(at + 3.75, "snare", 30)]
        else:
            # sixteenths down the kit over beats 3 and 4, kick under the low end
            run = ["snare", "snare", "high tom", "high tom", "mid tom", "mid tom", "floor tom", "floor tom"]
            for i, drum in enumerate(run):
                notes.append(_hit(at + 2 + i * 0.25, drum, 96 + (i % 2) * 14))
            notes += [_hit(at + 3.5, "kick", 104), _hit(at + 3.75, "kick", 100)]
    end = bars * 4.0
    notes += [_hit(end, "crash", 120), _hit(end, "kick", 115)]
    return sorted(notes, key=lambda n: (n.beat, n.pitch))
