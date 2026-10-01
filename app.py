from __future__ import annotations

import os
import traceback
from pathlib import Path

# Avoid a telemetry worker delaying shutdown or startup when the server has
# restricted outbound network access.
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

import gradio as gr

from pipeline import OUTPUT_DIR, model_health, run_full_pipeline


DEFAULT_SYSTEM_PROMPT = (
    "你是一名友好、可靠的中文数字人助手。回答要自然、简洁，适合直接朗读。"
    "不要使用 Markdown 表格、代码块或过多项目符号。"
)


def health_text() -> str:
    checks = model_health()
    lines = [f"{'OK' if ok else '缺失'}  {name}" for name, ok in checks["components"].items()]
    lines.insert(0, f"{'OK' if checks['cuda'] else '缺失'}  CUDA / {checks['gpu'] or '无 GPU'}")
    ready = checks["ready"] and checks["cuda"]
    return ("系统就绪\n" if ready else "模型不完整\n") + "\n".join(lines)


def generate(
    portrait,
    reference_audio,
    reference_text,
    question_audio,
    question_text,
    expression_mode,
    consent,
    system_prompt,
    temperature,
    history,
    progress=gr.Progress(),
):
    history = history or []

    def report(message: str):
        progress(0, desc=message)

    try:
        result = run_full_pipeline(
            portrait=portrait,
            reference_audio=reference_audio,
            reference_text=reference_text or None,
            user_audio=question_audio,
            user_text=question_text,
            consent=bool(consent),
            system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT,
            temperature=float(temperature),
            history=history,
            expression_mode=expression_mode,
            status=report,
        )
        updated_history = result["history"]
        return (
            result["recognized_text"],
            result["reply"],
            result["audio"],
            result["video"],
            result["reference_text"],
            "生成成功：ASR → LLM → 声音克隆/TTS → LivePortrait 表情动作 → MuseTalk 口型已全部完成。",
            updated_history,
            updated_history,
        )
    except Exception as exc:
        traceback.print_exc()
        return (
            "",
            "",
            None,
            None,
            reference_text or "",
            f"生成失败：{type(exc).__name__}: {exc}",
            history,
            history,
        )


def clear_dialog():
    return [], []


CSS = """
.gradio-container {max-width: 1280px !important; margin: auto !important;}
.hero {text-align: center; margin: 12px 0 20px;}
.hero h1 {font-size: 2rem; margin-bottom: 6px;}
.status-box textarea {font-family: ui-monospace, monospace;}
"""


with gr.Blocks(title="本地 AI 数字人", css=CSS) as demo:
    gr.HTML(
        "<div class='hero'><h1>本地 AI 数字人</h1>"
        "<p>上传一张正面照片和一段本人授权录音，即可替换形象与声音，并可选择正常或搞怪表情动作。</p></div>"
    )

    state = gr.State([])
    with gr.Row():
        with gr.Column(scale=1):
            gr.Markdown("### 1. 设置数字人")
            portrait = gr.Image(label="形象照片（建议正脸、肩部以上）", type="filepath")
            reference_audio = gr.Audio(
                label="声音参考（3–15秒，无音乐噪声）",
                sources=["upload", "microphone"],
                type="filepath",
            )
            reference_text = gr.Textbox(
                label="参考录音文字（可选，留空则自动识别）",
                placeholder="如果填写，请和录音内容完全一致",
            )
            expression_mode = gr.Radio(
                choices=[("正常表情", "normal"), ("眼神与微表情增强", "enhanced"), ("搞怪表情与动作", "funny")],
                value="normal",
                label="表情模式",
                info="增强模式会在说话时加入低幅度眨眼、转头和微表情；搞怪模式会加入大笑、摇头和眨眼动作",
            )
            consent = gr.Checkbox(
                label="我确认已获得照片及声音所有者授权",
                value=False,
            )

        with gr.Column(scale=1):
            gr.Markdown("### 2. 输入问题")
            question_audio = gr.Audio(
                label="语音问题（可选）",
                sources=["upload", "microphone"],
                type="filepath",
            )
            question_text = gr.Textbox(
                label="文字问题（与语音二选一）",
                placeholder="例如：请用两句话介绍一下你自己",
                lines=3,
            )
            with gr.Accordion("高级设置", open=False):
                system_prompt = gr.Textbox(label="系统提示词", value=DEFAULT_SYSTEM_PROMPT, lines=3)
                temperature = gr.Slider(0.1, 1.2, value=0.7, step=0.1, label="LLM 随机性")
            generate_btn = gr.Button("生成数字人回答", variant="primary", size="lg")
            clear_btn = gr.Button("清空对话记忆")
            runtime_status = gr.Textbox(label="运行状态", lines=3, interactive=False)
            health = gr.Textbox(label="模型状态", value=health_text(), lines=6, interactive=False, elem_classes="status-box")

    gr.Markdown("### 3. 生成结果")
    with gr.Row():
        recognized = gr.Textbox(label="ASR 识别的问题", interactive=False)
        reply_text = gr.Textbox(label="LLM 回答", lines=4, interactive=False)
        detected_reference = gr.Textbox(label="声音参考文字", lines=4, interactive=False)
    with gr.Row():
        reply_audio = gr.Audio(label="克隆声音回答")
        reply_video = gr.Video(label="数字人视频")
    chatbot = gr.Chatbot(label="对话历史", type="messages", height=280)

    generate_btn.click(
        fn=generate,
        inputs=[
            portrait,
            reference_audio,
            reference_text,
            question_audio,
            question_text,
            expression_mode,
            consent,
            system_prompt,
            temperature,
            state,
        ],
        outputs=[
            recognized,
            reply_text,
            reply_audio,
            reply_video,
            detected_reference,
            runtime_status,
            state,
            chatbot,
        ],
        api_name="generate_digital_human",
    )
    clear_btn.click(fn=clear_dialog, inputs=[], outputs=[state, chatbot], queue=False)


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    port = int(os.environ.get("PORT", "7860"))
    demo.queue(default_concurrency_limit=1).launch(
        server_name="0.0.0.0",
        server_port=port,
        allowed_paths=[str(Path(OUTPUT_DIR).resolve())],
        show_error=True,
    )
