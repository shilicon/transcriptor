# Transcriptor

本地访谈转写应用：上传音频或视频，自动生成带说话人姓名、时间戳、章节的逐字稿，并导出 Markdown / Word。

工作目录：`/Users/skmacair/work/transcriptor`

## 功能

- 本机运行，音频不上传云端
- MLX Whisper 转写（默认 `large-v3`，可切换 `turbo`）
- Resemblyzer 声纹聚类，自动分离 1–4 位说话人
- 按说话人合并连续发言，长回答自动分段
- 自动生成基础章节；可选连接本地 LLM 生成语义化章节标题
- 可在网页中修改说话人姓名、章节标题和时间点
- 导出 Markdown 和 Word（`.docx`）

## 启动

```bash
cd /Users/skmacair/work/transcriptor
./run.sh
```

打开 `http://127.0.0.1:8765`。也可以直接双击 `Transcriptor.command`，它会启动服务并自动打开浏览器。

当前目录已经准备好 `.venv` 和 `models/whisper-large-v3`，直接运行即可。换机器时执行：

```bash
./scripts/setup.sh
```

并把 MLX Whisper 模型放到 `models/whisper-large-v3/`。模型目录应包含 `config.json` 和 `weights.npz`。

## 使用流程

1. 上传音频/视频（MP3、WAV、M4A、MP4、MOV 等）。
2. 填写标题、说话人姓名（按首次发言顺序，用逗号分隔）、说话人数和语言。
3. 开始转写。页面会显示转写、说话人分离、整理、导出等阶段。
4. 完成后：
   - 在“说话人”里修正姓名；
   - 在“章节”里编辑时间点和标题；
   - 下载 Markdown 或 Word。

## 可选：AI 自动分章

应用本身不依赖任何云端 API。要生成语义化章节标题，可以接本地 OpenAI 兼容接口。

### LM Studio

```bash
./scripts/start_llm.sh
```

这个脚本会启动 LM Studio 服务并加载 Gemma 4B。也可以手动执行：
```bash
~/.lmstudio/bin/lms server start
~/.lmstudio/bin/lms load gemma-4-e4b-it-mlx
```

在网页“章节”页填写：

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

## 数据与目录

```text
data/jobs/<job-id>/
  input.mp3
  audio.wav
  job.json
  transcript.md
  transcript.docx
```

任务、转写结果和导出文件都保存在 `data/jobs`。删除网页中的任务会同时删除该目录。

环境变量：

- `TRANSCRIPTOR_DATA_DIR`：数据目录，默认 `data`
- `TRANSCRIPTOR_MODELS_DIR`：模型目录，默认 `models`
- `TRANSCRIPTOR_LLM_BASE_URL`：默认 LLM 地址
- `TRANSCRIPTOR_LLM_MODEL`：默认 LLM 模型

## 实现结构

- `app/main.py`：FastAPI 路由、上传、任务 API
- `app/pipeline.py`：转写 → 说话人分离 → 段落整理 → 章节 → 导出
- `app/transcribe.py`：ffmpeg 提取音轨、MLX Whisper 转写
- `app/diarize.py`：Resemblyzer 声纹嵌入 + KMeans 聚类
- `app/formatting.py`：说话人合并、分段、文本清理、术语校正
- `app/chapters.py`：基础分章与 LLM 分章
- `app/exporters.py`：Markdown / DOCX 导出
- `app/static/`、`app/templates/`：前端页面

## 说明

- 当前为单机单任务队列，同一时间处理一个任务，避免多个模型同时占用内存。
- 转写质量和说话人分离效果取决于录音质量；建议在安静环境、双人分轨或近场麦克风下录制。
- 自动分章和专有名词校正属于辅助功能，正式发布前建议人工复核。
- 默认上传上限 4 GB。
