"""SQLite storage and the data rules of the app (study 1).

Flow: at home each student records every practice sentence and submits. For students in the
'ai' condition the server then generates the sentence in their own voice, using their
recordings as the voice sample. At school students hear their own recording, then a model:
the AI self-voice ('ai' condition) or the native speaker audio the researcher uploaded ('native').

Files live under the data directory:
  recordings/<participant>/<sentence>.wav  the student's own reading
  clips/<participant>/<file>.wav           AI self-voice, one per sentence
  native/<sentence>.<ext>                  native speaker audio, one per sentence
"""

from __future__ import annotations

import random
import re
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

CONDITIONS = ("ai", "native")
PHASES = ("record", "listen", "closed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS participants (
    id TEXT PRIMARY KEY,
    passcode_hash TEXT NOT NULL,
    class_name TEXT NOT NULL DEFAULT '',
    condition TEXT NOT NULL DEFAULT 'ai',
    active INTEGER NOT NULL DEFAULT 1,
    submitted_at REAL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sentences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    lang TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    native_path TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS recordings (
    participant_id TEXT NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    sentence_id INTEGER NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    seconds REAL NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (participant_id, sentence_id)
);
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id TEXT NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    sentence_id INTEGER NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'queued',
    approved INTEGER NOT NULL DEFAULT 0,
    path TEXT,
    error TEXT,
    seconds REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (participant_id, sentence_id)
);
CREATE INDEX IF NOT EXISTS clips_status ON clips(status, id);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    subject TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id TEXT NOT NULL,
    sentence_id INTEGER,
    type TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Columns added after the first prototype; older databases get them on start.
MIGRATIONS = {
    "participants": {
        "class_name": "TEXT NOT NULL DEFAULT ''",
        "condition": "TEXT NOT NULL DEFAULT 'ai'",
        "submitted_at": "REAL",
    },
    "sentences": {"native_path": "TEXT"},
}

DEFAULT_SETTINGS = {
    "require_approval": "0",
    "allow_controls": "1",
    "survey_url": "",
    "phase": "record",
}

PARTICIPANT_ID_RE = re.compile(r"^[A-Z0-9_-]{1,32}$")
JAPANESE_RE = re.compile(r"[぀-ヿ㐀-鿿]")
NATIVE_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".webm"}


def guess_lang(text: str) -> str:
    return "ja" if JAPANESE_RE.search(text) else "en"


def balanced_conditions(count: int, rng: random.Random | None = None) -> list[str]:
    """Half 'ai', half 'native' (the odd one random), in random order."""
    rng = rng or random.SystemRandom()
    half = [CONDITIONS[i % 2] for i in range(count - count % 2)]
    if count % 2:
        half.append(rng.choice(CONDITIONS))
    rng.shuffle(half)
    return half


