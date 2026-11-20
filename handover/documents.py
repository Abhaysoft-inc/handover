"""Reading source documents and rendering drafted ones.

Claude reads PDFs natively (text layer *and* page images), so scanned and
non-English contracts need no separate OCR step: we pass the PDF as a document
block. DOCX is converted to text; images go in as image blocks."""

from __future__ import annotations

import base64
import io
import re
from pathlib import Path

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
IMAGES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


def extract_text(data: bytes, mime: str) -> tuple[str, int, bool]:
    """Return (text layer, page count, looks_scanned)."""
    if mime == PDF:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((p.extract_text() or "") for p in reader.pages).strip()
        pages = len(reader.pages)
        return text, pages, len(text) < 40 * max(1, pages)
    if mime == DOCX:
        from docx import Document

        d = Document(io.BytesIO(data))
        return "\n".join(p.text for p in d.paragraphs).strip(), 1, False
    if mime in IMAGES:
        return "", 1, True
    if mime.startswith("text/"):
        return data.decode("utf-8", errors="replace"), 1, False
    return "", 0, False


def claude_blocks(data: bytes, mime: str, name: str) -> list[dict]:
    """Content blocks that put a source document in front of Claude."""
    if mime == PDF:
        return [{"type": "document", "title": name,
                 "source": {"type": "base64", "media_type": PDF,
                            "data": base64.standard_b64encode(data).decode("ascii")}}]
    if mime in IMAGES:
        return [{"type": "image", "source": {"type": "base64", "media_type": mime,
                                             "data": base64.standard_b64encode(data).decode("ascii")}}]
    text, _, _ = extract_text(data, mime)
    return [{"type": "document", "title": name,
             "source": {"type": "text", "media_type": "text/plain", "data": text or "(empty document)"}}]


# ── markdown → DOCX / PDF ─────────────────────────────────────────────────

_INLINE = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*)")


def _blocks(md: str) -> list[tuple[str, str]]:
    """Tiny markdown reader: headings, bullets, numbered items, rules, paragraphs."""
    out: list[tuple[str, str]] = []
    para: list[str] = []

    def flush():
        if para:
            out.append(("p", " ".join(s.strip() for s in para)))
            para.clear()

    for raw in md.splitlines():
        line = raw.rstrip()
        s = line.strip()
        if not s:
            flush()
            continue
        if s.startswith("#"):
            flush()
            level = len(s) - len(s.lstrip("#"))
            out.append((f"h{min(level, 3)}", s.lstrip("#").strip()))
        elif re.match(r"^[-*•]\s+", s):
            flush()
            out.append(("li", re.sub(r"^[-*•]\s+", "", s)))
        elif re.match(r"^\d+[.)]\s+", s):
            flush()
            out.append(("ol", s))
        elif re.fullmatch(r"[-_*]{3,}", s):
            flush()
            out.append(("hr", ""))
        elif line.endswith("  ") or s.startswith("_____"):
            para.append(s)
            flush()
        else:
            para.append(s)
    flush()
    return out


def render_docx(title: str, body_md: str) -> bytes:
    from docx import Document
    from docx.shared import Pt

    d = Document()
    d.styles["Normal"].font.name = "Calibri"
    d.styles["Normal"].font.size = Pt(11)
    for kind, text in _blocks(body_md):
        if kind in ("h1", "h2", "h3"):
            d.add_heading(text, level=int(kind[1]))
            continue
        if kind == "hr":
            d.add_paragraph("―" * 20)
            continue
        p = d.add_paragraph(style="List Bullet" if kind == "li" else None)
        for part in _INLINE.split(text):
            if not part:
                continue
            if part.startswith("**") and part.endswith("**"):
                p.add_run(part[2:-2]).bold = True
            elif part.startswith("*") and part.endswith("*") and len(part) > 2:
                p.add_run(part[1:-1]).italic = True
            else:
                p.add_run(part)
    d.core_properties.title = title
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


_FONT_CACHE: dict[str, tuple[str, str]] = {}


def _pdf_fonts(text: str) -> tuple[str, str]:
    """Pick (regular, bold) fonts able to show the draft's script."""
    from reportlab.pdfbase import pdfmetrics

    if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", text):
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont

        if "cjk" not in _FONT_CACHE:
            pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
            _FONT_CACHE["cjk"] = ("HeiseiKakuGo-W5", "HeiseiKakuGo-W5")
        return _FONT_CACHE["cjk"]
    if "latin" not in _FONT_CACHE:
        from reportlab.pdfbase.ttfonts import TTFont

        candidates = [
            (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
            (Path("/Library/Fonts/Arial.ttf"), Path("/Library/Fonts/Arial Bold.ttf")),
            (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
             Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
        ]
        chosen = ("Helvetica", "Helvetica-Bold")
        for reg, bold in candidates:
            if reg.exists() and bold.exists():
                try:
                    pdfmetrics.registerFont(TTFont("DraftSans", str(reg)))
                    pdfmetrics.registerFont(TTFont("DraftSans-Bold", str(bold)))
                    chosen = ("DraftSans", "DraftSans-Bold")
                    break
                except Exception:
                    continue
        _FONT_CACHE["latin"] = chosen
    return _FONT_CACHE["latin"]


def render_pdf(title: str, body_md: str, letterhead: str) -> bytes:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

    reg, bold = _pdf_fonts(body_md + title)

    def inline(text: str) -> str:
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = re.sub(r"\*\*([^*]+)\*\*", rf'<font name="{bold}">\1</font>', text)
        return re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", text) if reg.startswith(("Helvetica", "Draft")) \
            else text.replace("*", "")

    body = ParagraphStyle("b", fontName=reg, fontSize=10.5, leading=15, spaceAfter=7)
    styles = {
        "h1": ParagraphStyle("h1", fontName=bold, fontSize=15, leading=19, spaceAfter=10, spaceBefore=4),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=12, leading=16, spaceAfter=6, spaceBefore=8),
        "h3": ParagraphStyle("h3", fontName=bold, fontSize=10.5, leading=14, spaceAfter=4, spaceBefore=6),
    }
    head = ParagraphStyle("lh", fontName=bold, fontSize=8.5, textColor=HexColor("#1f3a5f"), leading=11)
    story = [Paragraph(inline(letterhead), head), Spacer(1, 4),
             HRFlowable(width="100%", thickness=0.6, color=HexColor("#1f3a5f")), Spacer(1, 12)]
    bullets: list = []

    def flush_bullets():
        if bullets:
            story.append(ListFlowable([ListItem(b, leftIndent=12) for b in bullets], bulletType="bullet",
                                      start="•", leftIndent=12))
            bullets.clear()

    for kind, text in _blocks(body_md):
        if kind == "li":
            bullets.append(Paragraph(inline(text), body))
            continue
        flush_bullets()
        if kind in styles:
            story.append(Paragraph(inline(text), styles[kind]))
        elif kind == "hr":
            story.append(HRFlowable(width="100%", thickness=0.4, color=HexColor("#999999")))
        else:
            story.append(Paragraph(inline(text), body))
    flush_bullets()
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm, topMargin=18 * mm,
                      bottomMargin=18 * mm, title=title).build(story)
    return buf.getvalue()


def safe_filename(text: str) -> str:
    return re.sub(r"[^\w\-. ]+", "", text, flags=re.UNICODE).strip().replace(" ", "_")[:90] or "document"
