from __future__ import annotations

import gc
import hashlib
import json
import os
import pickle
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import torch
import torchaudio
import yaml
import numpy as np
from PIL import Image


PROJECT_DIR = Path(os.environ.get("DIGITAL_HUMAN_HOME", Path(__file__).resolve().parent))
MODEL_DIR = Path(os.environ.get("DIGITAL_HUMAN_MODELS", "/root/autodl-tmp/models"))
COSYVOICE_DIR = Path(os.environ.get("COSYVOICE_HOME", "/root/autodl-tmp/projects/CosyVoiceOfficial"))
MUSETALK_DIR = Path(os.environ.get("MUSETALK_HOME", "/root/autodl-tmp/projects/MuseTalk"))
MUSETALK_PYTHON = os.environ.get("MUSETALK_PYTHON", "/root/miniconda3/envs/szr/bin/python")
LIVEPORTRAIT_DIR = Path(os.environ.get("LIVEPORTRAIT_HOME", "/root/autodl-tmp/projects/LivePortrait"))
LIVEPORTRAIT_PYTHON = os.environ.get(
    "LIVEPORTRAIT_PYTHON", "/root/autodl-tmp/envs/digitalhuman/bin/python"
)

ASR_MODEL_DIR = MODEL_DIR / "SenseVoiceSmall"
TTS_MODEL_DIR = MODEL_DIR / "CosyVoice2-0.5B"
LLM_MODEL_DIR = MODEL_DIR / "Qwen2.5-1.5B-Instruct"
OUTPUT_DIR = PROJECT_DIR / "outputs"
PROFILE_DIR = OUTPUT_DIR / "profiles"
JOB_DIR = OUTPUT_DIR / "jobs"

SYSTEM_PROMPT = (
    "你是一名友好、可靠的中文数字人助手。回答要自然、简洁，适合直接朗读。"
    "不要使用 Markdown 表格、代码块或过多项目符号。"
)

_GPU_LOCK = threading.Lock()


class PipelineError(RuntimeError):
    pass


def _status(callback: Callable[[str], None] | None, message: str) -> None:
    if callback:
        callback(message)


def _release(*objects: Any) -> None:
    for obj in objects:
        try:
            del obj
        except Exception:
            pass
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 900,
    extra_env: dict[str, str] | None = None,
    heartbeat: Callable[[float], None] | None = None,
    heartbeat_interval: float = 10.0,
) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "CUDA_VISIBLE_DEVICES": "0"}
    environment.update(extra_env or {})
    if heartbeat is None:
        result = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            env=environment,
        )
    else:
        started = time.monotonic()
        next_heartbeat = started + max(float(heartbeat_interval), 1.0)
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as output:
            process = subprocess.Popen(
                command,
                cwd=str(cwd) if cwd else None,
                text=True,
                stdout=output,
                stderr=subprocess.STDOUT,
                env=environment,
            )
            try:
                while process.poll() is None:
                    elapsed = time.monotonic() - started
                    if elapsed >= timeout:
                        process.kill()
                        process.wait()
                        output.seek(0)
                        raise subprocess.TimeoutExpired(command, timeout, output=output.read())
                    if time.monotonic() >= next_heartbeat:
                        heartbeat(elapsed)
                        next_heartbeat = time.monotonic() + max(float(heartbeat_interval), 1.0)
                    time.sleep(0.5)
            except BaseException:
                # A cancellation raised by a heartbeat must also stop the
                # child GPU process; otherwise it keeps rendering an obsolete
                # product queue and blocks the replacement session.
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                raise
            output.seek(0)
            result = subprocess.CompletedProcess(command, process.returncode, output.read(), None)
    if result.returncode != 0:
        tail = "\n".join(result.stdout.splitlines()[-40:])
        raise PipelineError(f"命令执行失败（退出码 {result.returncode}）：\n{tail}")
    return result


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _audio_duration(path: Path) -> float:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        timeout=30,
    )
    return float(result.stdout.strip())


def normalize_reference_audio(source: str | Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-t",
            "15",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-af",
            "highpass=f=60,lowpass=f=7600,loudnorm=I=-20:LRA=7:TP=-1.5",
            str(destination),
        ],
        timeout=120,
    )
    duration = _audio_duration(destination)
    if duration < 3:
        raise PipelineError("声音参考录音至少需要 3 秒，建议使用 5–12 秒清晰、单人、无背景音乐的录音。")
    return destination


