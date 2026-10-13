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
