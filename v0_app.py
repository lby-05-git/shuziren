from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import gradio as gr
import requests
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from PIL import Image, ImageDraw
from pydantic import BaseModel

from app import demo
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
)
from v1_api import router as v1_router


V0_DIR = PROJECT_DIR / "outputs" / "v0_sessions"
STATIC_DIR = PROJECT_DIR / "v0_static"
V1_STATIC_DIR = PROJECT_DIR / "v1_static"
ASSET_DIR = PROJECT_DIR / "assets" / "v0"
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
FONT_PATH = Path(
    os.environ.get("V0_FONT", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
)
SAMPLE_PRODUCT = ASSET_DIR / "sample_product.png"
LIVE_LLM_BASE_URL = os.environ.get(
    "LIVE_LLM_BASE_URL", "https://api.apikey.fan/v1"
).rstrip("/")
LIVE_LLM_MODEL = os.environ.get("LIVE_LLM_MODEL", "deepseek-v4.1-flash").strip()
LIVE_LLM_API_KEY = os.environ.get("LIVE_LLM_API_KEY", "").strip()

DEFAULT_SCRIPT = (
    "大家好，欢迎来到直播间。今天给大家介绍的是这款一万毫安时轻薄充电宝。"
    "它支持二十二点五瓦快充，也支持 Type-C 双向快充，日常通勤和出差携带都很方便。"
    "原价九十九元，今天直播活动价只要七十九元。需要的朋友可以重点关注一下。"
)

_sessions: dict[str, dict[str, Any]] = {}
_sessions_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="v0-render")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _session_path(session_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{24}", session_id):
        raise HTTPException(status_code=404, detail="直播 Session 不存在")
    return V0_DIR / session_id


def _public_session(session: dict[str, Any]) -> dict[str, Any]:
    result = dict(session)
    session_id = result["id"]
    for private_key in (
        "product_image",
        "video_path",
        "portrait_path",
        "portrait_normalized",
        "voice_path",
        "voice_reference",
        "voice_prompt_text",
        "active_product_image",
        "error",
    ):
        result.pop(private_key, None)
    if result.get("render_status") == "ready":
        result["video_url"] = f"/api/v0/live/{session_id}/video"
        result["embed_url"] = f"/digital-human/embed?session={session_id}"
    public_interactions = []
    for item in result.get("interactions", []):
        public_item = {
            key: value
            for key, value in item.items()
            if key not in {"video_path", "product_image", "error"}
        }
        if item.get("status") == "ready" and item.get("video_path"):
            public_item["video_url"] = (
                f"/api/v0/live/{session_id}/interactions/{item['id']}/video"
            )
        public_interactions.append(public_item)
    result["interactions"] = public_interactions
    return result


def _write_session(session: dict[str, Any]) -> None:
    target = _session_path(session["id"]) / "session.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def _update_session(session_id: str, **changes: Any) -> dict[str, Any]:
    with _sessions_lock:
        session = _sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        session.update(changes)
        session["updated_at"] = _now()
        _write_session(session)
        return dict(session)


def _load_sessions() -> None:
    V0_DIR.mkdir(parents=True, exist_ok=True)
    for state_file in V0_DIR.glob("*/session.json"):
        try:
            session = json.loads(state_file.read_text(encoding="utf-8"))
            if session.get("render_status") in {"queued", "rendering"}:
                session["render_status"] = "failed"
                session["message"] = "服务器曾在生成过程中停止，请重新创建直播。"
            _sessions[session["id"]] = session
        except Exception:
            continue


def _ensure_sample_product() -> None:
    if SAMPLE_PRODUCT.exists():
        return
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (900, 700), "#f8fafc")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((225, 100, 675, 600), radius=65, fill="#111827", outline="#38bdf8", width=10)
    draw.rounded_rectangle((285, 155, 615, 545), radius=42, fill="#e2e8f0")
    draw.ellipse((350, 235, 550, 435), fill="#0f172a", outline="#38bdf8", width=12)
    draw.arc((385, 270, 515, 400), 35, 325, fill="#f8fafc", width=18)
    draw.rounded_rectangle((405, 425, 495, 510), radius=18, fill="#38bdf8")
    image.save(SAMPLE_PRODUCT, optimize=True)


