#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/root/autodl-tmp/projects/DigitalHuman"
PYTHON_BIN="/root/autodl-tmp/envs/digitalhuman/bin/python"

export DIGITAL_HUMAN_HOME="$PROJECT_DIR"
export DIGITAL_HUMAN_MODELS="/root/autodl-tmp/models"
export COSYVOICE_HOME="/root/autodl-tmp/projects/CosyVoiceOfficial"
export MUSETALK_HOME="/root/autodl-tmp/projects/MuseTalk"
export MUSETALK_PYTHON="/root/miniconda3/envs/szr/bin/python"
export LIVEPORTRAIT_HOME="/root/autodl-tmp/projects/LivePortrait"
export LIVEPORTRAIT_PYTHON="/root/autodl-tmp/envs/digitalhuman/bin/python"
export PYTHONUNBUFFERED=1
export GRADIO_ANALYTICS_ENABLED=False
export PORT="${PORT:-7860}"

mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/outputs"
cd "$PROJECT_DIR"
exec "$PYTHON_BIN" app.py