def normalize_portrait(source: str | Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source) as image:
            image = image.convert("RGB")
            image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            if image.width < 256 or image.height < 256:
                raise PipelineError("照片分辨率过低，请上传至少 256×256 的清晰正脸照片。")
            image.save(destination, format="PNG", optimize=True)
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError(f"无法读取照片：{exc}") from exc
    return destination


def transcribe(audio_path: str | Path, status: Callable[[str], None] | None = None) -> str:
    _status(status, "正在加载 ASR（SenseVoiceSmall）…")
    from funasr import AutoModel
    from funasr.utils.postprocess_utils import rich_transcription_postprocess

    model = None
    try:
        model = AutoModel(
            model=str(ASR_MODEL_DIR),
            trust_remote_code=True,
            device="cuda:0" if torch.cuda.is_available() else "cpu",
            disable_update=True,
        )
        _status(status, "正在识别语音…")
        result = model.generate(
            input=str(audio_path),
            cache={},
            language="auto",
            use_itn=True,
            batch_size_s=60,
        )
        if not result:
            raise PipelineError("ASR 未识别到有效语音。")
        text = rich_transcription_postprocess(result[0].get("text", "")).strip()
        text = re.sub(r"<\|[^|]+\|>", "", text).strip()
        if not text:
            raise PipelineError("ASR 未识别到有效文本，请使用更清晰的录音重试。")
        return text
    finally:
        _release(model)


def prepare_voice_profile(
    reference_audio: str | Path,
    reference_text: str | None = None,
    status: Callable[[str], None] | None = None,
) -> tuple[Path, str]:
    profile_id = _sha256(reference_audio)[:20]
    profile_path = PROFILE_DIR / profile_id
    profile_path.mkdir(parents=True, exist_ok=True)
    normalized_audio = profile_path / "reference.wav"
    metadata_path = profile_path / "profile.json"

    if not normalized_audio.exists():
        _status(status, "正在规范化声音参考录音…")
        normalize_reference_audio(reference_audio, normalized_audio)

    supplied_text = (reference_text or "").strip()
    if supplied_text:
        prompt_text = supplied_text
    elif metadata_path.exists():
        prompt_text = json.loads(metadata_path.read_text(encoding="utf-8"))["transcript"]
    else:
        prompt_text = transcribe(normalized_audio, status)

    metadata_path.write_text(
        json.dumps(
            {
                "profile_id": profile_id,
                "transcript": prompt_text,
                "duration_seconds": round(_audio_duration(normalized_audio), 3),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return normalized_audio, prompt_text


def generate_reply(
    user_text: str,
    history: list[dict[str, str]] | None = None,
    system_prompt: str = SYSTEM_PROMPT,
    temperature: float = 0.7,
    status: Callable[[str], None] | None = None,
) -> str:
    from transformers import AutoModelForCausalLM, AutoTokenizer

    _status(status, "正在加载本地 LLM（Qwen2.5-1.5B-Instruct）…")
    tokenizer = None
    model = None
    try:
        tokenizer = AutoTokenizer.from_pretrained(str(LLM_MODEL_DIR), local_files_only=True)
        model = AutoModelForCausalLM.from_pretrained(
            str(LLM_MODEL_DIR),
            torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
            low_cpu_mem_usage=True,
            local_files_only=True,
        )
        model = model.to("cuda" if torch.cuda.is_available() else "cpu").eval()
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt.strip() or SYSTEM_PROMPT}]
        messages.extend((history or [])[-8:])
        messages.append({"role": "user", "content": user_text})

        _status(status, "LLM 正在生成回复…")
        inputs = tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_tensors="pt",
        ).to(model.device)
        with torch.inference_mode():
            generated = model.generate(
                inputs,
                attention_mask=torch.ones_like(inputs),
                max_new_tokens=220,
                do_sample=temperature > 0.05,
                temperature=max(float(temperature), 0.05),
                top_p=0.9,
                repetition_penalty=1.08,
                pad_token_id=tokenizer.eos_token_id,
            )
        reply = tokenizer.decode(generated[0, inputs.shape[-1] :], skip_special_tokens=True).strip()
        if not reply:
            raise PipelineError("LLM 没有生成有效回复。")
        return reply
    finally:
        _release(model, tokenizer)


_CN_DIGITS = "零一二三四五六七八九"