def _srt_timestamp(seconds: float) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _wrap_subtitle(text: str, width: int = 20) -> str:
    text = text.strip()
    if len(text) <= width:
        return text
    return "\n".join(text[index : index + width] for index in range(0, len(text), width))


def _write_subtitles(script: str, duration: float, destination: Path) -> None:
    segments = [item.strip() for item in re.split(r"(?<=[。！？!?；;])", script) if item.strip()]
    if not segments:
        segments = [script.strip()]
    total_weight = sum(max(len(item), 1) for item in segments)
    cursor = 0.0
    blocks: list[str] = []
    for index, segment in enumerate(segments, start=1):
        segment_duration = duration * max(len(segment), 1) / total_weight
        end = duration if index == len(segments) else cursor + segment_duration
        blocks.append(
            f"{index}\n{_srt_timestamp(cursor)} --> {_srt_timestamp(end)}\n"
            f"{_wrap_subtitle(segment)}\n"
        )
        cursor = end
    destination.write_text("\n".join(blocks), encoding="utf-8")


def _compose_scene(
    avatar_video: Path,
    _product_image: Path,
    _product: dict[str, str],
    script: str,
    job_path: Path,
) -> Path:
    if not FONT_PATH.exists():
        raise RuntimeError(f"缺少中文字体：{FONT_PATH}")
    duration = _audio_duration(avatar_video)
    subtitle_path = job_path / "subtitles.srt"
    _write_subtitles(script, duration, subtitle_path)

    badge_path = job_path / "badge.txt"
    badge_path.write_text("AI 数字人", encoding="utf-8")

    font = str(FONT_PATH)
    filters = (
        "[0:v]split=2[background_source][portrait_source];"
        "[background_source]scale=1280:720:force_original_aspect_ratio=increase,"
        "crop=1280:720,boxblur=20:2,eq=brightness=-0.22[background];"
        "[portrait_source]scale=720:720:force_original_aspect_ratio=decrease,"
        "pad=720:720:(ow-iw)/2:(oh-ih)/2:color=0x08111f,"
        f"subtitles={subtitle_path}:force_style='FontName=Noto Sans CJK SC,"
        "FontSize=15,PrimaryColour=&H00FFFFFF,OutlineColour=&H90000000,"
        "BorderStyle=1,Outline=2,Shadow=0,MarginV=24,MarginL=20,MarginR=20,Alignment=2'[portrait];"
        "[background][portrait]overlay=(W-w)/2:0[stage];"
        "[stage]drawbox=x=25:y=22:w=172:h=49:color=0x2563eb@0.94:t=fill,"
        f"drawtext=fontfile={font}:textfile={badge_path}:"
        "fontcolor=white:fontsize=24:x=43:y=31[outv]"
    )
    output_path = job_path / "live_program.mp4"
    _run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(avatar_video),
            "-filter_complex",
            filters,
            "-map",
            "[outv]",
            "-map",
            "0:a:0?",
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        timeout=600,
    )
    if not output_path.exists() or output_path.stat().st_size < 10_000:
        raise RuntimeError("场景合成没有生成有效直播视频。")
    return output_path


