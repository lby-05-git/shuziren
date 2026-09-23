from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def verify_websocket(base_url: str, session_id: str) -> dict:
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    uri = f"{scheme}://{parsed.netloc}/ws/v1/live/{session_id}"
    async with websockets.connect(uri, open_timeout=10) as socket:
        payload = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
    check(payload.get("id") == session_id, "WebSocket 返回了错误 Session")
    return {"ok": True, "uri": uri, "version": payload.get("version")}


async def verify_webrtc(base_url: str, session: dict) -> dict:
    pc = RTCPeerConnection()
    pc.addTransceiver("video", direction="recvonly")
    pc.addTransceiver("audio", direction="recvonly")
    video_received = asyncio.Event()
    audio_received = asyncio.Event()

    @pc.on("track")
    async def on_track(track) -> None:
        try:
            await asyncio.wait_for(track.recv(), timeout=12)
            if track.kind == "video":
                video_received.set()
            elif track.kind == "audio":
                audio_received.set()
        except Exception:
            return

    try:
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        response = await asyncio.to_thread(
            requests.post,
            base_url + session["transport"]["webrtc_offer"],
            json={"sdp": pc.localDescription.sdp, "type": pc.localDescription.type},
            timeout=30,
        )
        response.raise_for_status()
        answer = response.json()
        await pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))
        await asyncio.wait_for(video_received.wait(), timeout=20)
        await asyncio.wait_for(audio_received.wait(), timeout=20)
        return {"ok": True, "video_frame": True, "audio_frame": True}
    finally:
        await pc.close()


def ffprobe(url: str) -> dict:
    process = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration:stream=index,codec_type,codec_name,width,height,avg_frame_rate",
            "-of", "json", url,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(process.stdout)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("session_id")
    parser.add_argument("--base", default="http://127.0.0.1:7860")
    parser.add_argument("--skip-rotation", action="store_true")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    session_url = f"{base}/api/v1/live/{args.session_id}"

    health = requests.get(f"{base}/api/v1/health", timeout=15).json()
    check(health.get("ok") is True, "V1 健康检查未通过")
    session = requests.get(session_url, timeout=15).json()
    check(session.get("render_status") == "ready", "V1 视频尚未生成完成")
    check(session.get("queue"), "商品队列为空")
    item = session["queue"][session.get("queue_index", 0)]
    check(item.get("render_status") == "ready", "当前商品视频未就绪")
    plan = item.get("script_plan") or []
    actions = {entry.get("action") for entry in plan}
    required_actions = {"welcome", "point_product", "price", "discount", "buy_now"}
    check(required_actions.issubset(actions), f"动作语义覆盖不足：{required_actions - actions}")
    check(len(item.get("script") or "") >= 180, "自动话术长度不足")

    video_url = base + item["video_url"]
    range_response = requests.get(video_url, headers={"Range": "bytes=0-4095"}, timeout=20)
    check(range_response.status_code in {200, 206}, "视频 Range 请求失败")
    check(len(range_response.content) > 1000, "视频响应内容不足")
    media = ffprobe(video_url)
    streams = media.get("streams") or []
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    check(video_stream is not None and audio_stream is not None, "直播视频缺少音频或视频轨")
    expected = (720, 1280) if session.get("orientation") == "portrait" else (1280, 720)
    check((video_stream.get("width"), video_stream.get("height")) == expected, "直播画面尺寸与模板方向不符")

    websocket_result = await verify_websocket(base, args.session_id)
    started = requests.post(f"{session_url}/start", timeout=15).json()
    check(started.get("playback") == "playing", "开始直播失败")
    webrtc_result = await verify_webrtc(base, started)
    paused = requests.post(f"{session_url}/pause", timeout=15).json()
    check(paused.get("playback") == "paused", "暂停直播失败")
    resumed = requests.post(f"{session_url}/resume", timeout=15).json()
    check(resumed.get("playback") == "playing", "恢复直播失败")

    rotation_result = {"skipped": True}
    if not args.skip_rotation:
        before_sequence = int(resumed.get("sequence") or 0)
        wait_seconds = min(int(resumed.get("remaining_seconds") or 15) + 2, 25)
        await asyncio.sleep(wait_seconds)
        rotated = requests.get(session_url, timeout=15).json()
        check(int(rotated.get("sequence") or 0) > before_sequence, "商品队列定时轮播未触发")
        rotation_result = {
            "ok": True,
            "wait_seconds": wait_seconds,
            "sequence_before": before_sequence,
            "sequence_after": rotated.get("sequence"),
            "queue_index": rotated.get("queue_index"),
            "loop_count": rotated.get("loop_count"),
        }
    stopped = requests.post(f"{session_url}/stop", timeout=15).json()
    check(stopped.get("playback") == "stopped", "停止直播失败")

    print(json.dumps({
        "ok": True,
        "version": session.get("version"),
        "session_id": args.session_id,
        "auto_script_chars": len(item.get("script") or ""),
        "segments": len(plan),
        "actions": sorted(actions),
        "scene_template": session.get("scene_template"),
        "orientation": session.get("orientation"),
        "media": media,
        "websocket": websocket_result,
        "webrtc": webrtc_result,
        "rotation": rotation_result,
        "controls": ["start", "pause", "resume", "stop"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
