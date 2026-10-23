"""Voicebox (https://github.com/jamiepine/voicebox, MIT) as the speech engine.

Voicebox is a desktop app with a local REST API on http://127.0.0.1:17493. It must be running
on the same computer. Nothing goes to the internet except Voicebox's own model downloads.

- AI self-voice: one cloned voice profile per student, built from their recordings and the
  sentences they read (Voicebox needs the transcript of every sample).
- Stock voice: a preset profile, e.g. "kokoro:af_heart" (engine:voice_id). Unless the researcher
  picks one, a voice that speaks the sentence's language is chosen (Kokoro's af_heart for
  English; a Japanese speaker, preferably of Qwen CustomVoice, for Japanese).
- Generation goes through POST /generate, which downloads a missing model by itself; the
  finished audio is fetched and the entry is deleted from Voicebox's history again.

Settings (.env): VOICEBOX_URL, VOICEBOX_ENGINE (default qwen), VOICEBOX_MODEL_SIZE (0.6B).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
import uuid
from pathlib import Path
from urllib import error, request

from .samples import build_samples, write_clip

log = logging.getLogger("ondoku.voicebox")

PROFILE_PREFIX = "ondoku-"
STANDARD_PREFIX = "ondoku-standard-"
PRESET_ENGINES = ("kokoro", "qwen_custom_voice")
LANGS = {"ja": "ja", "en": "en"}
LANGUAGE_NAMES = {"ja": ("ja", "japanese", "日本語"), "en": ("en", "english", "英語")}
# Kokoro voice ids start with a language letter: a/b = American/British English, j = Japanese.
KOKORO_LETTERS = {"ja": ("j",), "en": ("a", "b")}
DEFAULT_VOICE = {"en": "kokoro:af_heart"}


class VoiceboxError(RuntimeError):
    pass


class VoiceboxEngine:
    name = "voicebox"

    def __init__(self, device: str | None = None, base_url: str | None = None,
                 timeout: float = 1800.0, poll_seconds: float = 1.0):
        self.base_url = (base_url or os.environ.get("VOICEBOX_URL") or "http://127.0.0.1:17493").rstrip("/")
        self.clone_engine = os.environ.get("VOICEBOX_ENGINE") or "qwen"
        self.model_size = os.environ.get("VOICEBOX_MODEL_SIZE") or "0.6B"
        self.timeout = timeout
        self.poll_seconds = poll_seconds
        self._profiles: dict[str, str] = {}  # profile name -> id
        self._preset_cache: list[dict] | None = None
        try:
            self._call("GET", "/health")
        except VoiceboxError as exc:
            raise VoiceboxError(
                f"Voicebox に接続できません（{self.base_url}）。Voicebox を起動してから、もう一度試してください。"
            ) from exc

    # ---- HTTP -----------------------------------------------------------------

    def _call(self, method: str, path: str, body: dict | None = None, *, data: bytes | None = None,
              content_type: str | None = None, raw: bool = False, timeout: float = 120.0):
        headers = {}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        elif content_type:
            headers["Content-Type"] = content_type
        req = request.Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read()
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                detail = json.loads(detail).get("detail", detail)
            except (ValueError, AttributeError):
                pass
            raise VoiceboxError(f"Voicebox {method} {path}: {exc.code} {detail}") from exc
        except (error.URLError, OSError) as exc:
            raise VoiceboxError(f"Voicebox {method} {path}: {exc}") from exc
        if raw:
            return payload
        return json.loads(payload) if payload else None

    def _upload_sample(self, profile_id: str, audio: bytes, reference_text: str) -> None:
        boundary = uuid.uuid4().hex
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="reference_text"\r\n\r\n'.encode()
            + reference_text.encode("utf-8") + b"\r\n",
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sample.wav"\r\n'
            f"Content-Type: audio/wav\r\n\r\n".encode() + audio + b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
        self._call("POST", f"/profiles/{profile_id}/samples", data=b"".join(parts),
                   content_type=f"multipart/form-data; boundary={boundary}")

    # ---- profiles ---------------------------------------------------------------

    def _list_profiles(self) -> list[dict]:
        return self._call("GET", "/profiles") or []

    def _find_profile(self, name: str) -> str | None:
        if name in self._profiles:
            return self._profiles[name]
        for p in self._list_profiles():
            if p.get("name") == name:
                self._profiles[name] = p["id"]
                return p["id"]
        return None

    def _delete_profile(self, profile_id: str) -> None:
        try:
            self._call("DELETE", f"/profiles/{profile_id}")
        except VoiceboxError as exc:
            log.warning("could not delete Voicebox profile %s: %s", profile_id, exc)
        self._profiles = {k: v for k, v in self._profiles.items() if v != profile_id}

    def _clone_profile(self, references: list[Path], texts: list[str], lang: str) -> str:
        """One profile per student and set of recordings; re-recording makes a fresh one and
        the student's older profiles are removed."""
        student = references[0].parent.name
        recordings = [(Path(r), t) for r, t in zip(references, texts) if t and Path(r).is_file()]
        if not recordings:
            raise VoiceboxError("見本にできる録音がありません")
        digest = hashlib.sha1(
            "|".join(f"{p}:{p.stat().st_mtime_ns}:{t}" for p, t in recordings).encode()
        ).hexdigest()[:10]
        name = f"{PROFILE_PREFIX}{student}-{digest}"
        found = self._find_profile(name)
        if found:
            return found
        for p in self._list_profiles():
            if p.get("name", "").startswith(f"{PROFILE_PREFIX}{student}-"):
                self._delete_profile(p["id"])
        created = self._call("POST", "/profiles", {
            "name": name,
            "description": "音読練習アプリが作成（生徒の声）",
            "language": LANGS.get(lang, "en"),
            "voice_type": "cloned",
        })
        try:
            # Voicebox accepts 2-30 s per sample: join short readings, cut long passages.
            samples = build_samples(recordings)
            if not samples:
                raise VoiceboxError("見本の録音が短すぎます（合計3秒以上、はっきり読んだ録音が必要です）")
            with tempfile.TemporaryDirectory() as tmp:
                for i, clip in enumerate(samples):
                    path = Path(tmp) / f"{i}.wav"
                    write_clip(clip, path)
                    self._upload_sample(created["id"], path.read_bytes(), clip.text)
        except VoiceboxError:
            self._delete_profile(created["id"])
            raise
        self._profiles[name] = created["id"]
        return created["id"]

    def _preset_profile(self, voice: str, lang: str) -> tuple[str, str]:
        engine, _, voice_id = voice.partition(":")
        if engine not in PRESET_ENGINES or not voice_id:
            raise VoiceboxError(f"標準の声は「kokoro:af_heart」のように指定してください（今: {voice}）")
        name = f"{STANDARD_PREFIX}{engine}-{voice_id}-{LANGS.get(lang, 'en')}"
        found = self._find_profile(name)
        if found:
            return found, engine
        created = self._call("POST", "/profiles", {
            "name": name,
            "description": "音読練習アプリの標準AI音声",
            "language": LANGS.get(lang, "en"),
            "voice_type": "preset",
            "preset_engine": engine,
            "preset_voice_id": voice_id,
        })
        self._profiles[name] = created["id"]
        return created["id"], engine

    def forget(self, student: str) -> None:
        """Delete a student's voice profiles (and samples) from Voicebox."""
        for p in self._list_profiles():
            if p.get("name", "").startswith(f"{PROFILE_PREFIX}{student}-"):
                self._delete_profile(p["id"])

    # ---- generation -------------------------------------------------------------

    def _generate(self, profile_id: str, text: str, lang: str, engine: str, out_wav: Path,
                  model_size: str | None = None) -> None:
        body = {"profile_id": profile_id, "text": text, "language": LANGS.get(lang, "en"), "engine": engine}
        if model_size:
            body["model_size"] = model_size
        gen = self._call("POST", "/generate", body)
        gen_id = gen["id"]
        try:
            deadline = time.monotonic() + self.timeout
            status = gen
            while status.get("status") not in ("completed", "failed", "cancelled"):
                if time.monotonic() > deadline:
                    self._call("POST", f"/generate/{gen_id}/cancel")
                    raise VoiceboxError("Voicebox の生成が時間内に終わりませんでした")
                time.sleep(self.poll_seconds)
                status = self._call("GET", f"/history/{gen_id}")
            if status.get("status") != "completed":
                raise VoiceboxError(f"Voicebox の生成に失敗しました: {status.get('error') or status.get('status')}")
            out_wav.write_bytes(self._call("GET", f"/audio/{gen_id}", raw=True, timeout=300))
        finally:
            # Keep no copy of the student's generated voice inside Voicebox.
            try:
                self._call("DELETE", f"/history/{gen_id}")
            except VoiceboxError:
                pass

    def synthesize(self, text: str, lang: str, references: list[Path], out_wav: Path,
                   expected_seconds: float | None = None, reference_texts: list[str] | None = None) -> None:
        texts = reference_texts or []
        if len(texts) < len(references):
            raise VoiceboxError("Voicebox には見本の録音ごとに読んだ文が必要です")
        profile_id = self._clone_profile([Path(r) for r in references], texts, lang)
        self._generate(profile_id, text, lang, self.clone_engine, out_wav,
                       model_size=self.model_size if self.clone_engine in ("qwen", "tada") else None)

    def synthesize_standard(self, text: str, lang: str, voice: str, out_wav: Path,
                            expected_seconds: float | None = None) -> None:
        if ":" not in voice:  # not chosen (or a name from another engine)
            voice = self.default_standard_voice(lang)
        profile_id, engine = self._preset_profile(voice, lang)
        self._generate(profile_id, text, lang, engine, out_wav,
                       model_size=self.model_size if engine == "qwen_custom_voice" else None)

    def _presets(self) -> list[dict]:
        """Preset voices of every preset engine: {"voice": "engine:id", "engine", "id", "language"}."""
        if self._preset_cache is None:
            voices = []
            for engine in PRESET_ENGINES:
                try:
                    listed = self._call("GET", f"/profiles/presets/{engine}") or {}
                except VoiceboxError:
                    continue
                for v in listed.get("voices", []):
                    voices.append({"voice": f"{engine}:{v['voice_id']}", "engine": engine, "id": v["voice_id"],
                                   "language": str(v.get("language") or "").lower()})
            if not voices:
                return []  # Voicebox not answering yet: ask again next time
            self._preset_cache = voices
        return self._preset_cache

    def standard_voices(self) -> list[str]:
        return [v["voice"] for v in self._presets()]

    @staticmethod
    def _speaks(voice: dict, lang: str) -> bool:
        names = LANGUAGE_NAMES.get(lang, (lang,))
        if voice["language"]:
            return any(voice["language"].startswith(n) for n in names)
        return voice["engine"] == "kokoro" and voice["id"].startswith(KOKORO_LETTERS.get(lang, ()))

    def default_standard_voice(self, lang: str = "en") -> str:
        presets = self._presets()
        wanted = DEFAULT_VOICE.get(lang)
        if wanted and (not presets or any(v["voice"] == wanted for v in presets)):
            return wanted
        for engine in ("qwen_custom_voice", "kokoro"):
            for v in presets:
                if v["engine"] == engine and self._speaks(v, lang):
                    return v["voice"]
        # Qwen CustomVoice speakers can read every language it supports.
        for v in presets:
            if v["engine"] == "qwen_custom_voice":
                return v["voice"]
        if not presets:
            raise VoiceboxError("Voicebox から既成の声の一覧を取得できませんでした")
        raise VoiceboxError(f"この言語（{lang}）を話せる既成の声が Voicebox にありません。"
                            "管理画面「設定・出力」で標準AI音声の声を指定してください")

