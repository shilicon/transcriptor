# Transcriptor

原生 macOS 访谈转写应用（SwiftUI）：上传音视频或粘贴 YouTube 链接，自动生成带说话人姓名、时间戳、章节的逐字稿，并导出 Markdown / Word / SRT / VTT。

## 功能

- 音频只在本机处理，不上传云端
- `mlx-whisper` 本地转写（默认 `large-v3`，可切换 `turbo`）
- 支持本地音视频文件，或粘贴 YouTube 链接自动下载音频
- Resemblyzer 声纹聚类，自动分离 1–4 位说话人
- 按说话人合并连续发言，长回答自动分段
- **默认启用 AI 校正**：用 DeepSeek V4.1 Flash 自动修正明显 ASR 错误，不改写、不润色、不补充内容
- AI 校正可全局或按任务关闭；失败时保留原始转写，不降级到小模型
- 自动分章默认开启，也可按任务关闭
- 可在应用内修改逐字稿文字、说话人、章节标题和时间点
- 导出 Markdown、Word（`.docx`）和 SRT / VTT 字幕

## 安装与启动

首次使用先安装 Python 环境（需要 macOS Apple Silicon、`uv` 和 `ffmpeg`）：

```bash
git clone https://github.com/shilicon/transcriptor.git
cd transcriptor
./scripts/setup.sh
```

`setup.sh` 会创建 `.venv` 并安装 Python 依赖。还需要把 `mlx-whisper` 模型放到 `models/`，见下方“语音模型”。

构建并启动 App：

```bash
./macapp/build_app.sh
open Transcriptor.app
```

之后也可以双击 `Transcriptor.app` 或 `Transcriptor.command` 启动。App 是原生 SwiftUI 界面；启动后会自动拉起本地 Python 转写后端，关闭 App 时会一并停止后端。

重新构建 App 需要 Xcode / Swift 工具链。App 依赖仓库中的 `.venv` 和 `models/whisper-large-v3`，请从仓库内启动，不要单独移动 `.app`。

## 语音模型（mlx-whisper）

