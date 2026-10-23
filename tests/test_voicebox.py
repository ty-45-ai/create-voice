"""The Voicebox engine against a small fake of Voicebox's REST API (routes as in
jamiepine/voicebox backend/routes: profiles, samples, generate, history, audio)."""

import io
import json
import re
import threading
import uuid
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.engines.voicebox import VoiceboxEngine, VoiceboxError


def wav_bytes(seconds=1.0, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate))
    return buf.getvalue()


class FakeVoicebox:
    def __init__(self):
        self.profiles = {}      # id -> dict
        self.samples = {}       # profile id -> [(filename, reference_text)]
        self.generations = {}   # id -> dict
        self.polls = 0
        self.fail_next = False


def make_handler(state: FakeVoicebox):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj=None, raw=None, ctype="application/json"):
            body = raw if raw is not None else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            return self.rfile.read(int(self.headers.get("Content-Length", 0)))

        def do_GET(self):
            if self.path == "/health":
                return self._send(200, {"status": "healthy"})
            if self.path == "/profiles":
                return self._send(200, list(state.profiles.values()))
            if self.path == "/profiles/presets/kokoro":
                return self._send(200, {"engine": "kokoro", "voices": [
                    {"voice_id": "af_heart", "name": "Heart", "gender": "female", "language": "en"}]})
            if self.path == "/profiles/presets/qwen_custom_voice":
                return self._send(200, {"engine": "qwen_custom_voice", "voices": [
                    {"voice_id": "Ryan", "name": "Ryan", "gender": "male", "language": "en"},
                    {"voice_id": "Ono_Anna", "name": "Ono Anna", "gender": "female", "language": "ja"}]})
            m = re.fullmatch(r"/history/([\w-]+)", self.path)
            if m:
                gen = state.generations.get(m.group(1))
                if not gen:
                    return self._send(404, {"detail": "Generation not found"})
                state.polls += 1
                if state.polls >= 2 and gen["status"] == "generating":
                    gen["status"] = "failed" if gen.pop("fail", False) else "completed"
                return self._send(200, gen)
            m = re.fullmatch(r"/audio/([\w-]+)", self.path)
            if m and m.group(1) in state.generations:
                return self._send(200, raw=wav_bytes(), ctype="audio/wav")
            return self._send(404, {"detail": "Not Found"})

        def do_POST(self):
            if self.path == "/profiles":
                data = json.loads(self._body())
                if any(p["name"] == data["name"] for p in state.profiles.values()):
                    return self._send(400, {"detail": "already exists"})
                pid = uuid.uuid4().hex
                state.profiles[pid] = {"id": pid, **data}
                state.samples[pid] = []
                return self._send(200, state.profiles[pid])
            m = re.fullmatch(r"/profiles/([\w-]+)/samples", self.path)
            if m:
                body = self._body()
                text = re.search(rb'name="reference_text"\r\n\r\n(.*?)\r\n', body, re.S).group(1).decode()
                fname = re.search(rb'filename="([^"]+)"', body).group(1).decode()
                assert b"RIFF" in body
                state.samples[m.group(1)].append((fname, text))
                return self._send(200, {"id": uuid.uuid4().hex, "profile_id": m.group(1),
                                        "audio_path": fname, "reference_text": text})
            if self.path == "/generate":
                data = json.loads(self._body())
                if data["profile_id"] not in state.profiles:
                    return self._send(404, {"detail": "Profile not found"})
                gid = uuid.uuid4().hex
                state.polls = 0
                state.generations[gid] = {"id": gid, "status": "generating", "error": None, **data}
                if state.fail_next:
                    state.generations[gid]["fail"] = True
                    state.generations[gid]["error"] = "model exploded"
                    state.fail_next = False
                return self._send(200, state.generations[gid])
            return self._send(404, {"detail": "Not Found"})

        def do_DELETE(self):
            m = re.fullmatch(r"/profiles/([\w-]+)", self.path)
            if m and m.group(1) in state.profiles:
                del state.profiles[m.group(1)]
                state.samples.pop(m.group(1), None)
                return self._send(200, {"message": "deleted"})
            m = re.fullmatch(r"/history/([\w-]+)", self.path)
            if m and m.group(1) in state.generations:
                del state.generations[m.group(1)]
                return self._send(200, {"message": "deleted"})
            return self._send(404, {"detail": "Not Found"})

    return Handler


@pytest.fixture
def voicebox():
    state = FakeVoicebox()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield state, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def recordings(tmp_path, student="P001", n=2):
    d = tmp_path / student
    d.mkdir()
    paths = []
    for i in range(1, n + 1):
        p = d / f"{i}.wav"
        p.write_bytes(wav_bytes(2.0))
        paths.append(p)
    return paths


