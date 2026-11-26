# Transcriptor

原生 macOS 访谈转写应用（SwiftUI）：上传音视频或粘贴 YouTube 链接，自动生成带说话人姓名、时间戳、章节的逐字稿，并导出 Markdown / Word / SRT / VTT。

## 功能

- 音频只在本机处理，不上传云端
- `mlx-whisper` 本地转写（默认 `large-v3`，可切换 `turbo`）
- 支持本地音视频文件，或粘贴 YouTube 链接自动下载音频
- Resemblyzer 声纹聚类，自动分离 1–4 位说话人
- 按说话人合并连续发言，长回答使用 AI 语义分段（失败时自动回退到规则分段）
- **默认启用 AI 校正**：用 DeepSeek V4.1 Flash 自动修正明显 ASR 错误，不改写、不润色、不补充内容
- AI 校正可全局或按任务关闭；失败时保留原始转写，不降级到小模型
- 领域术语校正默认关闭，避免普通英文被误改
- 自动分章默认开启，也可按任务关闭
- 可在应用内修改逐字稿文字、说话人、章节标题和时间点
- 可用 AI 将逐字稿翻译为中文或英文，并在原文下方双语显示
- 导出 Markdown、Word（`.docx`）和 SRT / VTT 字幕，译文会同步写入导出文件

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

`build_app.sh` 会优先使用本机的 Developer ID 或 Apple Development 证书签名。稳定的签名可以让 macOS 记住钥匙串授权；如果只能使用 ad-hoc 签名，系统会在每次重建后把它当成新的 App，重新询问钥匙串密码。升级到稳定签名后，首次读取 API Key 时选择“始终允许”即可。

## 命令行使用

仓库根目录提供 `./transcriptor` 命令。它直接运行和 macOS App 相同的转写流水线，不需要先启动 Web 服务；任务完成后才返回。先查看全部选项：

```bash
./transcriptor --help
```

等价的 Python 调用是 `.venv/bin/python -m app.cli`。

本地音视频文件的完整示例：

```bash
./transcriptor \
  --file ./访谈.mp4 \
  --title "产品访谈" \
  --speakers "主持人,嘉宾" \
  --num-speakers 2 \
  --language zh \
  --model large-v3 \
  --initial-prompt "产品名：Transcriptor；人名：李明" \
  --ai-correction \
  --chapters \
  --no-glossary \
  --output-dir ./exports \
  --output-name product-interview
```

YouTube 链接使用同一组选项：

```bash
./transcriptor \
  --youtube "https://www.youtube.com/watch?v=..." \
  --title "线上访谈" \
  --speakers "主持人,嘉宾" \
  --num-speakers 2 \
  --language auto \
  --no-ai-correction \
  --chapters
```

命令行参数与 App 的“新建转写”字段对应：`--file` / `--youtube` 对应输入来源，`--title` 对应标题，`--speakers`、`--num-speakers`、`--language`、`--model` 和 `--initial-prompt` 对应表单字段，`--ai-correction` / `--no-ai-correction`、`--chapters` / `--no-chapters` 与 `--glossary` / `--no-glossary` 对应三个开关。DeepSeek 配置可以用 `--deepseek-base-url`、`--deepseek-model`、`--deepseek-api-key` 指定，也可以使用同名环境变量。

默认导出文件保存在 `data/jobs/<job-id>/`，包括 Markdown、Word、SRT 和 VTT。加上 `--output-dir` 会在完成后把四种文件复制到指定目录；`--output-name` 可以指定它们的共同文件名。需要给其他脚本调用时可以加 `--json`，命令只输出最终任务摘要。没有配置 DeepSeek API Key 时，CLI 仍会继续转写，只是跳过 AI 校正。

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
3. 选择是否启用 **AI 校正**（默认开）、**自动分章**（默认开）和 **领域术语校正**（默认关，仅专业访谈开启）。
4. 开始转写。窗口会显示转写、说话人分离、整理、AI 校正、章节、导出等阶段。
5. 完成后：
   - 查看 AI 校正结果，必要时撤销；
   - 在“说话人”里修正姓名；
   - 在“逐字稿”里编辑文字和逐段说话人，或点击“翻译”生成双语逐字稿；
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

