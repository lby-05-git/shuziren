#!/usr/bin/env python3
"""Create and verify a V1 live session through uploaded portrait/audio inputs."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:7860"
PROJECT = Path("/root/autodl-tmp/projects/DigitalHuman")
PORTRAIT = PROJECT / "outputs" / "portrait_test.png"
VOICE = PROJECT / "outputs" / "profiles" / "149cd606ff32c58b7abc" / "reference.wav"
REFERENCE_TEXT = "大家好，我正在录制一段用于数字人声克隆的语音样本。这段录音的目的是尽可能完整的保留我的声音特点。"


def main() -> None:
    products = [{
        "sku": "custom-profile-test", "name": "AI主播形象与音色验收商品", "link": "https://example.invalid/custom",
        "original_price": "129", "sale_price": "99", "selling_points": "自定义主播照片、自定义克隆音色、搞怪表情动作",
        "params": "V1融合验收", "promotion": "测试优惠", "duration_seconds": 15,
    }]
    with PORTRAIT.open("rb") as portrait, VOICE.open("rb") as voice:
        response = requests.post(
            BASE + "/api/v1/live/create",
            data={
                "products_json": json.dumps(products, ensure_ascii=False), "scene_template": "fashion",
                "orientation": "landscape", "expression_mode": "funny", "reference_text": REFERENCE_TEXT,
                "auto_rotate": "true",
            },
            files={
                "portrait_image": ("portrait.png", portrait, "image/png"),
                "reference_audio": ("voice.wav", voice, "audio/wav"),
            },
            timeout=120,
        )
    response.raise_for_status()
    session = response.json()
    session_id = session["id"]
    print(json.dumps({"created": session_id, "custom_portrait": session["custom_portrait"], "custom_voice": session["custom_voice"]}, ensure_ascii=False), flush=True)
    assert session["custom_portrait"] is True and session["custom_voice"] is True

    deadline = time.time() + 1800
    last_message = ""
    while time.time() < deadline:
        session = requests.get(BASE + f"/api/v1/live/{session_id}", timeout=15).json()
        item_message = session["queue"][0].get("message", "")
        if item_message != last_message:
            print(json.dumps({"status": session["render_status"], "stage": item_message}, ensure_ascii=False), flush=True)
            last_message = item_message
        if session["render_status"] in {"ready", "failed"}:
            break
        time.sleep(5)
    assert session["render_status"] == "ready", session
    assert session["expression_mode"] == "funny" and session["scene_template"] == "fashion"
    state = json.loads((PROJECT / "outputs" / "v1_sessions" / session_id / "session.json").read_text(encoding="utf-8"))
    assert Path(state["portrait_normalized"]).exists()
    assert Path(state["voice_reference"]).exists()
    funny_outputs = list((PROJECT / "outputs" / "v1_sessions" / session_id / "items" / "0" / "liveportrait").glob("*funny*.mp4"))
    assert funny_outputs, "funny LivePortrait output is missing"
    video = Path(state["queue"][0]["video_path"])
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)
    ], text=True))
    stream_types = {entry["codec_type"] for entry in probe["streams"]}
    assert {"video", "audio"}.issubset(stream_types)
    print(json.dumps({
        "ok": True, "session_id": session_id, "custom_portrait": True, "custom_voice": True,
        "expression_mode": "funny", "scene": "fashion", "duration": float(probe["format"]["duration"]),
        "streams": sorted(stream_types),
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
