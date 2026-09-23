#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/root/autodl-tmp/projects/DigitalHuman"
PYTHON_BIN="/root/autodl-tmp/envs/digitalhuman/bin/python"

# API 密钥只保存在服务器私有配置文件中，不进入源码或浏览器。
if [[ -f "$PROJECT_DIR/.env.llm" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.env.llm"
  set +a
fi

export DIGITAL_HUMAN_HOME="$PROJECT_DIR"
export DIGITAL_HUMAN_MODELS="/root/autodl-tmp/models"
export COSYVOICE_HOME="/root/autodl-tmp/projects/CosyVoiceOfficial"
export MUSETALK_HOME="/root/autodl-tmp/projects/MuseTalk"
export MUSETALK_PYTHON="/root/miniconda3/envs/szr/bin/python"
export LIVEPORTRAIT_HOME="/root/autodl-tmp/projects/LivePortrait"
export LIVEPORTRAIT_PYTHON="/root/autodl-tmp/envs/digitalhuman/bin/python"
export V0_FONT="/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
export PYTHONUNBUFFERED=1
export GRADIO_ANALYTICS_ENABLED=False
export PORT="${PORT:-7860}"

mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/outputs/v0_sessions" "$PROJECT_DIR/outputs/v1_sessions"
cd "$PROJECT_DIR"
exec "$PYTHON_BIN" -m uvicorn v0_app:app --host 0.0.0.0 --port "$PORT" --workers 1
