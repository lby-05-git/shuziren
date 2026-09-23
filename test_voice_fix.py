import json
from difflib import SequenceMatcher
from pathlib import Path
import re

from pipeline import synthesize_voice, transcribe


project = Path("/root/autodl-tmp/projects/DigitalHuman")
session = json.loads(
    (project / "outputs/v1_sessions/c19a634ce55d4d36a26a2f36/session.json").read_text(encoding="utf-8")
)
reference = Path(session["voice_reference"])
reference_text = str(session["voice_prompt_text"])
output = project / "outputs/live_voice_fixed_acceptance.wav"
plan = [
    {"action": "welcome", "emotion": "excited", "text": "欢迎来到直播间，今天咱们重点聊一台很有意思的手机。"},
    {"action": "point_product", "emotion": "confident", "text": "这款是vivo S50t元气版，灵感紫配色，十二GB加二百五十六GB。"},
    {"action": "buy_now", "emotion": "excited", "text": "喜欢的朋友先点个关注，马上开讲。"},
]
expected = "".join(item["text"] for item in plan)
synthesize_voice(
    expected,
    reference,
    reference_text,
    output,
    print,
    delivery_plan=plan,
    strict_content=True,
)
actual = transcribe(output, print)
normalize = lambda value: "".join(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", value.lower()))
score = SequenceMatcher(None, normalize(expected), normalize(actual), autojunk=False).ratio()
print(json.dumps({"expected": expected, "actual": actual, "similarity": score}, ensure_ascii=False, indent=2))
