from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import format_duration, format_ts


def _speaker_map(job: dict[str, Any]) -> dict[str, dict[str, str]]:
    return {
        speaker["id"]: speaker
        for speaker in job.get("speakers", [])
    }


def _chapter_blocks(job: dict[str, Any]) -> list[dict[str, Any]]:
    paragraphs = job.get("paragraphs", [])
    chapters = sorted(job.get("chapters", []), key=lambda c: float(c.get("start", 0)))
    if not chapters:
        chapters = [{"start": 0.0, "title": "全文"}]

    blocks: list[dict[str, Any]] = []
    for i, chapter in enumerate(chapters):
        start = float(chapter.get("start", 0))
        end = (
            float(chapters[i + 1].get("start", 0))
            if i + 1 < len(chapters)
            else float("inf")
        )
        items = [
            p for p in paragraphs
            if start <= float(p.get("start", 0)) < end
        ]
        if not items and i == 0:
            items = [p for p in paragraphs if float(p.get("start", 0)) < end]
        if items:
            blocks.append(
                {
                    "start": start,
                    "end": end,
                    "title": chapter.get("title") or f"第 {i + 1} 部分",
                    "paragraphs": items,
                }
            )
    return blocks or [{"start": 0, "end": float("inf"), "title": "全文", "paragraphs": paragraphs}]


def _correction_line(job: dict[str, Any]) -> tuple[str, str] | None:
    correction = job.get("correction")
    if not isinstance(correction, dict):
        return None
    status = correction.get("status")
    applied = int(correction.get("applied") or 0)
    model = correction.get("model") or "DeepSeek"
    if status == "applied":
        return ("AI 校正：", f"已应用 {applied} 处（{model}），仅修正明显识别错误；原始转写保留在任务数据中。")
    if status == "partial":
        return ("AI 校正：", f"已应用 {applied} 处（{model}），部分批次失败；原始转写保留在任务数据中。")
    if status == "unchanged":
        return ("AI 校正：", f"已完成（{model}），未发现明显错误。")
    if status == "failed":
        return ("AI 校正：", "失败，已保留原始转写。" + (f"原因：{correction.get('error')}" if correction.get("error") else ""))
    if status == "undone":
        return ("AI 校正：", "已撤销，当前为原始转写。")
    if status == "disabled":
        return ("AI 校正：", "未启用。")
    return None


def _metadata_lines(job: dict[str, Any]) -> list[tuple[str, str]]:
    names = "；".join(s.get("name", "") for s in job.get("speakers", []))
    audio = job.get("audio", {})
    lines = [
        ("音频：", f"{audio.get('filename', '')}（总长约 {format_duration(audio.get('duration'))}）"),
        ("访谈双方：", names),
        (
            "时间戳说明：",
            "均为音频文件内的起始时间，格式为“分:秒”。同一说话人连续发言合并为一段；"
            "长回答按语义转折再分段。",
        ),
        (
            "整理说明：",
            "由 Transcriptor 自动转写并做基础文本清理；说话人、专有名词和章节标题建议人工复核。",
        ),
    ]
    correction = _correction_line(job)
    if correction:
        lines.append(correction)
    if not (job.get("settings") or {}).get("chapters", True):
        lines.append(("自动分章：", "未启用。"))
    return lines


def render_markdown(job: dict[str, Any]) -> str:
    title = job.get("title") or "访谈逐字稿"
    speakers = _speaker_map(job)
    lines: list[str] = [f"# {title}", ""]
    for label, value in _metadata_lines(job):
        lines.append(f"**{label}** {value}  ")
    lines += ["", "---", ""]

    for i, block in enumerate(_chapter_blocks(job), start=1):
        lines += [f"## {i:02d} {block['title']}", ""]
        for paragraph in block["paragraphs"]:
            speaker = speakers.get(paragraph.get("speaker_id", ""), {})
            name = speaker.get("name") or paragraph.get("speaker_name") or "说话人"
            lines += [
                f"**{name}｜{format_ts(paragraph.get('start'))}**",
                "",
                paragraph.get("text", "").strip(),
                "",
            ]
    lines += ["---", "", "*（全片完）*", ""]
    return "\n".join(lines)


