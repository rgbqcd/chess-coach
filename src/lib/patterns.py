"""Buzz pattern primitives.

A pattern is a list of Steps: (level, duration_ms). level 0.0 means motor off.
Builders produce patterns from morse strings, counts, and count-groups, using
a Timing config so every duration is tunable from Viam component attributes.
"""

from __future__ import annotations

from dataclasses import dataclass

Step = tuple[float, int]  # (vibration level 0.0-1.0, duration ms)


@dataclass(frozen=True)
class Timing:
    dot_ms: int = 200
    dash_ms: int = 600
    gap_ms: int = 250  # between buzzes within a group / morse letter
    group_gap_ms: int = 900  # between count groups
    intensity: float = 0.7
    error_intensity: float = 0.4


# Morse table for the (stretch) morse-letter output mode and for signals.
MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.",
    "G": "--.", "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..",
    "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..",
    "1": ".----", "2": "..---", "3": "...--", "4": "....-", "5": ".....",
    "6": "-....", "7": "--...", "8": "---..", "9": "----.", "0": "-----",
}


def _join(chunks: list[list[Step]], gap: Step) -> list[Step]:
    out: list[Step] = []
    for i, chunk in enumerate(chunks):
        if i:
            out.append(gap)
        out.extend(chunk)
    return out


def from_morse(code: str, timing: Timing, level: float | None = None) -> list[Step]:
    """'.-' style string -> steps. Space separates letters (letter gap = group gap)."""
    lv = timing.intensity if level is None else level
    letters = code.split(" ")
    chunks = []
    for letter in letters:
        letter_steps = _join(
            [[(lv, timing.dot_ms if sym == "." else timing.dash_ms)] for sym in letter if sym in ".-"],
            (0.0, timing.gap_ms),
        )
        if letter_steps:
            chunks.append(letter_steps)
    return _join(chunks, (0.0, timing.group_gap_ms))


def from_text(text: str, timing: Timing, level: float | None = None) -> list[Step]:
    """Morse-encode plain text (letters/digits)."""
    return from_morse(" ".join(MORSE[c] for c in text.upper() if c in MORSE), timing, level)


def count(n: int, timing: Timing, level: float | None = None) -> list[Step]:
    """n short buzzes separated by intra-group gaps."""
    lv = timing.intensity if level is None else level
    return _join([[(lv, timing.dot_ms)] for _ in range(n)], (0.0, timing.gap_ms))


def count_groups(counts: list[int], timing: Timing, level: float | None = None) -> list[Step]:
    """Groups of short buzzes separated by group gaps: the move encoding."""
    return _join([count(n, timing, level) for n in counts], (0.0, timing.group_gap_ms))


def from_elements(elements: list[str], timing: Timing, level: float | None = None) -> list[Step]:
    """["short","long","pause"] -> steps. Adjacent buzzes get intra-group gaps."""
    lv = timing.intensity if level is None else level
    out: list[Step] = []
    prev_buzz = False
    for el in elements:
        if el == "pause":
            out.append((0.0, timing.group_gap_ms))
            prev_buzz = False
            continue
        if prev_buzz:
            out.append((0.0, timing.gap_ms))
        out.append((lv, timing.dot_ms if el == "short" else timing.dash_ms))
        prev_buzz = True
    return out


def duration_ms(steps: list[Step]) -> int:
    return sum(ms for _, ms in steps)


def signal(name: str, timing: Timing) -> list[Step]:
    """Reserved signals. All contain a dash (or are a lone long/low buzz), so a
    user counting short buzzes can never mistake a signal for a count group."""
    t, hi, lo = timing, min(1.0, timing.intensity + 0.2), timing.error_intensity
    table: dict[str, list[Step]] = {
        "ready": from_morse("-.-", t),  # long short long
        "attention": from_morse("--", t),
        "ack": from_morse("..", t),
        "error": [(lo, 1200)],  # one long low buzz
        "ambiguity": from_morse("---", t),
        "promotion": from_morse("-.-", t),
        "check": _join([[(hi, 100)] for _ in range(3)], (0.0, 100)),  # 3 rapid high dots
        "calibrate_relax": from_morse("...", t),
        "calibrate_squeeze": from_morse("-", t),
        "win": from_morse("---", t, hi),
        "loss": [(t.intensity, 2000)],
        "draw": from_morse("-.-.", t),
    }
    return table[name]


SIGNALS = (
    "ready", "attention", "ack", "error", "ambiguity", "promotion", "check",
    "calibrate_relax", "calibrate_squeeze", "win", "loss", "draw",
)
