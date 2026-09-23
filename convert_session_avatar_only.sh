#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/root/autodl-tmp/projects/DigitalHuman"
SESSION_ID="${1:?usage: convert_session_avatar_only.sh SESSION_ID}"
FORCE="${2:-}"
SESSION_DIR="$PROJECT_DIR/outputs/v0_sessions/$SESSION_ID"

if [[ ! "$SESSION_ID" =~ ^[a-f0-9]{24}$ ]] || [[ ! -d "$SESSION_DIR" ]]; then
  echo "invalid session: $SESSION_ID" >&2
  exit 2
fi

converted=0
while IFS= read -r -d '' video; do
  backup="${video%.mp4}.with_product.mp4"
  source_video="$video"
  if [[ -f "$backup" ]]; then
    [[ "$FORCE" == "--force" ]] || continue
    source_video="$backup"
  fi
  temporary="${video%.mp4}.avatar_only.tmp.mp4"
  rm -f "$temporary"
  ffmpeg -nostdin -y -v error -i "$source_video" \
    -filter_complex \
    "[0:v]crop=600:720:45:0,split=2[bg_source][portrait_source];\
[bg_source]scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,boxblur=20:2,eq=brightness=-0.22[background];\
[portrait_source]setsar=1[portrait];\
[background][portrait]overlay=(W-w)/2:0,setsar=1[outv]" \
    -map '[outv]' -map '0:a:0?' -c:v libx264 -preset veryfast -crf 20 \
    -pix_fmt yuv420p -c:a aac -b:a 160k -movflags +faststart "$temporary"
  if [[ ! -f "$backup" ]]; then
    mv "$video" "$backup"
  else
    rm -f "$video"
  fi
  mv "$temporary" "$video"
  converted=$((converted + 1))
done < <(find "$SESSION_DIR" -type f -name live_program.mp4 -print0)

/root/autodl-tmp/envs/digitalhuman/bin/python - "$SESSION_DIR/session.json" <<'PY'
import json
import sys
from datetime import datetime
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
data["updated_at"] = datetime.now().isoformat(timespec="seconds")
data["video_layout"] = "avatar_only"
temporary = path.with_suffix(".tmp")
temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
temporary.replace(path)
PY

echo "converted=$converted session=$SESSION_ID"