def _set_cell_font(run, size: float | None = None, bold: bool | None = None, color=None) -> None:
    from docx.oxml.ns import qn
    from docx.shared import Pt

    run.font.name = "Arial"
    if size:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if color is not None:
        run.font.color.rgb = color
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:eastAsia"), "PingFang SC")


def _prepare_styles(doc) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = RGBColor(0x22, 0x22, 0x22)
    normal.paragraph_format.line_spacing = 1.15
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
    rfonts = normal.element.get_or_add_rPr().get_or_add_rFonts()
    rfonts.set(qn("w:eastAsia"), "PingFang SC")
    for key in list(rfonts.attrib):
        if "theme" in key.lower():
            del rfonts.attrib[key]

    title = styles["Title"]
    title.font.name = "Arial"
    title.font.size = Pt(20)
    title.font.bold = True
    title.font.color.rgb = RGBColor(0x11, 0x11, 0x11)
    title.paragraph_format.space_after = Pt(8)
    rfonts = title.element.get_or_add_rPr().get_or_add_rFonts()
    rfonts.set(qn("w:eastAsia"), "PingFang SC")

    heading = styles["Heading 2"]
    heading.font.name = "Arial"
    heading.font.size = Pt(14)
    heading.font.bold = True
    heading.font.color.rgb = RGBColor(0x1F, 0x38, 0x64)
    heading.paragraph_format.space_before = Pt(18)
    heading.paragraph_format.space_after = Pt(8)
    rfonts = heading.element.get_or_add_rPr().get_or_add_rFonts()
    rfonts.set(qn("w:eastAsia"), "PingFang SC")


def _add_hr(doc) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    paragraph = doc.add_paragraph()
    ppr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "CCCCCC")
    borders.append(bottom)
    ppr.append(borders)


def render_docx(job: dict[str, Any], output_path: Path) -> None:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt, RGBColor

    doc = Document()
    section = doc.sections[0]
    section.top_margin = Inches(0.8)
    section.bottom_margin = Inches(0.8)
    section.left_margin = Inches(0.85)
    section.right_margin = Inches(0.85)
    _prepare_styles(doc)

    title = job.get("title") or "访谈逐字稿"
    p = doc.add_paragraph(title, style="Title")
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT

    for label, value in _metadata_lines(job):
        paragraph = doc.add_paragraph()
        paragraph.paragraph_format.space_after = Pt(4)
        run = paragraph.add_run(label)
        _set_cell_font(run, size=10, bold=True, color=RGBColor(0x44, 0x44, 0x44))
        run = paragraph.add_run(value)
        _set_cell_font(run, size=10, bold=False, color=RGBColor(0x44, 0x44, 0x44))

    _add_hr(doc)

    speakers = _speaker_map(job)
    for i, block in enumerate(_chapter_blocks(job), start=1):
        doc.add_heading(f"{i:02d} {block['title']}", level=2)
        for paragraph in block["paragraphs"]:
            speaker = speakers.get(paragraph.get("speaker_id", ""), {})
            name = speaker.get("name") or paragraph.get("speaker_name") or "说话人"
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(8)
            p.paragraph_format.space_after = Pt(2)
            run = p.add_run(f"{name}｜{format_ts(paragraph.get('start'))}")
            _set_cell_font(run, size=10.5, bold=True, color=RGBColor(0x33, 0x33, 0x33))
            body = doc.add_paragraph(paragraph.get("text", "").strip())
            body.paragraph_format.space_after = Pt(5)

    _add_hr(doc)
    end = doc.add_paragraph()
    end.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = end.add_run("（全片完）")
    _set_cell_font(run, size=9, bold=False, color=RGBColor(0x88, 0x88, 0x88))
    run.italic = True

    doc.core_properties.title = title
    doc.core_properties.subject = "访谈逐字稿"
    doc.save(str(output_path))
