from pathlib import Path

from pipeline import synthesize_voice


reference = Path("/root/autodl-tmp/projects/DigitalHuman/outputs/profiles/149cd606ff32c58b7abc/reference.wav")
output = Path("/root/autodl-tmp/projects/DigitalHuman/outputs/live_voice_acceptance.wav")
text = "欢迎来到直播间！这款商品今天有专属优惠，核心亮点非常实用，喜欢的朋友可以重点关注。"
plan = [
    {"action": "welcome", "emotion": "excited", "text": "欢迎来到直播间！"},
    {"action": "point_product", "emotion": "confident", "text": "这款商品今天有专属优惠，核心亮点非常实用，"},
    {"action": "buy_now", "emotion": "excited", "text": "喜欢的朋友可以重点关注。"},
]


def status(message: str) -> None:
    print(message, flush=True)


synthesize_voice(
    text,
    reference,
    "大家好，我正在录制一段用于数字人声克隆的语音样本。这段录音的目的是尽可能完整的保留我的声音特点。",
    output,
    status,
    delivery_plan=plan,
)
print(output)
