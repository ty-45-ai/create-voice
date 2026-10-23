"""Background generation: one thread takes queued clips in order and runs the speech engine."""

from __future__ import annotations

import logging
import threading
import time
import traceback
import wave
from pathlib import Path
from typing import Callable

from .engines import Engine, estimate_seconds
from .store import Store

log = logging.getLogger("ondoku.worker")


def wav_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except (wave.Error, OSError, EOFError):
        return 0.0


class Worker:
    def __init__(self, store: Store, engine_factory: Callable[[], Engine]):
        self.store = store
        self._engine_factory = engine_factory
        self._engine: Engine | None = None
        self.engine_error: str | None = None
        self.engine_loading = False
        self.current_clip: int | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # The model loads lazily on the worker thread so the web server starts at once.
    def engine(self) -> Engine:
        if self._engine is None:
            self.engine_loading = True
            try:
                log.info("loading speech engine ...")
                self._engine = self._engine_factory()
                self.engine_error = None
                log.info("speech engine ready: %s", self._engine.name)
            except Exception as exc:  # noqa: BLE001 - surfaced to the admin page
                self.engine_error = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                self.engine_loading = False
        return self._engine

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="ondoku-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self.process_once()
            except Exception:  # noqa: BLE001 - keep the loop alive
                log.exception("worker loop error")
                worked = False
            if not worked:
                # Engine failed to load: retry every minute instead of spinning.
                self._wake.wait(60 if self.engine_error else 3)
                self._wake.clear()

    def process_once(self) -> bool:
        try:
            engine = self.engine()
        except Exception:  # noqa: BLE001
            log.error("speech engine failed to load: %s", self.engine_error)
            return False
        # The stock-voice clips are few and shared by everyone, so they go first.
        std = self.store.claim_next_standard()
        if std is not None:
            self._make_standard(engine, std)
            return True
        clip = self.store.claim_next_clip()
        if clip is None:
            return False
        self.current_clip = clip["id"]
        out = self.store.new_clip_path(clip["participant_id"])
        started = time.time()
        try:
            samples = self.store.reference_samples(clip["participant_id"])
            if not samples:
                raise RuntimeError("no recordings to use as the voice sample")
            own = self.store.get_recording(clip["participant_id"], clip["sentence_id"])
            engine.synthesize(clip["text"], clip["lang"], [p for p, _ in samples], out,
                              expected_seconds=own["seconds"] if own else None,
                              reference_texts=[t for _, t in samples])
            if not out.is_file() or out.stat().st_size == 0:
                raise RuntimeError("engine produced no audio")
            if not self.store.finish_clip(clip["id"], out, wav_seconds(out)):
                out.unlink(missing_ok=True)
            log.info("clip %s done in %.1fs", clip["id"], time.time() - started)
        except Exception as exc:  # noqa: BLE001
            out.unlink(missing_ok=True)
            log.warning("clip %s failed: %s", clip["id"], exc)
            self.store.fail_clip(clip["id"], f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}")
        finally:
            self.current_clip = None
        return True

    def _make_standard(self, engine: Engine, std) -> None:
        sid = std["sentence_id"]
        out = self.store.new_standard_path(sid)
        voice = self.store.standard_voice(std["lang"])
        try:
            engine.synthesize_standard(std["text"], std["lang"], voice, out,
                                       expected_seconds=estimate_seconds(std["text"], std["lang"]))
            if not out.is_file() or out.stat().st_size == 0:
                raise RuntimeError("engine produced no audio")
            if not self.store.finish_standard(sid, out, wav_seconds(out)):
                out.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            out.unlink(missing_ok=True)
            log.warning("standard voice for sentence %s failed: %s", sid, exc)
            self.store.fail_standard(sid, f"{type(exc).__name__}: {exc}")

    def standard_voices(self) -> list[str]:
        """Stock voices of the loaded engine (empty until the model has loaded)."""
        try:
            return self._engine.standard_voices() if self._engine else []
        except Exception:  # noqa: BLE001
            return []

    def forget_participant(self, pid: str) -> None:
        """Ask the engine to drop anything it keeps for this student (e.g. Voicebox profiles)."""
        forget = getattr(self._engine, "forget", None)
        if forget is None:
            return
        try:
            forget(pid)
        except Exception:  # noqa: BLE001
            log.exception("engine could not forget participant %s", pid)

    def default_standard_voice(self, lang: str) -> str:
        """The stock voice used for `lang` when the researcher has not chosen one."""
        fn = getattr(self._engine, "default_standard_voice", None)
        try:
            return fn(lang) if fn else ""
        except Exception:  # noqa: BLE001
            return ""
