"""HTTP API and pages."""

from __future__ import annotations

import csv
import io
import logging
import mimetypes
import os
import time
import wave
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth
from .config import Settings, load_settings
from .engines import load_engine
from .store import (AUDIO_KINDS, CONDITIONS, NATIVE_EXTENSIONS, PARTICIPANT_ID_RE, Store, balanced_conditions,
                    guess_lang)
from .worker import Worker

log = logging.getLogger("ondoku")
STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
MIN_RECORDING_SECONDS = 0.5
MAX_RECORDING_SECONDS = 180.0
MIN_VOICE_SAMPLE_SECONDS = 3.0


# ---- request bodies ----------------------------------------------------------

class LoginBody(BaseModel):
    id: str = Field(max_length=64)
    passcode: str = Field(max_length=64)


class AdminLoginBody(BaseModel):
    password: str = Field(max_length=256)


class EventBody(BaseModel):
    type: Literal["play_own", "play_ai_self", "play_standard", "play_native", "play_model", "survey_open"]
    sentence_id: int | None = None
    detail: str = Field(default="", max_length=200)


class CreateParticipantsBody(BaseModel):
    count: int = Field(ge=1, le=300)
    prefix: str = Field(default="P", pattern=r"^[A-Za-z]{0,8}$")
    class_name: str = Field(default="", max_length=64)
    condition: Literal["ai", "native", "random"] = "random"


class ImportParticipantsBody(BaseModel):
    csv: str = Field(max_length=200_000)


class UpdateParticipantBody(BaseModel):
    active: bool | None = None
    class_name: str | None = Field(default=None, max_length=64)
    condition: Literal["ai", "native"] | None = None


class SentenceBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    lang: Literal["auto", "ja", "en"] = "auto"
    sort_order: int | None = None


class SentenceUpdateBody(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=1000)
    lang: Literal["auto", "ja", "en"] | None = None
    sort_order: int | None = None
    active: bool | None = None


class ApproveBody(BaseModel):
    approved: bool


class PhaseBody(BaseModel):
    class_name: str | None = Field(default=None, max_length=64)  # None = default for every class
    phase: Literal["record", "listen", "closed"]


class SettingsBody(BaseModel):
    require_approval: bool | None = None
    allow_controls: bool | None = None
    listen_after_submit: bool | None = None
    survey_url: str | None = Field(default=None, max_length=2000)
    standard_voice: str | None = Field(default=None, min_length=1, max_length=100)
    listen_sets: dict[Literal["ai", "native"], list[Literal["own", "ai_self", "standard", "native"]]] | None = None


# ---- helpers -----------------------------------------------------------------