每次转写都会保留原始识别文本和整理文本：任务中的 `raw_segments` 是不可变的 Whisper 原始分段，段落同时保留 `raw_text`（原始识别）、`formatted_text`（整理结果）和 `text`（当前文本）。撤销 AI 校正只恢复由 AI 自动修改、且之后没有被人工编辑的段落；人工修改会保留。旧任务没有原始分段时，不会把历史整理结果伪称为可恢复的原始识别文本。

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

## 逐字稿翻译

在任务详情的“逐字稿”页点击 **翻译**，选择 **简体中文** 或 **English**。

- 使用与 AI 校正相同的 API Key、Base URL 和模型 ID；任务创建时记录的模型配置优先，缺失时使用当前设置中的默认值。
- 每批译文翻译完成后会再执行一次 AI 校正，只修语法、术语、标点和明显误译，不改写、不合并、不增删段落；重新翻译会重新执行校正。
- 译文按段落保存到任务数据中，原文不被替换；在原文下方以“译”标记双语显示。
- 可以直接切换已有译文或隐藏译文，不需要重复调用 API；点击“重新翻译”会再次执行 AI 翻译和译文校正。
- 翻译期间如果逐字稿被人工修改，后端会拒绝这次结果并提示重新翻译，避免译文和原文错位。
- 手动导出 Markdown / Word / SRT / VTT 时，保存对话框会显示“包含译文”复选框；取消勾选则只导出原文，勾选时导出当前选择的译文语言。
- 导出内容不会逐句标注“译文（语言）”，只在文档元信息中列出一次译文语言。
- 任务目录里的导出文件会在翻译完成或原文修改后自动刷新，默认包含所有已有译文。

## 可选：AI 自动分章

应用本身不依赖任何云端 API。要生成语义化章节标题，可以接本地 OpenAI 兼容接口，也可以直接使用已配置的 DeepSeek API。

“章节”页的 **分章要求** 可以让用户指定分章粒度，例如“每 3–5 分钟一章，按技术主题细分”或“只按产品线分章”。不填写时默认生成 6–12 章；填写后模型会按该要求细分，最多 30 章。该要求只影响“AI 自动分章”，不会改变逐字稿内容。

### DeepSeek

先在 App 菜单 → **设置 → AI 校正** 中填写 DeepSeek API Key、Base URL 和模型 ID。然后在“章节”页把分章来源切换为 **DeepSeek**，再点“AI 自动分章”。Base URL 和模型 ID 也可以在章节页直接调整；API Key 只从 macOS 钥匙串读取。

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

首次打开 Web 界面会要求会话凭据，可从 `data/.session-token` 获取：

```bash
cat data/.session-token
```

原生 App 不需要打开浏览器，也不需要手动输入该凭据。

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

`data/.session-token` 是本地接口会话凭据，权限为 `0600`，不要分享或提交到仓库。

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
- `TRANSCRIPTOR_GLOSSARY`：默认是否启用领域术语校正（`1` / `0`，默认关闭）
- `TRANSCRIPTOR_YTDLP_PROXY`：下载 YouTube 音频时使用的代理
- `TRANSCRIPTOR_YTDLP_COOKIES`：yt-dlp cookies 文件路径，用于需要登录的视频

## 本地接口安全

后端只监听 `127.0.0.1`，并额外使用本机会话凭据保护任务接口：

- 后端启动时生成随机会话 token，写入 `data/.session-token`（权限 `0600`）
- 原生 App 通过 `X-Transcriptor-Token` 请求头携带 token
- 开发用 Web 界面需要先输入 `data/.session-token` 中的凭据，通过 `POST /api/session` 换取同源、`HttpOnly`、`SameSite=Strict` cookie；匿名访问首页不会自动获得凭据
- 所有请求校验 Host，浏览器请求额外校验 Origin；CORS 不是唯一防线
- token 和 DeepSeek API Key 不会写入任务 JSON、导出文件或 URL

这能阻止未认证的本地接口调用和跨站提交，但不能防御同一 macOS 用户下能读取 token 文件的恶意进程。