def test_clone_generate_and_cleanup(voicebox, tmp_path):
    state, url = voicebox
    engine = VoiceboxEngine(base_url=url, poll_seconds=0.01)
    refs = recordings(tmp_path)
    out = tmp_path / "out.wav"
    engine.synthesize("I like reading books.", "en", refs, out,
                      reference_texts=["I like reading books.", "My favorite color is blue."])
    assert out.read_bytes()[:4] == b"RIFF"
    (profile,) = state.profiles.values()
    assert profile["name"].startswith("ondoku-P001-") and profile["voice_type"] == "cloned"
    # Two 2-second readings are too short for Voicebox (2 s minimum) alone, so they are joined.
    assert [t for _, t in state.samples[profile["id"]]] == ["I like reading books. My favorite color is blue."]
    assert state.generations == {}  # removed from Voicebox history after download

    # Same recordings: the profile is reused, not duplicated.
    engine.synthesize("Hello.", "en", refs, out, reference_texts=["I like reading books.", "My favorite color is blue."])
    assert len(state.profiles) == 1

    # New recordings for the same student replace the old profile.
    refs[0].write_bytes(wav_bytes(3.0))
    engine.synthesize("Hello.", "en", refs, out, reference_texts=["Again.", "My favorite color is blue."])
    assert len(state.profiles) == 1 and profile["id"] not in state.profiles

    engine.forget("P001")
    assert state.profiles == {}


def test_standard_voice_preset(voicebox, tmp_path):
    state, url = voicebox
    engine = VoiceboxEngine(base_url=url, poll_seconds=0.01)
    assert engine.standard_voices() == ["kokoro:af_heart", "qwen_custom_voice:Ryan", "qwen_custom_voice:Ono_Anna"]
    out = tmp_path / "std.wav"
    engine.synthesize_standard("I like reading books.", "en", "Ana Florence", out)  # XTTS name -> default
    (profile,) = state.profiles.values()
    assert profile["voice_type"] == "preset" and profile["preset_engine"] == "kokoro"
    assert profile["preset_voice_id"] == "af_heart"
    with pytest.raises(VoiceboxError):
        engine.synthesize_standard("Hi.", "en", "nope:x", out)


def test_failed_generation_and_unreachable(voicebox, tmp_path):
    state, url = voicebox
    engine = VoiceboxEngine(base_url=url, poll_seconds=0.01)
    state.fail_next = True
    with pytest.raises(VoiceboxError, match="model exploded"):
        engine.synthesize("Hi.", "en", recordings(tmp_path), tmp_path / "x.wav", reference_texts=["a", "b"])
    assert state.generations == {}
    with pytest.raises(VoiceboxError, match="Voicebox を起動"):
        VoiceboxEngine(base_url="http://127.0.0.1:9")


def test_app_end_to_end_with_voicebox(voicebox, tmp_path):
    """Whole app flow with the Voicebox engine: submit -> clone + stock voice -> listen -> delete."""
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    from tests.test_app import login, make_wav

    state, url = voicebox
    app = create_app(Settings(data_dir=tmp_path / "data", engine="voicebox", admin_password="pw"),
                     start_worker=False, engine_factory=lambda: VoiceboxEngine(base_url=url, poll_seconds=0.01))
    with TestClient(app) as admin, TestClient(app) as kid:
        admin.post("/api/admin/login", json={"password": "pw"})
        admin.post("/api/admin/sentences", json={"text": "I like reading books."})
        (p,) = admin.post("/api/admin/participants", json={"count": 1}).json()
        login(kid, p)
        sid = kid.get("/api/me").json()["items"][0]["sentence_id"]
        kid.post(f"/api/recordings/{sid}", files={"file": ("v.wav", make_wav(4.0), "audio/wav")})
        assert kid.post("/api/submit").status_code == 200
        while app.state.worker.process_once():
            pass
        audios = kid.get("/api/me").json()["items"][0]["audios"]
        assert all(a["url"] for a in audios), audios
        names = sorted(pr["name"] for pr in state.profiles.values())
        assert names[0].startswith("ondoku-P001-") and names[1] == "ondoku-standard-kokoro-af_heart-en"
        # Deleting the student in the app removes their voice from Voicebox too.
        admin.delete(f"/api/admin/participants/{p['id']}")
        assert [pr["name"] for pr in state.profiles.values()] == ["ondoku-standard-kokoro-af_heart-en"]


def test_japanese_text_gets_a_japanese_stock_voice(voicebox, tmp_path):
    state, url = voicebox
    engine = VoiceboxEngine(base_url=url, poll_seconds=0.01)
    assert engine.default_standard_voice("en") == "kokoro:af_heart"
    assert engine.default_standard_voice("ja") == "qwen_custom_voice:Ono_Anna"
    engine.synthesize_standard("私は本日、情報基礎の授業を受けました。", "ja", "", tmp_path / "ja.wav")
    (profile,) = state.profiles.values()
    assert profile["preset_engine"] == "qwen_custom_voice" and profile["preset_voice_id"] == "Ono_Anna"
    assert profile["language"] == "ja"
