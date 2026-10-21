"""Turn a student's recordings into voice samples an engine accepts.

Voicebox only takes samples of 2-30 s, each with the exact text spoken in it. A short sentence
("I like reading books." ~1.5 s) is too short and a read-aloud passage is often too long, so:
- short recordings are joined (with a small pause) until a sample is long enough;
- a long recording is cut at the pause closest to a sentence boundary, keeping the sentences
  before the cut as the sample's text.
Pure standard library: works on the 16-bit PCM WAV files the browser uploads.
"""

from __future__ import annotations

import array
import math
import re
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

MIN_SECONDS = 3.0      # Voicebox minimum is 2 s; keep a margin after its own silence trimming
MAX_SECONDS = 25.0     # Voicebox maximum is 30 s
FRAME = 0.02
SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+|(?<=[。！？])")


@dataclass
class Clip:
    pcm: array.array      # mono int16
    rate: int
    text: str

    @property
    def seconds(self) -> float:
        return len(self.pcm) / self.rate


def read_clip(path: Path, text: str) -> Clip | None:
    try:
        with wave.open(str(path), "rb") as w:
            if w.getsampwidth() != 2:
                return None
            channels, rate = w.getnchannels(), w.getframerate()
            pcm = array.array("h", w.readframes(w.getnframes()))
    except (wave.Error, OSError, EOFError):
        return None
    if sys.byteorder == "big":
        pcm.byteswap()
    if channels == 2:
        pcm = array.array("h", ((pcm[i] + pcm[i + 1]) // 2 for i in range(0, len(pcm) - 1, 2)))
    return Clip(pcm, rate, text.strip())


def write_clip(clip: Clip, path: Path) -> None:
    pcm = array.array("h", clip.pcm)
    if sys.byteorder == "big":
        pcm.byteswap()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(clip.rate)
        w.writeframes(pcm.tobytes())


def _frame_levels(clip: Clip) -> list[float]:
    n = max(1, int(clip.rate * FRAME))
    levels = []
    for i in range(0, len(clip.pcm), n):
        chunk = clip.pcm[i:i + n]
        levels.append(math.sqrt(sum(s * s for s in chunk) / max(1, len(chunk))))
    return levels


def _pauses(clip: Clip, min_pause: float = 0.2) -> list[float]:
    """Centres (seconds) of the quiet stretches inside a clip."""
    levels = _frame_levels(clip)
    if not levels:
        return []
    threshold = max(levels) * 0.06
    pauses, run = [], 0
    for i, level in enumerate(levels + [threshold + 1]):
        if level <= threshold:
            run += 1
            continue
        if run * FRAME >= min_pause and i - run > 0:
            pauses.append((i - run / 2) * FRAME)
        run = 0
    return pauses


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_END.split(text.strip()) if s.strip()]


def joiner(text: str) -> str:
    return "" if re.search(r"[぀-ヿ㐀-鿿]", text) else " "


def cut_long(clip: Clip, max_seconds: float = MAX_SECONDS) -> Clip | None:
    """Keep the first sentences of a long reading, cut at the pause nearest their estimated end."""
    sentences = split_sentences(clip.text) or [clip.text]
    total_chars = sum(len(s) for s in sentences) or 1
    pauses = _pauses(clip)
    sep = joiner(clip.text)
    cum = 0
    best = None
    for k, sentence in enumerate(sentences, 1):
        cum += len(sentence)
        estimate = clip.seconds * cum / total_chars
        # Reading speed varies, so look for a pause within ~20 % of the estimate.
        tolerance = max(2.0, 0.2 * estimate)
        candidates = [p for p in pauses if abs(p - estimate) <= tolerance and MIN_SECONDS <= p <= max_seconds]
        if candidates:
            cut = min(candidates, key=lambda p: abs(p - estimate))
            best = (cut, sep.join(sentences[:k]))
        if estimate - tolerance > max_seconds:
            break
    if best is None:
        return None
    cut, text = best
    return Clip(clip.pcm[: int(cut * clip.rate)], clip.rate, text)


def build_samples(recordings: list[tuple[Path, str]], max_total: float = 30.0,
                  max_samples: int = 6) -> list[Clip]:
    """Samples between MIN_SECONDS and MAX_SECONDS, each with the text spoken in it."""
    samples: list[Clip] = []
    pending: Clip | None = None

    def add(clip: Clip) -> None:
        if MIN_SECONDS <= clip.seconds <= MAX_SECONDS and clip.text:
            samples.append(clip)

    for path, text in recordings:
        clip = read_clip(path, text)
        if clip is None or not clip.text:
            continue
        if clip.seconds > MAX_SECONDS:
            cut = cut_long(clip)
            if cut:
                add(cut)
        elif clip.seconds >= MIN_SECONDS:
            add(clip)
        else:
            if pending is None:
                pending = clip
            elif pending.rate == clip.rate and pending.seconds + clip.seconds + 0.3 <= MAX_SECONDS:
                gap = array.array("h", bytes(2 * int(0.3 * clip.rate)))
                pending = Clip(pending.pcm + gap + clip.pcm, clip.rate,
                               pending.text + joiner(pending.text) + clip.text)
            if pending is not None and pending.seconds >= MIN_SECONDS:
                add(pending)
                pending = None
        if len(samples) >= max_samples or sum(s.seconds for s in samples) >= max_total:
            break
    return samples[:max_samples]