def _integer_to_chinese(value: str) -> str:
    """Convert a non-negative integer to natural Mandarin TTS text."""
    value = value.lstrip("0") or "0"
    if len(value) > 12:
        return "".join(_CN_DIGITS[int(char)] for char in value)

    def four_digits(group: int) -> str:
        if group == 0:
            return ""
        units = ("", "十", "百", "千")
        digits = list(map(int, f"{group:04d}"))
        result = ""
        pending_zero = False
        for index, digit in enumerate(digits):
            if digit == 0:
                if result:
                    pending_zero = True
                continue
            if pending_zero:
                result += "零"
                pending_zero = False
            result += _CN_DIGITS[digit] + units[3 - index]
        return result

    number = int(value)
    if number == 0:
        return "零"
    groups: list[int] = []
    while number:
        groups.append(number % 10000)
        number //= 10000
    group_units = ("", "万", "亿")
    parts: list[str] = []
    zero_between = False
    for index in range(len(groups) - 1, -1, -1):
        group = groups[index]
        if group == 0:
            if parts:
                zero_between = True
            continue
        if parts and (zero_between or group < 1000):
            if not parts[-1].endswith("零"):
                parts.append("零")
        parts.append(four_digits(group) + group_units[index])
        zero_between = False
    result = "".join(parts)
    if result.startswith("一十"):
        result = result[1:]
    return result


def _number_to_spoken(value: str) -> str:
    integer, dot, fraction = value.partition(".")
    spoken = _integer_to_chinese(integer)
    if dot:
        spoken += "点" + "".join(_CN_DIGITS[int(char)] for char in fraction)
    return spoken


def _prepare_spoken_text(text: str, energetic: bool = False) -> str:
    """Create pronunciation-only text while leaving saved scripts/subtitles intact."""
    spoken = str(text or "")
    # Acronyms must be separated, otherwise the Chinese TTS may read them as a
    # word or merge them with the capacity number.
    acronym_letters = {
        "GB": "G B", "TB": "T B", "MB": "M B", "RAM": "R A M",
        "ROM": "R O M", "CPU": "C P U", "GPU": "G P U", "AI": "A I",
        "NFC": "N F C", "USB": "U S B", "IP": "I P",
    }
    pattern = r"(?i)(\d+(?:\.\d+)?)\s*(GB|TB|MB|RAM|ROM)(?![A-Za-z])"
    spoken = re.sub(
        pattern,
        lambda match: f"{_number_to_spoken(match.group(1))} {acronym_letters[match.group(2).upper()]}",
        spoken,
    )
    spoken = re.sub(
        r"(?i)(?<![A-Za-z])(GB|TB|MB|RAM|ROM|CPU|GPU|AI|NFC|USB|IP)(?![A-Za-z])",
        lambda match: acronym_letters[match.group(1).upper()],
        spoken,
    )
    spoken = re.sub(r"(?i)(\d+(?:\.\d+)?)\s*mAh\b", lambda match: f"{_number_to_spoken(match.group(1))} 毫安时", spoken)
    # Convert every remaining Arabic number, including model numbers and
    # prices, so the synthesizer never guesses how a numeric token is read.
    spoken = re.sub(r"\d+(?:\.\d+)?", lambda match: _number_to_spoken(match.group(0)), spoken)
    if energetic:
        spoken = re.sub(r"。\s*$", "！", spoken)
    return spoken


