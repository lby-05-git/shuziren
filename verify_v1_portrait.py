#!/usr/bin/env python3
"""Verify portrait rendering and a real product-video close-up."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import v1_api


def run(command: list[str]) -> str:
    return subprocess.check_output(command, text=True).strip()


def sample_rgb(video: Path, timestamp: float, x: int | None = None, y: int | None = None) -> tuple[int, int, int]:
    crop = "crop=40:40:(iw-40)/2:(ih-40)/2" if x is None else f"crop=20:20:{x}:{y}"
    raw = subprocess.check_output(
        [
            "ffmpeg", "-v", "error", "-ss", str(timestamp), "-i", str(video),
            "-frames:v", "1", "-vf", crop + ",scale=1:1,format=rgb24",
            "-f", "rawvideo", "-",
        ]
    )
    if len(raw) != 3:
        raise AssertionError(f"unexpected RGB sample length: {len(raw)}")
    return tuple(raw)  # type: ignore[return-value]


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_v1_portrait.py SESSION_ID")
    session_id = sys.argv[1]
    item_root = v1_api.V1_DIR / session_id / "items" / "0"
    avatar = item_root / "musetalk_results" / "v15" / "digital_human.mp4"
    if not avatar.exists():
        raise AssertionError(f"missing avatar video: {avatar}")

    with tempfile.TemporaryDirectory(prefix="v1-portrait-", dir=str(v1_api.V1_DIR)) as temp:
        work = Path(temp)
        product_video = work / "red-product.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                "color=c=red:s=640x640:r=25:d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                str(product_video),
            ],
            check=True,
        )
        product = {
            "name": "竖屏商品视频特写验收商品", "original_price": "99", "sale_price": "79",
            "selling_points": "商品视频特写", "params": "测试参数", "promotion": "测试优惠",
        }
        plan = [
            {"stage": "welcome", "action": "welcome", "emotion": "friendly", "text": "欢迎进入直播间。"},
            {"stage": "selling", "action": "point_product", "emotion": "excited", "text": "现在请看商品视频特写。"},
            {"stage": "price", "action": "price", "emotion": "excited", "text": "直播价格现在重点展示。"},
            {"stage": "cta", "action": "buy_now", "emotion": "excited", "text": "喜欢就点击商品了解详情。"},
        ]
        output, timed = v1_api._compose_v1_scene(
            avatar, v1_api.SAMPLE_PRODUCT, product_video, product, plan, work, "beauty", "portrait"
        )
        probe = json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)]))
        video_stream = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
        audio_stream = next(stream for stream in probe["streams"] if stream["codec_type"] == "audio")
        assert (video_stream["width"], video_stream["height"]) == (720, 1280)
        assert video_stream["codec_name"] == "h264"
        assert audio_stream["codec_name"] == "aac"
        duration = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(output)]))
        close_start = next(float(segment["start"]) for segment in timed if segment["action"] == "point_product")
        before = sample_rgb(output, 0.5)
        during = sample_rgb(output, min(duration - 0.5, close_start + 1.0))
        assert during[0] > during[1] + 60 and during[0] > during[2] + 60, (before, during)
        price_time = next(float(segment["start"]) + 0.5 for segment in timed if segment["action"] == "price")
        buy_time = next(float(segment["start"]) + 0.5 for segment in timed if segment["action"] == "buy_now")
        price_samples = []
        for x in (330, 450, 600, 670):
            baseline = sample_rgb(output, 0.5, x, 110)
            active = sample_rgb(output, price_time, x, 110)
            price_samples.append((baseline, active, sum(abs(a - b) for a, b in zip(baseline, active))))
        cta_samples = []
        for x in (390, 500, 620, 670):
            baseline = sample_rgb(output, 0.5, x, 52)
            active = sample_rgb(output, buy_time, x, 52)
            cta_samples.append((baseline, active, sum(abs(a - b) for a, b in zip(baseline, active))))
        assert max(sample[2] for sample in price_samples) > 45, price_samples
        assert max(sample[2] for sample in cta_samples) > 45, cta_samples
        price_color = price_samples[-1][1]
        cta_color = cta_samples[-1][1]
        assert price_color[0] > price_color[1] + 70 and price_color[2] > price_color[1] + 35, price_color
        assert cta_color[0] > cta_color[1] + 25 and cta_color[2] > cta_color[1] + 10, cta_color
        assert len(timed) == len(plan) and timed[-1]["end"] > timed[0]["start"]
        print(json.dumps({
            "ok": True, "orientation": "portrait", "resolution": "720x1280",
            "scene": "beauty", "product_video_closeup": True, "before_rgb": before,
            "closeup_rgb": during, "price_badge_max_delta": max(sample[2] for sample in price_samples),
            "cta_badge_max_delta": max(sample[2] for sample in cta_samples),
            "duration": round(duration, 3), "streams": ["h264", "aac"],
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
