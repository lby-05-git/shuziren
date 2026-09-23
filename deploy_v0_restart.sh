#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/root/autodl-tmp/projects/DigitalHuman"
PYTHON_BIN="/root/autodl-tmp/envs/digitalhuman/bin/python"

cd "$PROJECT_DIR"
"$PYTHON_BIN" -m py_compile v0_app.py v1_api.py verify_v1.py verify_v1_portrait.py verify_v1_ten_products.py verify_v1_custom_profile.py
for asset in v1_static/index.html v1_static/app.js v1_static/style.css v1_static/products.json; do
  [[ -s "$asset" ]] || { echo "missing V1 standalone asset: $asset" >&2; exit 1; }
done
mkdir -p logs

if [[ -f v0_server.pid ]]; then
  old_pid="$(cat v0_server.pid)"
  if [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
    kill "$old_pid"
    for _ in {1..20}; do
      kill -0 "$old_pid" 2>/dev/null || break
      sleep 0.5
    done
  fi
fi

nohup ./v0_start.sh > logs/v0_server.log 2>&1 &
echo $! > v0_server.pid

for _ in {1..60}; do
  if curl -fsS http://127.0.0.1:7860/api/v0/health >/tmp/digitalhuman-v0-health.json && \
     curl -fsS http://127.0.0.1:7860/api/v1/health >/tmp/digitalhuman-v1-health.json; then
    "$PYTHON_BIN" -m json.tool /tmp/digitalhuman-v0-health.json
    "$PYTHON_BIN" -m json.tool /tmp/digitalhuman-v1-health.json
    exit 0
  fi
  sleep 1
done

tail -80 logs/v0_server.log
exit 1
