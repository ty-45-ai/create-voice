import io
import math
import struct
import wave
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.engines.dummy import DummyEngine
from app.main import create_app
from app.store import balanced_conditions


def make_wav(seconds=2.0, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * 220 * i / rate))) for i in range(int(seconds * rate))
        ))
    return buf.getvalue()


def make_app(tmp_path, engine_factory=DummyEngine):
    settings = Settings(data_dir=tmp_path, engine="dummy", admin_password="secret-admin")
    return create_app(settings, start_worker=False, engine_factory=engine_factory)


@pytest.fixture
def env(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as admin:
        assert admin.post("/api/admin/login", json={"password": "secret-admin"}).status_code == 200
        yield app, admin, lambda: TestClient(app)


def drain(app):
    while app.state.worker.process_once():
        pass


def login(client, p):
    assert client.post("/api/login", json={"id": p["id"], "passcode": p["passcode"]}).status_code == 200


def record_all(client, me=None):
    me = me or client.get("/api/me").json()
    for item in me["items"]:
        r = client.post(f"/api/recordings/{item['sentence_id']}", files={"file": ("v.wav", make_wav(), "audio/wav")})
        assert r.status_code == 200, r.text


def setup_study(admin, n=2):
    admin.post("/api/admin/sentences", json={"text": "I like reading books."})
    admin.post("/api/admin/sentences", json={"text": "きょうは はれです。"})
    sids = [s["id"] for s in admin.get("/api/admin/sentences").json()]
    for sid in sids:
        admin.post(f"/api/admin/sentences/{sid}/native", files={"file": ("n.mp3", b"ID3fake-mp3", "audio/mpeg")})
    ai = admin.post("/api/admin/participants", json={"count": n, "class_name": "3-1", "condition": "ai"}).json()
    native = admin.post("/api/admin/participants", json={"count": n, "class_name": "3-1", "condition": "native"}).json()
    return ai, native


def test_home_record_then_school_listen(env):
    app, admin, client = env
    ai, native = setup_study(admin, n=1)
    admin.put("/api/admin/settings", json={"listen_after_submit": False})  # study 1: listen at school
    kid_ai, kid_native = client(), client()
    login(kid_ai, ai[0])
    login(kid_native, native[0])

    me = kid_ai.get("/api/me").json()
    assert me["phase"] == "record" and me["submitted"] is False
    assert [i["recorded"] for i in me["items"]] == [False, False]
    assert "model_url" not in me["items"][0]  # no peeking at the model during recording
    # Cannot submit before every sentence is recorded.
    assert kid_ai.post("/api/submit").status_code == 400
    record_all(kid_ai)
    record_all(kid_native)
    assert kid_ai.post("/api/submit").status_code == 200
    assert kid_native.post("/api/submit").status_code == 200
    # Locked after submitting.
    sid = me["items"][0]["sentence_id"]
    assert kid_ai.post(f"/api/recordings/{sid}", files={"file": ("v.wav", make_wav(), "audio/wav")}).status_code == 409

    # Only the AI-condition student gets AI clips.
    clips = admin.get("/api/admin/clips").json()
    assert {c["participant_id"] for c in clips} == {ai[0]["id"]} and len(clips) == 2
    drain(app)
    progress = admin.get("/api/admin/progress").json()[0]
    assert progress["submitted"] == 2 and progress["ai_ready"] == 1 and progress["not_submitted"] == []

    # Model audio is not reachable until the class moves to the listening stage.
    assert kid_ai.get(f"/api/model/{sid}/ai_self").status_code == 409
    admin.put("/api/admin/phase", json={"class_name": "3-1", "phase": "listen"})

    me_ai = kid_ai.get("/api/me").json()
    me_native = kid_native.get("/api/me").json()
    assert me_ai["phase"] == "listen"
    own = kid_ai.get(me_ai["items"][0]["own_url"])
    assert own.status_code == 200 and own.content[:4] == b"RIFF"
    audios_ai = me_ai["items"][0]["audios"]
    audios_native = me_native["items"][0]["audios"]
    # One model voice each, labelled just "お手本" so the condition stays hidden.
    assert [(a["kind"], a["label"]) for a in audios_ai] == [("own", "自分の音読"), ("ai_self", "お手本")]
    assert [(a["kind"], a["label"]) for a in audios_native] == [("own", "自分の音読"), ("native", "お手本")]
    model_ai = kid_ai.get(audios_ai[1]["url"])
    model_native = kid_native.get(audios_native[1]["url"])
    # A student cannot fetch a kind outside their condition's set.
    assert kid_native.get(f"/api/model/{sid}/ai_self").status_code == 404
    assert model_ai.content[:4] == b"RIFF"  # AI self-voice (dummy engine WAV)
    assert model_native.content == b"ID3fake-mp3"  # the uploaded native speaker audio
    assert model_native.headers["content-type"] == "audio/mpeg"
    # The condition itself is never sent to the student.
    assert "condition" not in str(me_ai)

    kid_ai.post("/api/events", json={"type": "play_ai_self", "sentence_id": sid, "detail": "rate=1.5;repeat=3"})
    log = admin.get("/api/admin/events.csv").text
    assert "play_ai_self" in log and "submit" in log and "rate=1.5;repeat=3" in log


def test_phase_per_class_and_closed(env):
    app, admin, client = env
    admin.post("/api/admin/sentences", json={"text": "Hello."})
    a = admin.post("/api/admin/participants", json={"count": 1, "prefix": "A", "class_name": "3-1", "condition": "ai"}).json()[0]
    b = admin.post("/api/admin/participants", json={"count": 1, "prefix": "B", "class_name": "3-2", "condition": "ai"}).json()[0]
    admin.put("/api/admin/phase", json={"class_name": "3-2", "phase": "closed"})
    ka, kb = client(), client()
    login(ka, a)
    login(kb, b)
    assert ka.get("/api/me").json()["phase"] == "record"
    me_b = kb.get("/api/me").json()
    assert me_b["phase"] == "closed" and me_b["items"] == []
    assert kb.post("/api/submit").status_code == 409


def test_privacy_between_students(env):
    app, admin, client = env
    ai, _ = setup_study(admin, n=2)
    k1, k2 = client(), client()
    login(k1, ai[0])
    record_all(k1)
    url = k1.get("/api/me").json()["items"][0]["own_url"]
    login(k2, ai[1])
    assert k2.get(url).status_code == 404  # same path, but resolves to k2's own (missing) recording
    assert k2.get("/api/admin/participants").status_code == 401


def test_approval_and_controls_settings(env):
    app, admin, client = env
    ai, _ = setup_study(admin, n=1)
    admin.put("/api/admin/settings", json={"require_approval": True, "allow_controls": False,
                                            "survey_url": "https://forms.example/?id={id}"})
    kid = client()
    login(kid, ai[0])
    record_all(kid)
    kid.post("/api/submit")
    drain(app)
    admin.put("/api/admin/phase", json={"class_name": None, "phase": "listen"})
    me = kid.get("/api/me").json()
    assert me["allow_controls"] is False
    assert me["survey_url"] == f"https://forms.example/?id={ai[0]['id']}"
    assert all(i["audios"][1]["url"] is None for i in me["items"])
    for c in admin.get("/api/admin/clips").json():
        admin.post(f"/api/admin/clips/{c['id']}/approve", json={"approved": True})
    assert all(i["audios"][1]["url"] for i in kid.get("/api/me").json()["items"])


def test_reopen_and_resubmit_regenerates(env):
    app, admin, client = env
    ai, _ = setup_study(admin, n=1)
    kid = client()
    login(kid, ai[0])
    record_all(kid)
    kid.post("/api/submit")
    drain(app)
    admin.post(f"/api/admin/participants/{ai[0]['id']}/reopen")
    me = kid.get("/api/me").json()
    assert me["submitted"] is False
    record_all(kid, me)
    kid.post("/api/submit")
    assert {c["status"] for c in admin.get("/api/admin/clips").json()} == {"queued"}
    drain(app)
    assert {c["status"] for c in admin.get("/api/admin/clips").json()} == {"done"}


def test_import_csv_balances_random_within_class(env):
    app, admin, client = env
    lines = "id,class,condition\n" + "\n".join(f"S{i:03d},3-{1 + i % 2},random" for i in range(20)) + "\nX001,3-3,native"
    created = admin.post("/api/admin/participants/import", json={"csv": lines}).json()
    assert len(created) == 21 and all(len(p["passcode"]) == 6 for p in created)
    for cls in ("3-1", "3-2"):
        conds = [p["condition"] for p in created if p["class_name"] == cls]
        assert conds.count("ai") == conds.count("native") == 5
    assert created[-1]["condition"] == "native"
    bad = admin.post("/api/admin/participants/import", json={"csv": "S001,3-1,ai\nbad id!,3-1,ai\nZ1,3-1,maybe"})
    assert bad.status_code == 400 and "すでに" in bad.text and "不正" in bad.text and "maybe" in bad.text


def test_balanced_conditions():
    for n in (1, 2, 7, 35):
        c = balanced_conditions(n)
        assert len(c) == n and abs(c.count("ai") - c.count("native")) <= 1


def test_exports_and_delete(env):
    app, admin, client = env
    ai, _ = setup_study(admin, n=1)
    kid = client()
    login(kid, ai[0])
    record_all(kid)
    kid.post("/api/submit")
    drain(app)
    z = zipfile.ZipFile(io.BytesIO(admin.get("/api/admin/recordings.zip").content))
    assert len(z.namelist()) == 2 and z.namelist()[0].startswith("3-1/P001_s")
    assert "P001" in admin.get("/api/admin/participants.csv").text

    store = app.state.store
    assert (store.recordings_dir / "P001").exists() and (store.clips_dir / "P001").exists()
    assert admin.delete("/api/admin/participants/P001").status_code == 200
    assert not (store.recordings_dir / "P001").exists() and not (store.clips_dir / "P001").exists()
    assert kid.get("/api/me").status_code == 401


def test_recording_validation(env):
    app, admin, client = env
    ai, _ = setup_study(admin, n=1)
    kid = client()
    login(kid, ai[0])
    sid = kid.get("/api/me").json()["items"][0]["sentence_id"]
    assert kid.post(f"/api/recordings/{sid}", files={"file": ("v.wav", make_wav(0.2), "audio/wav")}).status_code == 400
    assert kid.post(f"/api/recordings/{sid}", files={"file": ("v.wav", b"junk", "audio/wav")}).status_code == 400
    assert kid.post("/api/recordings/9999", files={"file": ("v.wav", make_wav(), "audio/wav")}).status_code == 404


def test_failed_engine_marks_clip_failed(tmp_path):
    class Broken:
        name = "broken"

        def synthesize(self, *a, **kw):
            raise RuntimeError("boom")

    app = make_app(tmp_path, Broken)
    with TestClient(app) as admin, TestClient(app) as kid:
        admin.post("/api/admin/login", json={"password": "secret-admin"})
        ai, _ = setup_study(admin, n=1)
        login(kid, ai[0])
        record_all(kid)
        kid.post("/api/submit")
        drain(app)
        clips = admin.get("/api/admin/clips").json()
        assert clips[0]["status"] == "failed" and "boom" in clips[0]["error"]
        assert admin.post("/api/admin/clips/regenerate-failed").json() == {"count": 2}


def test_login_throttle(env):
    app, admin, client = env
    admin.post("/api/admin/participants", json={"count": 1})
    kid = client()
    for _ in range(8):
        assert kid.post("/api/login", json={"id": "P001", "passcode": "nope"}).status_code == 401
    assert kid.post("/api/login", json={"id": "P001", "passcode": "nope"}).status_code == 429


def test_old_database_is_migrated(tmp_path):
    import sqlite3

    conn = sqlite3.connect(tmp_path / "ondoku.sqlite3")
    conn.executescript("""
        CREATE TABLE participants (id TEXT PRIMARY KEY, passcode_hash TEXT NOT NULL,
          group_name TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1, ref_path TEXT,
          ref_duration REAL, ref_uploaded_at REAL, created_at REAL NOT NULL);
        CREATE TABLE sentences (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, lang TEXT NOT NULL,
          group_name TEXT NOT NULL DEFAULT '', sort_order INTEGER NOT NULL DEFAULT 0,
          active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL);
        INSERT INTO participants(id, passcode_hash, group_name, created_at) VALUES ('P001', 'x', '5-1', 0);
    """)
    conn.commit()
    conn.close()
    app = make_app(tmp_path)
    with TestClient(app) as admin:
        admin.post("/api/admin/login", json={"password": "secret-admin"})
        rows = admin.get("/api/admin/participants").json()
        assert rows[0]["class_name"] == "5-1" and rows[0]["condition"] == "ai"
        assert admin.post("/api/admin/sentences", json={"text": "Hi."}).status_code == 200


def test_trim_tail_cuts_babble_after_the_sentence():
    import numpy as np

    from app.engines.postprocess import too_long, trim_tail

    sr = 24000
    t = np.arange(int(sr * 1.8)) / sr
    speech = 0.5 * np.sin(2 * np.pi * 200 * t)          # the sentence, 1.8 s
    gap = np.zeros(int(sr * 0.6))                       # pause after it
    babble = 0.5 * np.sin(2 * np.pi * 300 * t[: sr // 2])  # "I, I"
    wav = np.concatenate([speech, gap, babble, gap, babble, np.zeros(sr)]).astype(np.float32)

    out = trim_tail(wav, sr, expected_seconds=2.0)
    assert 1.8 <= len(out) / sr <= 2.1
    # A short pause inside the sentence (before 80 % of the expected length) is kept.
    inner = np.concatenate([speech[: sr // 2], np.zeros(int(sr * 0.5)), speech]).astype(np.float32)
    assert len(trim_tail(inner, sr, expected_seconds=2.6)) / sr > 2.7
    # Without an expected length only trailing silence goes.
    assert len(trim_tail(wav, sr, None)) / sr > 3.5
    assert too_long(5.0, 2.0) and not too_long(3.5, 2.0) and not too_long(9.0, None)


def test_three_voices_and_listen_right_after_submit(env):
    app, admin, client = env
    ai, native = setup_study(admin, n=1)
    # AI-condition students hear their own reading, their AI clone and the stock AI voice.
    r = admin.put("/api/admin/settings", json={"listen_sets": {"ai": ["standard", "own", "ai_self"]},
                                               "listen_after_submit": True})
    assert r.status_code == 200
    assert admin.get("/api/admin/status").json()["listen_sets"]["ai"] == ["own", "ai_self", "standard"]

    kid = client()
    login(kid, ai[0])
    record_all(kid)
    kid.post("/api/submit")
    me = kid.get("/api/me").json()
    assert me["phase"] == "listen"  # no need to wait for the class to switch
    audios = me["items"][0]["audios"]
    assert [(a["kind"], a["label"]) for a in audios] == [
        ("own", "自分の音読"), ("ai_self", "AIの声（自分の声）"), ("standard", "AIの声（標準）")]
    assert audios[0]["url"] and audios[1]["url"] is None and audios[2]["url"] is None

    drain(app)  # stock voice (one per sentence) and the student's clone
    audios = kid.get("/api/me").json()["items"][0]["audios"]
    assert all(a["url"] for a in audios)
    std = kid.get(audios[2]["url"])
    assert std.status_code == 200 and std.content[:4] == b"RIFF"
    sentences = admin.get("/api/admin/sentences").json()
    assert {s["standard_status"] for s in sentences} == {"done"}

    # Changing the stock voice re-queues every stock clip.
    admin.put("/api/admin/settings", json={"standard_voice": "Claribel Dervla"})
    assert {s["standard_status"] for s in admin.get("/api/admin/sentences").json()} == {"queued"}

    # The native group can be given its own set too, e.g. study 2's "native only".
    admin.put("/api/admin/settings", json={"listen_sets": {"native": ["native"]}})
    kid2 = client()
    login(kid2, native[0])
    record_all(kid2)
    kid2.post("/api/submit")
    audios = kid2.get("/api/me").json()["items"][0]["audios"]
    assert [(a["kind"], a["label"]) for a in audios] == [("native", "お手本")]


def test_admin_and_student_logins_coexist_in_one_browser(env):
    app, admin, client = env
    (p,) = admin.post("/api/admin/participants", json={"count": 1}).json()
    # The same client (one browser) logs in as a student while already logged in as admin.
    login(admin, p)
    assert admin.get("/api/me").status_code == 200
    assert admin.get("/api/admin/status").status_code == 200
    admin.post("/api/logout")
    assert admin.get("/api/me").status_code == 401
    assert admin.get("/api/admin/status").status_code == 200