def _render_session(session_id: str) -> None:
    try:
        session = _sessions[session_id]
        job_path = _session_path(session_id)

        def status(message: str) -> None:
            _update_session(session_id, message=message)

        _update_session(session_id, render_status="rendering", playback="preparing", progress=5)
        product = session["product"]
        product_image = Path(session["product_image"])
        script = session["script"]

        with _GPU_LOCK:
            portrait_source = Path(session.get("portrait_path") or FIXED_HOST)
            voice_source = Path(session.get("voice_path") or FIXED_VOICE)
            if session.get("custom_voice"):
                status("正在建立自定义主播音色…")
                voice_reference, voice_prompt_text = prepare_voice_profile(
                    voice_source,
                    session.get("reference_text") or None,
                    status,
                )
            else:
                voice_reference, voice_prompt_text = FIXED_VOICE, FIXED_VOICE_TEXT
            status("正在用主播音色合成直播语音…")
            speech = synthesize_voice(
                script,
                voice_reference,
                voice_prompt_text,
                job_path / "speech.wav",
                status,
            )
            _update_session(session_id, progress=35)

            portrait = normalize_portrait(portrait_source, job_path / "host.png")
            motion = animate_portrait(
                portrait,
                speech,
                job_path,
                session.get("expression_mode", "normal"),
                status,
            )
            _update_session(session_id, progress=60)

            avatar = render_avatar(motion, speech, job_path, status)
            _update_session(session_id, progress=82)

        status("正在合成商品、价格、字幕和 AI 标识…")
        final_video = _compose_scene(avatar, product_image, product, script, job_path)
        _update_session(
            session_id,
            render_status="ready",
            playback="stopped",
            progress=100,
            message="直播画面已准备完成，可以开始直播。",
            video_path=str(final_video),
            video_layout="avatar_only",
            portrait_normalized=str(portrait),
            voice_reference=str(voice_reference),
            voice_prompt_text=voice_prompt_text,
            duration=round(_audio_duration(final_video), 3),
        )
    except Exception as exc:
        traceback.print_exc()
        _update_session(
            session_id,
            render_status="failed",
            playback="stopped",
            message=f"生成失败：{type(exc).__name__}: {exc}",
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


def _product_facts(product: dict[str, str]) -> str:
    return (
        f"商品名称：{product['name']}；原价：{product['original_price']}元；"
        f"直播价：{product['sale_price']}元；卖点：{product.get('selling_points') or '未提供'}；"
        f"参数：{product.get('params') or '未提供'}；优惠：{product.get('promotion') or '未提供'}。"
    )


def _generate_live_text(user_text: str, system_prompt: str, temperature: float) -> tuple[str, str]:
    """优先使用外部 OpenAI 兼容接口；失败时降级到服务器本地 Qwen。"""
    if LIVE_LLM_API_KEY:
        try:
            response = requests.post(
                f"{LIVE_LLM_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {LIVE_LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": LIVE_LLM_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_text},
                    ],
                    "temperature": temperature,
                    "max_tokens": 800,
                    "thinking": {"type": "disabled"},
                    "stream": False,
                },
                timeout=(10, 90),
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            answer = re.sub(r"\s+", " ", str(content)).strip().strip('"')
            if answer:
                return answer, f"apikey.fan/{LIVE_LLM_MODEL}"
        except Exception as exc:
            print(f"外部直播 LLM 调用失败，改用本地 LLM：{type(exc).__name__}: {str(exc)[:180]}")
    with _GPU_LOCK:
        answer = generate_reply(user_text, [], system_prompt, temperature)
    return answer, "local/qwen2.5-1.5b"


def _render_interaction_video(
    session_id: str,
    interaction_id: str,
    answer: str,
    session: dict[str, Any],
    product: dict[str, str],
    product_image: Path,
) -> Path:
    interaction_path = _session_path(session_id) / "interactions" / interaction_id
    interaction_path.mkdir(parents=True, exist_ok=True)
    with _GPU_LOCK:
        voice_reference = Path(session.get("voice_reference") or FIXED_VOICE)
        voice_prompt_text = session.get("voice_prompt_text") or FIXED_VOICE_TEXT
        portrait_reference = Path(
            session.get("portrait_normalized")
            or session.get("portrait_path")
            or FIXED_HOST
        )
        speech = synthesize_voice(
            answer,
            voice_reference,
            voice_prompt_text,
            interaction_path / "reply.wav",
        )
        motion = animate_portrait(
            portrait_reference,
            speech,
            interaction_path,
            session.get("expression_mode", "normal"),
        )
        avatar = render_avatar(motion, speech, interaction_path)
    return _compose_scene(avatar, product_image, product, answer, interaction_path)


