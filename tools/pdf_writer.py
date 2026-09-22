"""검증된 Markdown 보고서를 읽기 쉬운 A4 PDF로 렌더링한다."""

import re
from html import escape
from pathlib import Path

from workflow_logging import get_logger, log_operation

FONT_CANDIDATES = (
    # AppleGothic은 macOS에서 한글 표시뿐 아니라 PDF 텍스트 검색/복사도 지원한다.
    Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
    Path("/System/Library/Fonts/Supplemental/NotoSansGothic-Regular.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
)


def _font_path():
    path = next((candidate for candidate in FONT_CANDIDATES if candidate.is_file()), None)
    if path is None:
        raise FileNotFoundError("한국어 PDF 폰트가 없음: Noto Sans CJK 또는 NanumGothic 설치 필요")
    return path


def _paragraph_text(text):
    text = escape(text.replace("**", "").strip())
    # 긴 URL도 줄바꿈할 수 있도록 링크 태그로 감싼다.
    return re.sub(
        r"(https?://[^\s<]+)",
        lambda match: f'<link href="{match.group(1)}" color="#245f85">{match.group(1)}</link>',
        text,
    )


def _styles(font_name):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontName=font_name,
            fontSize=19,
            leading=27,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#173a5e"),
            spaceAfter=18,
        ),
        "chapter": ParagraphStyle(
            "ReportChapter",
            parent=base["Heading1"],
            fontName=font_name,
            fontSize=14,
            leading=20,
            textColor=colors.HexColor("#155f82"),
            spaceBefore=15,
            spaceAfter=8,
            keepWithNext=True,
        ),
        "section": ParagraphStyle(
            "ReportSection",
            parent=base["Heading2"],
            fontName=font_name,
            fontSize=11.5,
            leading=17,
            textColor=colors.HexColor("#177d91"),
            spaceBefore=11,
            spaceAfter=6,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "ReportBody",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=9.3,
            leading=14.2,
            textColor=colors.HexColor("#263442"),
            spaceAfter=5,
            wordWrap="CJK",
        ),
        "bullet": ParagraphStyle(
            "ReportBullet",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=9.1,
            leading=13.8,
            leftIndent=13,
            firstLineIndent=-7,
            textColor=colors.HexColor("#263442"),
            spaceAfter=4,
            wordWrap="CJK",
        ),
        "note": ParagraphStyle(
            "ReportNote",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=8.8,
            leading=13.5,
            leftIndent=9,
            rightIndent=9,
            borderColor=colors.HexColor("#b8d4df"),
            borderWidth=0.7,
            borderPadding=7,
            backColor=colors.HexColor("#f2f8fa"),
            textColor=colors.HexColor("#35566a"),
            spaceAfter=8,
        ),
    }


def _story(markdown, styles):
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, Spacer

    story = []
    previous_chapter = None
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("# "):
            story.append(Paragraph(_paragraph_text(line[2:]), styles["title"]))
        elif line.startswith("## "):
            heading = line[3:]
            # REFERENCE는 새 페이지에서 시작해 본문과 서지를 분리한다.
            if heading == "REFERENCE" and story:
                story.append(PageBreak())
            story.append(Paragraph(_paragraph_text(heading), styles["chapter"]))
            previous_chapter = heading
        elif line.startswith("### "):
            story.append(Paragraph(_paragraph_text(line[4:]), styles["section"]))
        elif line.startswith("> "):
            story.append(Paragraph(_paragraph_text(line[2:]), styles["note"]))
        elif line.startswith("- "):
            story.append(Paragraph("• " + _paragraph_text(line[2:]), styles["bullet"]))
        elif (re.match(r"^\d+\.\s", line) and previous_chapter == "REFERENCE") or line.startswith(
            "근거 ID:"
        ):
            story.append(Paragraph(_paragraph_text(line), styles["bullet"]))
        else:
            story.append(Paragraph(_paragraph_text(line), styles["body"]))
    story.append(Spacer(1, 3 * mm))
    return story


@log_operation("PDF_RENDER")
def render_pdf(markdown: str, output_path: Path) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    font_name = "ReportKorean"
    pdfmetrics.registerFont(TTFont(font_name, str(_font_path())))
    styles = _styles(font_name)

    document = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=19 * mm,
        bottomMargin=17 * mm,
        title="ITME와 CXL-PIM 데이터센터 적용성 비교 평가",
        author="Agentic RAG",
        subject="KV Cache 확장 기술 다관점 비교 평가",
    )

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#d7e1e6"))
        canvas.line(18 * mm, 13 * mm, A4[0] - 18 * mm, 13 * mm)
        canvas.setFont(font_name, 8)
        canvas.setFillColor(colors.HexColor("#6a7b86"))
        canvas.drawCentredString(A4[0] / 2, 8.5 * mm, str(doc.page))
        canvas.restoreState()

    document.build(_story(markdown, styles), onFirstPage=footer, onLaterPages=footer)
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError("PDF 보고서 생성 실패")
    get_logger().info("PDF_READY | path=%s | bytes=%d", output_path, output_path.stat().st_size)
    return output_path
