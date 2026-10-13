"""Clean-up for autoregressive TTS output.

XTTS-v2 sometimes keeps talking after the sentence ends ("...books. I, I, I"), especially for
short sentences. We know roughly how long the sentence should take, because the student read
the same sentence aloud, so we cut at the first real pause after that point and, if the result
is still far too long, ask for another take.
"""

from __future__ import annotations

import numpy as np

FRAME_SECONDS = 0.02


def _voiced_frames(wav: np.ndarray, sr: int) -> np.ndarray:
    frame = max(1, int(sr * FRAME_SECONDS))
    n = len(wav) // frame
    if n == 0:
        return np.zeros(0, dtype=bool)
    rms = np.sqrt(np.mean(wav[: n * frame].reshape(n, frame) ** 2, axis=1))
    peak = float(rms.max()) if rms.size else 0.0
    return rms > max(peak * 0.08, 1e-4)


def trim_tail(wav: np.ndarray, sr: int, expected_seconds: float | None,
              min_gap: float = 0.4, pad: float = 0.15) -> np.ndarray:
    """Cut trailing babble: keep speech up to the first pause of `min_gap` seconds that starts
    after ~80 % of the expected length; always drop trailing silence."""
    voiced = _voiced_frames(wav, sr)
    if not voiced.any():
        return wav
    frame = int(sr * FRAME_SECONDS)
    last = int(np.nonzero(voiced)[0][-1])
    if expected_seconds:
        start = int(expected_seconds * 0.8 / FRAME_SECONDS)
        gap_frames = int(min_gap / FRAME_SECONDS)
        run = 0
        for i in range(max(start, 1), last + 1):
            if voiced[i]:
                run = 0
                continue
            run += 1
            if run >= gap_frames and voiced[: i - run + 1].any():
                last = i - run
                break
    end = min(len(wav), (last + 1) * frame + int(pad * sr))
    return wav[:end]


def too_long(seconds: float, expected_seconds: float | None) -> bool:
    """A take that is still this long after trimming most likely contains extra words."""
    if not expected_seconds:
        return False
    return seconds > expected_seconds * 1.6 + 0.8


def write_wav(path, wav: np.ndarray, sr: int) -> None:
    import wave

    pcm = (np.clip(wav, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
