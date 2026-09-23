# 本地 AI 数字人

这是一个可直接使用照片和录音替换形象、声音的完整版本。整条链路都在当前 GPU 服务器上运行，无需外部付费 API。

## V0 + V1.0 统一电商直播版

V1.0 已作为能力层融合进原 V0 FastAPI 服务，继续使用同一个 `v0_start.sh`、同一个 7860 端口和同一个商城直播页面。`/api/v0/*` 保持兼容，新建直播使用 `/api/v1/*` 或标准别名 `/api/live/*`。

V1.0 新增：

- 商品信息自动生成欢迎、介绍、卖点、参数、价格、优惠、场景、促单和切品话术；
- 1–30 件商品队列，每件可设置 15–3600 秒讲解时间，服务端自动轮播；
- `welcome`、`point_product`、`price`、`discount`、`size`、`recommend`、`buy_now`、`next_product`、`idle` 动作语义；
- 根据话术自动插入商品图片特写，首件商品可上传视频特写；
- 3C、食品、美妆、服装、通用五套场景，以及横屏 16:9、竖屏 9:16；
- REST API、WebSocket 状态通道、WebRTC 音视频协商，并保留 MP4 Range 播放回退；
- 商城原生页面中的形象、声音、场景、横竖屏、队列时长及开始/暂停/恢复/停止控制。
- AutoDL `/digital-human/#/live` 直接提供与 eget 商城一致的原生直播页面，不依赖本机商城进程或 iframe。

V1.0 接口：

```text
GET  /api/v1/health
POST /api/v1/live/create
GET  /api/v1/live/current
GET  /api/v1/live/{session_id}
POST /api/v1/live/{session_id}/start
POST /api/v1/live/{session_id}/pause
POST /api/v1/live/{session_id}/resume
POST /api/v1/live/{session_id}/stop
POST /api/v1/live/{session_id}/product
POST /api/v1/live/{session_id}/danmaku
POST /api/v1/live/{session_id}/webrtc/offer
WS   /ws/v1/live/{session_id}
```

自动验收与两小时稳定性测试：

```bash
python verify_v1.py <session_id>
python verify_v1_portrait.py <session_id>
python verify_v1_ten_products.py <session_id>
python verify_v1_custom_profile.py
TEST_SECONDS=7200 ./v1_soak_test.sh <session_id>
```

其中依次覆盖协议与控制、竖屏/商品视频/动作镜头、10 商品轮播、上传形象与克隆声音，以及连续 2 小时稳定运行。

## 电商直播 V0

V0 已增加独立 FastAPI 电商直播服务，入口为：

```text
http://服务器地址:7860/digital-human
```

V0 支持商品名称、图片、原价、直播价、卖点、参数、优惠信息和手工直播稿输入。也可以上传一张正面照片和一段 3–15 秒的参考录音，直接替换主播形象和声音；参考文字留空时会由 ASR 自动识别。系统串行执行 ASR、LLM、Voice Clone/TTS、LivePortrait、MuseTalk，并由 FFmpeg 合成 1280×720 的商品直播画面。最终视频包含商品图、价格牌、中文字幕和“AI 数字人”标识。

直播控制包含开始、暂停、恢复和停止，并提供“正常表情”和“搞怪表情与动作”两种模式。观众发送弹幕后，LLM 只依据当前商品资料生成回答，再用克隆声音和当前数字人形象生成一段回答视频；回答播放完会自动回到主直播画面。直播视频采用纯数字人布局，商品图和价格由商城右侧货盘展示，避免重复。商城可以使用 `/digital-human/embed?session=...` 嵌入，也可以通过 `/api/v0/*` 同源代理集成原生直播页面。同一服务仍在 `/studio` 保留原形象/声音实验室。

V0 启动：

```bash
cd /root/autodl-tmp/projects/DigitalHuman
nohup ./v0_start.sh > logs/v0_server.log 2>&1 &
echo $! > v0_server.pid
```

接口：

