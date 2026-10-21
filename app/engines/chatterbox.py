"""Resemble AI Chatterbox Multilingual (MIT licence), an alternative to XTTS-v2.

Experimental here: install with `pip install chatterbox-tts` in a separate virtual
environment, since it pins its own torch version. Supports 'ja' and 'en'.
"""

from __future__ import annotations

from pathlib import Path


class ChatterboxEngine:
    name = "chatterbox"

    def __init__(self, device: str | None = None):
        import torch
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS

        if device is None:
            if torch.cuda.is_available():
                device = "cuda"
            elif torch.backends.mps.is_available():
                device = "mps"
            else:
                device = "cpu"
        self.device = device
        self.model = ChatterboxMultilingualTTS.from_pretrained(device=device)

    def synthesize(self, text: str, lang: str, references: list[Path], out_wav: Path,
                   expected_seconds: float | None = None, reference_texts: list[str] | None = None) -> None:
        self._generate(text, lang, str(references[0]), out_wav)

    def synthesize_standard(self, text: str, lang: str, voice: str, out_wav: Path,
                            expected_seconds: float | None = None) -> None:
        # Chatterbox's built-in default voice; it has no named stock voices.
        self._generate(text, lang, None, out_wav)

    def standard_voices(self) -> list[str]:
        return ["default"]

    def _generate(self, text: str, lang: str, prompt: str | None, out_wav: Path) -> None:
        import soundfile

        # Chatterbox takes a single prompt clip: the longest recording.
        wav = self.model.generate(text, language_id=lang, audio_prompt_path=prompt)
        soundfile.write(str(out_wav), wav.squeeze().cpu().numpy(), self.model.sr)