def _reply_to_danmaku(session_id: str, interaction_id: str, question: str) -> None:
    try:
        with _sessions_lock:
            session = dict(_sessions[session_id])
            product = dict(session.get("active_product") or session["product"])
            product_image = Path(
                session.get("active_product_image") or session["product_image"]
            )
        interaction_path = _session_path(session_id) / "interactions" / interaction_id
        interaction_path.mkdir(parents=True, exist_ok=True)
        _update_interaction(session_id, interaction_id, status="thinking")
        facts = _product_facts(product)
        prompt = (
            "你是电商直播间的数字人主播。请用自然、热情、完整的中文口语回答观众，控制在60字以内且不少于15个汉字。"
            "你只能依据给出的当前商品资料回答；资料中没有的信息必须明确说暂未说明，不能编造。"
            "资料没有时请完整回答：当前商品资料没有说明这一点，建议以详情页或客服确认为准。"
            "不要使用Markdown。当前商品资料：" + facts
        )
        answer, provider = _generate_live_text(question, prompt, 0.35)
        _update_interaction(
            session_id, interaction_id, status="speaking", answer=answer, llm_provider=provider
        )
        final_video = _render_interaction_video(
            session_id, interaction_id, answer, session, product, product_image
        )
        _update_interaction(
            session_id,
            interaction_id,
            status="ready",
            answer=answer,
            video_path=str(final_video),
            video_layout="avatar_only",
            duration=round(_audio_duration(final_video), 3),
        )
    except Exception as exc:
        traceback.print_exc()
        _update_interaction(
            session_id,
            interaction_id,
            status="failed",
            answer="这个问题我暂时无法回答，请稍后再试。",
            error=f"{type(exc).__name__}: {exc}",
        )


def _comment_on_product(session_id: str, interaction_id: str) -> None:
    try:
        with _sessions_lock:
            session = dict(_sessions[session_id])
            interaction = next(
                item for item in session.get("interactions", []) if item["id"] == interaction_id
            )
            product = dict(interaction["product"])
            product_image = Path(interaction["product_image"])
        _update_interaction(session_id, interaction_id, status="thinking")
        prompt = (
            "你是专业、可信的电商数字人主播。请根据商品资料生成一段自然连贯的中文口播，"
            "控制在60到110个汉字，先说商品名，再突出两到三个卖点、直播价和优惠，最后用一句温和的购买引导收尾。"
            "只能使用资料中明确提供的信息，不得虚构库存、销量、赠品、功效、质量承诺或最低价保证。"
            "不要使用Markdown、标题、括号或项目符号。商品资料：" + _product_facts(product)
        )
        answer, provider = _generate_live_text("请开始讲解当前商品。", prompt, 0.55)
        _update_interaction(
            session_id, interaction_id, status="speaking", answer=answer, llm_provider=provider
        )
        final_video = _render_interaction_video(
            session_id, interaction_id, answer, session, product, product_image
        )
        _update_interaction(
            session_id,
            interaction_id,
            status="ready",
            answer=answer,
            video_path=str(final_video),
            video_layout="avatar_only",
            duration=round(_audio_duration(final_video), 3),
        )
    except Exception as exc:
        traceback.print_exc()
        _update_interaction(
            session_id,
            interaction_id,
            status="failed",
            answer="当前商品讲解生成失败，请稍后再试。",
            error=f"{type(exc).__name__}: {exc}",
        )


_ensure_sample_product()
_load_sessions()

api = FastAPI(title="电商直播数字人 V0", version="0.1.0")
api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
api.include_router(v1_router)


class DanmakuRequest(BaseModel):
    text: str
    user: str = "观众"


class ProductCommentaryRequest(BaseModel):
    sku: str = ""
    name: str
    original_price: str = ""
    sale_price: str
    selling_points: str = ""
    params: str = ""
    promotion: str = ""
    image_data: str = ""


@api.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/digital-human")


@api.get("/digital-human", include_in_schema=False)
def digital_human_page() -> RedirectResponse:
    mall_live_url = os.environ.get("EGET_MALL_URL", "").strip()
    return RedirectResponse(mall_live_url or "/digital-human/#/live")


