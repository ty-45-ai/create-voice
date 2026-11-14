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
    return admin.post("/api/admin/participants", json={"count": n, "class_name": "3-1"}).json()


def test_home_record_then_school_listen(env):
    app, admin, client = env
    kids = setup_study(admin, n=2)
    k1, k2 = client(), client()
    login(k1, kids[0])
    login(k2, kids[1])

    me = k1.get("/api/me").json()
    assert me["phase"] == "record" and me["submitted"] is False
    assert [i["recorded"] for i in me["items"]] == [False, False]
    assert "audios" not in me["items"][0]  # no peeking at the AI voices during recording
    # Cannot submit before every sentence is recorded.
    assert k1.post("/api/submit").status_code == 400
    record_all(k1)
    assert k1.post("/api/submit").status_code == 200
    # Locked after submitting.
    sid = me["items"][0]["sentence_id"]
    assert k1.post(f"/api/recordings/{sid}", files={"file": ("v.wav", make_wav(), "audio/wav")}).status_code == 409

    # Only students who submitted get AI clones; the others cannot reach AI audio yet.
    clips = admin.get("/api/admin/clips").json()
    assert {c["participant_id"] for c in clips} == {kids[0]["id"]} and len(clips) == 2
    assert k2.get(f"/api/model/{sid}/ai_self").status_code == 409
    progress = admin.get("/api/admin/progress").json()[0]
    assert progress["counts"]["generating"] == 1 and progress["counts"]["not_started"] == 1
    drain(app)
    progress = admin.get("/api/admin/progress").json()[0]
    assert [(st["id"], st["state"]) for st in progress["students"]] == [
        (kids[0]["id"], "ready"), (kids[1]["id"], "not_started")]

    me = k1.get("/api/me").json()
    assert me["phase"] == "listen"
    own = k1.get(me["items"][0]["own_url"])
    assert own.status_code == 200 and own.content[:4] == b"RIFF"
    # Everyone hears the same three sounds, in this order.
    audios = me["items"][0]["audios"]
    assert [(a["kind"], a["label"]) for a in audios] == [
        ("own", "自分の声"), ("ai_self", "AI音声（クローン）"), ("standard", "AI音声（標準）")]
    for a in audios[1:]:
        r = k1.get(a["url"])
        assert r.status_code == 200 and r.content[:4] == b"RIFF"
    assert k1.get(f"/api/model/{sid}/native").status_code == 404
    # The student who has not submitted is still recording.
    me2 = k2.get("/api/me").json()
    assert me2["phase"] == "record" and "audios" not in me2["items"][0]
    record_all(k2, me2)
    progress = admin.get("/api/admin/progress").json()[0]
    assert progress["counts"]["recording"] == 1

    k1.post("/api/events", json={"type": "play_ai_self", "sentence_id": sid, "detail": "rate=1.5;repeat=3"})
    log = admin.get("/api/admin/events.csv").text
    assert "play_ai_self" in log and "submit" in log and "rate=1.5;repeat=3" in log


def test_cannot_submit_twice_or_record_after_submitting(env):
    app, admin, client = env
    admin.post("/api/admin/sentences", json={"text": "Hello there, how are you today?"})
    (a,) = admin.post("/api/admin/participants", json={"count": 1}).json()
    k = client()
    login(k, a)
    sid = k.get("/api/me").json()["items"][0]["sentence_id"]
    upload = lambda: k.post(f"/api/recordings/{sid}", files={"file": ("v.wav", make_wav(4.0), "audio/wav")})
    assert upload().status_code == 200
    assert k.post("/api/submit").status_code == 200
    assert k.post("/api/submit").status_code == 409
    assert upload().status_code == 409
    assert k.get("/api/me").json()["phase"] == "listen"


def test_privacy_between_students(env):
    app, admin, client = env
    ai = setup_study(admin, n=2)
    k1, k2 = client(), client()
    login(k1, ai[0])
    record_all(k1)
    url = k1.get("/api/me").json()["items"][0]["own_url"]
    login(k2, ai[1])
    assert k2.get(url).status_code == 404  # same path, but resolves to k2's own (missing) recording
    assert k2.get("/api/admin/participants").status_code == 401


