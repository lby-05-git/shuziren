#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:7860}"
SESSION_ID="${1:-}"
TEST_SECONDS="${TEST_SECONDS:-7200}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-15}"
PROJECT_DIR="/root/autodl-tmp/projects/DigitalHuman"
PYTHON_BIN="${PYTHON_BIN:-/root/autodl-tmp/envs/digitalhuman/bin/python}"

if [[ ! "$SESSION_ID" =~ ^[a-f0-9]{24}$ ]]; then
  echo "usage: $0 <v1-session-id>" >&2
  exit 2
fi

LOG_DIR="$PROJECT_DIR/logs/v1_soak_${SESSION_ID}_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOG_DIR"
START_TS="$(date +%s)"
END_TS="$((START_TS + TEST_SECONDS))"
CHECKS=0
FAILURES=0

curl -fsS -X POST "$BASE_URL/api/v1/live/$SESSION_ID/start" > "$LOG_DIR/start.json"

while (( $(date +%s) < END_TS )); do
  NOW="$(date --iso-8601=seconds)"
  if curl -fsS "$BASE_URL/api/v1/health" > "$LOG_DIR/health.json" && \
     curl -fsS "$BASE_URL/api/v1/live/$SESSION_ID" > "$LOG_DIR/session.json"; then
    VIDEO_URL="$("$PYTHON_BIN" - "$LOG_DIR/session.json" <<'PY'
import json, sys
data=json.load(open(sys.argv[1], encoding='utf-8'))
print(data.get('video_url',''))
PY
)"
    if [[ -n "$VIDEO_URL" ]] && curl -fsS -r 0-4095 "$BASE_URL$VIDEO_URL" -o "$LOG_DIR/video.range"; then
      STATUS="ok"
    else
      STATUS="video_failed"
      FAILURES="$((FAILURES + 1))"
    fi
  else
    STATUS="api_failed"
    FAILURES="$((FAILURES + 1))"
  fi
  CHECKS="$((CHECKS + 1))"
  GPU="$(nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits 2>/dev/null || true)"
  printf '%s checks=%d failures=%d status=%s gpu=%s\n' "$NOW" "$CHECKS" "$FAILURES" "$STATUS" "$GPU" | tee -a "$LOG_DIR/monitor.log"
  sleep "$INTERVAL_SECONDS"
done

curl -fsS -X POST "$BASE_URL/api/v1/live/$SESSION_ID/stop" > "$LOG_DIR/stop.json" || true
"$PYTHON_BIN" - "$LOG_DIR/session.json" "$LOG_DIR/result.json" "$CHECKS" "$FAILURES" "$TEST_SECONDS" <<'PY'
import json, sys
session=json.load(open(sys.argv[1], encoding='utf-8'))
result={
  'ok': int(sys.argv[4]) == 0,
  'checks': int(sys.argv[3]),
  'failures': int(sys.argv[4]),
  'duration_seconds': int(sys.argv[5]),
  'sequence': session.get('sequence'),
  'loop_count': session.get('loop_count'),
  'queue_index': session.get('queue_index'),
}
open(sys.argv[2], 'w', encoding='utf-8').write(json.dumps(result, ensure_ascii=False, indent=2))
print(json.dumps(result, ensure_ascii=False, indent=2))
PY

(( FAILURES == 0 ))