def synthesize_voice(
    text: str,
    reference_audio: Path,
    reference_text: str,
    output_path: Path,
    status: Callable[[str], None] | None = None,
    delivery_plan: list[dict[str, Any]] | None = None,
    strict_content: bool = False,
) -> Path:
    for path in (COSYVOICE_DIR, COSYVOICE_DIR / "third_party" / "Matcha-TTS"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))

    from cosyvoice.cli.cosyvoice import CosyVoice2
    _status(status, "正在加载 Voice Clone / TTS（CosyVoice2）…")
    cosyvoice = None
    try:
        cosyvoice = CosyVoice2(
            str(TTS_MODEL_DIR),
            load_jit=False,
            load_trt=False,
            fp16=torch.cuda.is_available(),
        )
        _status(status, "正在用参考音色合成回复…")

        def delivery_style(entry: dict[str, Any]) -> tuple[str, str, float, float, float]:
            action = str(entry.get("action") or "idle")
            emotion = str(entry.get("emotion") or "friendly")
            if action in {"price", "discount"}:
                return (
                    "offer",
                    "请使用热情、有感染力的电商直播主播语气，突出价格和优惠数字，适当停顿，真实自然，不要喊叫。",
                    1.09,
                    0.18,
                    2.2,
                )
            if action == "buy_now":
                return (
                    "cta",
                    "请使用有推动力但不夸张的直播促单语气，节奏稍快，重点清楚，保持亲切可信。",
                    1.12,
                    0.16,
                    2.5,
                )
            if action in {"welcome", "next_product", "next"}:
                return (
                    "welcome",
                    "请使用热情明亮、自然亲切的直播主播语气，语调有起伏，语速稍快，不要机械朗读。",
                    1.10,
                    0.22,
                    1.8,
                )
            if action == "point_product" or emotion in {"excited", "confident"}:
                return (
                    "highlight",
                    "请使用有活力、有重点的商品讲解语气，强调核心卖点，语调自然起伏，不要喊叫。",
                    1.07,
                    0.14,
                    1.4,
                )
            if action == "size" or emotion == "neutral":
                return (
                    "detail",
                    "请使用清晰专业、自然耐听的直播讲解语气，参数读得准确，重点处稍作停顿。",
                    1.02,
                    0.14,
                    0.3,
                )
            return (
                "recommend",
                "请使用亲切、有交流感的直播推荐语气，像面对观众聊天一样自然，并带有适度感染力。",
                1.05,
                0.14,
                0.8,
            )

        delivery_chunks: list[dict[str, Any]] = []
        if delivery_plan:
            # Keep one TTS chunk per script-plan segment. Besides preventing
            # cross-segment word leakage, this gives us an exact subtitle
            # boundary from the generated sample count.
            for plan_index, entry in enumerate(delivery_plan):
                chunk_text = str(entry.get("text") or "").strip()
                if not chunk_text:
                    continue
                style_key, instruction, speed, pause, gain_db = delivery_style(entry)
                delivery_chunks.append({
                    "text": chunk_text,
                    "spoken_text": _prepare_spoken_text(
                        chunk_text,
                        energetic=style_key in {"offer", "cta", "welcome", "highlight"},
                    ),
                    "style": style_key,
                    # CosyVoice2 requires this terminator. Without it the
                    # instruction itself can be spoken aloud.
                    "instruction": f"You are a helpful assistant. {instruction}<|endofprompt|>",
                    "speed": speed,
                    "pause": pause,
                    "gain_db": gain_db,
                    "plan_index": plan_index,
                })
        else:
            sentences = [part.strip() for part in re.split(r"(?<=[。！？!?；;])", text) if part.strip()]
            current = ""
            for sentence in sentences or [text.strip()]:
                pieces = [sentence[pos : pos + 450] for pos in range(0, len(sentence), 450)]
                for piece in pieces:
                    if current and len(current) + len(piece) > 450:
                        delivery_chunks.append({"text": current, "spoken_text": _prepare_spoken_text(current), "style": "plain", "speed": 1.0, "pause": 0.0, "gain_db": 0.0, "plan_index": None})
                        current = piece
                    else:
                        current += piece
            if current:
                delivery_chunks.append({"text": current, "spoken_text": _prepare_spoken_text(current), "style": "plain", "speed": 1.0, "pause": 0.0, "gain_db": 0.0, "plan_index": None})

        audio_chunks = []
        timing_segments: list[dict[str, Any]] = []
        sample_cursor = 0
        for index, chunk in enumerate(delivery_chunks, start=1):
            _status(status, f"克隆音色合成进度 {index}/{len(delivery_chunks)}（{chunk['style']}）…")
            if delivery_plan and not strict_content:
                try:
                    generated = list(cosyvoice.inference_instruct2(
                        chunk["spoken_text"],
                        chunk["instruction"],
                        str(reference_audio),
                        stream=False,
                        speed=float(chunk["speed"]),
                    ))
                except Exception:
                    # Keep the user's cloned timbre available even if an older
                    # CosyVoice checkpoint rejects an instruction style.
                    generated = list(cosyvoice.inference_zero_shot(
                        chunk["spoken_text"], reference_text, str(reference_audio),
                        stream=False, speed=float(chunk["speed"]),
                    ))
            else:
                generated = list(cosyvoice.inference_zero_shot(
                    chunk["spoken_text"], reference_text, str(reference_audio),
                    stream=False, speed=float(chunk["speed"]),
                ))
            gain = 10.0 ** (float(chunk.get("gain_db") or 0.0) / 20.0)
            generated_audio = [torch.clamp(item["tts_speech"].cpu() * gain, -0.98, 0.98) for item in generated]
            if not generated_audio:
                raise PipelineError(f"TTS 第 {index} 段未生成有效音频。")
            segment_samples = sum(int(item.shape[1]) for item in generated_audio)
            segment_start = sample_cursor / cosyvoice.sample_rate
            audio_chunks.extend(generated_audio)
            sample_cursor += segment_samples
            segment_end = sample_cursor / cosyvoice.sample_rate
            timing_segments.append({
                "plan_index": chunk.get("plan_index"),
                "text": chunk["text"],
                "spoken_text": chunk["spoken_text"],
                "style": chunk["style"],
                "start": round(segment_start, 6),
                "end": round(segment_end, 6),
            })
            if chunk.get("pause") and index < len(delivery_chunks):
                pause_samples = max(1, int(cosyvoice.sample_rate * float(chunk["pause"])))
                audio_chunks.append(torch.zeros((1, pause_samples), dtype=audio_chunks[-1].dtype))
                sample_cursor += pause_samples
        if not audio_chunks:
            raise PipelineError("TTS 未生成有效音频。")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        torchaudio.save(str(output_path), torch.cat(audio_chunks, dim=1), cosyvoice.sample_rate)
        output_path.with_suffix(".timing.json").write_text(
            json.dumps(
                {
                    "sample_rate": cosyvoice.sample_rate,
                    "duration": round(sample_cursor / cosyvoice.sample_rate, 6),
                    "strict_content": bool(strict_content),
                    "segments": timing_segments,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return output_path
    finally:
        _release(cosyvoice)


def _build_funny_motion_template() -> Path:
    """Combine trusted built-in LivePortrait motions into one loopable sequence."""
    driving_dir = LIVEPORTRAIT_DIR / "assets" / "examples" / "driving"
    output_path = driving_dir / "digital_human_funny_v2.pkl"
    if output_path.exists():
        with output_path.open("rb") as handle:
            cached = pickle.load(handle)
        if "c_eyes_lst" in cached and "c_lip_lst" in cached:
            return output_path

    # Put the head action first so even very short replies visibly enter funny mode.
    source_paths = [driving_dir / name for name in ("shake_face.pkl", "laugh.pkl", "wink.pkl")]
    if not all(path.exists() for path in source_paths):
        raise PipelineError("LivePortrait 搞怪动作模板不完整。")

    sequences: list[list[dict[str, Any]]] = []
    for path in source_paths:
        # These pickle files are bundled with the locally installed official project.
        with path.open("rb") as handle:
            template = pickle.load(handle)
        sequences.append(template["motion"])

    combined: list[dict[str, Any]] = []
    for sequence in sequences:
        if not combined:
            combined.extend(sequence)
            continue
        first = sequence[0]
        previous = combined[-1]
        for frame in sequence[1:]:
            aligned = {
                "exp": frame["exp"] - first["exp"] + previous["exp"],
                "t": frame["t"] - first["t"] + previous["t"],
                "scale": frame["scale"] / first["scale"] * previous["scale"],
                "R": (frame["R"] @ first["R"].transpose(0, 2, 1)) @ previous["R"],
            }
            combined.append(aligned)

    # Return to the initial pose so repeated playback remains smooth.
    combined.extend(reversed(combined[1:-1]))
    payload = {
        "n_frames": len(combined),
        "output_fps": 25,
        "motion": combined,
        "c_eyes_lst": [np.zeros((1, 2), dtype=np.float32) for _ in combined],
        "c_lip_lst": [np.zeros((1, 1), dtype=np.float32) for _ in combined],
    }
    with output_path.open("wb") as handle:
        pickle.dump(payload, handle)
    return output_path


def _build_enhanced_motion_template() -> Path:
    """Build a restrained talking loop with periodic blink/head micro-expressions."""
    driving_dir = LIVEPORTRAIT_DIR / "assets" / "examples" / "driving"
    output_path = driving_dir / "digital_human_enhanced_v1.pkl"
    if output_path.exists():
        with output_path.open("rb") as handle:
            cached = pickle.load(handle)
        if cached.get("motion") and cached.get("n_frames") == len(cached["motion"]):
            return output_path

    source_paths = [
        driving_dir / "talking.pkl",
        driving_dir / "wink.pkl",
        driving_dir / "shake_face.pkl",
    ]
    if not all(path.exists() for path in source_paths):
        raise PipelineError("LivePortrait 眼神与微表情动作素材不完整。")

    with source_paths[0].open("rb") as handle:
        talking = pickle.load(handle)
    with source_paths[1].open("rb") as handle:
        wink = pickle.load(handle)
    with source_paths[2].open("rb") as handle:
        shake = pickle.load(handle)

    # Keep the natural talking motion as the main rhythm, then add two short,
    # low-amplitude expression beats.  The relative pose alignment prevents a
    # visible jump when the blink or head turn starts.
    combined = list(talking["motion"])
    for sequence, strength in ((wink["motion"], 0.52), (shake["motion"], 0.28)):
        first = sequence[0]
        previous = combined[-1]
        for frame in sequence:
            aligned = {
                "exp": previous["exp"] + (frame["exp"] - first["exp"]) * strength,
                "t": previous["t"] + (frame["t"] - first["t"]) * strength,
                "scale": previous["scale"] + (frame["scale"] - first["scale"]) * strength,
                "R": (frame["R"] @ first["R"].transpose(0, 2, 1)) @ previous["R"],
            }
            combined.append(aligned)
            previous = aligned

    # Return to the talking pose so looping does not snap back at the seam.
    combined.extend(reversed(combined[1:-1]))
    payload = {
        "n_frames": len(combined),
        "output_fps": 25,
        "motion": combined,
        "c_eyes_lst": [np.zeros((1, 2), dtype=np.float32) for _ in combined],
        "c_lip_lst": [np.zeros((1, 1), dtype=np.float32) for _ in combined],
    }
    with output_path.open("wb") as handle:
        pickle.dump(payload, handle)
    return output_path


def _build_normal_motion_template() -> Path:
    driving_dir = LIVEPORTRAIT_DIR / "assets" / "examples" / "driving"
    source_path = driving_dir / "talking.pkl"
    output_path = driving_dir / "digital_human_normal.pkl"
    if output_path.exists():
        with output_path.open("rb") as handle:
            cached = pickle.load(handle)
        if "c_eyes_lst" in cached and "c_lip_lst" in cached:
            return output_path
    if not source_path.exists():
        raise PipelineError("LivePortrait 正常动作模板不完整。")
    with source_path.open("rb") as handle:
        payload = pickle.load(handle)
    count = len(payload["motion"])
    payload["c_eyes_lst"] = [np.zeros((1, 2), dtype=np.float32) for _ in range(count)]
    payload["c_lip_lst"] = [np.zeros((1, 1), dtype=np.float32) for _ in range(count)]
    with output_path.open("wb") as handle:
        pickle.dump(payload, handle)
    return output_path


def animate_portrait(
    portrait: str | Path,
    speech_audio: str | Path,
    job_path: Path,
    expression_mode: str,
    status: Callable[[str], None] | None = None,
) -> Path:
    mode = expression_mode if expression_mode in {"normal", "funny", "enhanced"} else "normal"
    if mode == "enhanced":
        template_path = _build_enhanced_motion_template()
        multiplier = "0.88"
        mode_text = "眼神与微表情增强"
    elif mode == "funny":
        template_path = _build_funny_motion_template()
        multiplier = "1.65"
        mode_text = "搞怪表情与动作"
    else:
        template_path = _build_normal_motion_template()
        multiplier = "0.72"
        mode_text = "正常表情"

    if not template_path.exists():
        raise PipelineError(f"LivePortrait 动作模板不存在：{template_path}")

    output_dir = job_path / "liveportrait"
    output_dir.mkdir(parents=True, exist_ok=True)
    _status(status, f"正在生成{mode_text}（LivePortrait）…")
    _run(
        [
            LIVEPORTRAIT_PYTHON,
            "inference.py",
            "-s",
            str(portrait),
            "-d",
            str(template_path),
            "-o",
            str(output_dir),
            "--driving-option",
            "expression-friendly",
            "--driving-multiplier",
            multiplier,
            "--animation-region",
            "all",
        ],
        cwd=LIVEPORTRAIT_DIR,
        timeout=1200,
        extra_env={"PYTHONPATH": str(LIVEPORTRAIT_DIR)},
    )
    raw_video = output_dir / f"{Path(portrait).stem}--{template_path.stem}.mp4"
    if not raw_video.exists() or raw_video.stat().st_size < 1024:
        raise PipelineError("LivePortrait 已退出，但没有生成有效视频。")

    duration = max(_audio_duration(Path(speech_audio)), 0.1)
    looped_video = output_dir / "portrait_motion.mp4"
    _run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-stream_loop",
            "-1",
            "-i",
            str(raw_video),
            "-t",
            f"{duration:.3f}",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "25",
            str(looped_video),
        ],
        timeout=300,
    )
    return looped_video


def _render_avatar_realtime(
    portrait: str | Path,
    speech_audio: str | Path,
    job_path: Path,
    status: Callable[[str], None] | None = None,
    avatar_cache_key: str | None = None,
) -> Path:
    """Render with MuseTalk's preprocessed-avatar (online) pipeline."""
    avatar_source = Path(portrait)
    # The first bytes contain the same initial motion frames for every product
    # made from this presenter, while avoiding a hash over a 300-second file.
    digest = hashlib.sha256()
    if avatar_cache_key:
        digest.update(f"realtime-avatar-v2:{avatar_cache_key}".encode("utf-8"))
    else:
        # Standalone calls do not have the original portrait hash. Hash the
        # first decoded frame rather than MP4 bytes, whose container metadata
        # changes between otherwise identical renders.
        frame = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-i", str(avatar_source),
                "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=True,
        ).stdout
        digest.update(frame)
    avatar_id = f"digital_human_{digest.hexdigest()[:20]}"
    cache_dir = MUSETALK_DIR / "results" / "v15" / "avatars" / avatar_id
    required = [
        cache_dir / "latents.pt",
        cache_dir / "coords.pkl",
        cache_dir / "mask_coords.pkl",
        cache_dir / "avator_info.json",
    ]
    prepared = all(path.exists() and path.stat().st_size > 0 for path in required)
    if cache_dir.exists() and not prepared:
        shutil.rmtree(cache_dir)

    source_for_preparation = avatar_source
    if not prepared:
        source_for_preparation = job_path / "realtime_avatar_source.mp4"
        _status(status, "在线模式首次建立主播缓存（后续商品直接复用）…")
        _run(
            [
                "ffmpeg", "-y", "-v", "error", "-i", str(avatar_source),
                "-t", "6", "-an", "-c:v", "libx264", "-preset", "veryfast",
                "-pix_fmt", "yuv420p", "-r", "25", str(source_for_preparation),
            ],
            timeout=180,
        )
    else:
        _status(status, "正在复用在线主播缓存生成口型…")

    output_name = f"digital_human_{job_path.name}_{uuid.uuid4().hex[:8]}"
    config_path = job_path / "musetalk_realtime.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                avatar_id: {
                    "video_path": str(source_for_preparation),
                    "bbox_shift": 0,
                    "preparation": not prepared,
                    "audio_clips": {output_name: str(speech_audio)},
                }
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    audio_seconds = max(_audio_duration(Path(speech_audio)), 1.0)
    expected_seconds = max(45.0, audio_seconds * 1.5 + (120.0 if not prepared else 0.0))

    def realtime_heartbeat(elapsed: float) -> None:
        percent = min(95, max(1, int(elapsed * 100 / expected_seconds)))
        mode = "缓存准备与在线推理" if not prepared else "在线推理"
        _status(status, f"MuseTalk {mode}进度 {percent}%…")

    _run(
        [
            MUSETALK_PYTHON,
            "scripts/realtime_inference.py",
            "--inference_config", str(config_path),
            "--version", "v15",
            "--unet_config", "./models/musetalkV15/musetalk.json",
            "--unet_model_path", "./models/musetalkV15/unet.pth",
            "--whisper_dir", "./models/whisper",
            "--batch_size", "20",
            "--fps", "25",
        ],
        cwd=MUSETALK_DIR,
        timeout=max(1800, min(7200, int(audio_seconds * 6 + 900))),
        extra_env={"PYTHONPATH": str(MUSETALK_DIR)},
        heartbeat=realtime_heartbeat,
        heartbeat_interval=5,
    )
    generated = cache_dir / "vid_output" / f"{output_name}.mp4"
    if not generated.exists() or generated.stat().st_size < 1024:
        raise PipelineError("MuseTalk 在线模式已退出，但没有生成有效视频。")
    output_dir = job_path / "musetalk_results" / "v15"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "digital_human.mp4"
    shutil.copy2(generated, output_path)
    _status(status, "MuseTalk 在线模式口型视频生成完成")
    return output_path


