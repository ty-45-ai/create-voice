"""Coqui XTTS-v2 via the `coqui-tts` package (pip only, no separate WebUI).

- Japanese and English from one model; clones a voice from a ~6 s or longer sample.
- Model weights (~1.8 GB) download on first use into the coqui cache.
- Weights are under the Coqui Public Model License (non-commercial use). Read it, then set
  COQUI_TOS_AGREED=1 to confirm; the engine will not start otherwise.
"""

from __future__ import annotations

import os
from pathlib import Path

from .postprocess import too_long, trim_tail, write_wav

MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"
LANGS = {"ja": "ja", "en": "en"}

# Steadier than the model's shipped defaults (temperature 0.85, repetition_penalty 2.0),
# which on short sentences tend to repeat words after the sentence ends.
GENERATION = {
    "temperature": 0.65,
    "repetition_penalty": 10.0,
    "top_k": 30,
    "top_p": 0.8,
    "length_penalty": 1.0,
}
MAX_TAKES = 3


class XTTSEngine:
    name = "xtts"

    def __init__(self, device: str | None = None):
        if os.environ.get("COQUI_TOS_AGREED") != "1":
            raise RuntimeError(
                "XTTS-v2 の利用規約 (Coqui Public Model License) を確認のうえ、"
                ".env に COQUI_TOS_AGREED=1 を設定してください。"
            )
        import torch
        from TTS.api import TTS

        if device is None:
            # XTTS on Apple's MPS backend is unreliable; Macs use the CPU.
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        self.tts = TTS(MODEL_NAME).to(device)

    def synthesize(self, text: str, lang: str, references: list[Path], out_wav: Path,
                   expected_seconds: float | None = None, reference_texts: list[str] | None = None) -> None:
        # XTTS conditions on up to ~30 s of speech; several clips are combined.
        refs, total = [], 0.0
        for ref in references:
            refs.append(str(ref))
            total += _seconds(ref)
            if total >= 30:
                break
        self._generate(out_wav, expected_seconds, text=text, language=LANGS.get(lang, "en"), speaker_wav=refs)

    def synthesize_standard(self, text: str, lang: str, voice: str, out_wav: Path,
                            expected_seconds: float | None = None) -> None:
        voice = voice or self.default_standard_voice(lang)
        voices = self.standard_voices()
        if voices and voice not in voices:
            raise ValueError(f"標準の声「{voice}」はありません。候補: {', '.join(voices[:8])} など")
        self._generate(out_wav, expected_seconds, text=text, language=LANGS.get(lang, "en"), speaker=voice)

    def standard_voices(self) -> list[str]:
        return list(self.tts.speakers or [])

    def default_standard_voice(self, lang: str) -> str:
        return "Ana Florence"

    def _generate(self, out_wav: Path, expected_seconds: float | None, **inputs) -> None:
        import numpy as np

        sr = self.tts.synthesizer.output_sample_rate
        best = None
        for _ in range(MAX_TAKES):
            wav = np.asarray(self.tts.tts(**inputs, **GENERATION), dtype=np.float32)
            wav = trim_tail(wav, sr, expected_seconds)
            seconds = len(wav) / sr
            if best is None or seconds < best[0]:
                best = (seconds, wav)
            if not too_long(seconds, expected_seconds):
                break
        write_wav(out_wav, best[1], sr)


def _seconds(path: Path) -> float:
    import wave

    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except (wave.Error, OSError, EOFError):
        return 0.0
