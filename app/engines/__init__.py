"""Speech engines. Each engine clones the voice in a reference WAV and reads a sentence aloud."""

from __future__ import annotations

from typing import Protocol
from pathlib import Path


class Engine(Protocol):
    name: str

    def synthesize(self, text: str, lang: str, references: list[Path], out_wav: Path,
                   expected_seconds: float | None = None) -> None:
        """Write speech for `text` (lang 'ja' or 'en') in the voice heard in `references`
        (the student's own recordings, longest first) to `out_wav`. `expected_seconds` is how
        long the student took to read this text, used to cut off extra generated speech."""

    def synthesize_standard(self, text: str, lang: str, voice: str, out_wav: Path,
                            expected_seconds: float | None = None) -> None:
        """Write speech for `text` in the engine's built-in stock voice `voice` (not cloned)."""

    def standard_voices(self) -> list[str]:
        """Names of the built-in stock voices, for the admin to choose from."""


def estimate_seconds(text: str, lang: str) -> float:
    """Rough reading time, used to trim stock-voice output when no recording exists."""
    per_second = 7.0 if lang == "ja" else 14.0
    return len(text.strip()) / per_second + 0.3


ENGINE_NAMES = ("xtts", "chatterbox", "dummy")


def load_engine(name: str, device: str | None = None) -> Engine:
    if name == "dummy":
        from .dummy import DummyEngine

        return DummyEngine()
    if name == "xtts":
        from .xtts import XTTSEngine

        return XTTSEngine(device=device)
    if name == "chatterbox":
        from .chatterbox import ChatterboxEngine

        return ChatterboxEngine(device=device)
    raise ValueError(f"unknown engine {name!r}; choose one of {', '.join(ENGINE_NAMES)}")
