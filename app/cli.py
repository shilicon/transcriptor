"""Command-line entry point for creating and running transcription jobs."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Sequence

from .config import (
    ALLOWED_EXTENSIONS,
    DEFAULT_AI_CORRECTION,
    DEFAULT_CHAPTERS,
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_WHISPER_MODEL,
    MAX_UPLOAD_BYTES,
    WHISPER_MODELS,
)
from .download import DownloadError, normalize_youtube_url
from .job_setup import build_job, validate_job_options
from .pipeline import run_pipeline
from .secrets import set_job_secret
from .utils import job_dir, load_job, save_job, slugify


EXPORT_EXTENSIONS = ("md", "docx", "srt", "vtt")


class CLIError(RuntimeError):
    """An expected command-line validation or setup error."""


def _boolean_option(
    parser: argparse.ArgumentParser,
    name: str,
    default: bool,
    help_text: str,
) -> None:
    group = parser.add_mutually_exclusive_group()
    flag = name.replace("_", "-")
    label = help_text.rstrip("。")
    for prefix in ("启用", "使用"):
        if label.startswith(prefix):
            label = label[len(prefix):].strip()
            break
    group.add_argument(
        f"--{flag}",
        dest=name,
        action="store_true",
        help=help_text,
    )
    group.add_argument(
        f"--no-{flag}",
        dest=name,
        action="store_false",
        help=f"关闭{label}。",
    )
    parser.set_defaults(**{name: default})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transcriptor",
        description="创建并运行一次 Transcriptor 转写任务。选项与 macOS App 的“新建转写”对应。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--file",
        "--input",
        dest="file",
        type=Path,
        metavar="PATH",
        help="本地音频或视频文件。",
    )
    source.add_argument(
        "--youtube",
        "--youtube-url",
        dest="youtube",
        metavar="URL",
        help="YouTube 视频链接，只下载音频后转写。",
    )

    parser.add_argument("--title", default="", help="任务标题；不填时使用文件名或 YouTube 标题。")
    parser.add_argument(
        "--speakers",
        default="说话人 1, 说话人 2",
        help="说话人姓名，按首次发言顺序用逗号分隔。",
    )
    parser.add_argument(
        "--num-speakers",
        type=int,
        choices=range(1, 5),
        metavar="N",
        default=2,
        help="说话人数，范围 1–4。",
    )
    parser.add_argument(
        "--language",
        choices=("auto", "en", "zh"),
        default="auto",
        help="转写语言。",
    )
    parser.add_argument(
        "--model",
        choices=tuple(WHISPER_MODELS),
        default=DEFAULT_WHISPER_MODEL,
        help="语音模型。",
    )
    parser.add_argument(
        "--initial-prompt",
        default="",
        help="可选的初始提示词，例如人名、产品名或术语。",
    )
    _boolean_option(parser, "ai_correction", DEFAULT_AI_CORRECTION, "启用 AI 校正。")
    _boolean_option(parser, "chapters", DEFAULT_CHAPTERS, "启用自动分章。")

    parser.add_argument(
        "--deepseek-base-url",
        default=os.environ.get("TRANSCRIPTOR_DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL),
        help="DeepSeek API Base URL。",
    )
    parser.add_argument(
        "--deepseek-model",
        default=os.environ.get("TRANSCRIPTOR_DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL),
        help="DeepSeek 模型 ID。",
    )
    parser.add_argument(
        "--deepseek-api-key",
        "--api-key",
        dest="deepseek_api_key",
        default=os.environ.get("TRANSCRIPTOR_DEEPSEEK_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY", ""),
        help="DeepSeek API Key；也可通过 TRANSCRIPTOR_DEEPSEEK_API_KEY 提供。",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        metavar="DIR",
        help="完成后把 Markdown、Word、SRT、VTT 复制到指定目录。默认只保存在 data/jobs/<id>/。",
    )
    parser.add_argument(
        "--output-name",
        metavar="NAME",
        help="输出文件的共同文件名（不含扩展名），需配合 --output-dir 使用。",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="只输出最终 JSON 摘要，适合脚本调用。",
    )
    return parser


def _copy_input(source: Path, folder: Path) -> tuple[str, int]:
    source = source.expanduser()
    if not source.exists():
        raise CLIError(f"找不到输入文件：{source}")
    if not source.is_file():
        raise CLIError(f"输入路径不是文件：{source}")
    suffix = source.suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise CLIError(f"不支持的文件类型：{suffix or '未知'}。支持：{allowed}")
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise CLIError(f"无法读取输入文件：{source}（{exc}）") from exc
    if size <= 0:
        raise CLIError("输入文件为空。")
    if size > MAX_UPLOAD_BYTES:
        raise CLIError("输入文件超过 4 GB 限制。")

    destination = folder / f"input{suffix}"
    try:
        if source.resolve() != destination.resolve():
            shutil.copyfile(source, destination)
    except OSError as exc:
        raise CLIError(f"复制输入文件失败：{exc}") from exc
    return source.name, size


def _create_job(args: argparse.Namespace) -> str:
    try:
        validate_job_options(args.num_speakers, args.model)
    except ValueError as exc:
        raise CLIError(str(exc)) from exc

    job_id = uuid.uuid4().hex[:10]
    folder = job_dir(job_id)
    try:
        folder.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise CLIError(f"无法创建任务目录：{folder}（{exc}）") from exc

    try:
        title_from_url = False
        if args.file is not None:
            source = args.file.expanduser()
            original_name, size = _copy_input(source, folder)
            title = args.title or source.stem
            audio = {
                "filename": original_name,
                "source_filename": f"input{source.suffix.lower()}",
                "duration": None,
                "size": size,
            }
        else:
            try:
                url = normalize_youtube_url(args.youtube)
            except DownloadError as exc:
                raise CLIError(str(exc)) from exc
            title = args.title or "YouTube 视频"
            title_from_url = not args.title.strip()
            audio = {
                "filename": url,
                "source_filename": "",
                "source_url": url,
                "duration": None,
                "size": 0,
            }

        job = build_job(
            job_id=job_id,
            title=title,
            speakers=args.speakers,
            num_speakers=args.num_speakers,
            language=args.language,
            model=args.model,
            initial_prompt=args.initial_prompt,
            ai_correction=args.ai_correction,
            chapters=args.chapters,
            deepseek_base_url=args.deepseek_base_url,
            deepseek_model=args.deepseek_model,
            audio=audio,
            title_from_url=title_from_url,
        )
        if args.deepseek_api_key.strip():
            set_job_secret(job_id, "deepseek_api_key", args.deepseek_api_key.strip())
        save_job(job)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return job_id


def _run_job(job_id: str, show_progress: bool) -> dict[str, Any]:
    worker = threading.Thread(target=run_pipeline, args=(job_id,), daemon=True)
    worker.start()
    last_progress: tuple[str | None, float | None] | None = None

    while worker.is_alive():
        job = load_job(job_id)
        if show_progress and job:
            current = (job.get("stage"), job.get("progress"))
            if current != last_progress:
                print(
                    f"[{float(job.get('progress') or 0):5.1f}%] {job.get('stage') or '处理中'}",
                    flush=True,
                )
                last_progress = current
        worker.join(0.3)

    worker.join()
    job = load_job(job_id)
    if not job:
        raise CLIError(f"任务完成后找不到任务数据：{job_id}")
    if show_progress:
        current = (job.get("stage"), job.get("progress"))
        if current != last_progress:
            print(
                f"[{float(job.get('progress') or 0):5.1f}%] {job.get('stage') or '完成'}",
                flush=True,
            )
    return job


def _copy_exports(job: dict[str, Any], output_dir: Path, output_name: str | None) -> dict[str, str]:
    source_dir = job_dir(str(job["id"]))
    destination_dir = output_dir.expanduser()
    try:
        destination_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CLIError(f"无法创建输出目录：{destination_dir}（{exc}）") from exc

    raw_name = (output_name or str(job.get("title") or "transcript")).strip()
    for extension in EXPORT_EXTENSIONS:
        suffix = f".{extension}"
        if raw_name.lower().endswith(suffix):
            raw_name = raw_name[: -len(suffix)]
            break
    base_name = slugify(Path(raw_name).name, fallback="transcript")

    copied: dict[str, str] = {}
    try:
        for extension in EXPORT_EXTENSIONS:
            source = source_dir / f"transcript.{extension}"
            if not source.exists():
                continue
            destination = destination_dir / f"{base_name}.{extension}"
            shutil.copy2(source, destination)
            copied[extension] = str(destination)
    except OSError as exc:
        raise CLIError(f"复制导出文件失败：{exc}") from exc
    return copied


def _summary(job: dict[str, Any], copied: dict[str, str]) -> dict[str, Any]:
    return {
        "id": job.get("id"),
        "title": job.get("title"),
        "status": job.get("status"),
        "stage": job.get("stage"),
        "progress": job.get("progress"),
        "error": job.get("error"),
        "job_dir": str(job_dir(str(job.get("id")))),
        "exports": copied or {
            extension: str(job_dir(str(job.get("id"))) / f"transcript.{extension}")
            for extension in EXPORT_EXTENSIONS
            if (job_dir(str(job.get("id"))) / f"transcript.{extension}").exists()
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.output_name and args.output_dir is None:
            raise CLIError("--output-name 需要配合 --output-dir 使用。")
        job_id = _create_job(args)
        if not args.json:
            print(f"已创建任务 {job_id}：{load_job(job_id).get('title')}")
        job = _run_job(job_id, show_progress=not args.json)
        copied = (
            _copy_exports(job, args.output_dir, args.output_name)
            if args.output_dir and job.get("status") == "done"
            else {}
        )
        result = _summary(job, copied)
    except CLIError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已中断。任务数据仍保存在 data/jobs/ 下，可从任务目录查看状态。", file=sys.stderr)
        return 130

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    elif job.get("status") == "done":
        print(f"转写完成：{result['job_dir']}")
        for extension, path in result["exports"].items():
            print(f"  {extension}: {path}")
    else:
        print(f"转写失败：{job.get('error') or '未知错误'}", file=sys.stderr)
    return 0 if job.get("status") == "done" else 1


if __name__ == "__main__":  # pragma: no cover - exercised through the wrapper
    raise SystemExit(main())