def test_approval_and_controls_settings(env):
    app, admin, client = env
    ai = setup_study(admin, n=1)
    admin.put("/api/admin/settings", json={"require_approval": True, "allow_controls": False,
                                            "survey_url": "https://forms.example/?id={id}"})
    kid = client()
    login(kid, ai[0])
    record_all(kid)
    kid.post("/api/submit")
    drain(app)
    me = kid.get("/api/me").json()
    assert me["allow_controls"] is False
    assert me["survey_url"] == f"https://forms.example/?id={ai[0]['id']}"
    assert all(i["audios"][1]["url"] is None for i in me["items"])
    for c in admin.get("/api/admin/clips").json():
        admin.post(f"/api/admin/clips/{c['id']}/approve", json={"approved": True})
    assert all(i["audios"][1]["url"] for i in kid.get("/api/me").json()["items"])


def test_reopen_and_resubmit_regenerates(env):
    app, admin, client = env
    ai = setup_study(admin, n=1)
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


def test_import_csv(env):
    app, admin, client = env
    lines = "id,class\n" + "\n".join(f"S{i:03d},3-{1 + i % 2}" for i in range(20)) + "\nX001,3-3,native"
    created = admin.post("/api/admin/participants/import", json={"csv": lines}).json()
    assert len(created) == 21 and all(len(p["passcode"]) == 6 for p in created)
    assert created[-1] == {"id": "X001", "passcode": created[-1]["passcode"], "class_name": "3-3"}
    bad = admin.post("/api/admin/participants/import", json={"csv": "S001,3-1\nbad id!,3-1"})
    assert bad.status_code == 400 and "すでに" in bad.text and "不正" in bad.text


def test_exports_and_delete(env):
    app, admin, client = env
    ai = setup_study(admin, n=1)
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
    ai = setup_study(admin, n=1)
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
        ai = setup_study(admin, n=1)
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
        assert rows[0]["class_name"] == "5-1"
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


def test_listen_right_after_submit_and_stock_voice_per_language(env):
    app, admin, client = env
    ai = setup_study(admin, n=1)
    kid = client()
    login(kid, ai[0])
    record_all(kid)
    kid.post("/api/submit")
    me = kid.get("/api/me").json()
    assert me["phase"] == "listen"  # listening starts as soon as the student submits
    audios = me["items"][0]["audios"]
    assert audios[0]["url"] and audios[1]["url"] is None and audios[2]["url"] is None

    drain(app)  # stock voice (one per sentence) and the student's clone
    audios = kid.get("/api/me").json()["items"][0]["audios"]
    assert all(a["url"] for a in audios)
    sentences = admin.get("/api/admin/sentences").json()
    assert {s["standard_status"] for s in sentences} == {"done"}
    assert all("has_native" not in s for s in sentences)

    # The stock voice is set per language; changing one re-queues only that language's clips.
    status = admin.get("/api/admin/status").json()
    assert status["standard_voice"] == {"en": "", "ja": ""} and status["standard_failures"] == []
    admin.put("/api/admin/settings", json={"standard_voices": {"ja": "Claribel Dervla"}})
    by_lang = {s["lang"]: s["standard_status"] for s in admin.get("/api/admin/sentences").json()}
    assert by_lang == {"en": "done", "ja": "queued"}
    assert admin.get("/api/admin/status").json()["standard_voice"]["ja"] == "Claribel Dervla"


def test_failed_stock_voice_is_reported(tmp_path):
    class NoStock(DummyEngine):
        def synthesize_standard(self, *a, **kw):
            raise RuntimeError("voice cannot speak ja")

    app = make_app(tmp_path, NoStock)
    with TestClient(app) as admin:
        admin.post("/api/admin/login", json={"password": "secret-admin"})
        setup_study(admin, n=1)
        drain(app)
        failures = admin.get("/api/admin/status").json()["standard_failures"]
        assert len(failures) == 2 and "voice cannot speak ja" in failures[0]["error"]
        sid = failures[0]["sentence_id"]
        admin.post(f"/api/admin/sentences/{sid}/standard/regenerate")
        assert len(admin.get("/api/admin/status").json()["standard_failures"]) == 1