@api.get("/digital-human/", response_class=HTMLResponse, include_in_schema=False)
def digital_human_v1_page() -> HTMLResponse:
    return HTMLResponse((V1_STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@api.get("/digital-human/app.js", include_in_schema=False)
def digital_human_v1_script() -> FileResponse:
    return FileResponse(V1_STATIC_DIR / "app.js", media_type="text/javascript")


@api.get("/digital-human/style.css", include_in_schema=False)
def digital_human_v1_style() -> FileResponse:
    return FileResponse(V1_STATIC_DIR / "style.css", media_type="text/css")


@api.get("/digital-human/products.json", include_in_schema=False)
def digital_human_v1_products() -> FileResponse:
    return FileResponse(V1_STATIC_DIR / "products.json", media_type="application/json")


@api.get("/digital-human/images/{_image_name}", include_in_schema=False)
def digital_human_product_image(_image_name: str) -> FileResponse:
    image_path = V1_STATIC_DIR / "images" / Path(_image_name).name
    if image_path.is_file() and image_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        return FileResponse(image_path)
    return FileResponse(SAMPLE_PRODUCT, media_type="image/png")


@api.get("/api/digital-human/config", include_in_schema=False)
def digital_human_config(request: Request) -> dict[str, str]:
    origin = str(request.base_url).rstrip("/")
    websocket = ("wss://" if request.url.scheme == "https" else "ws://") + request.url.netloc
    return {"api_base": "", "public_base": origin, "websocket_base": websocket, "version": "V1.0"}


@api.get("/digital-human/studio", response_class=HTMLResponse, include_in_schema=False)
def digital_human_studio() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@api.get("/digital-human/embed", response_class=HTMLResponse, include_in_schema=False)
def digital_human_embed() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "embed.html").read_text(encoding="utf-8"))


@api.get("/api/v0/health")
def health() -> dict[str, Any]:
    checks = model_health()
    return {
        "ok": checks["ready"] and FIXED_HOST.exists() and FIXED_VOICE.exists() and FONT_PATH.exists(),
        "version": "V0",
        "models": checks,
        "fixed_host": FIXED_HOST.exists(),
        "fixed_voice": FIXED_VOICE.exists(),
        "font": FONT_PATH.exists(),
        "sessions": len(_sessions),
        "llm": {
            "provider": "apikey.fan" if LIVE_LLM_API_KEY else "local-fallback",
            "model": LIVE_LLM_MODEL if LIVE_LLM_API_KEY else "qwen2.5-1.5b",
            "configured": bool(LIVE_LLM_API_KEY),
        },
    }


@api.get("/api/v0/fixed-host", include_in_schema=False)
def fixed_host() -> FileResponse:
    return FileResponse(FIXED_HOST)


@api.get("/api/v0/sample-product", include_in_schema=False)
def sample_product() -> FileResponse:
    return FileResponse(SAMPLE_PRODUCT)


@api.post("/api/v0/live/create")
async def create_live(
    product_name: str = Form(...),
    original_price: str = Form(...),
    sale_price: str = Form(...),
    selling_points: str = Form(""),
    product_params: str = Form(""),
    promotion: str = Form(""),
    script: str = Form(...),
    reference_text: str = Form(""),
    expression_mode: str = Form("normal"),
    product_image: UploadFile | None = File(None),
    portrait_image: UploadFile | None = File(None),
    reference_audio: UploadFile | None = File(None),
) -> dict[str, Any]:
    product_name = product_name.strip()
    script = script.strip()
    if not product_name:
        raise HTTPException(status_code=422, detail="商品名称不能为空")
    if len(script) < 10 or len(script) > 1200:
        raise HTTPException(status_code=422, detail="直播稿需为 10–1200 个字符")

    session_id = uuid.uuid4().hex[:24]
    job_path = _session_path(session_id)
    job_path.mkdir(parents=True, exist_ok=False)
    image_path = job_path / "product.png"
    if product_image and product_image.filename:
        content = await product_image.read()
        if len(content) > 12 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="商品图片不能超过 12 MB")
        upload_path = job_path / "product_upload"
        upload_path.write_bytes(content)
        try:
            with Image.open(upload_path) as image:
                image.convert("RGB").save(image_path, "PNG", optimize=True)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"商品图片无法读取：{exc}") from exc
        finally:
            upload_path.unlink(missing_ok=True)
    else:
        shutil.copy2(SAMPLE_PRODUCT, image_path)

    portrait_path = FIXED_HOST
    if portrait_image and portrait_image.filename:
        portrait_content = await portrait_image.read()
        if len(portrait_content) > 12 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="主播照片不能超过 12 MB")
        portrait_path = job_path / "portrait_upload"
        portrait_path.write_bytes(portrait_content)
        try:
            with Image.open(portrait_path) as image:
                image.verify()
        except Exception as exc:
            portrait_path.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail=f"主播照片无法读取：{exc}") from exc

    voice_path = FIXED_VOICE
    custom_voice = bool(reference_audio and reference_audio.filename)
    if custom_voice:
        audio_content = await reference_audio.read()
        if len(audio_content) > 30 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="声音参考不能超过 30 MB")
        voice_path = job_path / "voice_upload"
        voice_path.write_bytes(audio_content)

    session = {
        "id": session_id,
        "version": "V0",
        "created_at": _now(),
        "updated_at": _now(),
        "render_status": "queued",
        "playback": "preparing",
        "progress": 0,
        "message": "任务已进入单 GPU 生成队列。",
        "product": {
            "name": product_name,
            "original_price": original_price.strip(),
            "sale_price": sale_price.strip(),
            "selling_points": selling_points.strip(),
            "params": product_params.strip(),
            "promotion": promotion.strip(),
        },
        "script": script,
        "product_image": str(image_path),
        "portrait_path": str(portrait_path),
        "voice_path": str(voice_path),
        "custom_portrait": portrait_path != FIXED_HOST,
        "custom_voice": custom_voice,
        "reference_text": reference_text.strip(),
        "expression_mode": expression_mode if expression_mode in {"normal", "funny", "enhanced"} else "normal",
        "video_layout": "avatar_only",
        "interactions": [],
    }
    with _sessions_lock:
        _sessions[session_id] = session
        _write_session(session)
    _executor.submit(_render_session, session_id)
    return _public_session(session)