转写由 [`mlx-whisper`](https://github.com/ml-explore/mlx-examples/tree/main/whisper) 在 Apple Silicon 上本地完成，音频不会离开本机。模型需要是 MLX 格式：

- `large-v3`（默认）：`models/whisper-large-v3/`
- `turbo`：`models/whisper-large-v3-turbo/`

模型目录应包含 `config.json` 和权重文件（`weights.npz` 或 `weights.safetensors`）。可以从 Hugging Face 下载：

```bash
.venv/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download('mlx-community/whisper-large-v3-mlx', local_dir='models/whisper-large-v3')"
```

如果无法直接访问 Hugging Face，可以在命令前加 `HF_ENDPOINT=https://hf-mirror.com`；如果下载卡住，再设置 `HF_HUB_DISABLE_XET=1`。

如需 `turbo`：

```bash
.venv/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download('mlx-community/whisper-large-v3-turbo', local_dir='models/whisper-large-v3-turbo')"
```

## 使用流程

1. 在 App 里点击“新建转写”，选择音视频文件，或切换到 **YouTube 链接**。
2. 填写标题、说话人姓名（按首次发言顺序，用逗号分隔）、说话人数和语言。
3. 选择是否启用 **AI 校正**（默认开）和 **自动分章**（默认开）。
4. 开始转写。窗口会显示转写、说话人分离、整理、AI 校正、章节、导出等阶段。
5. 完成后：
   - 查看 AI 校正结果，必要时撤销；
   - 在“说话人”里修正姓名；
   - 在“逐字稿”里编辑文字和逐段说话人；
   - 在“章节”里编辑时间点和标题；
   - 导出 Markdown、Word 或 SRT / VTT 字幕。

## 从 YouTube 链接转写

在“新建任务”里选择 **YouTube 链接**，粘贴视频地址即可。后端会用 `yt-dlp` 只下载最佳音频流，然后走和本地文件相同的转写流程。

- **不会下载 YouTube 自带字幕**，字幕完全由本地 `mlx-whisper` 转写生成
- 支持 `youtube.com/watch`、`youtu.be`、Shorts、直播回放等单视频链接；播放列表链接只取第一个视频
- `setup.sh` 会安装 `yt-dlp`；建议同时安装 Node.js 或 Deno，供 yt-dlp 处理 YouTube 播放器挑战
- 如果视频需要登录验证，设置 `TRANSCRIPTOR_YTDLP_COOKIES` 指向导出的 cookies 文件
- 网络需要代理时，设置 `TRANSCRIPTOR_YTDLP_PROXY`；yt-dlp 也会读取 `HTTPS_PROXY`
- YouTube 风控规则经常变化，下载失败时可先升级：`uv pip install -U yt-dlp`

原生 App 从 Finder 启动时不会继承终端环境变量。需要 cookies 或代理时，可以先在终端执行 `launchctl setenv TRANSCRIPTOR_YTDLP_COOKIES /path/to/cookies.txt`（代理同理），再重新打开 App；也可以直接从终端运行 `./Transcriptor.app/Contents/MacOS/Transcriptor`。

SRT / VTT 字幕只包含转写文本，不包含说话人姓名，适合直接上传到视频平台。字幕时间点来自转写分段；在应用内修改逐字稿后，字幕文本会同步更新。

## AI 校正

默认使用 **DeepSeek V4.1 Flash API**，直接修正明显错误并应用：

- 人名、公司名、产品名
- 明显的同音词、ASR 误识别
- 标点、大小写、句子边界
- 意外重复的词

不会做：

- 改写、润色、调整语序
- 删除口语词
- 合并/拆分段落
- 增加解释或补充内容
- 修改数字、时间戳、说话人

后端会逐条校验模型返回的局部修改；不合格的修改会被丢弃。DeepSeek 不可用时，任务继续，保留原始转写并提示失败，不会回退到低能力模型。

API Key 保存在 macOS 钥匙串。在 App 菜单 → **设置 → AI 校正** 中填写：

- DeepSeek API Key
- Base URL（默认 `https://api.deepseek.com`）
- 模型 ID（默认 `deepseek-flash`，以你的 DeepSeek 账号实际可用标识为准）

校正时逐字稿文本会发送到 DeepSeek API，音频不会离开本机。

## 可选：AI 自动分章

应用本身不依赖任何云端 API。要生成语义化章节标题，可以接本地 OpenAI 兼容接口。

### LM Studio

```bash
./scripts/start_llm.sh
```

这个脚本会启动 LM Studio 服务并加载 Gemma 4B。也可以手动执行：

```bash
lms server start
lms load gemma-4-e4b-it-mlx
```

如果 `lms` 不在 `PATH` 中，请使用 LM Studio 安装目录下的 CLI。

在 App 的“章节”页填写：

- Endpoint：`http://127.0.0.1:1234/v1`（以 LM Studio 实际端口为准，应用会自动探测 1234/1235/11434）
- Model：`gemma-4-e4b`，或留空自动选择

建议使用 Gemma 这类非推理模型。Qwen 的 reasoning 模型可能把 token 全部用在思考过程上，导致章节 JSON 为空。

### Ollama

```bash
ollama serve
ollama pull qwen3
```

Endpoint 填 `http://127.0.0.1:11434/v1`，Model 填 `qwen3`。

接口不可用时，仍可使用“基础分章”，或手动编辑章节。

## 开发用 Web 界面

仓库里仍保留一套浏览器界面，仅用于开发调试：

```bash
./run.sh
# http://127.0.0.1:8765
```

原生 App 不需要打开浏览器。

## 数据与目录

```text
data/jobs/<job-id>/
  input.mp3
  audio.wav
  job.json
  transcript.md
  transcript.docx
  transcript.srt
  transcript.vtt
```

任务、转写结果和导出文件都保存在 `data/jobs`。删除 App 中的任务会同时删除该目录。

环境变量：

- `TRANSCRIPTOR_DATA_DIR`：数据目录，默认 `data`
- `TRANSCRIPTOR_MODELS_DIR`：模型目录，默认 `models`
- `TRANSCRIPTOR_LLM_BASE_URL`：AI 分章默认 LLM 地址
- `TRANSCRIPTOR_LLM_MODEL`：AI 分章默认 LLM 模型
- `TRANSCRIPTOR_DEEPSEEK_API_KEY`：DeepSeek API Key（原生 App 通常用钥匙串）
- `TRANSCRIPTOR_DEEPSEEK_BASE_URL`：DeepSeek Base URL
- `TRANSCRIPTOR_DEEPSEEK_MODEL`：DeepSeek 模型 ID
- `TRANSCRIPTOR_AI_CORRECTION`：默认是否启用 AI 校正（`1` / `0`）
- `TRANSCRIPTOR_CHAPTERS`：默认是否自动分章（`1` / `0`）
- `TRANSCRIPTOR_YTDLP_PROXY`：下载 YouTube 音频时使用的代理
- `TRANSCRIPTOR_YTDLP_COOKIES`：yt-dlp cookies 文件路径，用于需要登录的视频

## 实现结构

- `app/main.py`：FastAPI 路由、上传、YouTube 链接、任务 API
- `app/pipeline.py`：转写 → 说话人分离 → 段落整理 → 章节 → 导出
- `app/download.py`：yt-dlp 下载 YouTube 音频（不下载平台字幕）
- `app/transcribe.py`：ffmpeg 提取音轨、`mlx-whisper` 转写
- `app/diarize.py`：Resemblyzer 声纹嵌入 + KMeans 聚类
- `app/formatting.py`：说话人合并、分段、文本清理、术语校正
- `app/subtitles.py`：SRT / VTT 字幕生成与编辑同步
- `app/correction.py`：DeepSeek 自动校正、局部修改校验、原稿保留
- `app/chapters.py`：基础分章与 LLM 分章
- `app/exporters.py`：Markdown / DOCX / 字幕导出
- `app/static/`、`app/templates/`：开发用浏览器界面
- `macapp/Sources/Transcriptor/`：原生 SwiftUI App
- `macapp/build_app.sh`：构建 `Transcriptor.app`

## 测试

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## 说明

- 当前为单机单任务队列，同一时间处理一个任务，避免多个模型同时占用内存。
- 转写质量和说话人分离效果取决于录音质量；建议在安静环境、双人分轨或近场麦克风下录制。
- 自动分章和专有名词校正属于辅助功能，正式发布前建议人工复核。
- 默认上传上限 4 GB。