## 逐字稿分段原则

这是转写结果的硬性设计原则，后续修改必须保持：

- 每个段落的目标长度约为 **10 句话**。同一说话人的连续长发言必须切分，不能因为说话人相同就合并成一个很长的段落。
- 优先使用 **AI 语义分段**：把较长的同一说话人发言按句子拆分后交给 AI，根据话题、论点和叙事转折选择段落边界，而不是机械地每 10 句切一刀。
- AI 分段使用与 AI 校正相同的 API 配置和模型；如果任务关闭了 AI 校正、没有配置 Key、请求超时或返回的计划不合格，必须自动回退到规则分段，不能阻塞转写。
- AI 分段的硬性安全上限为 12 句。
- 段落不跨说话人；说话人切换时仍单独分段。
- Whisper 的原始分段经常在句子中间结束。生成逐字稿前必须先用 `split_segments_at_sentences()` 按句子边界切分，再交给 `build_paragraphs()`，否则句数上限会失效。
- 规则分段的上限常量为 `TARGET_SENTENCES_PER_PARAGRAPH = 10`。

实现位置：`app/segmentation.py`（AI 语义分段）、`app/formatting.py`（句子切分与规则分段）；流水线调用位置：`app/pipeline.py`。任何修改分段逻辑的提交都必须更新 `tests/test_formatting.py`、`tests/test_segmentation.py` 和流水线回归测试。

已有任务可以在“逐字稿”页点击 **重新分段**。该操作会按当前文字重新生成段落，并清除已有译文；原有的 AI 校正撤销记录也可能失效。确认对话框会明确提示这些影响。若配置了 AI，会优先使用语义分段；AI 不可用时自动回退规则分段，并在页面上显示提示。

## 实现结构

- `app/main.py`：FastAPI 路由、上传、YouTube 链接、任务 API
- `app/cli.py`：命令行转写入口，复用同一流水线
- `app/job_setup.py`：App / CLI 共用的任务构建与参数校验
- `app/security.py`：本机会话 token、Host / Origin 校验
- `app/pipeline.py`：转写 → 说话人分离 → 段落整理 → 章节 → 导出
- `app/download.py`：yt-dlp 下载 YouTube 音频（不下载平台字幕）
- `app/transcribe.py`：ffmpeg 提取音轨、`mlx-whisper` 转写
- `app/diarize.py`：Resemblyzer 声纹嵌入 + KMeans 聚类
- `app/formatting.py`：说话人合并、分段、文本清理、术语校正
- `app/subtitles.py`：SRT / VTT 字幕生成与编辑同步
- `app/correction.py`：DeepSeek 自动校正、局部修改校验、原稿保留
- `app/chapters.py`：基础分章与 LLM 分章
- `app/exporters.py`：Markdown / DOCX / 字幕导出
- `transcriptor`：命令行包装脚本
- `app/static/`、`app/templates/`：开发用浏览器界面
- `macapp/Sources/Transcriptor/`：原生 SwiftUI App
- `macapp/build_app.sh`：构建 `Transcriptor.app`

## 测试

```bash
.venv/bin/python -m unittest discover -s tests -v
./tests/run_native_probes.sh
node tests/web_state_probe.js
```

原生探针会用测试替身验证任务切换竞态和大文件上传请求体不驻留内存；Web 探针用实际 `app.js` 的处理函数验证保存乱序和分章重叠，不需要网络、模型或密钥。

## 说明

- 当前为单机单任务队列，同一时间处理一个任务，避免多个模型同时占用内存。
- 本地文件上传使用磁盘临时 multipart 文件和流式请求体，客户端不会把整个文件读入内存；服务端同样分块写入任务目录。
- 转写质量和说话人分离效果取决于录音质量；建议在安静环境、双人分轨或近场麦克风下录制。
- 自动分章和专有名词校正属于辅助功能，正式发布前建议人工复核。
- 默认上传上限 4 GB。
- 音频时长、段落和章节时间上限为 30 天；超出范围的任务会被标记为损坏，原生界面不会因时间格式转换崩溃。