def parse_wav_seconds(data: bytes, min_seconds: float, max_seconds: float) -> float:
    """Validate an uploaded recording (the browser always sends 16-bit PCM WAV)."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            if w.getsampwidth() != 2 or w.getnchannels() not in (1, 2):
                raise HTTPException(400, "WAV は 16bit モノラル/ステレオのみ対応しています")
            if not 8000 <= w.getframerate() <= 96000:
                raise HTTPException(400, "サンプリングレートが不正です")
            seconds = w.getnframes() / float(w.getframerate())
    except (wave.Error, EOFError) as exc:
        raise HTTPException(400, "音声ファイルを読み取れませんでした") from exc
    if seconds < min_seconds:
        raise HTTPException(400, "録音が短すぎます。もう一度録音してください")
    if seconds > max_seconds:
        raise HTTPException(400, f"録音が長すぎます（{max_seconds:.0f}秒まで）")
    return seconds


def audio_response(path: str | None) -> FileResponse:
    if not path or not Path(path).is_file():
        raise HTTPException(404, "音声がありません")
    media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    if path.endswith(".m4a"):
        media_type = "audio/mp4"
    # Played in the page only: no download name, no caching on shared computers.
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "no-store", "Content-Disposition": "inline"},
    )


def fmt_time(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""


def csv_response(rows: list[list], name: str) -> StreamingResponse:
    buf = io.StringIO()
    buf.write("﻿")  # BOM so Excel opens Japanese text correctly
    csv.writer(buf).writerows(rows)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


def ensure_admin_password(store: Store, settings: Settings) -> None:
    if settings.admin_password or store.get_setting("admin_password_hash"):
        return
    password = auth.new_passcode(12)
    store.set_setting("admin_password_hash", auth.hash_secret(password))
    note = store.data_dir / "admin-password.txt"
    note.write_text(password + "\n", encoding="utf-8")
    os.chmod(note, 0o600)
    log.warning("管理者パスワードを生成しました: %s （%s にも保存。ログイン後に削除してください）", password, note)


AUDIO_LABELS = {
    "own": "自分の音読",
    "ai_self": "AIの声（自分の声）",
    "standard": "AIの声（標準）",
    "native": "ネイティブの音声",
}


def audio_path(store: Store, item: dict, kind: str) -> str | None:
    """File behind one of the sounds a student can hear for a sentence, or None if not ready."""
    if kind == "own":
        return item.get("own_path")
    if kind == "native":
        return item["native_path"]
    if kind == "standard":
        return item["standard_path"] if item["standard_status"] == "done" else None
    if kind == "ai_self":
        if item["clip_status"] != "done":
            return None
        if store.require_approval() and not item["approved"]:
            return None
        clip = store.get_clip(item["clip_id"])
        return clip["path"] if clip else None
    return None


def labels_for(kinds: list[str]) -> dict[str, str]:
    """With a single model voice (study 1) it is just 'お手本', so the condition stays hidden."""
    models = [k for k in kinds if k != "own"]
    out = {k: AUDIO_LABELS[k] for k in kinds}
    if len(models) == 1:
        out[models[0]] = "お手本"
    return out


# ---- app ---------------------------------------------------------------------

def create_app(settings: Settings | None = None, start_worker: bool = True, engine_factory=None) -> FastAPI:
    settings = settings or load_settings()
    store = Store(settings.data_dir, require_approval_default=settings.require_approval)
    ensure_admin_password(store, settings)
    admin_hash = auth.hash_secret(settings.admin_password) if settings.admin_password else None
    worker = Worker(store, engine_factory or (lambda: load_engine(settings.engine, settings.device)))
    throttle = auth.LoginThrottle()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if start_worker:
            worker.start()
        yield
        worker.stop()

    app = FastAPI(title="Ondoku Voice", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.worker = worker
    app.state.settings = settings

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Permissions-Policy", "microphone=(self)")
        return response

    def set_cookie(response: Response, token: str, ttl: int) -> None:
        response.set_cookie(
            auth.COOKIE_NAME, token, max_age=ttl, httponly=True, samesite="strict",
            secure=settings.secure_cookie, path="/",
        )

    def session(request: Request, role: str):
        token = request.cookies.get(auth.COOKIE_NAME)
        row = store.get_session(token) if token else None
        if row is None or row["role"] != role:
            raise HTTPException(401, "ログインしてください")
        return row

    def participant(request: Request):
        row = session(request, "participant")
        p = store.get_participant(row["subject"])
        if p is None or not p["active"]:
            raise HTTPException(401, "ログインしてください")
        return p

    def admin(request: Request):
        return session(request, "admin")

    def client_key(request: Request, name: str) -> str:
        host = request.client.host if request.client else "?"
        return f"{host}|{name}"

    def require_phase(p, phase: str) -> None:
        if store.phase_for(p["class_name"]) != phase:
            raise HTTPException(409, "今はこの操作はできません。画面を読み込み直してください")

    def active_sentence(sid: int):
        s = store.get_sentence(sid)
        if s is None or not s["active"]:
            raise HTTPException(404, "文が見つかりません")
        return s

    # ---- pages ----

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
    def admin_page():
        return FileResponse(STATIC_DIR / "admin.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # ---- participant API ----

    @app.post("/api/login")
    def login(body: LoginBody, request: Request, response: Response):
        pid = body.id.strip().upper()
        key = client_key(request, pid)
        if throttle.blocked(key):
            raise HTTPException(429, "ログインの失敗が続いたため、5分ほど待ってからやり直してください")
        p = store.get_participant(pid) if PARTICIPANT_ID_RE.match(pid) else None
        if p is None or not p["active"] or not auth.verify_secret(body.passcode.strip().lower(), p["passcode_hash"]):
            throttle.fail(key)
            raise HTTPException(401, "ID または あいことば が違います")
        throttle.reset(key)
        token = auth.new_token()
        store.create_session(token, "participant", pid, auth.PARTICIPANT_TTL)
        store.log_event(pid, "login")
        set_cookie(response, token, auth.PARTICIPANT_TTL)
        return {"ok": True}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        token = request.cookies.get(auth.COOKIE_NAME)
        if token:
            store.delete_session(token)
        response.delete_cookie(auth.COOKIE_NAME, path="/")
        return {"ok": True}

    def items_with_paths(p) -> list[dict]:
        items = store.items_for(p["id"])
        for it in items:
            rec = store.get_recording(p["id"], it["sentence_id"]) if it["recorded_seconds"] is not None else None
            it["own_path"] = rec["path"] if rec else None
        return items

    @app.get("/api/me")
    def me(p=Depends(participant)):
        phase = store.effective_phase(p)
        kinds = store.listen_set(p["condition"])
        labels = labels_for(kinds)
        items = []
        if phase != "closed":
            for it in items_with_paths(p):
                sid = it["sentence_id"]
                recorded = it["recorded_seconds"] is not None
                entry = {
                    "sentence_id": sid,
                    "text": it["text"],
                    "lang": it["lang"],
                    "recorded": recorded,
                    "own_url": f"/api/recordings/{sid}/audio?v={int(it['recorded_at'])}" if recorded else None,
                }
                if phase == "listen":
                    entry["audios"] = [
                        {
                            "kind": k,
                            "label": labels[k],
                            "url": (entry["own_url"] if k == "own" else f"/api/model/{sid}/{k}")
                            if audio_path(store, it, k) else None,
                        }
                        for k in kinds
                    ]
                items.append(entry)
        survey = store.get_setting("survey_url") or ""
        return {
            "id": p["id"],
            "phase": phase,
            "submitted": bool(p["submitted_at"]),
            "allow_controls": store.flag("allow_controls"),
            "survey_url": survey.replace("{id}", p["id"]) if survey else "",
            "items": items,
        }

    @app.post("/api/recordings/{sid}")
    async def upload_recording(sid: int, file: UploadFile = File(...), p=Depends(participant)):
        require_phase(p, "record")
        if p["submitted_at"]:
            raise HTTPException(409, "提出済みのため録り直せません。先生に連絡してください")
        active_sentence(sid)
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "ファイルが大きすぎます")
        seconds = parse_wav_seconds(data, MIN_RECORDING_SECONDS, MAX_RECORDING_SECONDS)
        store.save_recording(p["id"], sid, data, seconds)
        store.log_event(p["id"], "record", sid, f"{seconds:.1f}s")
        return {"ok": True, "seconds": round(seconds, 1)}

    @app.get("/api/recordings/{sid}/audio")
    def own_recording(sid: int, p=Depends(participant)):
        rec = store.get_recording(p["id"], sid)
        return audio_response(rec["path"] if rec else None)

    @app.post("/api/submit")
    def submit(p=Depends(participant)):
        require_phase(p, "record")
        items = store.items_for(p["id"])
        missing = [i for i in items if i["recorded_seconds"] is None]
        if not items or missing:
            raise HTTPException(400, f"まだ録音していない文が {len(missing)} 個あります")
        if sum(i["recorded_seconds"] for i in items) < MIN_VOICE_SAMPLE_SECONDS:
            raise HTTPException(400, "録音が短すぎます。はっきりと読んで録り直してください")
        store.submit(p["id"])
        store.log_event(p["id"], "submit")
        worker.wake()
        return {"ok": True}

    @app.get("/api/model/{sid}/{kind}")
    def model_voice(sid: int, kind: str, p=Depends(participant)):
        if store.effective_phase(p) != "listen":
            raise HTTPException(409, "今はこの操作はできません。画面を読み込み直してください")
        if kind not in AUDIO_KINDS or kind == "own" or kind not in store.listen_set(p["condition"]):
            raise HTTPException(404, "音声がありません")
        item = next((i for i in items_with_paths(p) if i["sentence_id"] == sid), None)
        path = audio_path(store, item, kind) if item else None
        if path is None:
            raise HTTPException(404, "音声はまだ準備中です")
        return audio_response(path)

    @app.post("/api/events")
    def event(body: EventBody, p=Depends(participant)):
        store.log_event(p["id"], body.type, body.sentence_id, body.detail)
        return {"ok": True}

    # ---- admin API ----

    @app.post("/api/admin/login")
    def admin_login(body: AdminLoginBody, request: Request, response: Response):
        key = client_key(request, "admin")
        if throttle.blocked(key):
            raise HTTPException(429, "ログイン失敗が続いたため、5分ほど待ってください")
        stored = admin_hash or store.get_setting("admin_password_hash") or ""
        if not auth.verify_secret(body.password, stored):
            throttle.fail(key)
            raise HTTPException(401, "パスワードが違います")
        throttle.reset(key)
        token = auth.new_token()
        store.create_session(token, "admin", "admin", auth.ADMIN_TTL)
        set_cookie(response, token, auth.ADMIN_TTL)
        return {"ok": True}

    @app.get("/api/admin/status")
    def admin_status(_=Depends(admin)):
        return {
            "engine": settings.engine,
            "engine_loading": worker.engine_loading,
            "engine_error": worker.engine_error,
            "current_clip": worker.current_clip,
            "queue": store.queue_stats(),
            "require_approval": store.require_approval(),
            "allow_controls": store.flag("allow_controls"),
            "survey_url": store.get_setting("survey_url") or "",
            "default_phase": store.get_setting("phase") or "record",
            "sentence_count": store.active_sentence_count(),
            "listen_after_submit": store.flag("listen_after_submit"),
            "standard_voice": store.get_setting("standard_voice") or "",
            "standard_voices": worker.standard_voices(),
            "listen_sets": {c: store.listen_set(c) for c in CONDITIONS},
        }

    @app.get("/api/admin/progress")
    def progress(_=Depends(admin)):
        return store.progress()

    @app.put("/api/admin/phase")
    def set_phase(body: PhaseBody, _=Depends(admin)):
        store.set_phase(body.class_name, body.phase)
        return {"ok": True}

    # participants

    @app.get("/api/admin/participants")
    def list_participants(_=Depends(admin)):
        rows = store.list_participants()
        for r in rows:
            r["submitted_at"] = fmt_time(r["submitted_at"])
            r["last_activity"] = fmt_time(r["last_activity"])
        return rows

    def create_one(pid: str, class_name: str, condition: str) -> dict:
        code = auth.new_passcode()
        store.add_participant(pid, auth.hash_secret(code), class_name, condition)
        return {"id": pid, "passcode": code, "class_name": class_name, "condition": condition}

    @app.post("/api/admin/participants")
    def create_participants(body: CreateParticipantsBody, _=Depends(admin)):
        ids = store.next_participant_ids(body.prefix.upper(), body.count)
        conditions = balanced_conditions(len(ids)) if body.condition == "random" else [body.condition] * len(ids)
        return [create_one(pid, body.class_name.strip(), cond) for pid, cond in zip(ids, conditions)]

    @app.post("/api/admin/participants/import")
    def import_participants(body: ImportParticipantsBody, _=Depends(admin)):
        """CSV lines: id,class,condition (condition = ai / native / random; header optional)."""
        rows = [r for r in csv.reader(io.StringIO(body.csv.strip())) if any(c.strip() for c in r)]
        if rows and rows[0] and rows[0][0].strip().lower() in {"id", "参加者id"}:
            rows = rows[1:]
        parsed, errors = [], []
        for n, r in enumerate(rows, 1):
            pid = (r[0] if r else "").strip().upper()
            cls = r[1].strip() if len(r) > 1 else ""
            cond = (r[2].strip().lower() if len(r) > 2 else "") or "random"
            if not PARTICIPANT_ID_RE.match(pid):
                errors.append(f"{n}行目: ID「{pid}」が不正です（英数字・-・_ のみ）")
            elif store.get_participant(pid) or any(pid == x[0] for x in parsed):
                errors.append(f"{n}行目: ID「{pid}」はすでにあります")
            elif cond not in (*CONDITIONS, "random"):
                errors.append(f"{n}行目: 条件「{cond}」は ai / native / random のいずれかにしてください")
            else:
                parsed.append((pid, cls, cond))
        if errors:
            raise HTTPException(400, "\n".join(errors[:20]))
        # 'random' rows are balanced within each class.
        by_class: dict[str, list[int]] = {}
        for i, (_, cls, cond) in enumerate(parsed):
            if cond == "random":
                by_class.setdefault(cls, []).append(i)
        final = [cond for _, _, cond in parsed]
        for indexes in by_class.values():
            for i, cond in zip(indexes, balanced_conditions(len(indexes))):
                final[i] = cond
        return [create_one(pid, cls, cond) for (pid, cls, _), cond in zip(parsed, final)]

    def existing_participant(pid: str):
        p = store.get_participant(pid)
        if p is None:
            raise HTTPException(404, "参加者が見つかりません")
        return p

    @app.patch("/api/admin/participants/{pid}")
    def update_participant(pid: str, body: UpdateParticipantBody, _=Depends(admin)):
        existing_participant(pid)
        store.update_participant(
            pid, active=body.active, condition=body.condition,
            class_name=body.class_name.strip() if body.class_name is not None else None,
        )
        worker.wake()
        return {"ok": True}

    @app.post("/api/admin/participants/{pid}/passcode")
    def reset_passcode(pid: str, _=Depends(admin)):
        existing_participant(pid)
        code = auth.new_passcode()
        store.update_participant(pid, passcode_hash=auth.hash_secret(code))
        return {"id": pid, "passcode": code}

    @app.post("/api/admin/participants/{pid}/reopen")
    def reopen(pid: str, _=Depends(admin)):
        existing_participant(pid)
        store.reopen(pid)
        return {"ok": True}

    @app.delete("/api/admin/participants/{pid}")
    def delete_participant(pid: str, _=Depends(admin)):
        existing_participant(pid)
        store.delete_participant(pid)
        return {"ok": True}

    @app.get("/api/admin/participants/{pid}/recordings")
    def participant_recordings(pid: str, _=Depends(admin)):
        existing_participant(pid)
        out = []
        for it in store.items_for(pid):
            out.append({
                "sentence_id": it["sentence_id"], "text": it["text"],
                "seconds": it["recorded_seconds"],
                "url": f"/api/admin/participants/{pid}/recordings/{it['sentence_id']}/audio"
                if it["recorded_seconds"] is not None else None,
            })
        return out

    @app.get("/api/admin/participants/{pid}/recordings/{sid}/audio")
    def participant_recording_audio(pid: str, sid: int, _=Depends(admin)):
        rec = store.get_recording(pid, sid)
        return audio_response(rec["path"] if rec else None)

    # sentences

    @app.get("/api/admin/sentences")
    def list_sentences(_=Depends(admin)):
        rows = store.list_sentences()
        std = store.standard_status()
        for r in rows:
            r["has_native"] = bool(r.pop("native_path"))
            s = std.get(r["id"])
            r["standard_status"] = s["status"] if s else None
            r["standard_error"] = (s["error"] or "").split("\n")[0] if s else ""
        return rows

    @app.post("/api/admin/sentences")
    def add_sentence(body: SentenceBody, _=Depends(admin)):
        text = body.text.strip()
        lang = guess_lang(text) if body.lang == "auto" else body.lang
        sid = store.add_sentence(text, lang, body.sort_order)
        worker.wake()
        return {"id": sid}

    @app.patch("/api/admin/sentences/{sid}")
    def update_sentence(sid: int, body: SentenceUpdateBody, _=Depends(admin)):
        current = store.get_sentence(sid)
        if current is None:
            raise HTTPException(404, "文が見つかりません")
        text = body.text.strip() if body.text is not None else None
        lang = body.lang
        if lang == "auto":
            lang = guess_lang(text if text is not None else current["text"])
        store.update_sentence(sid, text=text, lang=lang, sort_order=body.sort_order, active=body.active)
        worker.wake()
        return {"ok": True}

    @app.delete("/api/admin/sentences/{sid}")
    def delete_sentence(sid: int, _=Depends(admin)):
        store.delete_sentence(sid)
        return {"ok": True}

    @app.post("/api/admin/sentences/{sid}/native")
    async def upload_native(sid: int, file: UploadFile = File(...), _=Depends(admin)):
        if store.get_sentence(sid) is None:
            raise HTTPException(404, "文が見つかりません")
        ext = Path(file.filename or "").suffix.lower()
        if ext not in NATIVE_EXTENSIONS:
            raise HTTPException(400, "MP3・WAV・M4A・AAC・OGG のいずれかを選んでください")
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "ファイルが大きすぎます")
        store.set_native_audio(sid, data, ext)
        return {"ok": True}

    @app.delete("/api/admin/sentences/{sid}/native")
    def delete_native(sid: int, _=Depends(admin)):
        store.clear_native_audio(sid)
        return {"ok": True}

    @app.get("/api/admin/sentences/{sid}/standard")
    def standard_audio(sid: int, _=Depends(admin)):
        s = store.get_standard(sid)
        return audio_response(s["path"] if s and s["status"] == "done" else None)

    @app.post("/api/admin/sentences/{sid}/standard/regenerate")
    def regenerate_standard(sid: int, _=Depends(admin)):
        store.requeue_standard(sid)
        worker.wake()
        return {"ok": True}

    @app.get("/api/admin/sentences/{sid}/native")
    def native_audio(sid: int, _=Depends(admin)):
        s = store.get_sentence(sid)
        return audio_response(s["native_path"] if s else None)

    # AI clips

    @app.get("/api/admin/clips")
    def list_clips(participant_id: str | None = None, status: str | None = None, _=Depends(admin)):
        rows = store.list_clips(participant_id, status)
        for r in rows:
            r["updated_at"] = fmt_time(r["updated_at"])
        return rows

    @app.get("/api/admin/clips/{clip_id}/audio")
    def admin_clip_audio(clip_id: int, _=Depends(admin)):
        clip = store.get_clip(clip_id)
        return audio_response(clip["path"] if clip else None)

    @app.post("/api/admin/clips/regenerate-failed")
    def regenerate_failed(_=Depends(admin)):
        rows = store.list_clips(status="failed")
        for r in rows:
            store.requeue_clip(r["id"])
        worker.wake()
        return {"count": len(rows)}

    @app.post("/api/admin/clips/regenerate-all")
    def regenerate_all(_=Depends(admin)):
        count = store.requeue_all_clips() + store.requeue_standard()
        worker.wake()
        return {"count": count}

    @app.post("/api/admin/clips/{clip_id}/regenerate")
    def regenerate(clip_id: int, _=Depends(admin)):
        if store.get_clip(clip_id) is None:
            raise HTTPException(404, "音声がありません")
        store.requeue_clip(clip_id)
        worker.wake()
        return {"ok": True}

    @app.post("/api/admin/clips/{clip_id}/approve")
    def approve(clip_id: int, body: ApproveBody, _=Depends(admin)):
        if store.get_clip(clip_id) is None:
            raise HTTPException(404, "音声がありません")
        store.set_clip_approved(clip_id, body.approved)
        return {"ok": True}

    # settings and exports

    @app.put("/api/admin/settings")
    def update_settings(body: SettingsBody, _=Depends(admin)):
        if body.require_approval is not None:
            store.set_setting("require_approval", "1" if body.require_approval else "0")
        if body.allow_controls is not None:
            store.set_setting("allow_controls", "1" if body.allow_controls else "0")
        if body.listen_after_submit is not None:
            store.set_setting("listen_after_submit", "1" if body.listen_after_submit else "0")
        if body.listen_sets is not None:
            for cond, kinds in body.listen_sets.items():
                store.set_listen_set(cond, list(kinds))
            worker.wake()
        if body.standard_voice is not None and body.standard_voice.strip() != store.get_setting("standard_voice"):
            store.set_setting("standard_voice", body.standard_voice.strip())
            store.requeue_standard()
            worker.wake()
        if body.survey_url is not None:
            url = body.survey_url.strip()
            if url and not url.startswith(("https://", "http://")):
                raise HTTPException(400, "アンケートのURLは https:// で始めてください")
            store.set_setting("survey_url", url)
        return {"ok": True}

    @app.get("/api/admin/events.csv")
    def events_csv(_=Depends(admin)):
        rows = [["event_id", "datetime", "participant_id", "class", "condition", "type",
                 "sentence_id", "sentence_text", "detail"]]
        for e in store.list_events():
            rows.append([e["id"], fmt_time(e["created_at"]), e["participant_id"], e["class_name"] or "",
                         e["condition"] or "", e["type"], e["sentence_id"] or "", e["text"] or "", e["detail"]])
        return csv_response(rows, f"ondoku-events-{time.strftime('%Y%m%d-%H%M')}.csv")

    @app.get("/api/admin/participants.csv")
    def participants_csv(_=Depends(admin)):
        n = store.active_sentence_count()
        rows = [["participant_id", "class", "condition", "active", "recorded", "sentences",
                 "submitted_at", "ai_clips_done", "last_activity"]]
        for p in store.list_participants():
            rows.append([p["id"], p["class_name"], p["condition"], p["active"], p["recorded"], n,
                         fmt_time(p["submitted_at"]), p["clip_done"] if p["condition"] == "ai" else "",
                         fmt_time(p["last_activity"])])
        return csv_response(rows, f"ondoku-participants-{time.strftime('%Y%m%d-%H%M')}.csv")

    @app.get("/api/admin/recordings.zip")
    def recordings_zip(_=Depends(admin)):
        """Every student recording, named <class>/<id>_<sentence>.wav, for analysis."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for r in store.all_recordings():
                if Path(r["path"]).is_file():
                    folder = r["class_name"] or "no-class"
                    z.write(r["path"], f"{folder}/{r['participant_id']}_s{r['sentence_id']}.wav")
        buf.seek(0)
        return StreamingResponse(
            buf, media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="ondoku-recordings-{time.strftime("%Y%m%d-%H%M")}.zip"'},
        )

    return app
