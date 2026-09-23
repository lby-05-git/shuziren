#!/usr/bin/env bash
set -uo pipefail

PROJECT_DIR="/root/autodl-tmp/projects/DigitalHuman"
SESSION_ID="${1:?usage: v0_soak_test.sh SESSION_ID [SECONDS]}"
TEST_SECONDS="${2:-1800}"
BASE_URL="http://127.0.0.1:7860"
VIDEO="$PROJECT_DIR/outputs/v0_sessions/$SESSION_ID/live_program.mp4"
REPORT="$PROJECT_DIR/outputs/v0_sessions/$SESSION_ID/soak_report.txt"
PLAYER_LOG="$PROJECT_DIR/outputs/v0_sessions/$SESSION_ID/soak_player.log"

start_epoch=$(date +%s)
samples=0
failures=0
video_failures=0

{
  echo "V0 soak test"
  echo "session=$SESSION_ID"
  echo "started_at=$(date -Iseconds)"
  echo "target_seconds=$TEST_SECONDS"
} > "$REPORT"

ffmpeg -re -stream_loop -1 -v error -i "$VIDEO" -t "$TEST_SECONDS" -f null - > "$PLAYER_LOG" 2>&1 &
player_pid=$!

while kill -0 "$player_pid" 2>/dev/null; do
  page_code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/digital-human" || true)
  health_code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/api/v0/health" || true)
  video_code=$(curl -s -o /dev/null -w '%{http_code}' -H 'Range: bytes=0-1023' "$BASE_URL/api/v0/live/$SESSION_ID/video" || true)
  samples=$((samples + 1))
  if [[ "$page_code" != "200" || "$health_code" != "200" ]]; then
    failures=$((failures + 1))
  fi
  if [[ "$video_code" != "206" ]]; then
    video_failures=$((video_failures + 1))
  fi
  sleep 10
done

wait "$player_pid"
player_exit=$?
end_epoch=$(date +%s)
elapsed=$((end_epoch - start_epoch))

{
  echo "finished_at=$(date -Iseconds)"
  echo "elapsed_seconds=$elapsed"
  echo "samples=$samples"
  echo "http_failures=$failures"
  echo "range_failures=$video_failures"
  echo "player_exit=$player_exit"
  if [[ "$player_exit" -eq 0 && "$failures" -eq 0 && "$video_failures" -eq 0 && "$elapsed" -ge "$TEST_SECONDS" ]]; then
    echo "result=PASS"
  else
    echo "result=FAIL"
  fi
} >> "$REPORT"