def test_native_condition_is_migrated_away(tmp_path):
    import sqlite3

    app = make_app(tmp_path)
    with TestClient(app) as admin:
        admin.post("/api/admin/login", json={"password": "secret-admin"})
        (p,) = setup_study(admin, n=1)
    # Simulate data left by the version with conditions and native audio.
    native_dir = tmp_path / "native"
    native_dir.mkdir()
    (native_dir / "1-x.mp3").write_bytes(b"ID3")
    conn = sqlite3.connect(tmp_path / "ondoku.sqlite3")
    conn.execute("UPDATE participants SET condition='native', submitted_at=1")
    conn.execute("UPDATE sentences SET native_path=?", (str(native_dir / "1-x.mp3"),))
    conn.execute("INSERT INTO settings VALUES ('listen_set:native', 'own,native')")
    conn.execute("INSERT OR REPLACE INTO settings VALUES ('standard_voice', 'kokoro:af_bella')")
    conn.commit()
    conn.close()
    app = make_app(tmp_path)
    with TestClient(app) as admin:
        admin.post("/api/admin/login", json={"password": "secret-admin"})
        assert not native_dir.exists()
        # The former native-condition student now gets an AI clone too.
        assert {c["participant_id"] for c in admin.get("/api/admin/clips").json()} == {p["id"]}
        assert admin.get("/api/admin/status").json()["standard_voice"] == {"en": "kokoro:af_bella", "ja": ""}
    conn = sqlite3.connect(tmp_path / "ondoku.sqlite3")
    assert conn.execute("SELECT COUNT(*) FROM settings WHERE key LIKE 'listen_set:%'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM sentences WHERE native_path IS NOT NULL").fetchone()[0] == 0


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


def test_passwords_can_be_looked_up_changed_and_exported(env):
    app, admin, client = env
    (p,) = admin.post("/api/admin/participants", json={"count": 1, "class_name": "2-1"}).json()
    rows = admin.get("/api/admin/participants").json()
    assert rows[0]["passcode"] == p["passcode"]  # visible to the researcher at any time
    # A password the researcher chooses; case does not matter when logging in.
    r = admin.post(f"/api/admin/participants/{p['id']}/passcode", json={"password": "Sakura25"})
    assert r.json() == {"id": p["id"], "passcode": "sakura25"}
    kid = client()
    assert kid.post("/api/login", json={"id": p["id"], "passcode": p["passcode"]}).status_code == 401
    assert kid.post("/api/login", json={"id": p["id"], "passcode": "SAKURA25"}).status_code == 200
    assert admin.post(f"/api/admin/participants/{p['id']}/passcode", json={"password": "a b"}).status_code == 400
    # Empty = a new random one.
    new = admin.post(f"/api/admin/participants/{p['id']}/passcode", json={"password": ""}).json()["passcode"]
    assert len(new) == 6 and new != "sakura25"
    # CSV import may set passwords; the participants CSV carries them.
    created = admin.post("/api/admin/participants/import", json={"csv": "S001,2-2,hikari7\nS002,2-2"}).json()
    assert created[0]["passcode"] == "hikari7" and len(created[1]["passcode"]) == 6
    bad = admin.post("/api/admin/participants/import", json={"csv": "S003,2-2,x"})
    assert bad.status_code == 400 and "パスワード" in bad.text
    csv_text = admin.get("/api/admin/participants.csv").text
    assert "password" in csv_text and "hikari7" in csv_text and new in csv_text
    # Class names like 2-2 are written so that Excel keeps them as text, not a date.
    assert '"=""2-2"""' in csv_text


def test_fill_missing_passwords_for_students_made_before(env):
    import sqlite3

    app, admin, client = env
    made = admin.post("/api/admin/participants", json={"count": 2}).json()
    conn = sqlite3.connect(app.state.store.db_path)
    conn.execute("UPDATE participants SET passcode=NULL WHERE id=?", (made[0]["id"],))
    conn.commit()
    conn.close()
    filled = admin.post("/api/admin/participants/fill-missing-passwords").json()
    assert [f["id"] for f in filled] == [made[0]["id"]]
    kid = client()
    assert kid.post("/api/login", json={"id": made[0]["id"], "passcode": filled[0]["passcode"]}).status_code == 200
    assert all(r["passcode"] for r in admin.get("/api/admin/participants").json())


def test_bulk_delete_participants(env):
    app, admin, client = env
    trial = admin.post("/api/admin/participants", json={"count": 3, "class_name": "仮"}).json()
    keep = admin.post("/api/admin/participants", json={"count": 1, "prefix": "S", "class_name": "2-1"}).json()
    kid = client()
    login(kid, trial[0])
    ids = [p["id"] for p in trial] + ["NOPE"]
    assert admin.post("/api/admin/participants/delete", json={"ids": ids}).json() == {"deleted": ids[:3]}
    assert [r["id"] for r in admin.get("/api/admin/participants").json()] == [keep[0]["id"]]
    assert kid.get("/api/me").status_code == 401
    assert admin.post("/api/admin/participants/delete", json={"ids": []}).status_code == 422
