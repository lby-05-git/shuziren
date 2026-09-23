import json
import re
import subprocess
from difflib import SequenceMatcher
from pathlib import Path

from pipeline import transcribe


project = Path("/root/autodl-tmp/projects/DigitalHuman")
session_id = "36feac1fe2114b3488a96376"
session = json.loads(
    (project / "outputs/v1_sessions" / session_id / "session.json").read_text(encoding="utf-8")
)
item = session["queue"][0]
video = Path(item["video_path"])
audio = project / "outputs/final_acceptance_audit.wav"
subprocess.run(
    [
        "ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn",
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio),
    ],
    check=True,
)
actual = transcribe(audio, print)
expected = item["script"]
normalize = lambda value: "".join(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", value.lower()))
similarity = SequenceMatcher(None, normalize(expected), normalize(actual), autojunk=False).ratio()
markers = ["请使用热情", "请使用有活力", "直播主播语气", "不要机械朗读", "语调自然起伏"]
leaks = [marker for marker in markers if normalize(marker) in normalize(actual)]
plan = item.get("script_plan") or []
timing_valid = bool(plan) and all(
    isinstance(entry.get("start"), (int, float))
    and isinstance(entry.get("end"), (int, float))
    and 0 <= float(entry["start"]) < float(entry["end"]) <= 300.05
    and (index == 0 or float(entry["start"]) >= float(plan[index - 1]["end"]) - 0.01)
    for index, entry in enumerate(plan)
)
report = {
    "session_id": session_id,
    "product": item["product"]["name"],
    "video_duration": item.get("video_duration"),
    "expected_chars": len(normalize(expected)),
    "transcript_chars": len(normalize(actual)),
    "similarity": round(similarity, 4),
    "prompt_leak": leaks,
    "spoken_content_passed": similarity >= 0.72 and not leaks,
    "subtitle_segments": len(plan),
    "subtitle_timing_valid": timing_valid,
    "subtitle_first": plan[0] if plan else None,
    "subtitle_last": plan[-1] if plan else None,
    "expected_script": expected,
    "transcript": actual,
}
report_path = project / "outputs/final_acceptance_audit.json"
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