@api.get("/api/v0/live/current")
def current_live() -> dict[str, Any]:
    with _sessions_lock:
        if not _sessions:
            raise HTTPException(status_code=404, detail="当前没有直播 Session")
        ordered = sorted(_sessions.values(), key=lambda item: item.get("created_at", ""), reverse=True)
        session = next((item for item in ordered if item.get("render_status") == "ready"), ordered[0])
        return _public_session(session)


@api.get("/api/v0/live/{session_id}")
def get_live(session_id: str) -> dict[str, Any]:
    _session_path(session_id)
    with _sessions_lock:
        session = _sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="直播 Session 不存在")
        return _public_session(session)


@api.post("/api/v0/live/{session_id}/danmaku")
def ask_danmaku(session_id: str, request: DanmakuRequest) -> dict[str, Any]:
    question = request.text.strip()
    user = request.user.strip()[:20] or "观众"
    if not question:
        raise HTTPException(status_code=422, detail="弹幕内容不能为空")
    if len(question) > 120:
        raise HTTPException(status_code=422, detail="问题弹幕不能超过 120 字")
    with _sessions_lock:
        session = _sessions.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="直播 Session 不存在")
        if session.get("render_status") != "ready":
            raise HTTPException(status_code=409, detail="直播尚未准备完成")
        pending = sum(
            item.get("status") in {"queued", "thinking", "speaking"}
            for item in session.get("interactions", [])
        )
        if pending >= 3:
            raise HTTPException(status_code=429, detail="数字人正在回答其他观众，请稍后再发")
        active_product = dict(session.get("active_product") or session["product"])
        interaction = {
            "id": uuid.uuid4().hex[:16],
            "user": user,
            "question": question,
            "answer": "",
            "status": "queued",
            "type": "danmaku",
            "product": active_product,
            "created_at": _now(),
        }
        session.setdefault("interactions", []).append(interaction)
        session["interactions"] = session["interactions"][-30:]
        session["updated_at"] = _now()
        _write_session(session)
    _executor.submit(_reply_to_danmaku, session_id, interaction["id"], question)
    return interaction


