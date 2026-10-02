from __future__ import annotations

import asyncio
import base64
from difflib import SequenceMatcher
import hashlib
from html import unescape
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from PIL import Image, ImageDraw
from pydantic import BaseModel

from pipeline import (
    PROJECT_DIR,
    _GPU_LOCK,
    _audio_duration,
    _run,
    animate_portrait,
    generate_reply,
    model_health,
    normalize_portrait,
    prepare_voice_profile,
    render_avatar,
    synthesize_voice,
    transcribe,
)


router = APIRouter()
V1_DIR = PROJECT_DIR / "outputs" / "v1_sessions"
ASSET_DIR = PROJECT_DIR / "assets" / "v1"
FIXED_HOST = Path(os.environ.get("V0_FIXED_HOST", PROJECT_DIR / "outputs" / "portrait_test.png"))
FIXED_VOICE = Path(
    os.environ.get(
        "V0_FIXED_VOICE",
        PROJECT_DIR / "outputs" / "profiles" / "149cd606ff32c58b7abc" / "reference.wav",
    )
)
FIXED_VOICE_TEXT = os.environ.get(
    "V0_FIXED_VOICE_TEXT",
    "大家好，我正在录制一段用于数字人声克隆的语音样本。这段录音的目的是尽可能完整的保留我的声音特点。",
)
FONT_PATH = Path(os.environ.get("V0_FONT", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"))
SAMPLE_PRODUCT = ASSET_DIR / "sample_product.png"
LIVE_LLM_API_KEY = os.environ.get("LIVE_LLM_API_KEY", "").strip()
LIVE_LLM_BASE_URL = os.environ.get("LIVE_LLM_BASE_URL", "https://api.apikey.fan/v1").rstrip("/")
LIVE_LLM_MODEL = os.environ.get("LIVE_LLM_MODEL", "deepseek-v4.1-flash").strip()
SCRIPT_LIBRARY_DB = PROJECT_DIR / "outputs" / "script_library.sqlite3"
VIDEO_LIBRARY_DIR = PROJECT_DIR / "outputs" / "video_library"
SCRIPT_FORMAT_VERSION = "v2-emotion-camera-director-20261002"
VIDEO_FORMAT_VERSION = "v2-emotion-camera-director-layered-20261002"
VIDEO_LAYOUT = "layered"

ACTION_LIBRARY: dict[str, dict[str, str]] = {
    "welcome": {"label": "欢迎", "motion": "open", "camera": "medium"},
    "point_product": {"label": "指向商品", "motion": "point", "camera": "product_closeup"},
    "price": {"label": "强调价格", "motion": "emphasis", "camera": "medium_close"},
    "discount": {"label": "强调优惠", "motion": "emphasis", "camera": "medium_close"},
    "size": {"label": "介绍尺寸", "motion": "measure", "camera": "product_closeup"},
    "recommend": {"label": "推荐", "motion": "recommend", "camera": "medium"},
    "buy_now": {"label": "促单", "motion": "call_to_action", "camera": "medium_close"},
    "next_product": {"label": "切换商品", "motion": "transition", "camera": "wide"},
    "idle": {"label": "待机", "motion": "idle", "camera": "medium"},
}

CAMERA_LABELS: dict[str, str] = {
    "wide": "全景",
    "medium": "中景",
    "medium_close": "中近景",
    "product_closeup": "商品特写",
}


def _camera_for(action: str, requested: Any = None) -> str:
    camera = str(requested or "").strip()
    if camera in CAMERA_LABELS:
        return camera
    return ACTION_LIBRARY.get(action, ACTION_LIBRARY["idle"])["camera"]


def _normalize_plan_cameras(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for source in plan:
        item = dict(source)
        action = str(item.get("action") or "idle")
        item["camera"] = _camera_for(action, item.get("camera"))
        normalized.append(item)
    return normalized

SCENE_TEMPLATES: dict[str, dict[str, str]] = {
    "3c": {"label": "3C直播间", "color": "2563eb", "accent": "38bdf8"},
    "food": {"label": "食品直播间", "color": "ea580c", "accent": "fbbf24"},
    "beauty": {"label": "美妆直播间", "color": "db2777", "accent": "f9a8d4"},
    "fashion": {"label": "服装直播间", "color": "334155", "accent": "cbd5e1"},
    "general": {"label": "通用直播间", "color": "4f46e5", "accent": "a5b4fc"},
}

_sessions: dict[str, dict[str, Any]] = {}
_sessions_lock = threading.RLock()
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="v1-render")
_search_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_search_cache_lock = threading.Lock()
_script_library_lock = threading.RLock()

try:
    from aiortc import RTCPeerConnection, RTCSessionDescription
    from aiortc.contrib.media import MediaPlayer

    WEBRTC_AVAILABLE = True
except Exception:
    RTCPeerConnection = RTCSessionDescription = MediaPlayer = None
    WEBRTC_AVAILABLE = False

_peer_connections: set[Any] = set()


class DanmakuRequest(BaseModel):
    text: str
    user: str = "观众"


class ProductSwitchRequest(BaseModel):
    index: int


class QueueOrderRequest(BaseModel):
    skus: list[str]


class SceneSwitchRequest(BaseModel):
    scene_template: str


class RTCOfferRequest(BaseModel):
    sdp: str
    type: str


class ScriptPreviewRequest(BaseModel):
    product: dict[str, Any]
    duration_seconds: int = 300
    has_next: bool = True


class RenderCancelled(RuntimeError):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _session_path(session_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{24}", session_id):
        raise HTTPException(status_code=404, detail="V1 直播 Session 不存在")
    return V1_DIR / session_id


def _ensure_sample_product() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    if SAMPLE_PRODUCT.exists():
        return
    image = Image.new("RGB", (900, 900), "#eff6ff")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((180, 155, 720, 745), radius=70, fill="#ffffff", outline="#2563eb", width=14)
    draw.ellipse((350, 270, 550, 470), fill="#0f172a", outline="#38bdf8", width=12)
    draw.rounded_rectangle((395, 520, 505, 640), radius=20, fill="#2563eb")
    image.save(SAMPLE_PRODUCT, optimize=True)


def _write_session(session: dict[str, Any]) -> None:
    target = _session_path(session["id"]) / "session.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def _cleanup_item_intermediates(item_path: Path) -> None:
    """Remove reproducible render scratch files while preserving uploads and final video."""
    for directory_name in ("musetalk_results", "liveportrait"):
        directory = item_path / directory_name
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)
    for file_name in (
        "speech.wav", "speech.timing.json", "portrait.png", "musetalk_job.yaml",
        "musetalk_realtime.yaml", "realtime_avatar_source.mp4",
    ):
        temporary = item_path / file_name
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _file_sha256(path: Path | None) -> str:
    if not path or not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_sessions() -> None:
    V1_DIR.mkdir(parents=True, exist_ok=True)
    resume_candidates: list[tuple[str, str]] = []
    for state_file in V1_DIR.glob("*/session.json"):
        try:
            session = json.loads(state_file.read_text(encoding="utf-8"))
            needs_resume = session.get("render_status") in {"queued", "rendering"}
            for item_index, item in enumerate(session.get("queue", [])):
                retry_known_hotfix = (
                    item.get("render_status") == "failed"
                    and any(
                        marker in str(item.get("message") or "")
                        for marker in ("musetalk_timeout", "TimeoutExpired", "timed out")
                    )
                )
                if item.get("render_status") in {"queued", "rendering"} or retry_known_hotfix:
                    _cleanup_item_intermediates(_session_path(session["id"]) / "items" / str(item_index))
                    item["render_status"] = "queued"
                    item["message"] = "服务恢复后等待续跑"
                    item.pop("error", None)
                    needs_resume = True
            if needs_resume:
                ready_count = sum(item.get("render_status") == "ready" for item in session.get("queue", []))
                session["render_status"] = "ready" if ready_count else "queued"
                if not ready_count:
                    session["playback"] = "preparing"
                    session["manual_pause"] = False
                session["message"] = f"已恢复 {ready_count} 件成品，未完成商品将自动续跑"
                session["updated_at"] = _now()
                resume_candidates.append((str(session.get("created_at") or ""), session["id"]))
            for item in session.get("queue", []):
                if item.get("script_plan") and item.get("product"):
                    item_research = dict(item.get("research") or {})
                    render_seconds = int(item_research.get("fast_clip_seconds") or _render_duration(item))
                    saved = _save_script_version(
                        dict(item["product"]),
                        _item_duration(item),
                        render_seconds,
                        list(item["script_plan"]),
                        str(item.get("llm_provider") or "session-backfill"),
                        item_research,
                    )
                    item["script_version_id"] = saved["id"]
            _sessions[session["id"]] = session
            _write_session(session)
        except Exception:
            continue
    if resume_candidates:
        _, latest_session_id = max(resume_candidates)
        _executor.submit(_render_v1_session, latest_session_id)


def _item_duration(item: dict[str, Any]) -> int:
    return max(15, min(int(item.get("duration_seconds") or 300), 3600))


def _render_duration(item: dict[str, Any]) -> int:
    """The spoken video must cover the complete configured product slot."""
    return _item_duration(item)


def _fit_speech_to_duration(
    speech_path: Path,
    target_seconds: int,
    status: Callable[[str], None] | None = None,
) -> tuple[Path, float, float]:
    """Time-fit cloned speech so the complete script fills its product slot."""
    source_seconds = _audio_duration(speech_path)
    target = float(max(15, target_seconds))
    if abs(source_seconds - target) <= 0.75:
        return speech_path, source_seconds, source_seconds

    # ffmpeg's atempo accepts 0.5..2.0 per stage. Build a safe chain for
    # unusual voices while preserving every spoken word (never truncate text).
    tempo = max(0.05, source_seconds / target)
    stages: list[float] = []
    while tempo < 0.5:
        stages.append(0.5)
        tempo /= 0.5
    while tempo > 2.0:
        stages.append(2.0)
        tempo /= 2.0
    stages.append(tempo)
    filters = [f"atempo={value:.8f}" for value in stages]
    filters.extend([f"apad=pad_dur={target:.3f}", f"atrim=duration={target:.3f}"])
    fitted = speech_path.with_name(f"{speech_path.stem}.fitted.wav")
    if status:
        status(f"正在把完整话术语音校准到 {int(target)} 秒…")
    _run(
        [
            "ffmpeg", "-y", "-v", "error", "-i", str(speech_path),
            "-filter:a", ",".join(filters), "-c:a", "pcm_s16le", str(fitted),
        ],
        timeout=max(120, int(target * 2)),
    )
    fitted.replace(speech_path)
    return speech_path, source_seconds, _audio_duration(speech_path)


def _normalize_audit_text(text: str) -> str:
    return "".join(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", str(text or "").lower()))


def _audit_spoken_script(
    speech_path: Path,
    expected_text: str,
    status: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if status:
        status("正在用 ASR 核对实际口播与原话术…")
    transcript = transcribe(speech_path, status)
    expected = _normalize_audit_text(expected_text)
    actual = _normalize_audit_text(transcript)
    similarity = SequenceMatcher(None, expected, actual, autojunk=False).ratio() if expected and actual else 0.0
    leak_markers = (
        "请使用热情", "请使用有活力", "请使用亲切", "请使用清晰专业",
        "直播主播语气", "不要机械朗读", "语调自然起伏", "youareahelpfulassistant",
    )
    leaked = [marker for marker in leak_markers if _normalize_audit_text(marker) in actual]
    return {
        "transcript": transcript,
        "similarity": round(similarity, 4),
        "prompt_leak": leaked,
        "passed": similarity >= 0.72 and not leaked,
    }


def _speech_timed_plan(
    plan: list[dict[str, Any]],
    speech_path: Path,
    fitted_seconds: float,
) -> list[dict[str, Any]]:
    timing_path = speech_path.with_suffix(".timing.json")
    if not timing_path.exists():
        return plan
    try:
        payload = json.loads(timing_path.read_text(encoding="utf-8"))
        raw_duration = max(float(payload.get("duration") or 0.0), 0.001)
        scale = float(fitted_seconds) / raw_duration
        by_index = {
            int(entry["plan_index"]): entry
            for entry in payload.get("segments") or []
            if entry.get("plan_index") is not None
        }
        timed: list[dict[str, Any]] = []
        for index, source in enumerate(plan):
            item = dict(source)
            timing = by_index.get(index)
            if timing:
                item["start"] = round(max(0.0, float(timing["start"]) * scale), 3)
                item["end"] = round(min(float(fitted_seconds), float(timing["end"]) * scale), 3)
            timed.append(item)
        if timed and all("start" in item and "end" in item for item in timed):
            return timed
    except Exception:
        pass
    return plan


def _ensure_script_library() -> None:
    SCRIPT_LIBRARY_DB.parent.mkdir(parents=True, exist_ok=True)
    with _script_library_lock, sqlite3.connect(SCRIPT_LIBRARY_DB, timeout=30) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS script_versions (
                id TEXT PRIMARY KEY,
                cache_key TEXT NOT NULL,
                sku TEXT NOT NULL,
                product_name TEXT NOT NULL,
                scheduled_duration_seconds INTEGER NOT NULL,
                render_duration_seconds INTEGER NOT NULL,
                provider TEXT NOT NULL,
                script TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                research_json TEXT NOT NULL,
                product_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_script_versions_lookup ON script_versions(cache_key, created_at DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_script_versions_sku ON script_versions(sku, created_at DESC)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS video_versions (
                id TEXT PRIMARY KEY,
                cache_key TEXT NOT NULL UNIQUE,
                sku TEXT NOT NULL,
                product_name TEXT NOT NULL,
                scheduled_duration_seconds INTEGER NOT NULL,
                script_version_id TEXT NOT NULL,
                portrait_sha256 TEXT NOT NULL,
                voice_sha256 TEXT NOT NULL,
                scene_template TEXT NOT NULL,
                orientation TEXT NOT NULL,
                expression_mode TEXT NOT NULL,
                video_path TEXT NOT NULL,
                video_duration REAL NOT NULL,
                timed_plan_json TEXT NOT NULL,
                components_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_video_versions_sku ON video_versions(sku, created_at DESC)"
        )


def _script_cache_key(
    product: dict[str, Any],
    scheduled_duration_seconds: int,
    render_duration_seconds: int,
) -> str:
    stable_product = {
        key: str(product.get(key) or "")
        for key in (
            "sku", "name", "original_price", "sale_price",
            "selling_points", "params", "promotion",
        )
    }
    payload = {
        "format": SCRIPT_FORMAT_VERSION,
        "model": LIVE_LLM_MODEL,
        "product": stable_product,
        "scheduled_duration_seconds": int(scheduled_duration_seconds),
        "render_duration_seconds": int(render_duration_seconds),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_saved_script(
    product: dict[str, Any],
    scheduled_duration_seconds: int,
    render_duration_seconds: int,
) -> dict[str, Any] | None:
    cache_key = _script_cache_key(product, scheduled_duration_seconds, render_duration_seconds)
    with _script_library_lock, sqlite3.connect(SCRIPT_LIBRARY_DB, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM script_versions WHERE cache_key = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (cache_key,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "cache_key": row["cache_key"],
        "provider": row["provider"],
        "script": row["script"],
        "plan": json.loads(row["plan_json"]),
        "research": json.loads(row["research_json"]),
        "created_at": row["created_at"],
    }


def _save_script_version(
    product: dict[str, Any],
    scheduled_duration_seconds: int,
    render_duration_seconds: int,
    plan: list[dict[str, Any]],
    provider: str,
    research: dict[str, Any],
) -> dict[str, Any]:
    _ensure_script_library()
    cache_key = _script_cache_key(product, scheduled_duration_seconds, render_duration_seconds)
    script = "".join(str(segment.get("text") or "") for segment in plan)
    with _script_library_lock, sqlite3.connect(SCRIPT_LIBRARY_DB, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        existing = connection.execute(
            "SELECT id, created_at FROM script_versions WHERE cache_key = ? AND script = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (cache_key, script),
        ).fetchone()
        if existing:
            return {"id": existing["id"], "cache_key": cache_key, "created_at": existing["created_at"]}
        version_id = uuid.uuid4().hex
        created_at = _now()
        connection.execute(
            """
            INSERT INTO script_versions (
                id, cache_key, sku, product_name, scheduled_duration_seconds,
                render_duration_seconds, provider, script, plan_json,
                research_json, product_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                cache_key,
                str(product.get("sku") or ""),
                str(product.get("name") or "商城直播商品"),
                int(scheduled_duration_seconds),
                int(render_duration_seconds),
                provider,
                script,
                json.dumps(plan, ensure_ascii=False),
                json.dumps(research, ensure_ascii=False),
                json.dumps(product, ensure_ascii=False, sort_keys=True),
                created_at,
            ),
        )
    return {"id": version_id, "cache_key": cache_key, "created_at": created_at}


def _script_versions_for_sku(sku: str) -> list[dict[str, Any]]:
    with _script_library_lock, sqlite3.connect(SCRIPT_LIBRARY_DB, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, sku, product_name, scheduled_duration_seconds,
                   render_duration_seconds, provider, script, created_at
            FROM script_versions WHERE sku = ? ORDER BY created_at DESC, rowid DESC
            """,
            (sku,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "sku": row["sku"],
            "product_name": row["product_name"],
            "duration_seconds": row["scheduled_duration_seconds"],
            "render_clip_seconds": row["render_duration_seconds"],
            "provider": row["provider"],
            "script_chars": len(row["script"]),
            "script": row["script"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def _video_cache_identity(
    item: dict[str, Any],
    script_version_id: str,
    script: str,
    portrait: Path,
    voice_reference: Path,
    voice_prompt_text: str,
    scene_template: str,
    orientation: str,
    expression_mode: str,
) -> tuple[str, dict[str, Any]]:
    product = item.get("product") or {}
    stable_product = {
        key: str(product.get(key) or "")
        for key in (
            "sku", "name", "original_price", "sale_price",
            "selling_points", "params", "promotion",
        )
    }
    product_video = Path(item["product_video_path"]) if item.get("product_video_path") else None
    components = {
        "format": VIDEO_FORMAT_VERSION,
        "video_layout": VIDEO_LAYOUT,
        "product": stable_product,
        "product_image_sha256": _file_sha256(Path(item.get("product_image") or "")),
        "product_video_sha256": _file_sha256(product_video),
        "script_version_id": str(script_version_id),
        "script_sha256": hashlib.sha256(script.encode("utf-8")).hexdigest(),
        "scheduled_duration_seconds": _item_duration(item),
        "render_duration_seconds": _render_duration(item),
        "portrait_sha256": _file_sha256(portrait),
        "voice_sha256": _file_sha256(voice_reference),
        "voice_prompt_sha256": hashlib.sha256(voice_prompt_text.encode("utf-8")).hexdigest(),
        "scene_template": scene_template,
        "orientation": orientation,
        "expression_mode": expression_mode,
    }
    encoded = json.dumps(components, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), components


def _load_saved_video(cache_key: str) -> dict[str, Any] | None:
    with _script_library_lock, sqlite3.connect(SCRIPT_LIBRARY_DB, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM video_versions WHERE cache_key = ? LIMIT 1",
            (cache_key,),
        ).fetchone()
    if not row:
        return None
    video_path = Path(row["video_path"])
    if not video_path.exists() or video_path.stat().st_size < 10_000:
        return None
    return {
        "id": row["id"],
        "cache_key": row["cache_key"],
        "video_path": row["video_path"],
        "video_duration": float(row["video_duration"]),
        "script_plan": json.loads(row["timed_plan_json"]),
        "created_at": row["created_at"],
    }


def _save_video_version(
    cache_key: str,
    components: dict[str, Any],
    item: dict[str, Any],
    script_version_id: str,
    video_path: Path,
    video_duration: float,
    timed_plan: list[dict[str, Any]],
) -> dict[str, Any]:
    _ensure_script_library()
    existing = _load_saved_video(cache_key)
    if existing:
        return existing

    product = item.get("product") or {}
    sku = re.sub(r"[^A-Za-z0-9._-]+", "_", str(product.get("sku") or "unknown"))
    version_id = uuid.uuid4().hex
    created_at = _now()
    target_dir = VIDEO_LIBRARY_DIR / sku
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{cache_key}.mp4"
    if not target.exists():
        try:
            os.link(video_path, target)
        except OSError:
            shutil.copy2(video_path, target)
    metadata = {
        "id": version_id,
        "cache_key": cache_key,
        "sku": str(product.get("sku") or ""),
        "product": product,
        "duration_seconds": _item_duration(item),
        "script_version_id": script_version_id,
        "video_duration": round(float(video_duration), 3),
        "components": components,
        "created_at": created_at,
        "video": target.name,
    }
    metadata_path = target.with_suffix(".json")
    temporary = metadata_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(metadata_path)

    with _script_library_lock, sqlite3.connect(SCRIPT_LIBRARY_DB, timeout=30) as connection:
        connection.execute(
            "DELETE FROM video_versions WHERE cache_key = ? AND video_path != ?",
            (cache_key, str(target)),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO video_versions (
                id, cache_key, sku, product_name, scheduled_duration_seconds,
                script_version_id, portrait_sha256, voice_sha256,
                scene_template, orientation, expression_mode, video_path,
                video_duration, timed_plan_json, components_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                cache_key,
                str(product.get("sku") or ""),
                str(product.get("name") or "商城直播商品"),
                _item_duration(item),
                script_version_id,
                str(components.get("portrait_sha256") or ""),
                str(components.get("voice_sha256") or ""),
                str(components.get("scene_template") or ""),
                str(components.get("orientation") or ""),
                str(components.get("expression_mode") or ""),
                str(target),
                float(video_duration),
                json.dumps(timed_plan, ensure_ascii=False),
                json.dumps(components, ensure_ascii=False, sort_keys=True),
                created_at,
            ),
        )
    saved = _load_saved_video(cache_key)
    if not saved:
        raise RuntimeError("口型视频版本已生成，但无法写入视频库")
    return saved


def _video_versions_for_sku(sku: str) -> list[dict[str, Any]]:
    with _script_library_lock, sqlite3.connect(SCRIPT_LIBRARY_DB, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, cache_key, sku, product_name, scheduled_duration_seconds,
                   script_version_id, scene_template, orientation,
                   expression_mode, video_duration, video_path, created_at
            FROM video_versions WHERE sku = ? ORDER BY created_at DESC, rowid DESC
            """,
            (sku,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "cache_key": row["cache_key"],
            "sku": row["sku"],
            "product_name": row["product_name"],
            "duration_seconds": row["scheduled_duration_seconds"],
            "script_version_id": row["script_version_id"],
            "scene_template": row["scene_template"],
            "orientation": row["orientation"],
            "expression_mode": row["expression_mode"],
            "video_duration": row["video_duration"],
            "available": Path(row["video_path"]).exists(),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def _current_action(session: dict[str, Any], item: dict[str, Any]) -> dict[str, str]:
    plan = item.get("script_plan") or []
    if not plan or session.get("playback") in {"preparing", "stopped"}:
        camera = _camera_for("idle")
        return {
            "action": "idle", "emotion": "neutral", **ACTION_LIBRARY["idle"],
            "camera": camera, "camera_label": CAMERA_LABELS[camera],
        }
    elapsed = float(session.get("item_elapsed_seconds") or 0)
    if session.get("playback") == "playing" and session.get("item_started_epoch"):
        elapsed += max(0.0, time.time() - float(session["item_started_epoch"]))
    video_duration = max(float(item.get("video_duration") or 1), 1)
    cursor = elapsed % video_duration
    for segment in plan:
        if float(segment.get("start") or 0) <= cursor < float(segment.get("end") or video_duration):
            action = segment.get("action") if segment.get("action") in ACTION_LIBRARY else "idle"
            camera = _camera_for(action, segment.get("camera"))
            return {
                "action": action,
                "emotion": str(segment.get("emotion") or "neutral"),
                **ACTION_LIBRARY[action],
                "camera": camera,
                "camera_label": CAMERA_LABELS[camera],
            }
    action = plan[-1].get("action") if plan[-1].get("action") in ACTION_LIBRARY else "idle"
    camera = _camera_for(action, plan[-1].get("camera"))
    return {
        "action": action,
        "emotion": str(plan[-1].get("emotion") or "neutral"),
        **ACTION_LIBRARY[action],
        "camera": camera,
        "camera_label": CAMERA_LABELS[camera],
    }


def _public_session(session: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: value
        for key, value in session.items()
        if key not in {
            "portrait_path",
            "portrait_normalized",
            "voice_path",
            "voice_reference",
            "voice_prompt_text",
            "item_started_epoch",
            "item_deadline_epoch",
            "error",
        }
    }
    queue_public: list[dict[str, Any]] = []
    for index, item in enumerate(session.get("queue", [])):
        public_item = {
            key: value
            for key, value in item.items()
            if key not in {"video_path", "archive_path", "product_image", "product_video_path", "error"}
        }
        if item.get("render_status") == "ready" and item.get("video_path"):
            public_item["video_url"] = f"/api/v1/live/{session['id']}/items/{index}/video"
        public_item["video_saved"] = bool(item.get("archive_path") and Path(item["archive_path"]).exists())
        queue_public.append(public_item)
    result["queue"] = queue_public
    index = int(session.get("queue_index") or 0)
    if queue_public:
        index = max(0, min(index, len(queue_public) - 1))
        current_private = session["queue"][index]
        current_public = queue_public[index]
        result["queue_index"] = index
        result["active_product"] = current_public.get("product")
        result["product"] = current_public.get("product")
        result["script"] = current_public.get("script", "")
        result["script_plan"] = current_public.get("script_plan", [])
        result["current_action"] = _current_action(session, current_private)
        if current_public.get("video_url"):
            result["video_url"] = current_public["video_url"]
        duration = _item_duration(current_private)
        elapsed = float(session.get("item_elapsed_seconds") or 0)
        if session.get("playback") == "playing" and session.get("item_started_epoch"):
            elapsed += max(0.0, time.time() - float(session["item_started_epoch"]))
        result["remaining_seconds"] = max(0, int(round(duration - elapsed)))
    public_interactions = []
    for item in result.get("interactions", []):
        public_item = {key: value for key, value in item.items() if key not in {"video_path", "error"}}
        if item.get("status") == "ready" and item.get("video_path"):
            public_item["video_url"] = f"/api/v1/live/{session['id']}/interactions/{item['id']}/video"
        public_interactions.append(public_item)
    result["interactions"] = public_interactions
    result["transport"] = {
        "webrtc_available": WEBRTC_AVAILABLE,
        "webrtc_offer": f"/api/v1/live/{session['id']}/webrtc/offer",
        "websocket": f"/ws/v1/live/{session['id']}",
        "fallback": "progressive-mp4",
    }
    return result


def _update_session(session_id: str, **changes: Any) -> dict[str, Any]:
    with _sessions_lock:
        session = _sessions[session_id]
        if "progress" in changes:
            target_status = changes.get("render_status", session.get("render_status"))
            if target_status not in {"failed", "cancelled"}:
                changes["progress"] = max(int(session.get("progress") or 0), int(changes["progress"] or 0))
        session.update(changes)
        session["updated_at"] = _now()
        _write_session(session)
        return dict(session)


def _update_queue_item(session_id: str, index: int, **changes: Any) -> None:
    with _sessions_lock:
        session = _sessions[session_id]
        session["queue"][index].update(changes)
        session["updated_at"] = _now()
        _write_session(session)


def _ensure_render_active(session_id: str) -> None:
    with _sessions_lock:
        session = _sessions.get(session_id)
        if not session or session.get("cancel_requested"):
            raise RenderCancelled("生成任务已取消")


def _set_stage_progress(
    session_id: str,
    index: int,
    queue_count: int,
    fraction: float,
    message: str,
) -> None:
    fraction = max(0.0, min(float(fraction), 1.0))
    if index == 0:
        progress = 8 + int(42 * fraction)
    else:
        remaining = max(queue_count - 1, 1)
        progress = 50 + int(50 * ((index - 1) + fraction) / remaining)
    _update_session(
        session_id,
        progress=min(progress, 99),
        message=f"第 {index + 1}/{queue_count} 件：{message}",
    )


def _product_facts(product: dict[str, Any]) -> str:
    return (
        f"商品名称：{product.get('name', '')}；商品链接：{product.get('link', '')}；"
        f"原价：{product.get('original_price', '')}；直播价：{product.get('sale_price', '')}；"
        f"卖点：{product.get('selling_points', '')}；参数：{product.get('params', '')}；"
        f"优惠：{product.get('promotion', '')}。"
    )


def _clean_search_text(value: str, limit: int = 1200) -> str:
    value = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", unescape(value)).strip()
    return value[:limit]


def _search_product_web(product: dict[str, Any]) -> dict[str, Any]:
    name = str(product.get("name") or "").strip()
    params = str(product.get("params") or "").strip()
    query = f"{name} {params} 官方 参数 配置 评测".strip()
    cache_key = re.sub(r"\s+", " ", query.lower())
    with _search_cache_lock:
        cached = _search_cache.get(cache_key)
        if cached and time.time() - cached[0] < 21_600:
            return json.loads(json.dumps(cached[1], ensure_ascii=False))

    result: dict[str, Any] = {"query": query, "provider": "bing", "sources": [], "error": ""}
    try:
        response = requests.get(
            "https://cn.bing.com/search",
            params={"q": query, "count": 8, "setlang": "zh-CN"},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            },
            timeout=18,
        )
        response.raise_for_status()
        blocks = re.findall(r'<li[^>]+class="[^"]*b_algo[^"]*"[\s\S]*?</li>', response.text, flags=re.I)
        for block in blocks[:8]:
            link = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>([\s\S]*?)</a>', block, flags=re.I)
            if not link:
                continue
            url = unescape(link.group(1)).strip()
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            snippet_match = re.search(r'<p[^>]*>([\s\S]*?)</p>', block, flags=re.I)
            source = {
                "title": _clean_search_text(link.group(2), 180),
                "url": url[:700],
                "snippet": _clean_search_text(snippet_match.group(1) if snippet_match else "", 600),
            }
            if source["title"] or source["snippet"]:
                result["sources"].append(source)
            if len(result["sources"]) >= 6:
                break
        if not result["sources"]:
            result["error"] = "搜索成功，但未解析到可用结果"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)[:180]}"

    with _search_cache_lock:
        _search_cache[cache_key] = (time.time(), result)
    return json.loads(json.dumps(result, ensure_ascii=False))


def _fallback_plan(product: dict[str, Any], has_next: bool, duration_seconds: int = 120) -> list[dict[str, str]]:
    name = str(product.get("name") or "这款商品")
    sale = str(product.get("sale_price") or "页面展示价格")
    original = str(product.get("original_price") or "日常价格")
    points = str(product.get("selling_points") or "实用、方便，适合日常使用")
    params = str(product.get("params") or "具体规格请以商品详情页为准")
    promotion = str(product.get("promotion") or "直播优惠请以商城页面显示为准")
    if duration_seconds <= 90:
        short_name = name[:32]
        short_points = points[:42]
        short_params = params[:28]
        short_promotion = promotion[:32]
        return [
            {"stage": "welcome", "action": "welcome", "emotion": "friendly", "text": f"欢迎来到直播间，现在为大家介绍{short_name}。"},
            {"stage": "introduction", "action": "point_product", "emotion": "confident", "text": f"这款商品的重点是{short_points}，外观和细节可以看当前画面。"},
            {"stage": "parameters", "action": "recommend", "emotion": "professional", "text": f"规格方面是{short_params}，更多参数请以商城详情页为准。"},
            {"stage": "price", "action": "price", "emotion": "excited", "text": f"原价{original}，当前直播价{sale}，下单前请核对页面价格。"},
            {"stage": "discount", "action": "discount", "emotion": "excited", "text": f"本场优惠是{short_promotion}，活动以商城实时显示为准。"},
            {"stage": "buy_now", "action": "buy_now", "emotion": "enthusiastic", "text": "感兴趣的朋友可以点击右侧商品查看详情，确认规格后再下单。"},
            {"stage": "next", "action": "next_product" if has_next else "idle", "emotion": "friendly", "text": "接下来继续介绍下一件商品。" if has_next else "稍后继续为大家重复商品重点。"},
        ]
    return [
        {"stage": "welcome", "action": "welcome", "emotion": "friendly", "text": f"大家好，欢迎来到 eget 数字人直播间，今天为大家带来一款值得关注的{name}。"},
        {"stage": "introduction", "action": "point_product", "emotion": "confident", "text": f"现在镜头里介绍的就是{name}，大家可以先看一下商品外观和整体设计。"},
        {"stage": "selling_points", "action": "recommend", "emotion": "enthusiastic", "text": f"它的核心卖点包括{points}，日常使用方便，也能覆盖多种常见使用场景。"},
        {"stage": "parameters", "action": "size", "emotion": "professional", "text": f"规格参数方面，{params}，下单前也可以在右侧商品详情中再次确认。"},
        {"stage": "price", "action": "price", "emotion": "excited", "text": f"价格方面，日常参考价是{original}，当前商城直播价是{sale}，实际金额以页面显示为准。"},
        {"stage": "discount", "action": "discount", "emotion": "excited", "text": f"本场优惠信息是{promotion}，有需要的朋友可以留意页面上的实时活动。"},
        {"stage": "scenario", "action": "recommend", "emotion": "warm", "text": f"如果你正在寻找兼顾实用性和使用体验的商品，{name}可以作为一个重点比较的选择。"},
        {"stage": "buy_now", "action": "buy_now", "emotion": "enthusiastic", "text": "感兴趣的朋友可以点击右侧商品进入详情页，确认规格和优惠后再下单。"},
        {"stage": "next", "action": "next_product" if has_next else "idle", "emotion": "friendly", "text": "接下来继续为大家介绍下一件商品。" if has_next else "稍后我还会继续重复重点，大家有问题也可以发送弹幕。"},
    ]


def _extract_json(text: str) -> Any:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.S)
    start = min([pos for pos in (cleaned.find("{"), cleaned.find("[")) if pos >= 0], default=-1)
    if start > 0:
        cleaned = cleaned[start:]
    for candidate in (cleaned, cleaned[: cleaned.rfind("}") + 1], cleaned[: cleaned.rfind("]") + 1]):
        try:
            return json.loads(candidate)
        except Exception:
            continue
    raise ValueError("LLM 没有返回有效 JSON")


def _generate_plan(
    product: dict[str, Any],
    has_next: bool,
    duration_seconds: int,
) -> tuple[list[dict[str, str]], str, dict[str, Any]]:
    duration_seconds = max(15, min(int(duration_seconds or 300), 3600))
    script_seconds = min(duration_seconds, 600)
    # CosyVoice with the lively delivery profile speaks about 4.3 Chinese
    # characters per second. Generate a small surplus so duration fitting
    # speeds speech up slightly instead of stretching it into a flat drawl.
    target_chars = max(70, int(script_seconds * 4.8))
    segment_count = max(7, min(24, round(script_seconds / 18)))
    per_segment = max(10, round(target_chars / segment_count))
    research = _search_product_web(product)
    research["duration_seconds"] = duration_seconds
    research["script_seconds"] = script_seconds
    research["target_chars"] = target_chars
    fallback = _normalize_plan_cameras(_fallback_plan(product, has_next, duration_seconds))
    research["camera_direction"] = True
    if not LIVE_LLM_API_KEY:
        research["llm_error"] = "LIVE_LLM_API_KEY 未加载"
        return fallback, "local/duration-template-no-key", research
    actions = ", ".join(ACTION_LIBRARY)
    sources_text = json.dumps(research.get("sources") or [], ensure_ascii=False)
    prompt = (
        "你是专业电商直播编导。请综合商城商品事实和联网搜索摘要，生成自然、具体、不重复的中文直播话术。"
        f"商品列表设定讲解时长为{duration_seconds}秒；本次生成目标约{script_seconds}秒，"
        f"总字数目标{target_chars}字（允许上下浮动12%），约{segment_count}段，每段约{per_segment}字。"
        "必须依次覆盖欢迎、商品介绍、核心卖点、参数、价格、优惠、使用场景、促单、切换商品；"
        "同一卖点不要换句话重复。价格、优惠和购买链接必须只采用商城事实；"
        "联网摘要只用于补充可核验的功能、规格、适用人群和使用场景。"
        "若搜索资料冲突或无法确认，就明确提示以官方商品页为准，禁止虚构最低价、库存、销量、赠品、功效或参数。"
        "只返回JSON对象，格式为{\"segments\":[{\"stage\":\"welcome\",\"text\":\"...\"," 
        "\"action\":\"welcome\",\"emotion\":\"friendly\",\"camera\":\"medium\"}]}。"
        "action只能使用：" + actions + "；camera只能使用wide、medium、medium_close、product_closeup。"
        "请让情绪、动作和镜头匹配：商品细节用product_closeup，价格和促单用medium_close，切品用wide。"
        "不要Markdown，不要输出JSON以外内容。商城事实：" + _product_facts(product) +
        " 联网搜索结果：" + sources_text
    )

    def request_segments(user_prompt: str, temperature: float = 0.35) -> list[dict[str, str]]:
        response = requests.post(
            f"{LIVE_LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {LIVE_LLM_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": LIVE_LLM_MODEL,
                "messages": [{"role": "user", "content": user_prompt}],
                "temperature": temperature,
                "max_tokens": min(8000, max(1400, target_chars * 2)),
                "thinking": {"type": "disabled"},
            },
            timeout=90,
        )
        response.raise_for_status()
        payload = _extract_json(response.json()["choices"][0]["message"]["content"])
        segments = payload.get("segments") if isinstance(payload, dict) else payload
        result: list[dict[str, str]] = []
        for entry in segments or []:
            text = str(entry.get("text") or "").strip()
            action = str(entry.get("action") or "idle")
            if text and action in ACTION_LIBRARY:
                result.append({
                    "stage": str(entry.get("stage") or action),
                    "text": text[:320],
                    "action": action,
                    "emotion": str(entry.get("emotion") or "neutral")[:24],
                    "camera": _camera_for(action, entry.get("camera")),
                })
        return result

    def plan_chars(plan: list[dict[str, str]]) -> int:
        return sum(len(segment["text"]) for segment in plan)

    def force_upper_bound(plan: list[dict[str, str]], maximum: int) -> list[dict[str, str]]:
        """Keep every required action while preventing speech from overrunning its slot."""
        total = plan_chars(plan)
        if total <= maximum:
            return plan
        ratio = maximum / max(total, 1)
        fitted: list[dict[str, str]] = []
        for segment in plan:
            original = segment["text"]
            limit = max(12, int(len(original) * ratio))
            shortened = original[:limit]
            punctuation = max(shortened.rfind(mark) for mark in "。！？；")
            if punctuation >= int(limit * 0.62):
                shortened = shortened[: punctuation + 1]
            elif len(original) > limit:
                shortened = shortened.rstrip("，、；： ") + "。"
            fitted.append({**segment, "text": shortened})
        return fitted

    try:
        cleaned = request_segments(prompt, 0.45)
        required_actions = {"welcome", "point_product", "price", "discount", "buy_now", "next_product" if has_next else "idle"}
        returned_actions = {segment["action"] for segment in cleaned}
        if len(cleaned) < 4:
            raise ValueError("结构化直播话术有效段落不足")
        missing_actions = required_actions - returned_actions
        if missing_actions:
            fallback_by_action = {
                segment["action"]: segment
                for segment in _fallback_plan(product, has_next, duration_seconds)
            }
            for missing_action in missing_actions:
                repair = fallback_by_action.get(missing_action)
                if repair:
                    cleaned.append(repair)
            action_order = {
                "welcome": 0, "point_product": 1, "size": 2, "recommend": 3,
                "price": 4, "discount": 5, "buy_now": 6, "next_product": 7, "idle": 8,
            }
            cleaned.sort(key=lambda segment: action_order.get(segment["action"], 9))
            research["structure_repairs"] = sorted(missing_actions)
            returned_actions = {segment["action"] for segment in cleaned}
        total_chars = plan_chars(cleaned)
        if not required_actions.issubset(returned_actions):
            raise ValueError("结构化直播话术段落或必需动作不足")

        lower_bound = max(60, int(target_chars * 0.88))
        upper_bound = max(lower_bound, int(target_chars * 1.12))
        if not lower_bound <= total_chars <= upper_bound:
            calibration_prompt = (
                "你是直播话术时长校准器。请在不改变商品事实、价格、优惠、购买链接、段落action和整体顺序的前提下，"
                f"把下面总计{total_chars}字的话术改写为{target_chars}字左右，最终正文总字数必须在"
                f"{lower_bound}到{upper_bound}字之间。不要新增未经证实的参数，不要重复同一卖点。"
                "只返回与输入相同格式的JSON对象，不要Markdown。原话术：" +
                json.dumps({"segments": cleaned}, ensure_ascii=False)
            )
            calibrated = request_segments(calibration_prompt, 0.2)
            calibrated_actions = {segment["action"] for segment in calibrated}
            if len(calibrated) >= 6 and required_actions.issubset(calibrated_actions):
                cleaned = calibrated
                total_chars = plan_chars(cleaned)
                research["duration_calibration"] = "llm-second-pass"

        if total_chars > upper_bound:
            cleaned = force_upper_bound(cleaned, upper_bound)
            total_chars = plan_chars(cleaned)
            research["duration_calibration"] = "llm-second-pass+hard-upper-bound"
        if total_chars < lower_bound:
            raise ValueError(f"模型话术过短：{total_chars}/{target_chars} 字")
        research["actual_chars"] = total_chars
        research["duration_bounds"] = [lower_bound, upper_bound]
        return cleaned[:24], f"bing+apikey.fan/{LIVE_LLM_MODEL}", research
    except Exception as exc:
        research["llm_error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
        return fallback, "local/duration-template-fallback", research


def _extend_plan_for_audio_duration(
    product: dict[str, Any],
    plan: list[dict[str, Any]],
    current_seconds: float,
    target_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Append factual material when lively TTS is shorter than the product slot."""
    current_chars = sum(len(str(item.get("text") or "")) for item in plan)
    if not LIVE_LLM_API_KEY or current_seconds <= 1:
        return plan, {"extended": False, "reason": "llm-unavailable"}
    desired_total = int(current_chars * target_seconds / current_seconds * 1.03)
    additional_chars = max(90, desired_total - current_chars)
    available_slots = max(0, 24 - len(plan))
    if available_slots == 0:
        return plan, {"extended": False, "reason": "segment-limit"}
    additional_count = min(available_slots, max(2, min(8, round(additional_chars / 150))))
    research = _search_product_web(product)
    prompt = (
        "你是专业电商直播编导。现有口播在直播带货语速下时长不足，请只补写新的中文口播段落，"
        "不能改写或重复现有段落。补写内容必须继续围绕同一商品，增加可核验的使用场景、选购建议、"
        "参数解释、价格提醒、互动提问或购买注意事项；不得虚构参数、库存、销量、赠品、最低价或功效。"
        f"请补写约{additional_chars}字，共{additional_count}段。"
        "action只能从point_product、size、recommend、price、discount、buy_now中选择。"
        "camera只能从wide、medium、medium_close、product_closeup中选择，并与内容匹配。"
        "只返回JSON对象，格式为{\"segments\":[{\"stage\":\"detail\",\"text\":\"...\","
        "\"action\":\"recommend\",\"emotion\":\"friendly\",\"camera\":\"medium\"}]}，不要Markdown。"
        "商城事实：" + _product_facts(product) +
        " 联网摘要：" + json.dumps(research.get("sources") or [], ensure_ascii=False) +
        " 现有话术：" + json.dumps([item.get("text") for item in plan], ensure_ascii=False)
    )
    try:
        response = requests.post(
            f"{LIVE_LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {LIVE_LLM_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": LIVE_LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.35,
                "max_tokens": min(5000, max(900, additional_chars * 2)),
                "thinking": {"type": "disabled"},
            },
            timeout=90,
        )
        response.raise_for_status()
        payload = _extract_json(response.json()["choices"][0]["message"]["content"])
        segments = payload.get("segments") if isinstance(payload, dict) else payload
        allowed = {"point_product", "size", "recommend", "price", "discount", "buy_now"}
        additions: list[dict[str, Any]] = []
        existing_text = "".join(str(item.get("text") or "") for item in plan)
        for entry in segments or []:
            text = str(entry.get("text") or "").strip()
            action = str(entry.get("action") or "recommend")
            if text and action in allowed and text not in existing_text:
                additions.append({
                    "stage": str(entry.get("stage") or action),
                    "text": text[:360],
                    "action": action,
                    "emotion": str(entry.get("emotion") or "friendly")[:24],
                    "camera": _camera_for(action, entry.get("camera")),
                })
            if len(additions) >= available_slots:
                break
        added_chars = sum(len(item["text"]) for item in additions)
        if added_chars < max(60, int(additional_chars * 0.55)):
            return plan, {"extended": False, "reason": f"extension-too-short:{added_chars}"}
        insert_at = len(plan)
        if plan and str(plan[-1].get("action")) in {"next_product", "idle"}:
            insert_at -= 1
        extended = [*plan[:insert_at], *additions, *plan[insert_at:]]
        return extended[:24], {
            "extended": True,
            "previous_seconds": round(current_seconds, 3),
            "target_seconds": round(target_seconds, 3),
            "requested_additional_chars": additional_chars,
            "added_chars": added_chars,
            "added_segments": len(additions),
        }
    except Exception as exc:
        return plan, {"extended": False, "reason": f"{type(exc).__name__}: {str(exc)[:180]}"}


def _srt_timestamp(seconds: float) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _write_plan_subtitles(plan: list[dict[str, Any]], duration: float, destination: Path) -> list[dict[str, Any]]:
    has_audio_timing = bool(plan) and all(
        isinstance(item.get("start"), (int, float))
        and isinstance(item.get("end"), (int, float))
        and float(item["end"]) > float(item["start"])
        for item in plan
    )
    total = sum(max(len(str(item.get("text") or "")), 1) for item in plan) or 1
    cursor = 0.0
    blocks: list[str] = []
    timed: list[dict[str, Any]] = []
    for index, item in enumerate(plan, start=1):
        text = str(item.get("text") or "").strip()
        if has_audio_timing:
            cursor = max(0.0, min(float(item["start"]), duration))
            end = max(cursor + 0.05, min(float(item["end"]), duration))
        else:
            span = duration * max(len(text), 1) / total
            end = duration if index == len(plan) else cursor + span
        wrapped = "\n".join(text[pos : pos + 20] for pos in range(0, len(text), 20))
        blocks.append(f"{index}\n{_srt_timestamp(cursor)} --> {_srt_timestamp(end)}\n{wrapped}\n")
        timed_item = dict(item)
        timed_item.update(start=round(cursor, 3), end=round(end, 3))
        timed.append(timed_item)
        cursor = end
    destination.write_text("\n".join(blocks), encoding="utf-8")
    return timed


def _compose_v1_scene(
    avatar_video: Path,
    product_image: Path,
    product_video: Path | None,
    product: dict[str, Any],
    plan: list[dict[str, Any]],
    job_path: Path,
    scene_template: str,
    orientation: str,
    status: Callable[[str], None] | None = None,
) -> tuple[Path, list[dict[str, Any]]]:
    if not FONT_PATH.exists():
        raise RuntimeError(f"缺少中文字体：{FONT_PATH}")
    duration = max(_audio_duration(avatar_video), 0.1)
    subtitle_path = job_path / "subtitles.srt"
    timed_plan = _write_plan_subtitles(plan, duration, subtitle_path)
    template = SCENE_TEMPLATES.get(scene_template, SCENE_TEMPLATES["general"])
    label_path = job_path / "scene_label.txt"
    label_path.write_text(f"{template['label']} · AI数字人", encoding="utf-8")
    if orientation == "portrait":
        width, height, portrait_width = 720, 1280, 720
        font_size = 22
    else:
        width, height, portrait_width = 1280, 720, 720
        font_size = 24
    font = str(FONT_PATH)
    color = template["color"]
    # The generated file contains only the presenter. Product cards and
    # subtitles are rendered by the browser as independent, synchronized
    # layers so they can never cover the presenter's face.
    filters = (
        "[0:v]split=2[background_source][portrait_source];"
        f"[background_source]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
        f"boxblur=22:2,eq=brightness=-0.24,drawbox=x=0:y=0:w=iw:h=ih:color=0x{color}@0.16:t=fill[background];"
        f"[portrait_source]scale={portrait_width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={portrait_width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x08111f[portrait];"
        "[background][portrait]overlay=(W-w)/2:0[base];"
        f"[base]"
        f"drawbox=x=24:y=20:w=310:h=48:color=0x{color}@0.94:t=fill,"
        f"drawtext=fontfile={font}:textfile={label_path}:fontcolor=white:fontsize={font_size}:x=42:y=30[outv]"
    )
    output_path = job_path / "v1_program.mp4"
    expected_seconds = max(30.0, duration * 0.75)

    def compose_heartbeat(elapsed: float) -> None:
        if status:
            percent = min(95, max(1, int(elapsed * 100 / expected_seconds)))
            status(f"独立数字人层合成进度 {percent}%…")

    _run(
        [
            "ffmpeg", "-y", "-v", "error", "-i", str(avatar_video),
            "-filter_complex", filters, "-map", "[outv]", "-map", "0:a:0?", "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(output_path),
        ],
        timeout=900,
        heartbeat=compose_heartbeat,
        heartbeat_interval=5,
    )
    if not output_path.exists() or output_path.stat().st_size < 10_000:
        raise RuntimeError("V1 场景合成没有生成有效视频")
    return output_path, timed_plan


def _render_v1_session(session_id: str) -> None:
    successful = 0
    try:
        with _sessions_lock:
            session = dict(_sessions[session_id])
        _ensure_render_active(session_id)
        _update_session(session_id, render_status="rendering", progress=2, message="正在建立主播形象与音色…")
        job_path = _session_path(session_id)
        with _GPU_LOCK:
            _ensure_render_active(session_id)
            portrait_source = Path(session.get("portrait_path") or FIXED_HOST)
            voice_source = Path(session.get("voice_path") or FIXED_VOICE)
            if session.get("custom_voice"):
                voice_reference, voice_prompt_text = prepare_voice_profile(
                    voice_source, session.get("reference_text") or None
                )
            else:
                voice_reference, voice_prompt_text = FIXED_VOICE, FIXED_VOICE_TEXT
            portrait = normalize_portrait(portrait_source, job_path / "host.png")
        _ensure_render_active(session_id)
        _update_session(
            session_id,
            portrait_normalized=str(portrait),
            voice_reference=str(voice_reference),
            voice_prompt_text=voice_prompt_text,
            progress=8,
            message="主播准备完成，正在自动生成商品话术…",
        )

        queue_count = len(session["queue"])
        render_cache: dict[str, dict[str, Any]] = {}
        for index in range(queue_count):
            _ensure_render_active(session_id)
            with _sessions_lock:
                current_session = _sessions[session_id]
                item = dict(current_session["queue"][index])
            existing_video = Path(item.get("video_path") or "")
            if item.get("render_status") == "ready" and item.get("video_path") and existing_video.exists():
                successful += 1
                continue
            product = dict(item["product"])
            item_path = _session_path(session_id) / "items" / str(index)
            item_path.mkdir(parents=True, exist_ok=True)
            try:
                cache_signature = json.dumps(
                    {
                        key: value
                        for key, value in product.items()
                        if key not in {"sku", "link"}
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ) + f"|{current_session.get('scene_template')}|{current_session.get('orientation')}|{current_session.get('expression_mode')}|{_item_duration(item)}"
                if not item.get("product_video_path") and cache_signature in render_cache:
                    cached = render_cache[cache_signature]
                    successful += 1
                    _update_queue_item(
                        session_id,
                        index,
                        render_status="ready",
                        message="已复用相同商品直播片段",
                        script=cached["script"],
                        script_plan=cached["script_plan"],
                        llm_provider=cached["llm_provider"],
                        research=cached.get("research", {}),
                        video_path=cached["video_path"],
                        archive_path=cached.get("archive_path", ""),
                        video_cache_key=cached.get("video_cache_key", ""),
                        video_version_id=cached.get("video_version_id", ""),
                        video_duration=cached["video_duration"],
                        video_layout=VIDEO_LAYOUT,
                    )
                    with _sessions_lock:
                        live_session = _sessions[session_id]
                        live_session["progress"] = 50 if index == 0 else min(
                            99, 50 + int(50 * index / max(queue_count - 1, 1))
                        )
                        live_session["message"] = f"已生成 {successful}/{queue_count} 件商品，直播可以开始"
                        live_session["updated_at"] = _now()
                        _write_session(live_session)
                    continue
                scheduled_seconds = _item_duration(item)
                render_seconds = _render_duration(item)
                saved_script = _load_saved_script(product, scheduled_seconds, render_seconds)
                minimum_reusable_chars = max(70, int(min(render_seconds, 600) * 3.3))
                if saved_script and len(str(saved_script.get("script") or "")) < minimum_reusable_chars:
                    saved_script = None
                if saved_script:
                    _set_stage_progress(session_id, index, queue_count, 0.14, "正在复用已保存的商品话术")
                    _update_queue_item(session_id, index, render_status="rendering", message="正在复用已保存的商品话术")
                    plan = [
                        {key: value for key, value in dict(segment).items() if key not in {"start", "end"}}
                        for segment in saved_script["plan"]
                    ]
                    provider = f"saved/{saved_script['provider']}"
                    research = dict(saved_script.get("research") or {})
                    research.update({
                        "scheduled_duration_seconds": scheduled_seconds,
                        "fast_clip_seconds": render_seconds,
                        "script_reused": True,
                        "script_version_id": saved_script["id"],
                        "script_saved_at": saved_script["created_at"],
                    })
                    script_version = {"id": saved_script["id"]}
                else:
                    _set_stage_progress(session_id, index, queue_count, 0.08, "DeepSeek 正在生成结构化直播话术")
                    _update_queue_item(session_id, index, render_status="rendering", message="DeepSeek 正在生成结构化直播话术")
                    plan, provider, research = _generate_plan(
                        product,
                        index < queue_count - 1,
                        render_seconds,
                    )
                    research["scheduled_duration_seconds"] = scheduled_seconds
                    research["fast_clip_seconds"] = render_seconds
                    research["script_reused"] = False
                    script_version = _save_script_version(
                        product,
                        scheduled_seconds,
                        render_seconds,
                        plan,
                        provider,
                        research,
                    )
                    research["script_version_id"] = script_version["id"]
                _ensure_render_active(session_id)
                script = "".join(entry["text"] for entry in plan)
                _update_queue_item(
                    session_id, index, script=script, script_plan=plan, llm_provider=provider,
                    research=research, script_version_id=script_version["id"],
                    message="正在合成数字人语音与动作",
                )
                video_cache_key, video_components = _video_cache_identity(
                    item,
                    script_version["id"],
                    script,
                    portrait,
                    voice_reference,
                    voice_prompt_text,
                    current_session.get("scene_template", "general"),
                    current_session.get("orientation", "landscape"),
                    current_session.get("expression_mode", "normal"),
                )
                saved_video = _load_saved_video(video_cache_key)
                def item_status(message: str) -> None:
                    _ensure_render_active(session_id)
                    _update_queue_item(session_id, index, message=message)
                    lowered = message.lower()
                    percent_match = re.search(r"进度\s*(\d+)%", message)
                    reported_percent = min(100, int(percent_match.group(1))) if percent_match else 0
                    chunk_match = re.search(r"进度\s*(\d+)\s*/\s*(\d+)", message)
                    if "场景合成进度" in message or "独立数字人层合成进度" in message:
                        fraction = 0.86 + 0.13 * reported_percent / 100
                    elif "口型" in message or "musetalk" in lowered:
                        fraction = 0.56 + 0.28 * reported_percent / 100 if percent_match else 0.56
                    elif "表情" in message or "liveportrait" in lowered:
                        fraction = 0.42
                    elif chunk_match:
                        fraction = 0.18 + 0.16 * int(chunk_match.group(1)) / max(int(chunk_match.group(2)), 1)
                    elif "音色" in message or "语音" in message or "合成" in message:
                        fraction = 0.24
                    else:
                        fraction = 0.18
                    _set_stage_progress(session_id, index, queue_count, fraction, message)

                if saved_video:
                    _set_stage_progress(session_id, index, queue_count, 0.99, "完全匹配，正在复用已保存口型视频")
                    video = Path(saved_video["video_path"])
                    timed_plan = list(saved_video["script_plan"])
                    video_duration = round(float(saved_video["video_duration"]), 3)
                    archive_path = video
                    video_version_id = saved_video["id"]
                    research["video_reused"] = True
                    research["video_version_id"] = video_version_id
                    research["video_saved_at"] = saved_video["created_at"]
                    ready_message = "已复用完全匹配的保存口型视频"
                else:
                    with _GPU_LOCK:
                        _ensure_render_active(session_id)
                        speech = synthesize_voice(
                            script, voice_reference, voice_prompt_text, item_path / "speech.wav", item_status,
                            delivery_plan=plan,
                            strict_content=True,
                        )
                        # Lively delivery should be created from enough copy,
                        # never by heavily slowing a short recording. Measure
                        # the real cloned speech and ask the LLM for additional
                        # factual paragraphs when it is below the product slot.
                        extension_attempts: list[dict[str, Any]] = []
                        for extension_index in range(2):
                            measured_seconds = _audio_duration(speech)
                            if measured_seconds >= float(render_seconds) * 0.96:
                                break
                            item_status(
                                f"直播语速口播约 {measured_seconds:.0f} 秒，不足 {render_seconds} 秒，"
                                f"正在补充有效话术（{extension_index + 1}/2）…"
                            )
                            extended_plan, extension_info = _extend_plan_for_audio_duration(
                                product, plan, measured_seconds, float(render_seconds)
                            )
                            extension_attempts.append(extension_info)
                            if not extension_info.get("extended"):
                                break
                            plan = extended_plan
                            script = "".join(entry["text"] for entry in plan)
                            research["audio_duration_extensions"] = extension_attempts
                            research["actual_chars"] = len(script)
                            research["script_reused"] = False
                            script_version = _save_script_version(
                                product,
                                scheduled_seconds,
                                render_seconds,
                                plan,
                                f"{provider}+duration-extension",
                                research,
                            )
                            research["script_version_id"] = script_version["id"]
                            _update_queue_item(
                                session_id,
                                index,
                                script=script,
                                script_plan=plan,
                                research=research,
                                script_version_id=script_version["id"],
                                message="补充话术已保存，正在重新合成直播带货语音",
                            )
                            video_cache_key, video_components = _video_cache_identity(
                                item,
                                script_version["id"],
                                script,
                                portrait,
                                voice_reference,
                                voice_prompt_text,
                                current_session.get("scene_template", "general"),
                                current_session.get("orientation", "landscape"),
                                current_session.get("expression_mode", "normal"),
                            )
                            speech = synthesize_voice(
                                script,
                                voice_reference,
                                voice_prompt_text,
                                item_path / "speech.wav",
                                item_status,
                                delivery_plan=plan,
                                strict_content=True,
                            )
                        measured_seconds = _audio_duration(speech)
                        research["audio_duration_extensions"] = extension_attempts
                        if measured_seconds < float(render_seconds) * 0.90:
                            raise RuntimeError(
                                f"直播语速口播仅 {measured_seconds:.1f} 秒，补充话术后仍不足 "
                                f"{render_seconds} 秒，已阻止用慢放方式凑时长"
                            )
                        speech, raw_audio_seconds, fitted_audio_seconds = _fit_speech_to_duration(
                            speech, render_seconds, item_status
                        )
                        spoken_audit = _audit_spoken_script(speech, script, item_status)
                        spoken_audit["mode"] = "strict-zero-shot"
                        if not spoken_audit["passed"]:
                            raise RuntimeError(
                                "实际口播未通过原话术 ASR 核对，"
                                f"一致度 {spoken_audit['similarity']:.1%}，已阻止保存错误视频"
                            )
                        speech_timed_plan = _speech_timed_plan(plan, speech, fitted_audio_seconds)
                        research["raw_audio_seconds"] = round(raw_audio_seconds, 3)
                        research["fitted_audio_seconds"] = round(fitted_audio_seconds, 3)
                        research["duration_fitted"] = abs(raw_audio_seconds - fitted_audio_seconds) > 0.75
                        research["spoken_audit"] = spoken_audit
                        _update_queue_item(session_id, index, research=research)
                        motion = animate_portrait(
                            portrait,
                            speech,
                            item_path,
                            current_session.get("expression_mode", "normal"),
                            item_status,
                        )
                        avatar = render_avatar(
                            motion,
                            speech,
                            item_path,
                            item_status,
                            avatar_cache_key=(
                                f"{_file_sha256(portrait)}:"
                                f"{current_session.get('expression_mode', 'normal')}"
                            ),
                        )
                    _ensure_render_active(session_id)
                    _set_stage_progress(session_id, index, queue_count, 0.86, "正在合成独立数字人视频层")
                    _update_queue_item(session_id, index, message="正在合成独立数字人视频层")
                    video, timed_plan = _compose_v1_scene(
                        avatar,
                        Path(item["product_image"]),
                        Path(item["product_video_path"]) if item.get("product_video_path") else None,
                        product,
                        speech_timed_plan,
                        item_path,
                        current_session.get("scene_template", "general"),
                        current_session.get("orientation", "landscape"),
                        item_status,
                    )
                    _ensure_render_active(session_id)
                    video_duration = round(_audio_duration(video), 3)
                    saved_video = _save_video_version(
                        video_cache_key,
                        video_components,
                        item,
                        script_version["id"],
                        video,
                        video_duration,
                        timed_plan,
                    )
                    archive_path = Path(saved_video["video_path"])
                    video_version_id = saved_video["id"]
                    research["video_reused"] = False
                    research["video_version_id"] = video_version_id
                    research["video_saved_at"] = saved_video["created_at"]
                    ready_message = (
                        "原话术口播核对通过"
                        f"（一致度 {spoken_audit['similarity']:.1%}），口型视频已保存为新版本"
                    )
                successful += 1
                _update_queue_item(
                    session_id,
                    index,
                    render_status="ready",
                    message=ready_message,
                    video_path=str(video),
                    archive_path=str(archive_path),
                    video_cache_key=video_cache_key,
                    video_version_id=video_version_id,
                    video_duration=video_duration,
                    script_plan=timed_plan,
                    video_layout=VIDEO_LAYOUT,
                    research=research,
                )
                _cleanup_item_intermediates(item_path)
                render_cache[cache_signature] = {
                    "script": script,
                    "script_plan": timed_plan,
                    "llm_provider": provider,
                    "research": research,
                    "video_path": str(video),
                    "archive_path": str(archive_path),
                    "video_cache_key": video_cache_key,
                    "video_version_id": video_version_id,
                    "video_duration": video_duration,
                    "video_layout": VIDEO_LAYOUT,
                }
                progress = 50 if index == 0 else min(
                    99, 50 + int(50 * index / max(queue_count - 1, 1))
                )
                with _sessions_lock:
                    live_session = _sessions[session_id]
                    if live_session.get("render_status") != "ready":
                        live_session["render_status"] = "ready"
                        live_session["queue_index"] = index
                    live_session["progress"] = max(int(live_session.get("progress") or 0), progress)
                    should_auto_play = (
                        live_session.get("playback") in {"preparing", "paused"}
                        and not live_session.get("manual_pause", False)
                    )
                    if should_auto_play:
                        now = time.time()
                        live_session["playback"] = "playing"
                        live_session["queue_index"] = index
                        live_session["item_elapsed_seconds"] = 0
                        live_session["item_started_epoch"] = now
                        live_session["item_deadline_epoch"] = now + _item_duration(live_session["queue"][index])
                        live_session["sequence"] = int(live_session.get("sequence") or 0) + 1
                        live_session["message"] = f"第 {index + 1} 件商品已就绪，已自动开始直播"
                    else:
                        live_session["message"] = f"已生成 {successful}/{queue_count} 件商品，直播可以开始"
                    live_session["updated_at"] = _now()
                    _write_session(live_session)
            except RenderCancelled:
                _cleanup_item_intermediates(item_path)
                raise
            except Exception as exc:
                traceback.print_exc()
                _cleanup_item_intermediates(item_path)
                _update_queue_item(
                    session_id,
                    index,
                    render_status="failed",
                    message=f"生成失败：{type(exc).__name__}: {exc}",
                    error=traceback.format_exc()[-5000:],
                )
        if successful:
            _update_session(
                session_id,
                render_status="ready",
                progress=100,
                message=f"V1 商品队列已就绪，共 {successful} 件商品可自动轮播",
            )
        else:
            _update_session(session_id, render_status="failed", progress=0, message="所有商品直播片段均生成失败")
    except RenderCancelled:
        _update_session(
            session_id,
            render_status="cancelled",
            playback="stopped",
            message="生成任务已被较新的上传任务替代",
        )
    except Exception as exc:
        traceback.print_exc()
        _update_session(
            session_id,
            render_status="failed",
            playback="stopped",
            message=f"V1 生成失败：{type(exc).__name__}: {exc}",
            error=traceback.format_exc()[-5000:],
        )


def _update_interaction(session_id: str, interaction_id: str, **changes: Any) -> None:
    with _sessions_lock:
        session = _sessions[session_id]
        for item in session.setdefault("interactions", []):
            if item["id"] == interaction_id:
                item.update(changes)
                break
        session["updated_at"] = _now()
        _write_session(session)


def _reply_to_danmaku(session_id: str, interaction_id: str, question: str) -> None:
    try:
        with _sessions_lock:
            session = dict(_sessions[session_id])
            queue_item = dict(session["queue"][int(session.get("queue_index") or 0)])
        product = dict(queue_item["product"])
        prompt = (
            "你是电商直播数字人。只能依据当前商品事实回答，控制在60字以内；"
            "资料没有的信息要明确说暂未说明，不得编造。当前商品事实：" + _product_facts(product)
        )
        answer = generate_reply(question, None, prompt)
        _update_interaction(session_id, interaction_id, status="speaking", answer=answer)
        path = _session_path(session_id) / "interactions" / interaction_id
        path.mkdir(parents=True, exist_ok=True)
        with _GPU_LOCK:
            speech = synthesize_voice(
                answer,
                Path(session.get("voice_reference") or FIXED_VOICE),
                session.get("voice_prompt_text") or FIXED_VOICE_TEXT,
                path / "reply.wav",
            )
            portrait = Path(session.get("portrait_normalized") or session.get("portrait_path") or FIXED_HOST)
            motion = animate_portrait(portrait, speech, path, session.get("expression_mode", "normal"))
            avatar = render_avatar(motion, speech, path)
        plan = [{"stage": "answer", "action": "recommend", "emotion": "friendly", "text": answer}]
        video, timed = _compose_v1_scene(
            avatar,
            Path(queue_item["product_image"]),
            None,
            product,
            plan,
            path,
            session.get("scene_template", "general"),
            session.get("orientation", "landscape"),
        )
        _update_interaction(
            session_id,
            interaction_id,
            status="ready",
            answer=answer,
            video_path=str(video),
            script_plan=timed,
            duration=round(_audio_duration(video), 3),
        )
    except Exception as exc:
        traceback.print_exc()
        _update_interaction(
            session_id,
            interaction_id,
            status="failed",
            answer="这个问题暂时无法回答，请稍后再试。",
            error=f"{type(exc).__name__}: {exc}",
        )


def _save_image_data(image_data: str, destination: Path) -> None:
    if not image_data:
        shutil.copy2(SAMPLE_PRODUCT, destination)
        return
    encoded = image_data.split(",", 1)[-1]
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="单张商品图片不能超过 8 MB")
    upload = destination.with_suffix(".upload")
    upload.write_bytes(raw)
    try:
        with Image.open(upload) as image:
            image.convert("RGB").save(destination, "PNG", optimize=True)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="商品图片无法读取") from exc
    finally:
        upload.unlink(missing_ok=True)


def _next_ready_index(session: dict[str, Any], current: int) -> int | None:
    queue = session.get("queue", [])
    for offset in range(1, len(queue) + 1):
        candidate = (current + offset) % len(queue)
        if queue[candidate].get("render_status") == "ready":
            return candidate
    return None


def _scheduler_loop() -> None:
    while True:
        time.sleep(0.8)
        now = time.time()
        with _sessions_lock:
            for session in _sessions.values():
                deadline = session.get("item_deadline_epoch")
                if session.get("playback") != "playing" or not deadline or now < float(deadline):
                    continue
                current = int(session.get("queue_index") or 0)
                next_index = _next_ready_index(session, current)
                if next_index is None:
                    session["playback"] = "stopped"
                    session["message"] = "没有可播放的商品片段"
                elif next_index <= current:
                    session["loop_count"] = int(session.get("loop_count") or 0) + 1
                    if not session.get("auto_rotate", True):
                        session["playback"] = "stopped"
                        session["message"] = "商品队列播放完成"
                        session["item_deadline_epoch"] = None
                        _write_session(session)
                        continue
                if session.get("playback") == "playing" and next_index is not None:
                    session["queue_index"] = next_index
                    session["item_elapsed_seconds"] = 0
                    session["item_started_epoch"] = now
                    session["item_deadline_epoch"] = now + _item_duration(session["queue"][next_index])
                    session["sequence"] = int(session.get("sequence") or 0) + 1
                    session["message"] = f"自动切换到第 {next_index + 1} 件商品"
                session["updated_at"] = _now()
                _write_session(session)


_ensure_sample_product()
_ensure_script_library()
if os.environ.get("V1_DISABLE_AUTOSTART") != "1":
    _load_sessions()
    threading.Thread(target=_scheduler_loop, name="v1-queue-scheduler", daemon=True).start()


@router.get("/api/v1/health")
@router.get("/api/live/health")
def health() -> dict[str, Any]:
    checks = model_health()
    return {
        "ok": checks.get("ready", False) and FIXED_HOST.exists() and FIXED_VOICE.exists(),
        "version": "V1.0",
        "models": checks,
        "sessions": len(_sessions),
        "features": {
            "auto_script": True,
            "web_product_research": True,
            "duration_aware_script": True,
            "versioned_script_library": True,
            "versioned_video_library": True,
            "exact_video_reuse": True,
            "musetalk_realtime_avatar_cache": True,
            "musetalk_offline_fallback": True,
            "delivery_directed_voice_clone": True,
            "independent_browser_layers": ["presenter", "product", "subtitle", "danmaku"],
            "llm": f"apikey.fan/{LIVE_LLM_MODEL}" if LIVE_LLM_API_KEY else "not-configured",
            "product_queue": True,
            "action_library": list(ACTION_LIBRARY),
            "action_effects": {
                "point_product": "前端商品层突出显示",
                "size": "前端参数与字幕同步",
                "price": "前端价格强调",
                "discount": "前端优惠强调",
                "buy_now": "前端促单卡片",
                "welcome_recommend_idle": "LivePortrait 表情与头部动作",
            },
            "video_layout": VIDEO_LAYOUT,
            "scene_templates": list(SCENE_TEMPLATES),
            "orientations": ["landscape", "portrait"],
            "websocket": True,
            "webrtc": WEBRTC_AVAILABLE,
            "mp4_fallback": True,
        },
    }


@router.post("/api/v1/script/preview")
def preview_script(request: ScriptPreviewRequest) -> dict[str, Any]:
    plan, provider, research = _generate_plan(
        request.product,
        request.has_next,
        request.duration_seconds,
    )
    return {
        "provider": provider,
        "duration_seconds": max(15, min(int(request.duration_seconds or 300), 3600)),
        "script_chars": sum(len(item.get("text", "")) for item in plan),
        "segments": plan,
        "research": research,
    }


@router.get("/api/v1/scripts/{sku}/versions")
def script_versions(sku: str) -> dict[str, Any]:
    versions = _script_versions_for_sku(sku)
    return {"sku": sku, "count": len(versions), "versions": versions}


@router.get("/api/v1/videos/{sku}/versions")
def video_versions(sku: str) -> dict[str, Any]:
    versions = _video_versions_for_sku(sku)
    return {"sku": sku, "count": len(versions), "versions": versions}


@router.post("/api/v1/live/create")
@router.post("/api/live/create")
async def create_live(
    products_json: str = Form(""),
    products_file: UploadFile | None = File(None),
    scene_template: str = Form("general"),
    orientation: str = Form("landscape"),
    expression_mode: str = Form("normal"),
    reference_text: str = Form(""),
    auto_rotate: bool = Form(True),
    portrait_image: UploadFile | None = File(None),
    reference_audio: UploadFile | None = File(None),
    product_video: UploadFile | None = File(None),
) -> dict[str, Any]:
    products_payload = products_json
    if products_file and products_file.filename:
        products_bytes = await products_file.read()
        if len(products_bytes) > 32 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="商品队列数据不能超过 32 MB")
        try:
            products_payload = products_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=422, detail="商品队列文件必须为 UTF-8 JSON") from exc
    try:
        products = json.loads(products_payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="商品队列 JSON 无效") from exc
    if not isinstance(products, list) or not products or len(products) > 30:
        raise HTTPException(status_code=422, detail="商品队列需包含 1–30 件商品")
    scene_template = scene_template if scene_template in SCENE_TEMPLATES else "general"
    orientation = orientation if orientation in {"landscape", "portrait"} else "landscape"
    expression_mode = expression_mode if expression_mode in {"normal", "funny", "enhanced"} else "normal"

    session_id = uuid.uuid4().hex[:24]
    job_path = _session_path(session_id)
    job_path.mkdir(parents=True, exist_ok=False)
    portrait_path = FIXED_HOST
    if portrait_image and portrait_image.filename:
        content = await portrait_image.read()
        if len(content) > 12 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="主播照片不能超过 12 MB")
        portrait_path = job_path / "portrait_upload"
        portrait_path.write_bytes(content)
        try:
            with Image.open(portrait_path) as image:
                image.verify()
        except Exception as exc:
            raise HTTPException(status_code=422, detail="主播照片无法读取") from exc
    voice_path = FIXED_VOICE
    custom_voice = bool(reference_audio and reference_audio.filename)
    if custom_voice:
        content = await reference_audio.read()
        if len(content) > 30 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="声音参考不能超过 30 MB")
        voice_path = job_path / "voice_upload"
        voice_path.write_bytes(content)
    first_product_video: Path | None = None
    if product_video and product_video.filename:
        content = await product_video.read()
        if len(content) > 80 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="商品特写视频不能超过 80 MB")
        first_product_video = job_path / "product_video_upload.mp4"
        first_product_video.write_bytes(content)

    queue: list[dict[str, Any]] = []
    for index, raw in enumerate(products):
        if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
            raise HTTPException(status_code=422, detail=f"第 {index + 1} 件商品缺少名称")
        item_path = job_path / "items" / str(index)
        item_path.mkdir(parents=True, exist_ok=True)
        image_path = item_path / "product.png"
        try:
            _save_image_data(str(raw.get("image_data") or ""), image_path)
        except (ValueError, base64.binascii.Error) as exc:
            raise HTTPException(status_code=422, detail=f"第 {index + 1} 件商品图片数据无效") from exc
        product = {
            "sku": str(raw.get("sku") or "")[:80],
            "name": str(raw.get("name") or "").strip()[:180],
            "link": str(raw.get("link") or "")[:500],
            "original_price": str(raw.get("original_price") or "")[:40],
            "sale_price": str(raw.get("sale_price") or "")[:40],
            "selling_points": str(raw.get("selling_points") or "")[:700],
            "params": str(raw.get("params") or "")[:700],
            "promotion": str(raw.get("promotion") or "")[:300],
        }
        queue.append({
            "id": uuid.uuid4().hex[:16],
            "product": product,
            "duration_seconds": max(15, min(int(raw.get("duration_seconds") or 300), 3600)),
            "render_status": "queued",
            "message": "等待生成",
            "script": "",
            "script_plan": [],
            "product_image": str(image_path),
            "product_video_path": str(first_product_video) if index == 0 and first_product_video else "",
        })

    session = {
        "id": session_id,
        "version": "V1.0",
        "created_at": _now(),
        "updated_at": _now(),
        "render_status": "queued",
        "playback": "preparing",
        "progress": 0,
        "message": "V1 商品队列已进入生成任务",
        "queue": queue,
        "queue_index": 0,
        "sequence": 0,
        "loop_count": 0,
        "auto_rotate": bool(auto_rotate),
        "manual_pause": False,
        "scene_template": scene_template,
        "display_scene_template": scene_template,
        "orientation": orientation,
        "expression_mode": expression_mode,
        "portrait_path": str(portrait_path),
        "voice_path": str(voice_path),
        "custom_portrait": portrait_path != FIXED_HOST,
        "custom_voice": custom_voice,
        "cancel_requested": False,
        "reference_text": reference_text.strip(),
        "item_elapsed_seconds": 0,
        "item_started_epoch": None,
        "item_deadline_epoch": None,
        "interactions": [],
    }
    with _sessions_lock:
        for previous in _sessions.values():
            if previous.get("render_status") in {"queued", "rendering"}:
                previous["cancel_requested"] = True
                previous["render_status"] = "cancelled"
                previous["playback"] = "stopped"
                previous["message"] = "已被新的上传任务替代，正在释放生成资源"
                previous["updated_at"] = _now()
                _write_session(previous)
        _sessions[session_id] = session
        _write_session(session)
    _executor.submit(_render_v1_session, session_id)
    return _public_session(session)


@router.get("/api/v1/live/current")
@router.get("/api/live/current")
def current_live() -> dict[str, Any]:
    with _sessions_lock:
        if not _sessions:
            raise HTTPException(status_code=404, detail="当前没有 V1 直播 Session")
        session = sorted(_sessions.values(), key=lambda item: item.get("created_at", ""), reverse=True)[0]
        return _public_session(session)


@router.post("/api/v1/live/{session_id}/retry")
def retry_live(session_id: str) -> dict[str, Any]:
    with _sessions_lock:
        source = _sessions.get(session_id)
        if not source:
            raise HTTPException(status_code=404, detail="V1 直播 Session 不存在")
        source = json.loads(json.dumps(source, ensure_ascii=False))

    new_id = uuid.uuid4().hex[:24]
    job_path = _session_path(new_id)
    job_path.mkdir(parents=True, exist_ok=False)

    portrait_path = FIXED_HOST
    source_portrait = Path(source.get("portrait_path") or FIXED_HOST)
    if source.get("custom_portrait") and source_portrait.exists():
        portrait_path = job_path / "portrait_upload"
        shutil.copy2(source_portrait, portrait_path)

    voice_path = FIXED_VOICE
    source_voice = Path(source.get("voice_path") or FIXED_VOICE)
    if source.get("custom_voice") and source_voice.exists():
        voice_path = job_path / "voice_upload"
        shutil.copy2(source_voice, voice_path)

    queue: list[dict[str, Any]] = []
    for index, old_item in enumerate(source.get("queue") or []):
        item_path = job_path / "items" / str(index)
        item_path.mkdir(parents=True, exist_ok=True)
        image_path = item_path / "product.png"
        old_image = Path(old_item.get("product_image") or SAMPLE_PRODUCT)
        shutil.copy2(old_image if old_image.exists() else SAMPLE_PRODUCT, image_path)
        product_video_path = ""
        old_video = Path(old_item.get("product_video_path") or "")
        if old_item.get("product_video_path") and old_video.exists():
            copied_video = item_path / "product_video_upload.mp4"
            shutil.copy2(old_video, copied_video)
            product_video_path = str(copied_video)
        queue.append({
            "id": uuid.uuid4().hex[:16],
            "product": dict(old_item.get("product") or {}),
            "duration_seconds": _item_duration(old_item),
            "render_status": "queued",
            "message": "等待生成",
            "script": "",
            "script_plan": [],
            "product_image": str(image_path),
            "product_video_path": product_video_path,
        })
    if not queue:
        raise HTTPException(status_code=422, detail="原直播会话没有可重试的商品")

    retried = {
        "id": new_id,
        "version": "V1.0",
        "created_at": _now(),
        "updated_at": _now(),
        "render_status": "queued",
        "playback": "preparing",
        "progress": 0,
        "message": "正在重新启动最新的数字人生成任务",
        "queue": queue,
        "queue_index": 0,
        "sequence": 0,
        "loop_count": 0,
        "auto_rotate": bool(source.get("auto_rotate", True)),
        "manual_pause": False,
        "scene_template": source.get("scene_template", "general"),
        "display_scene_template": source.get("display_scene_template", source.get("scene_template", "general")),
        "orientation": source.get("orientation", "landscape"),
        "expression_mode": source.get("expression_mode", "normal"),
        "portrait_path": str(portrait_path),
        "voice_path": str(voice_path),
        "custom_portrait": portrait_path != FIXED_HOST,
        "custom_voice": voice_path != FIXED_VOICE,
        "cancel_requested": False,
        "reference_text": source.get("reference_text", ""),
        "item_elapsed_seconds": 0,
        "item_started_epoch": None,
        "item_deadline_epoch": None,
        "interactions": [],
    }
    with _sessions_lock:
        for previous in _sessions.values():
            if previous.get("render_status") in {"queued", "rendering"}:
                previous["cancel_requested"] = True
                previous["render_status"] = "cancelled"
                previous["playback"] = "stopped"
                previous["message"] = "已被重新生成任务替代"
                previous["updated_at"] = _now()
                _write_session(previous)
        _sessions[new_id] = retried
        _write_session(retried)
    _executor.submit(_render_v1_session, new_id)
    return _public_session(retried)


def _clone_live_with_queue(
    source: dict[str, Any],
    source_items: list[dict[str, Any]],
    message: str,
    acceptance_sample: bool = False,
) -> dict[str, Any]:
    """Create a safe replacement session for a changed mall product list."""
    new_id = uuid.uuid4().hex[:24]
    job_path = _session_path(new_id)
    job_path.mkdir(parents=True, exist_ok=False)

    portrait_path = FIXED_HOST
    source_portrait = Path(source.get("portrait_path") or FIXED_HOST)
    if source.get("custom_portrait") and source_portrait.exists():
        portrait_path = job_path / "portrait_upload"
        shutil.copy2(source_portrait, portrait_path)

    voice_path = FIXED_VOICE
    source_voice = Path(source.get("voice_path") or FIXED_VOICE)
    if source.get("custom_voice") and source_voice.exists():
        voice_path = job_path / "voice_upload"
        shutil.copy2(source_voice, voice_path)

    queue: list[dict[str, Any]] = []
    for index, old_item in enumerate(source_items):
        item_path = job_path / "items" / str(index)
        item_path.mkdir(parents=True, exist_ok=True)
        image_path = item_path / "product.png"
        old_image = Path(old_item.get("product_image") or SAMPLE_PRODUCT)
        shutil.copy2(old_image if old_image.exists() else SAMPLE_PRODUCT, image_path)
        product_video_path = ""
        old_video = Path(old_item.get("product_video_path") or "")
        if old_item.get("product_video_path") and old_video.exists():
            copied_video = item_path / "product_video_upload.mp4"
            shutil.copy2(old_video, copied_video)
            product_video_path = str(copied_video)
        queue.append({
            "id": uuid.uuid4().hex[:16],
            "product": dict(old_item.get("product") or {}),
            "duration_seconds": _item_duration(old_item),
            "render_status": "queued",
            "message": "等待生成",
            "script": "",
            "script_plan": [],
            "product_image": str(image_path),
            "product_video_path": product_video_path,
        })
    if not queue:
        raise HTTPException(status_code=422, detail="同步后的商品队列不能为空")

    replacement = {
        "id": new_id,
        "version": "V1.0",
        "created_at": _now(),
        "updated_at": _now(),
        "render_status": "queued",
        "playback": "preparing",
        "progress": 0,
        "message": message,
        "queue": queue,
        "queue_index": 0,
        "sequence": 0,
        "loop_count": 0,
        "auto_rotate": bool(source.get("auto_rotate", True)),
        "manual_pause": False,
        "scene_template": source.get("scene_template", "general"),
        "display_scene_template": source.get("display_scene_template", source.get("scene_template", "general")),
        "orientation": source.get("orientation", "landscape"),
        "expression_mode": source.get("expression_mode", "normal"),
        "portrait_path": str(portrait_path),
        "voice_path": str(voice_path),
        "custom_portrait": portrait_path != FIXED_HOST,
        "custom_voice": voice_path != FIXED_VOICE,
        "cancel_requested": False,
        "reference_text": source.get("reference_text", ""),
        "item_elapsed_seconds": 0,
        "item_started_epoch": None,
        "item_deadline_epoch": None,
        "interactions": [],
        "acceptance_sample": bool(acceptance_sample),
    }
    with _sessions_lock:
        for previous in _sessions.values():
            if previous.get("render_status") in {"queued", "rendering"}:
                previous["cancel_requested"] = True
                previous["render_status"] = "cancelled"
                previous["playback"] = "stopped"
                previous["message"] = "商城商品列表已变化，旧生成队列已停止"
                previous["updated_at"] = _now()
                _write_session(previous)
        _sessions[new_id] = replacement
        _write_session(replacement)
    _executor.submit(_render_v1_session, new_id)
    return _public_session(replacement)


@router.post("/api/v1/live/{session_id}/acceptance-sample")
@router.post("/api/live/{session_id}/acceptance-sample")
def create_acceptance_sample(session_id: str) -> dict[str, Any]:
    """Regenerate only the first product before committing GPU time to a queue."""
    with _sessions_lock:
        source = _sessions.get(session_id)
        if not source:
            raise HTTPException(status_code=404, detail="V1 直播 Session 不存在")
        source = json.loads(json.dumps(source, ensure_ascii=False))
    queue = list(source.get("queue") or [])
    if not queue:
        raise HTTPException(status_code=422, detail="当前直播没有可生成的商品")
    return _clone_live_with_queue(
        source,
        [queue[0]],
        "正在生成第 1 件商品的在线模式与直播语气验收样片",
        acceptance_sample=True,
    )


@router.get("/api/v1/live/{session_id}")
@router.get("/api/live/{session_id}")
def get_live(session_id: str) -> dict[str, Any]:
    _session_path(session_id)
    with _sessions_lock:
        session = _sessions.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="V1 直播 Session 不存在")
        return _public_session(session)


def control_live(session_id: str, action: str) -> dict[str, Any]:
    if action not in {"start", "pause", "resume", "stop"}:
        raise HTTPException(status_code=404, detail="未知直播控制操作")
    with _sessions_lock:
        session = _sessions.get(session_id)
        if not session or session.get("render_status") != "ready":
            raise HTTPException(status_code=409, detail="V1 直播画面尚未生成完成")
        index = int(session.get("queue_index") or 0)
        if session["queue"][index].get("render_status") != "ready":
            ready = next((i for i, item in enumerate(session["queue"]) if item.get("render_status") == "ready"), None)
            if ready is None:
                raise HTTPException(status_code=409, detail="商品队列暂无可播放片段")
            index = ready
            session["queue_index"] = ready
        now = time.time()
        if action in {"start", "resume"}:
            session["manual_pause"] = False
            elapsed = float(session.get("item_elapsed_seconds") or 0)
            if action == "start":
                elapsed = 0
            session["item_elapsed_seconds"] = elapsed
            session["item_started_epoch"] = now
            session["item_deadline_epoch"] = now + max(1, _item_duration(session["queue"][index]) - elapsed)
            session["playback"] = "playing"
        elif action == "pause":
            session["manual_pause"] = True
            started = session.get("item_started_epoch")
            if started:
                session["item_elapsed_seconds"] = min(
                    _item_duration(session["queue"][index]),
                    float(session.get("item_elapsed_seconds") or 0) + max(0, now - float(started)),
                )
            session["item_started_epoch"] = None
            session["item_deadline_epoch"] = None
            session["playback"] = "paused"
        else:
            session["manual_pause"] = True
            session["item_elapsed_seconds"] = 0
            session["item_started_epoch"] = None
            session["item_deadline_epoch"] = None
            session["playback"] = "stopped"
        session["message"] = {"start": "V1 直播已开始", "pause": "V1 直播已暂停", "resume": "V1 直播已恢复", "stop": "V1 直播已停止"}[action]
        session["updated_at"] = _now()
        _write_session(session)
        return _public_session(session)


@router.post("/api/v1/live/{session_id}/product")
@router.post("/api/live/{session_id}/product")
def switch_product(session_id: str, request: ProductSwitchRequest) -> dict[str, Any]:
    with _sessions_lock:
        session = _sessions.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="V1 直播 Session 不存在")
        if request.index < 0 or request.index >= len(session["queue"]):
            raise HTTPException(status_code=422, detail="商品序号超出队列范围")
        if session["queue"][request.index].get("render_status") != "ready":
            raise HTTPException(status_code=409, detail="该商品直播片段仍在生成")
        session["queue_index"] = request.index
        session["sequence"] = int(session.get("sequence") or 0) + 1
        session["item_elapsed_seconds"] = 0
        if session.get("playback") == "playing":
            now = time.time()
            session["item_started_epoch"] = now
            session["item_deadline_epoch"] = now + _item_duration(session["queue"][request.index])
        session["message"] = f"已切换到第 {request.index + 1} 件商品"
        session["updated_at"] = _now()
        _write_session(session)
        return _public_session(session)


@router.post("/api/v1/live/{session_id}/queue/order")
@router.post("/api/live/{session_id}/queue/order")
def reorder_queue(session_id: str, request: QueueOrderRequest) -> dict[str, Any]:
    """Make the mall's visible list the single source of truth."""
    requested_skus: list[str] = []
    seen_skus: set[str] = set()
    for raw_sku in request.skus:
        sku = str(raw_sku or "").strip()
        if sku and sku not in seen_skus:
            requested_skus.append(sku)
            seen_skus.add(sku)
    if not requested_skus:
        raise HTTPException(status_code=422, detail="商品顺序不能为空")

    with _sessions_lock:
        live_session = _sessions.get(session_id)
        if not live_session:
            raise HTTPException(status_code=404, detail="V1 直播 Session 不存在")
        old_queue = list(live_session.get("queue") or [])
        if not old_queue:
            raise HTTPException(status_code=409, detail="当前直播没有商品队列")
        actual_skus = [str((item.get("product") or {}).get("sku") or "") for item in old_queue]
        if actual_skus == requested_skus:
            return _public_session(live_session)

        buckets: dict[str, list[dict[str, Any]]] = {}
        for item in old_queue:
            sku = str((item.get("product") or {}).get("sku") or "")
            buckets.setdefault(sku, []).append(item)
        missing = [sku for sku in requested_skus if sku not in buckets]
        if missing:
            raise HTTPException(
                status_code=409,
                detail="商城列表包含当前生成队列中没有的商品，请重新生成商品队列",
            )
        selected_items = [buckets[sku].pop(0) for sku in requested_skus]
        has_active_worker = any(
            item.get("render_status") in {"queued", "rendering"}
            for item in old_queue
        )
        source = json.loads(json.dumps(live_session, ensure_ascii=False))

        if not has_active_worker:
            old_index = max(0, min(int(live_session.get("queue_index") or 0), len(old_queue) - 1))
            active_id = str(old_queue[old_index].get("id") or "")
            live_session["queue"] = selected_items
            live_session["queue_index"] = next(
                (index for index, item in enumerate(selected_items) if str(item.get("id") or "") == active_id),
                0,
            )
            live_session["sequence"] = int(live_session.get("sequence") or 0) + 1
            live_session["message"] = f"已同步商城右侧商品列表，共 {len(selected_items)} 件"
            live_session["updated_at"] = _now()
            _write_session(live_session)
            return _public_session(live_session)

    # Never mutate indexes beneath an active MuseTalk worker. Clone only the
    # selected products into a fresh session and cancel the obsolete queue.
    source_buckets: dict[str, list[dict[str, Any]]] = {}
    for item in source.get("queue") or []:
        sku = str((item.get("product") or {}).get("sku") or "")
        source_buckets.setdefault(sku, []).append(item)
    source_items = [source_buckets[sku].pop(0) for sku in requested_skus]
    return _clone_live_with_queue(
        source,
        source_items,
        f"已按商城右侧商品列表重建队列，共 {len(source_items)} 件",
    )


@router.post("/api/v1/live/{session_id}/danmaku")
@router.post("/api/live/{session_id}/danmaku")
def ask_danmaku(session_id: str, request: DanmakuRequest) -> dict[str, Any]:
    question = request.text.strip()
    if not question or len(question) > 120:
        raise HTTPException(status_code=422, detail="弹幕问题需为 1–120 个字符")
    with _sessions_lock:
        session = _sessions.get(session_id)
        if not session or session.get("render_status") != "ready":
            raise HTTPException(status_code=409, detail="V1 直播尚未准备完成")
        pending = sum(item.get("status") in {"queued", "thinking", "speaking"} for item in session.get("interactions", []))
        if pending >= 3:
            raise HTTPException(status_code=429, detail="数字人正在回答其他观众，请稍后再发")
        interaction = {
            "id": uuid.uuid4().hex[:16],
            "user": request.user.strip()[:20] or "观众",
            "question": question,
            "answer": "",
            "status": "queued",
            "type": "danmaku",
            "created_at": _now(),
        }
        session.setdefault("interactions", []).append(interaction)
        session["interactions"] = session["interactions"][-30:]
        session["updated_at"] = _now()
        _write_session(session)
    _executor.submit(_reply_to_danmaku, session_id, interaction["id"], question)
    return interaction


@router.post("/api/v1/live/{session_id}/start")
@router.post("/api/live/{session_id}/start")
def start_live(session_id: str) -> dict[str, Any]:
    return control_live(session_id, "start")


@router.post("/api/v1/live/{session_id}/pause")
@router.post("/api/live/{session_id}/pause")
def pause_live(session_id: str) -> dict[str, Any]:
    return control_live(session_id, "pause")


@router.post("/api/v1/live/{session_id}/resume")
@router.post("/api/live/{session_id}/resume")
def resume_live(session_id: str) -> dict[str, Any]:
    return control_live(session_id, "resume")


@router.post("/api/v1/live/{session_id}/stop")
@router.post("/api/live/{session_id}/stop")
def stop_live(session_id: str) -> dict[str, Any]:
    return control_live(session_id, "stop")


@router.post("/api/v1/live/{session_id}/scene")
@router.post("/api/live/{session_id}/scene")
def switch_scene(session_id: str, request: SceneSwitchRequest) -> dict[str, Any]:
    scene_template = str(request.scene_template or "").strip()
    if scene_template not in SCENE_TEMPLATES:
        raise HTTPException(status_code=422, detail="未知直播场景")
    with _sessions_lock:
        session = _sessions.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="V1 直播 Session 不存在")
        session["display_scene_template"] = scene_template
        session["message"] = f"已切换到{SCENE_TEMPLATES[scene_template]['label']}"
        session["updated_at"] = _now()
        _write_session(session)
        return _public_session(session)


@router.get("/api/v1/live/{session_id}/items/{index}/video", include_in_schema=False)
def item_video(session_id: str, index: int) -> FileResponse:
    with _sessions_lock:
        session = _sessions.get(session_id)
        if not session or index < 0 or index >= len(session["queue"]):
            raise HTTPException(status_code=404, detail="商品直播片段不存在")
        item = session["queue"][index]
        if item.get("render_status") != "ready" or not item.get("video_path"):
            raise HTTPException(status_code=404, detail="商品直播片段尚未就绪")
        path = Path(item["video_path"])
    return FileResponse(path, media_type="video/mp4", filename=f"v1-{session_id}-{index}.mp4")


@router.get("/api/v1/live/{session_id}/interactions/{interaction_id}/video", include_in_schema=False)
def interaction_video(session_id: str, interaction_id: str) -> FileResponse:
    with _sessions_lock:
        session = _sessions.get(session_id)
        item = next((entry for entry in (session or {}).get("interactions", []) if entry["id"] == interaction_id), None)
        if not item or item.get("status") != "ready" or not item.get("video_path"):
            raise HTTPException(status_code=404, detail="数字人回答视频尚未就绪")
        path = Path(item["video_path"])
    return FileResponse(path, media_type="video/mp4", filename=f"v1-reply-{interaction_id}.mp4")


@router.post("/api/v1/live/{session_id}/webrtc/offer")
@router.post("/api/live/{session_id}/webrtc/offer")
async def webrtc_offer(session_id: str, request: RTCOfferRequest) -> dict[str, str]:
    if not WEBRTC_AVAILABLE:
        raise HTTPException(status_code=503, detail="WebRTC 组件尚未安装，播放器将使用 MP4 回退")
    with _sessions_lock:
        session = _sessions.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="V1 直播 Session 不存在")
        index = int(session.get("queue_index") or 0)
        item = session["queue"][index]
        if item.get("render_status") != "ready" or not item.get("video_path"):
            raise HTTPException(status_code=409, detail="当前商品直播片段尚未就绪")
        video_path = str(item["video_path"])
    pc = RTCPeerConnection()
    _peer_connections.add(pc)
    player = MediaPlayer(video_path, loop=True)
    if player.audio:
        pc.addTrack(player.audio)
    if player.video:
        pc.addTrack(player.video)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange() -> None:
        if pc.connectionState in {"failed", "closed", "disconnected"}:
            await pc.close()
            _peer_connections.discard(pc)

    await pc.setRemoteDescription(RTCSessionDescription(sdp=request.sdp, type=request.type))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    while pc.iceGatheringState != "complete":
        await asyncio.sleep(0.05)
    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}


@router.websocket("/ws/v1/live/{session_id}")
async def live_status_socket(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    try:
        while True:
            with _sessions_lock:
                session = _sessions.get(session_id)
                payload = _public_session(session) if session else {"error": "not_found"}
            await websocket.send_json(payload)
            if not session:
                break
            await asyncio.sleep(0.8)
    except WebSocketDisconnect:
        return