def _render_avatar_offline(
    portrait: str | Path,
    speech_audio: str | Path,
    job_path: Path,
    status: Callable[[str], None] | None = None,
) -> Path:
    avatar_source = Path(portrait)
    if avatar_source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        avatar_source = normalize_portrait(avatar_source, job_path / "portrait.png")
    config_path = job_path / "musetalk_job.yaml"
    result_dir = job_path / "musetalk_results"
    result_name = "digital_human.mp4"
    config_path.write_text(
        yaml.safe_dump(
            {
                "task_0": {
                    "video_path": str(avatar_source),
                    "audio_path": str(speech_audio),
                    "result_name": result_name,
                }
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _status(status, "数字人引擎正在生成口型视频（MuseTalk 1.5）…")
    audio_seconds = max(_audio_duration(Path(speech_audio)), 1.0)
    # Long-form (for example, 300-second) clips run substantially slower than
    # real time on a single GPU. Keep progress realistic and leave enough room
    # for MuseTalk to finish instead of discarding an almost-complete render.
    expected_seconds = max(120.0, audio_seconds * 8.0)
    musetalk_timeout = max(1800, min(14400, int(audio_seconds * 15 + 900)))

    def musetalk_heartbeat(elapsed: float) -> None:
        percent = min(95, max(1, int(elapsed * 100 / expected_seconds)))
        _status(status, f"MuseTalk 口型生成进度 {percent}%…")

    _run(
        [
            MUSETALK_PYTHON,
            "scripts/inference.py",
            "--inference_config",
            str(config_path),
            "--result_dir",
            str(result_dir),
            "--output_vid_name",
            result_name,
            "--version",
            "v15",
            "--unet_config",
            "./models/musetalkV15/musetalk.json",
            "--unet_model_path",
            "./models/musetalkV15/unet.pth",
            "--use_float16",
            "--batch_size",
            "8",
        ],
        cwd=MUSETALK_DIR,
        timeout=musetalk_timeout,
        extra_env={"PYTHONPATH": str(MUSETALK_DIR)},
        heartbeat=musetalk_heartbeat,
        heartbeat_interval=8,
    )
    output_path = result_dir / "v15" / result_name
    if not output_path.exists() or output_path.stat().st_size < 1024:
        raise PipelineError("MuseTalk 已退出，但没有生成有效视频。")
    return output_path


def render_avatar(
    portrait: str | Path,
    speech_audio: str | Path,
    job_path: Path,
    status: Callable[[str], None] | None = None,
    avatar_cache_key: str | None = None,
) -> Path:
    if os.environ.get("MUSETALK_REALTIME", "1").strip().lower() not in {"0", "false", "off"}:
        try:
            return _render_avatar_realtime(
                portrait, speech_audio, job_path, status, avatar_cache_key=avatar_cache_key
            )
        except Exception as exc:
            _status(status, f"在线模式异常，自动回退离线模式：{type(exc).__name__}")
    return _render_avatar_offline(portrait, speech_audio, job_path, status)


def run_full_pipeline(
    portrait: str,
    reference_audio: str,
    reference_text: str,
    user_audio: str | None,
    user_text: str,
    system_prompt: str,
    temperature: float,
    history: list[dict[str, str]] | None,
    consent: bool,
    expression_mode: str = "normal",
    status: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if not consent:
        raise PipelineError("请先确认你拥有照片与录音的使用授权。")
    if not portrait:
        raise PipelineError("请上传一张清晰正脸照片。")
    if not reference_audio:
        raise PipelineError("请上传一段声音参考录音。")
    if not user_audio and not (user_text or "").strip():
        raise PipelineError("请录制/上传问题语音，或直接输入问题文字。")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    job_path = JOB_DIR / uuid.uuid4().hex
    job_path.mkdir(parents=True, exist_ok=False)

    with _GPU_LOCK:
        normalized_portrait = normalize_portrait(portrait, job_path / "portrait.png")
        normalized_reference, prompt_text = prepare_voice_profile(reference_audio, reference_text, status)
        recognized_text = transcribe(user_audio, status) if user_audio else user_text.strip()
        reply = generate_reply(recognized_text, history, system_prompt, temperature, status)
        speech_path = synthesize_voice(reply, normalized_reference, prompt_text, job_path / "reply.wav", status)
        motion_video = animate_portrait(
            normalized_portrait, speech_path, job_path, expression_mode, status
        )
        video_path = render_avatar(motion_video, speech_path, job_path, status)

    next_history = list(history or [])[-8:]
    next_history.extend(
        [
            {"role": "user", "content": recognized_text},
            {"role": "assistant", "content": reply},
        ]
    )
    _status(status, "完成")
    return {
        "recognized_text": recognized_text,
        "reply": reply,
        "reference_text": prompt_text,
        "audio": str(speech_path),
        "video": str(video_path),
        "motion_video": str(motion_video),
        "expression_mode": expression_mode,
        "history": next_history,
        "job_id": job_path.name,
    }


def model_health() -> dict[str, Any]:
    checks = {
        "asr": ASR_MODEL_DIR / "model.pt",
        "tts": TTS_MODEL_DIR / "cosyvoice2.yaml",
        "llm": LLM_MODEL_DIR / "config.json",
        "expression": LIVEPORTRAIT_DIR / "pretrained_weights" / "liveportrait" / "base_models" / "appearance_feature_extractor.pth",
        "avatar": MUSETALK_DIR / "models" / "musetalkV15" / "unet.pth",
    }
    return {
        "cuda": torch.cuda.is_available(),
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "components": {name: path.exists() for name, path in checks.items()},
        "ready": all(path.exists() for path in checks.values()),
    }
