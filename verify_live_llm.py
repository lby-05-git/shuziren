"""Small deployment check for the configured OpenAI-compatible live LLM."""

from __future__ import annotations

import json
import os

import requests


base_url = os.environ["LIVE_LLM_BASE_URL"].rstrip("/")
model = os.environ["LIVE_LLM_MODEL"]
headers = {
    "Authorization": f"Bearer {os.environ['LIVE_LLM_API_KEY']}",
    "Content-Type": "application/json",
}
response = requests.post(
    f"{base_url}/chat/completions",
    headers=headers,
    json={
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是电商数字人主播。只能使用资料生成60到110字的中文口播，不得编造。"
                    "商品：vivo S50t；原价3299元；直播价2999元；卖点：骁龙8s、长焦Live、超声波指纹。"
                ),
            },
            {"role": "user", "content": "请开始讲解当前商品。"},
        ],
        "temperature": 0.3,
        "max_tokens": 800,
        "thinking": {"type": "disabled"},
        "stream": False,
    },
    timeout=(10, 90),
)
print("status", response.status_code)
payload = response.json()
if not response.ok:
    print(json.dumps(payload, ensure_ascii=False)[:1000])
    raise SystemExit(1)
message = payload["choices"][0]["message"]
print("model", payload.get("model", model))
print("content", message.get("content"))
print("reasoning_content", message.get("reasoning_content"))
