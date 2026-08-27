from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from models.edit import EditSegment


@dataclass(frozen=True, slots=True)
class Caption:
    start: float
    end: float
    text: str


@dataclass(frozen=True, slots=True)
class TimedWord:
    start: float
    end: float
    text: str


def alignment_words(alignment: dict, offset: float = 0.0) -> list[TimedWord]:
    characters = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    if not (len(characters) == len(starts) == len(ends)):
        return []

    result: list[TimedWord] = []
    buffer: list[str] = []
    word_start = 0.0
    word_end = 0.0
    for character, start, end in zip(characters, starts, ends):
        character = str(character)
        if character.isspace():
            if buffer:
                result.append(TimedWord(word_start + offset, word_end + offset, "".join(buffer)))
                buffer = []
            continue
        if not buffer:
            word_start = float(start)
        buffer.append(character)
        word_end = float(end)
    if buffer:
        result.append(TimedWord(word_start + offset, word_end + offset, "".join(buffer)))
    return result


def words_to_captions(
    words: list[TimedWord],
    *,
    max_words: int = 5,
    max_characters: int = 28,
    max_duration: float = 2.4,
) -> list[Caption]:
    captions: list[Caption] = []
    current: list[TimedWord] = []
    for word in words:
        candidate = " ".join(item.text for item in [*current, word])
        candidate_duration = word.end - (current[0].start if current else word.start)
        if current and (
            len(current) >= max_words
            or len(candidate) > max_characters
            or candidate_duration > max_duration
        ):
            captions.append(
                Caption(
                    current[0].start,
                    max(current[-1].end, current[0].start + 0.35),
                    " ".join(item.text for item in current),
                )
            )
            current = []
        current.append(word)
    if current:
        captions.append(
            Caption(
                current[0].start,
                max(current[-1].end, current[0].start + 0.35),
                " ".join(item.text for item in current),
            )
        )
    return captions


def captions_for_segments(
    segments: list[EditSegment], clip_durations: list[float]
) -> list[Caption]:
    captions: list[Caption] = []
    offset = 0.0
    for segment, duration in zip(segments, clip_durations):
        captions.extend(words_to_captions(alignment_words(segment.alignment, offset)))
        offset += duration
    return captions


def _srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def write_srt(captions: list[Caption], destination: str | Path) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    blocks = [
        f"{index}\n{_srt_timestamp(item.start)} --> {_srt_timestamp(item.end)}\n{item.text}"
        for index, item in enumerate(captions, start=1)
    ]
    destination.write_text("\n\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8")
    return destination


def _ass_timestamp(seconds: float) -> str:
    centiseconds = max(0, int(round(seconds * 100)))
    hours, centiseconds = divmod(centiseconds, 360_000)
    minutes, centiseconds = divmod(centiseconds, 6_000)
    secs, centiseconds = divmod(centiseconds, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


def _ass_text(text: str) -> str:
    safe = text.replace("{", "(").replace("}", ")").replace("\n", " ")
    words = safe.split()
    if len(safe) <= 24 or len(words) < 4:
        wrapped = safe
    else:
        midpoint = len(words) // 2
        wrapped = " ".join(words[:midpoint]) + r"\N" + " ".join(words[midpoint:])
    return r"{\fad(80,100)}" + wrapped


def write_ass(
    captions: list[Caption],
    destination: str | Path,
    *,
    width: int = 1080,
    height: int = 1920,
) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    font_size = max(34, round(width * 0.075))
    margin_v = round(height * 0.20)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Narracion,Bahnschrift,{font_size},&H00E8F1F7,&H0000D7FF,&H00101012,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,80,80,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [
        "Dialogue: 0,"
        f"{_ass_timestamp(item.start)},{_ass_timestamp(item.end)},"
        f"Narracion,,0,0,0,,{_ass_text(item.text)}"
        for item in captions
    ]
    destination.write_text(header + "\n".join(lines) + "\n", encoding="utf-8-sig")
    return destination