```text
GET  /api/v0/health
POST /api/v0/live/create
GET  /api/v0/live/current
GET  /api/v0/live/{session_id}
POST /api/v0/live/{session_id}/start
POST /api/v0/live/{session_id}/pause
POST /api/v0/live/{session_id}/resume
POST /api/v0/live/{session_id}/stop
POST /api/v0/live/{session_id}/danmaku
GET  /api/v0/live/{session_id}/video
GET  /api/v0/live/{session_id}/interactions/{interaction_id}/video
WS   /ws/v0/live/{session_id}
```

商城同源代理配置示例：

```bash
EGET_DIGITAL_HUMAN=http://127.0.0.1:7860 python server.py
```

弹幕问答与商城商品讲解优先使用 OpenAI Chat Completions 兼容接口。密钥保存在服务器的 `.env.llm`（权限 `600`），不要写入前端或提交到代码仓库：

```bash
LIVE_LLM_API_KEY=你的密钥
LIVE_LLM_BASE_URL=https://api.apikey.fan/v1
LIVE_LLM_MODEL=deepseek-v4.1-flash
```

未配置或外部接口临时失败时，服务会自动降级到本机 Qwen，直播问答仍可继续。

## 已集成能力

- ASR：SenseVoiceSmall，识别用户问题和声音参考文本。
- LLM：Qwen2.5-1.5B-Instruct，生成中文对话回答。
- Voice Clone + TTS：CosyVoice2-0.5B 零样本声音克隆。
- 表情动作引擎：LivePortrait，赋予静态照片头部、眼睛和面部表情动作。
- 口型引擎：MuseTalk 1.5，根据回答音频生成同步口型并保留表情动作。
- 表情模式：正常表情，以及带大笑、摇头、眨眼的搞怪表情与动作。
- 界面：Gradio Web UI，同时暴露 `generate_digital_human` API。

GPU 资源按 ASR → LLM → TTS → LivePortrait → MuseTalk 串行复用，适配单张 RTX 4090 24 GB。

## 启动

```bash
cd /root/autodl-tmp/projects/DigitalHuman
./start.sh
```

默认端口是 `7860`。AutoDL 需要在“自定义服务”中将外网端口映射到容器的 `7860`。

后台运行：

```bash
nohup ./start.sh > logs/server.log 2>&1 &
tail -f logs/server.log
```

当前服务的 PID 记录在 `server.pid`，健康检查：

```bash
curl -I http://127.0.0.1:7860/
```

Gradio Python 客户端调用时使用 `api_name="/generate_digital_human"`；具体入参可在页面底部的 API 说明中查看。

## 使用方法

1. 上传一张清晰正面照，建议单人、正脸、肩部以上。
2. 上传或录制 3–15 秒声音，尽量无音乐、无回声、情绪平稳。
3. 参考文字可以留空，系统会自动识别；手工填写时需与录音逐字一致。
4. 选择“正常表情”或“搞怪表情与动作”。搞怪模式会在说话时加入大笑、摇头和眨眼。
5. 上传语音问题或输入文字问题，勾选授权确认，点击生成。
6. 之后更换照片就会更换形象，更换参考录音就会更换声音。

首次运行每个阶段会载入模型，整段视频生成可能需要几分钟。输出保存在 `outputs/jobs/`。相同声音参考会按内容哈希复用预处理结果。

## 当前服务器验收环境

- 统一编排环境：`/root/autodl-tmp/envs/digitalhuman`，PyTorch 2.5.1 + CUDA 12.1。
- MuseTalk 独立环境：`/root/miniconda3/envs/szr`，PyTorch 2.0.1 + CUDA 11.8，`mmcv 2.0.1`。
- LivePortrait 环境：复用统一编排环境，动作模板来自本地官方示例并组合成平滑循环。
- 端到端验收：同一任务内完成 ASR、LLM、零样本声音克隆/TTS、LivePortrait 表情动作和 MuseTalk 1.5 口型合成。

## 安全与合规

只能使用自己的或已获明确授权的照片和声音。界面要求用户主动确认授权；请不要将生成内容用于冒充、欺诈或误导他人。

## 目录

```text
DigitalHuman/
├── app.py          # Web 界面
├── pipeline.py     # 统一编排
├── start.sh        # 启动脚本
├── logs/
└── outputs/
    ├── profiles/   # 参考声音缓存
    └── jobs/       # 每次任务的音频、视频
```