class Store:
    def __init__(self, data_dir: Path, require_approval_default: bool = False):
        self.data_dir = data_dir
        self.recordings_dir = data_dir / "recordings"
        self.clips_dir = data_dir / "clips"
        self.native_dir = data_dir / "native"
        for d in (self.data_dir, self.recordings_dir, self.clips_dir, self.native_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.db_path = data_dir / "ondoku.sqlite3"
        self._write_lock = threading.Lock()
        with self.tx() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)
            defaults = dict(DEFAULT_SETTINGS, require_approval="1" if require_approval_default else "0")
            for key, value in defaults.items():
                conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value))
            # A crash mid-generation leaves rows in 'processing'; run them again.
            conn.execute("UPDATE clips SET status='queued' WHERE status='processing'")

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        for table, columns in MIGRATIONS.items():
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in columns.items():
                if name not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        have = {r["name"] for r in conn.execute("PRAGMA table_info(participants)")}
        if "group_name" in have:  # first prototype stored the class as 'group'
            conn.execute("UPDATE participants SET class_name=group_name WHERE class_name=''")

    # ---- connection helpers -------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock:
            conn = self._connect()
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    # ---- settings -----------------------------------------------------------

    def get_setting(self, key: str) -> str | None:
        with self.read() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def flag(self, key: str) -> bool:
        return self.get_setting(key) == "1"

    def require_approval(self) -> bool:
        return self.flag("require_approval")

    def phase_for(self, class_name: str) -> str:
        """Each class can be in its own stage; classes without one follow the default."""
        return self.get_setting(f"phase:{class_name}") or self.get_setting("phase") or "record"

    def set_phase(self, class_name: str | None, phase: str) -> None:
        if phase not in PHASES:
            raise ValueError(phase)
        self.set_setting("phase" if class_name is None else f"phase:{class_name}", phase)

    # ---- sessions -----------------------------------------------------------

    def create_session(self, token: str, role: str, subject: str, ttl_seconds: float) -> None:
        now = time.time()
        with self.tx() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            conn.execute(
                "INSERT INTO sessions(token, role, subject, expires_at) VALUES (?, ?, ?, ?)",
                (token, role, subject, now + ttl_seconds),
            )

    def get_session(self, token: str) -> sqlite3.Row | None:
        with self.read() as conn:
            return conn.execute(
                "SELECT * FROM sessions WHERE token=? AND expires_at>=?", (token, time.time())
            ).fetchone()

    def delete_session(self, token: str) -> None:
        with self.tx() as conn:
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))

    # ---- participants -------------------------------------------------------

    def get_participant(self, pid: str) -> sqlite3.Row | None:
        with self.read() as conn:
            return conn.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()

    def next_participant_ids(self, prefix: str, count: int) -> list[str]:
        with self.read() as conn:
            rows = conn.execute("SELECT id FROM participants WHERE id LIKE ?", (prefix + "%",)).fetchall()
        used = {int(r["id"][len(prefix):]) for r in rows if r["id"][len(prefix):].isdigit()}
        ids, n = [], 1
        while len(ids) < count:
            if n not in used:
                ids.append(f"{prefix}{n:03d}")
            n += 1
        return ids

    def add_participant(self, pid: str, passcode_hash: str, class_name: str, condition: str) -> None:
        if condition not in CONDITIONS:
            raise ValueError(condition)
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO participants(id, passcode_hash, class_name, condition, created_at) VALUES (?, ?, ?, ?, ?)",
                (pid, passcode_hash, class_name, condition, time.time()),
            )

    def update_participant(self, pid: str, *, active: bool | None = None, class_name: str | None = None,
                           condition: str | None = None, passcode_hash: str | None = None) -> None:
        with self.tx() as conn:
            if active is not None:
                conn.execute("UPDATE participants SET active=? WHERE id=?", (int(active), pid))
                if not active:
                    conn.execute("DELETE FROM sessions WHERE role='participant' AND subject=?", (pid,))
            if class_name is not None:
                conn.execute("UPDATE participants SET class_name=? WHERE id=?", (class_name, pid))
            if condition is not None:
                if condition not in CONDITIONS:
                    raise ValueError(condition)
                conn.execute("UPDATE participants SET condition=? WHERE id=?", (condition, pid))
            if passcode_hash is not None:
                conn.execute("UPDATE participants SET passcode_hash=? WHERE id=?", (passcode_hash, pid))
                conn.execute("DELETE FROM sessions WHERE role='participant' AND subject=?", (pid,))
        self.sync_clips(participant_id=pid)

    def list_participants(self) -> list[dict]:
        with self.read() as conn:
            rows = conn.execute(
                """
                SELECT p.id, p.class_name, p.condition, p.active, p.submitted_at, p.created_at,
                  (SELECT COUNT(*) FROM recordings r JOIN sentences s ON s.id=r.sentence_id
                     WHERE r.participant_id=p.id AND s.active=1) AS recorded,
                  (SELECT COUNT(*) FROM clips c JOIN sentences s ON s.id=c.sentence_id
                     WHERE c.participant_id=p.id AND s.active=1) AS clip_total,
                  (SELECT COUNT(*) FROM clips c JOIN sentences s ON s.id=c.sentence_id
                     WHERE c.participant_id=p.id AND s.active=1 AND c.status='done') AS clip_done,
                  (SELECT COUNT(*) FROM clips c WHERE c.participant_id=p.id AND c.status='failed') AS clip_failed,
                  (SELECT MAX(created_at) FROM events e WHERE e.participant_id=p.id) AS last_activity
                FROM participants p ORDER BY p.class_name, p.id
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def class_names(self) -> list[str]:
        with self.read() as conn:
            return [r["class_name"] for r in conn.execute(
                "SELECT DISTINCT class_name FROM participants ORDER BY class_name")]

    def progress(self) -> list[dict]:
        """Per class: how many submitted, and whether the AI voices for them are ready."""
        sentence_count = self.active_sentence_count()
        out: dict[str, dict] = {}
        for p in self.list_participants():
            if not p["active"]:
                continue
            c = out.setdefault(p["class_name"], {
                "class_name": p["class_name"], "phase": self.phase_for(p["class_name"]),
                "total": 0, "submitted": 0, "ai": 0, "native": 0,
                "ai_ready": 0, "not_submitted": [], "ai_waiting": [],
            })
            c["total"] += 1
            c[p["condition"]] += 1
            if p["submitted_at"]:
                c["submitted"] += 1
            else:
                c["not_submitted"].append(p["id"])
            if p["condition"] == "ai":
                if p["submitted_at"] and sentence_count and p["clip_done"] >= sentence_count:
                    c["ai_ready"] += 1
                elif p["submitted_at"]:
                    c["ai_waiting"].append(p["id"])
        return list(out.values())

    def delete_participant(self, pid: str) -> None:
        """Remove every trace of a participant: rows, recordings, generated audio."""
        with self.tx() as conn:
            conn.execute("DELETE FROM sessions WHERE role='participant' AND subject=?", (pid,))
            conn.execute("DELETE FROM events WHERE participant_id=?", (pid,))
            conn.execute("DELETE FROM clips WHERE participant_id=?", (pid,))
            conn.execute("DELETE FROM recordings WHERE participant_id=?", (pid,))
            conn.execute("DELETE FROM participants WHERE id=?", (pid,))
        shutil.rmtree(self.recordings_dir / pid, ignore_errors=True)
        shutil.rmtree(self.clips_dir / pid, ignore_errors=True)

    # ---- recordings ---------------------------------------------------------

    def save_recording(self, pid: str, sid: int, wav_bytes: bytes, seconds: float) -> None:
        d = self.recordings_dir / pid
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{sid}.wav"
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(wav_bytes)
        tmp.replace(path)
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO recordings(participant_id, sentence_id, path, seconds, created_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(participant_id, sentence_id) DO UPDATE SET path=excluded.path, "
                "seconds=excluded.seconds, created_at=excluded.created_at",
                (pid, sid, str(path), seconds, time.time()),
            )

    def get_recording(self, pid: str, sid: int) -> sqlite3.Row | None:
        with self.read() as conn:
            return conn.execute(
                "SELECT * FROM recordings WHERE participant_id=? AND sentence_id=?", (pid, sid)
            ).fetchone()

    def recordings_for(self, pid: str) -> list[sqlite3.Row]:
        with self.read() as conn:
            return conn.execute(
                """SELECT r.* FROM recordings r JOIN sentences s ON s.id=r.sentence_id
                   WHERE r.participant_id=? AND s.active=1 ORDER BY s.sort_order, s.id""",
                (pid,),
            ).fetchall()

    def all_recordings(self) -> list[sqlite3.Row]:
        with self.read() as conn:
            return conn.execute(
                """SELECT r.*, p.class_name, p.condition FROM recordings r
                   JOIN participants p ON p.id=r.participant_id ORDER BY r.participant_id, r.sentence_id"""
            ).fetchall()

    def reference_paths(self, pid: str) -> list[Path]:
        """The student's own readings, longest first, used as the voice sample for cloning."""
        rows = sorted(self.recordings_for(pid), key=lambda r: r["seconds"], reverse=True)
        return [Path(r["path"]) for r in rows if Path(r["path"]).is_file()]

    def submit(self, pid: str) -> None:
        """Lock in the home recordings. For the AI condition this (re)starts generation."""
        with self.tx() as conn:
            conn.execute("UPDATE participants SET submitted_at=? WHERE id=?", (time.time(), pid))
            old = conn.execute("SELECT path FROM clips WHERE participant_id=?", (pid,)).fetchall()
            conn.execute("DELETE FROM clips WHERE participant_id=?", (pid,))
        for r in old:
            if r["path"]:
                Path(r["path"]).unlink(missing_ok=True)
        self.sync_clips(participant_id=pid)

    def reopen(self, pid: str) -> None:
        """Let a student record again (e.g. a bad recording); they must submit again."""
        with self.tx() as conn:
            conn.execute("UPDATE participants SET submitted_at=NULL WHERE id=?", (pid,))

    # ---- sentences ----------------------------------------------------------

    def list_sentences(self, active_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM sentences"
        if active_only:
            sql += " WHERE active=1"
        with self.read() as conn:
            return [dict(r) for r in conn.execute(sql + " ORDER BY sort_order, id").fetchall()]

    def active_sentence_count(self) -> int:
        with self.read() as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM sentences WHERE active=1").fetchone()["n"]

    def get_sentence(self, sid: int) -> sqlite3.Row | None:
        with self.read() as conn:
            return conn.execute("SELECT * FROM sentences WHERE id=?", (sid,)).fetchone()

    def add_sentence(self, text: str, lang: str, sort_order: int | None = None) -> int:
        with self.tx() as conn:
            if sort_order is None:
                row = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 10 AS n FROM sentences").fetchone()
                sort_order = row["n"]
            sid = conn.execute(
                "INSERT INTO sentences(text, lang, sort_order, created_at) VALUES (?, ?, ?, ?)",
                (text, lang, sort_order, time.time()),
            ).lastrowid
        self.sync_clips()
        return sid

    def update_sentence(self, sid: int, *, text: str | None = None, lang: str | None = None,
                        sort_order: int | None = None, active: bool | None = None) -> None:
        with self.tx() as conn:
            cur = conn.execute("SELECT * FROM sentences WHERE id=?", (sid,)).fetchone()
            if cur is None:
                raise KeyError(sid)
            text_changed = (text is not None and text != cur["text"]) or (lang is not None and lang != cur["lang"])
            conn.execute(
                "UPDATE sentences SET text=?, lang=?, sort_order=?, active=? WHERE id=?",
                (
                    text if text is not None else cur["text"],
                    lang if lang is not None else cur["lang"],
                    sort_order if sort_order is not None else cur["sort_order"],
                    int(active) if active is not None else cur["active"],
                    sid,
                ),
            )
            if text_changed:
                conn.execute(
                    "UPDATE clips SET status='queued', approved=0, error=NULL, updated_at=? WHERE sentence_id=?",
                    (time.time(), sid),
                )
        self.sync_clips()

    def set_native_audio(self, sid: int, data: bytes, ext: str) -> None:
        cur = self.get_sentence(sid)
        if cur is None:
            raise KeyError(sid)
        path = self.native_dir / f"{sid}-{uuid.uuid4().hex[:8]}{ext}"
        path.write_bytes(data)
        with self.tx() as conn:
            conn.execute("UPDATE sentences SET native_path=? WHERE id=?", (str(path), sid))
        if cur["native_path"]:
            Path(cur["native_path"]).unlink(missing_ok=True)

    def clear_native_audio(self, sid: int) -> None:
        cur = self.get_sentence(sid)
        with self.tx() as conn:
            conn.execute("UPDATE sentences SET native_path=NULL WHERE id=?", (sid,))
        if cur and cur["native_path"]:
            Path(cur["native_path"]).unlink(missing_ok=True)

    def delete_sentence(self, sid: int) -> None:
        cur = self.get_sentence(sid)
        with self.tx() as conn:
            old = conn.execute(
                "SELECT path FROM clips WHERE sentence_id=? UNION ALL SELECT path FROM recordings WHERE sentence_id=?",
                (sid, sid),
            ).fetchall()
            conn.execute("DELETE FROM clips WHERE sentence_id=?", (sid,))
            conn.execute("DELETE FROM recordings WHERE sentence_id=?", (sid,))
            conn.execute("DELETE FROM sentences WHERE id=?", (sid,))
        for r in old:
            if r["path"]:
                Path(r["path"]).unlink(missing_ok=True)
        if cur and cur["native_path"]:
            Path(cur["native_path"]).unlink(missing_ok=True)

    # ---- the student's view ---------------------------------------------------

    def items_for(self, pid: str) -> list[dict]:
        with self.read() as conn:
            rows = conn.execute(
                """
                SELECT s.id AS sentence_id, s.text, s.lang, s.native_path,
                       r.seconds AS recorded_seconds, r.created_at AS recorded_at,
                       c.id AS clip_id, c.status AS clip_status, c.approved
                FROM sentences s
                LEFT JOIN recordings r ON r.sentence_id = s.id AND r.participant_id = ?
                LEFT JOIN clips c ON c.sentence_id = s.id AND c.participant_id = ?
                WHERE s.active = 1
                ORDER BY s.sort_order, s.id
                """,
                (pid, pid),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- clips (AI self-voice) ----------------------------------------------

    def sync_clips(self, participant_id: str | None = None) -> int:
        """Queue an AI clip for every (submitted 'ai' student, active sentence) pair without one."""
        now = time.time()
        sql = """
            INSERT OR IGNORE INTO clips(participant_id, sentence_id, status, created_at, updated_at)
            SELECT p.id, s.id, 'queued', ?, ?
            FROM participants p JOIN sentences s
            WHERE p.condition = 'ai' AND p.submitted_at IS NOT NULL AND p.active = 1 AND s.active = 1
        """
        params: list = [now, now]
        if participant_id is not None:
            sql += " AND p.id = ?"
            params.append(participant_id)
        with self.tx() as conn:
            return conn.execute(sql, params).rowcount

    def get_clip(self, cid: int) -> sqlite3.Row | None:
        with self.read() as conn:
            return conn.execute(
                """SELECT c.*, s.text, s.lang FROM clips c
                   JOIN sentences s ON s.id=c.sentence_id WHERE c.id=?""",
                (cid,),
            ).fetchone()

    def list_clips(self, participant_id: str | None = None, status: str | None = None) -> list[dict]:
        sql = """SELECT c.id, c.participant_id, c.sentence_id, c.status, c.approved, c.error, c.seconds,
                        c.updated_at, s.text, s.lang, p.class_name
                 FROM clips c JOIN sentences s ON s.id=c.sentence_id
                 JOIN participants p ON p.id=c.participant_id WHERE 1=1"""
        params: list = []
        if participant_id:
            sql += " AND c.participant_id=?"
            params.append(participant_id)
        if status:
            sql += " AND c.status=?"
            params.append(status)
        sql += " ORDER BY c.participant_id, s.sort_order, s.id"
        with self.read() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def queue_stats(self) -> dict:
        with self.read() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS n FROM clips GROUP BY status").fetchall()
        stats = {"queued": 0, "processing": 0, "done": 0, "failed": 0}
        stats.update({r["status"]: r["n"] for r in rows})
        return stats

    def claim_next_clip(self) -> sqlite3.Row | None:
        with self.tx() as conn:
            row = conn.execute(
                """SELECT c.id FROM clips c JOIN participants p ON p.id=c.participant_id
                   WHERE c.status='queued' AND p.active=1 ORDER BY c.id LIMIT 1"""
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE clips SET status='processing', error=NULL, updated_at=? WHERE id=?",
                (time.time(), row["id"]),
            )
        return self.get_clip(row["id"])

    def new_clip_path(self, pid: str) -> Path:
        d = self.clips_dir / pid
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{uuid.uuid4().hex}.wav"

    def finish_clip(self, cid: int, path: Path, seconds: float) -> bool:
        """Record a finished clip. Returns False if the clip was deleted or re-queued meanwhile."""
        with self.tx() as conn:
            row = conn.execute("SELECT path, status FROM clips WHERE id=?", (cid,)).fetchone()
            if row is None or row["status"] != "processing":
                return False
            conn.execute(
                "UPDATE clips SET status='done', path=?, seconds=?, approved=0, error=NULL, updated_at=? WHERE id=?",
                (str(path), seconds, time.time(), cid),
            )
        if row["path"] and row["path"] != str(path):
            Path(row["path"]).unlink(missing_ok=True)
        return True

    def fail_clip(self, cid: int, error: str) -> None:
        with self.tx() as conn:
            conn.execute(
                "UPDATE clips SET status='failed', error=?, updated_at=? WHERE id=? AND status='processing'",
                (error[:1000], time.time(), cid),
            )

    def requeue_clip(self, cid: int) -> None:
        with self.tx() as conn:
            conn.execute(
                "UPDATE clips SET status='queued', approved=0, error=NULL, updated_at=? WHERE id=?",
                (time.time(), cid),
            )

    def requeue_all_clips(self) -> int:
        with self.tx() as conn:
            return conn.execute(
                "UPDATE clips SET status='queued', approved=0, error=NULL, updated_at=? WHERE status != 'processing'",
                (time.time(),),
            ).rowcount

    def set_clip_approved(self, cid: int, approved: bool) -> None:
        with self.tx() as conn:
            conn.execute("UPDATE clips SET approved=? WHERE id=?", (int(approved), cid))

    # ---- events -------------------------------------------------------------

    def log_event(self, pid: str, type_: str, sentence_id: int | None = None, detail: str = "") -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO events(participant_id, sentence_id, type, detail, created_at) VALUES (?, ?, ?, ?, ?)",
                (pid, sentence_id, type_, detail[:200], time.time()),
            )

    def list_events(self) -> list[sqlite3.Row]:
        with self.read() as conn:
            return conn.execute(
                """SELECT e.*, s.text, p.class_name, p.condition FROM events e
                   LEFT JOIN sentences s ON s.id=e.sentence_id
                   LEFT JOIN participants p ON p.id=e.participant_id
                   ORDER BY e.id"""
            ).fetchall()