@api.post("/api/v0/live/{session_id}/commentary")
def create_product_commentary(
    session_id: str, request: ProductCommentaryRequest
) -> dict[str, Any]:
    name = request.name.strip()
    if not name or len(name) > 160:
        raise HTTPException(status_code=422, detail="商品名称不能为空且不能超过160字")
    product = {
        "sku": request.sku.strip()[:80],
        "name": name,
        "original_price": request.original_price.strip()[:40],
        "sale_price": request.sale_price.strip()[:40],
        "selling_points": request.selling_points.strip()[:500],
        "params": request.params.strip()[:500],
        "promotion": request.promotion.strip()[:200],
    }
    with _sessions_lock:
        session = _sessions.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="直播 Session 不存在")
        if session.get("render_status") != "ready":
            raise HTTPException(status_code=409, detail="直播尚未准备完成")
        pending = next(
            (
                item for item in reversed(session.get("interactions", []))
                if item.get("status") in {"queued", "thinking", "speaking"}
            ),
            None,
        )
        if pending:
            raise HTTPException(status_code=409, detail="数字人正在生成上一段讲解或回答，请稍后再试")

    image_path = Path(session["product_image"])
    image_data = request.image_data.strip()
    if image_data:
        encoded = image_data.split(",", 1)[-1]
        try:
            raw = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise HTTPException(status_code=422, detail="商品图片数据无效") from exc
        if len(raw) > 6 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="商品图片不能超过6MB")
        image_dir = _session_path(session_id) / "product_images"
        image_dir.mkdir(parents=True, exist_ok=True)
        image_path = image_dir / f"{uuid.uuid4().hex[:12]}.png"
        upload_path = image_path.with_suffix(".upload")
        upload_path.write_bytes(raw)
        try:
            with Image.open(upload_path) as image:
                image.convert("RGB").save(image_path, "PNG", optimize=True)
        except Exception as exc:
            image_path.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail="商品图片无法读取") from exc
        finally:
            upload_path.unlink(missing_ok=True)

    interaction = {
        "id": uuid.uuid4().hex[:16],
        "user": "数字人主播",
        "question": f"正在讲解：{name}",
        "answer": "",
        "status": "queued",
        "type": "commentary",
        "product": product,
        "product_image": str(image_path),
        "created_at": _now(),
    }
    with _sessions_lock:
        session = _sessions[session_id]
        session["active_product"] = product
        session["active_product_image"] = str(image_path)
        session.setdefault("interactions", []).append(interaction)
        session["interactions"] = session["interactions"][-30:]
        session["updated_at"] = _now()
        _write_session(session)
    _executor.submit(_comment_on_product, session_id, interaction["id"])
    return {key: value for key, value in interaction.items() if key != "product_image"}


@api.post("/api/v0/live/{session_id}/{action}")
def control_live(session_id: str, action: str) -> dict[str, Any]:
    if action not in {"start", "pause", "resume", "stop"}:
        raise HTTPException(status_code=404, detail="未知直播控制操作")
    with _sessions_lock:
        session = _sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="直播 Session 不存在")
        if session.get("render_status") != "ready":
            raise HTTPException(status_code=409, detail="直播画面尚未生成完成")
    playback = {"start": "playing", "pause": "paused", "resume": "playing", "stop": "stopped"}[action]
    message = {
        "start": "直播已开始",
        "pause": "直播已暂停",
        "resume": "直播已恢复",
        "stop": "直播已停止",
    }[action]
    return _public_session(_update_session(session_id, playback=playback, message=message))


@api.get("/api/v0/live/{session_id}/video", include_in_schema=False)
def live_video(session_id: str) -> FileResponse:
    with _sessions_lock:
        session = _sessions.get(session_id)
        if not session or session.get("render_status") != "ready":
            raise HTTPException(status_code=404, detail="直播视频尚未就绪")
        path = Path(session["video_path"])
    return FileResponse(path, media_type="video/mp4", filename=f"v0-{session_id}.mp4")


@api.get(
    "/api/v0/live/{session_id}/interactions/{interaction_id}/video",
    include_in_schema=False,
)
def interaction_video(session_id: str, interaction_id: str) -> FileResponse:
    with _sessions_lock:
        session = _sessions.get(session_id)
        item = next(
            (entry for entry in (session or {}).get("interactions", []) if entry["id"] == interaction_id),
            None,
        )
        if not item or item.get("status") != "ready" or not item.get("video_path"):
            raise HTTPException(status_code=404, detail="数字人回答视频尚未就绪")
        path = Path(item["video_path"])
    return FileResponse(path, media_type="video/mp4", filename=f"reply-{interaction_id}.mp4")


@api.websocket("/ws/v0/live/{session_id}")
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


app = gr.mount_gradio_app(api, demo, path="/studio")
