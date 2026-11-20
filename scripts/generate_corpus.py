"""Generate the Handover demo corpus: a deliberately messy "company Drive".

Produces sample_data/demo_drive/ (what the agent sees) and
sample_data/ground_truth.json (used only by Simulation mode and for checks).

All dates are relative to an anchor day ("today" for the demo) so the scenario
stays urgent whenever you regenerate:

    uv run python scripts/generate_corpus.py                 # anchor = today
    uv run python scripts/generate_corpus.py --anchor 2026-11-19

Formats covered: typed PDFs, image-only scanned PDFs (German, Japanese), a DOCX
offer letter; languages: English, German, Spanish, French, Japanese.
"""

from __future__ import annotations

import argparse
import calendar
import json
import math
import random
import shutil
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "sample_data"
DRIVE = OUT / "demo_drive"

COMPANY = "Aster Robotics"
CEO = "Helena Brandt"


# ── date helpers ───────────────────────────────────────────────────────────


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def add_years(d: date, years: int) -> date:
    return add_months(d, 12 * years)


EN_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December"]
DE_MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
             "August", "September", "Oktober", "November", "Dezember"]
ES_MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
FR_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
             "août", "septembre", "octobre", "novembre", "décembre"]


def en(d: date) -> str:
    return f"{EN_MONTHS[d.month - 1]} {d.day}, {d.year}"


def en_dmy(d: date) -> str:
    return f"{d.day} {EN_MONTHS[d.month - 1]} {d.year}"


def de(d: date) -> str:
    return f"{d.day}. {DE_MONTHS[d.month - 1]} {d.year}"


def es(d: date) -> str:
    return f"{d.day} de {ES_MONTHS[d.month - 1]} de {d.year}"


def fr(d: date) -> str:
    day = "1er" if d.day == 1 else str(d.day)
    return f"{day} {FR_MONTHS[d.month - 1]} {d.year}"


def ja(d: date) -> str:
    return f"{d.year}年{d.month}月{d.day}日"


def iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


# ── document model ─────────────────────────────────────────────────────────
# A document is a list of blocks: (kind, payload)
#   ("title", str) ("subtitle", str) ("h", str) ("p", str) ("space", px)
#   ("sig", [(label, signer_name_or_None), ...])  -> signature lines


@dataclass
class Doc:
    path: str                     # relative path inside the demo drive
    fmt: str                      # typed | scanned | docx
    blocks: list
    lang: str = "en"
    letterhead: str | None = None
    truth: dict = field(default_factory=dict)


# ── fonts ──────────────────────────────────────────────────────────────────

WIN_FONTS = Path("C:/Windows/Fonts")
RL_FONTS = None
try:
    import reportlab

    RL_FONTS = Path(reportlab.__file__).parent / "fonts"
except Exception:  # pragma: no cover
    pass


def find_font(candidates: list[Path]) -> Path | None:
    for c in candidates:
        if c and c.exists():
            return c
    return None


LATIN_REG = find_font([WIN_FONTS / "times.ttf", Path("/Library/Fonts/Times New Roman.ttf"),
                       Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"),
                       RL_FONTS / "Vera.ttf" if RL_FONTS else None])
LATIN_BOLD = find_font([WIN_FONTS / "timesbd.ttf", Path("/Library/Fonts/Times New Roman Bold.ttf"),
                        Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"),
                        RL_FONTS / "VeraBd.ttf" if RL_FONTS else None])
SCRIPT = find_font([WIN_FONTS / "Inkfree.ttf", WIN_FONTS / "segoesc.ttf",
                    Path("/System/Library/Fonts/Supplemental/SnellRoundhand.ttc"),
                    RL_FONTS / "VeraIt.ttf" if RL_FONTS else None])
CJK = find_font([WIN_FONTS / "msgothic.ttc", WIN_FONTS / "YuGothM.ttc",
                 Path("/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc"),
                 Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")])


# ── renderers ──────────────────────────────────────────────────────────────


def render_typed_pdf(doc: Doc, target: Path) -> None:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.lib.pagesizes import A4, LETTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    sig_font = "Times-Italic"
    if SCRIPT and SCRIPT.suffix.lower() == ".ttf":
        try:
            pdfmetrics.registerFont(TTFont("Signature", str(SCRIPT)))
            sig_font = "Signature"
        except Exception:
            pass

    pagesize = LETTER if doc.lang == "en" else A4
    body = ParagraphStyle("body", fontName="Times-Roman", fontSize=10.5, leading=14.2,
                          alignment=TA_JUSTIFY, spaceAfter=6)
    title = ParagraphStyle("title", fontName="Times-Bold", fontSize=15, leading=19,
                           alignment=TA_CENTER, spaceAfter=4)
    subtitle = ParagraphStyle("subtitle", parent=body, alignment=TA_CENTER, fontSize=10,
                              textColor=HexColor("#444444"), spaceAfter=12)
    heading = ParagraphStyle("h", fontName="Times-Bold", fontSize=11, leading=14,
                             spaceBefore=6, spaceAfter=3)
    letter = ParagraphStyle("lh", fontName="Helvetica-Bold", fontSize=9, leading=11,
                            textColor=HexColor("#1f3a5f"))

    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    story = []
    if doc.letterhead:
        story += [Paragraph(esc(doc.letterhead), letter), Spacer(1, 10)]
    for kind, payload in doc.blocks:
        if kind == "title":
            story.append(Paragraph(esc(payload), title))
        elif kind == "subtitle":
            story.append(Paragraph(esc(payload), subtitle))
        elif kind == "h":
            story.append(Paragraph(esc(payload), heading))
        elif kind == "p":
            story.append(Paragraph(esc(payload), body))
        elif kind == "space":
            story.append(Spacer(1, payload))
        elif kind == "sig":
            cells = []
            for label, signer in payload:
                sig = Paragraph(
                    f'<font name="{sig_font}" size="17" color="#1d3f8f">{esc(signer)}</font>'
                    if signer else "&nbsp;", body)
                cells.append([sig, Paragraph("_" * 34, body),
                              Paragraph(esc(label).replace("\n", "<br/>"), body)])
            cols = list(map(list, zip(*cells)))
            t = Table(cols, colWidths=[82 * mm] * len(payload))
            t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
            story += [Spacer(1, 16), t]

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont("Times-Roman", 8)
        canvas.setFillColor(HexColor("#777777"))
        canvas.drawRightString(pagesize[0] - 20 * mm, 12 * mm, f"Page {d.page}")
        canvas.restoreState()

    target.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(target), pagesize=pagesize, leftMargin=22 * mm, rightMargin=22 * mm,
                      topMargin=20 * mm, bottomMargin=20 * mm,
                      title=Path(doc.path).stem, author=COMPANY).build(
        story, onFirstPage=footer, onLaterPages=footer)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int, cjk: bool) -> list[str]:
    if cjk:
        lines, cur = [], ""
        for ch in text:
            if draw.textlength(cur + ch, font=font) > width:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        return lines + ([cur] if cur else [])
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    return lines + ([cur] if cur else [])


def _scribble(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, rnd: random.Random, color):
    pts = []
    for i in range(46):
        t = i / 45
        px = x + t * w
        py = y + math.sin(t * rnd.uniform(9, 14) + rnd.uniform(0, 3)) * rnd.uniform(6, 16) \
            - t * rnd.uniform(4, 12)
        pts.append((px, py))
    draw.line(pts, fill=color, width=3, joint="curve")


def render_scanned_pdf(doc: Doc, target: Path, seed: int) -> None:
    rnd = random.Random(seed)
    cjk = doc.lang == "ja"
    W, H, M = 1240, 1754, 118                     # A4 @150dpi
    if cjk:
        if not CJK:
            print(f"  ! skipped {doc.path}: no CJK font found on this machine")
            return
        reg = ImageFont.truetype(str(CJK), 23)
        bold = ImageFont.truetype(str(CJK), 25)
        ttl = ImageFont.truetype(str(CJK), 34)
    else:
        reg = ImageFont.truetype(str(LATIN_REG), 25)
        bold = ImageFont.truetype(str(LATIN_BOLD), 26)
        ttl = ImageFont.truetype(str(LATIN_BOLD), 34)
    sigf = ImageFont.truetype(str(SCRIPT), 40) if SCRIPT else bold

    pages: list[Image.Image] = []
    page = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(page)
    y = M

    def new_page():
        nonlocal page, draw, y
        pages.append(page)
        page = Image.new("RGB", (W, H), "white")
        draw = ImageDraw.Draw(page)
        y = M

    ink = (25, 25, 28)
    for kind, payload in doc.blocks:
        if kind in ("title", "subtitle", "h", "p"):
            font = {"title": ttl, "subtitle": reg, "h": bold, "p": reg}[kind]
            gap = {"title": 18, "subtitle": 26, "h": 6, "p": 14}[kind]
            lead = int(font.size * (1.55 if cjk else 1.38))
            for line in _wrap(draw, payload, font, W - 2 * M, cjk):
                if y + lead > H - M:
                    new_page()
                x = M
                if kind in ("title", "subtitle"):
                    x = (W - draw.textlength(line, font=font)) / 2
                draw.text((x + rnd.uniform(-0.6, 0.6), y), line, font=font, fill=ink)
                y += lead
            y += gap
        elif kind == "space":
            y += payload
        elif kind == "sig":
            if y + 210 > H - M:
                new_page()
            y += 40
            colw = (W - 2 * M) // len(payload)
            for i, (label, signer) in enumerate(payload):
                x = M + i * colw
                if signer:
                    if cjk:
                        # hanko seal
                        cx, cy, r = x + 250, y + 30, 38
                        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(196, 32, 38), width=5)
                        seal = ImageFont.truetype(str(CJK), 26)
                        draw.text((cx - 26, cy - 15), signer[:2], font=seal, fill=(196, 32, 38))
                    else:
                        draw.text((x + 10, y - 8), signer, font=sigf, fill=(30, 52, 140))
                        _scribble(draw, x + 10, y + 46, 240, rnd, (30, 52, 140))
                draw.line((x, y + 70, x + colw - 50, y + 70), fill=ink, width=2)
                for j, part in enumerate(label.split("\n")):
                    draw.text((x, y + 80 + j * 30), part, font=reg, fill=ink)
            y += 180
    pages.append(page)

    scanned = []
    mode = "RGB" if cjk else "L"          # keep colour for the red hanko seals
    for p in pages:
        # scanner artefacts: grey tone, speckles, blur, skew, uneven exposure
        p = p.convert(mode)
        px = p.load()
        for _ in range(2600):
            x, yy = rnd.randrange(W), rnd.randrange(H)
            v = rnd.randrange(90, 200)
            px[x, yy] = (v, v, v) if mode == "RGB" else v
        p = p.filter(ImageFilter.GaussianBlur(0.75))
        p = p.point(lambda v: int(v * 0.93 + 6))
        p = p.rotate(rnd.uniform(-1.3, 1.3), resample=Image.BICUBIC,
                     fillcolor=(236, 236, 236) if mode == "RGB" else 236)
        shade = Image.linear_gradient("L").resize((W, H)).point(lambda v: 255 - v // 14)
        p = Image.composite(p, Image.new(mode, (W, H), (200, 200, 200) if mode == "RGB" else 200), shade)
        scanned.append(p)

    target.parent.mkdir(parents=True, exist_ok=True)
    scanned[0].save(target, "PDF", resolution=150, save_all=True, append_images=scanned[1:],
                    quality=72)


def render_docx(doc: Doc, target: Path) -> None:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    d = Document()
    style = d.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    if doc.letterhead:
        p = d.add_paragraph()
        r = p.add_run(doc.letterhead)
        r.bold = True
        r.font.color.rgb = RGBColor(0x1F, 0x3A, 0x5F)
    for kind, payload in doc.blocks:
        if kind == "title":
            p = d.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r = p.add_run(payload)
            r.bold = True
            r.font.size = Pt(15)
        elif kind == "subtitle":
            p = d.add_paragraph(payload)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        elif kind == "h":
            p = d.add_paragraph()
            p.add_run(payload).bold = True
        elif kind == "p":
            d.add_paragraph(payload)
        elif kind == "sig":
            for label, signer in payload:
                d.add_paragraph("")
                p = d.add_paragraph()
                if signer:
                    r = p.add_run(signer)
                    r.italic = True
                    r.font.size = Pt(16)
                    r.font.color.rgb = RGBColor(0x1D, 0x3F, 0x8F)
                d.add_paragraph("_" * 32)
                d.add_paragraph(label)
    target.parent.mkdir(parents=True, exist_ok=True)
    d.core_properties.author = f"{COMPANY} India HR"
    d.save(str(target))


# ── the scenario ───────────────────────────────────────────────────────────


def clause(heading: str, original: str, english: str | None = None, page: int = 1) -> dict:
    return {"heading": heading, "original_text": original,
            "english_text": english or original, "page": page}


def build(A: date) -> tuple[list[Doc], dict]:
    docs: list[Doc] = []
    us_lh = f"{COMPANY.upper()} INC.  ·  88 Meridian Avenue, New York, NY 10013  ·  legal@aster-robotics.example"

    # 1 ── Daniel O'Connor: executive agreement, auto-renews, 90-day notice → CRITICAL
    d_deadline = A + timedelta(days=6)
    d_end = d_deadline + timedelta(days=90)
    d_start = add_years(d_end, -3) + timedelta(days=1)
    d_sign = d_start - timedelta(days=14)
    docs.append(Doc(
        path="HR/Contracts/US/OConnor_Daniel_Executive_Employment_Agreement.pdf", fmt="typed",
        letterhead=us_lh,
        blocks=[
            ("title", "EXECUTIVE EMPLOYMENT AGREEMENT"),
            ("subtitle", f"Dated {en(d_sign)}"),
            ("p", f"This Executive Employment Agreement (the \"Agreement\") is entered into as of {en(d_sign)} by and "
                  f"between Aster Robotics Inc., a Delaware corporation with offices at 88 Meridian Avenue, New York, "
                  f"NY 10013 (the \"Company\"), and Daniel O'Connor (the \"Executive\")."),
            ("h", "1. Position and Duties"),
            ("p", "The Company employs the Executive as Vice President, Sales (Americas), reporting to the Chief "
                  "Executive Officer. The Executive shall devote substantially all business time and efforts to the "
                  "Company and shall be based in the Company's New York office."),
            ("h", "2. Term; Automatic Renewal"),
            ("p", f"The term of employment shall commence on {en(d_start)} and continue until {en(d_end)} (the "
                  f"\"Initial Term\"). Thereafter, this Agreement shall automatically renew for successive twelve (12) "
                  f"month periods (each a \"Renewal Term\") unless either party delivers written notice of non-renewal "
                  f"to the other party at least ninety (90) days prior to the expiration of the then-current term."),
            ("h", "3. Compensation"),
            ("p", "(a) Base Salary. The Company shall pay the Executive a base salary of $270,000 per year, payable in "
                  "accordance with the Company's regular payroll practices. (b) Annual Bonus. The Executive shall be "
                  "eligible for an annual bonus with a target of forty percent (40%) of Base Salary, based on "
                  "objectives set by the Chief Executive Officer. (c) Equity. Equity awards are governed by a separate "
                  "stock option agreement."),
            ("h", "4. Benefits"),
            ("p", "The Executive shall be eligible for the Company's standard benefit plans and twenty (20) days of "
                  "paid time off per year."),
            ("h", "5. Termination"),
            ("p", "(a) The Company may terminate the Executive's employment for Cause at any time. (b) The Company may "
                  "terminate the Executive's employment without Cause upon thirty (30) days' written notice. "
                  "(c) Non-Renewal by the Company. A notice of non-renewal delivered by the Company under Section 2 "
                  "shall be treated as a termination without Cause for purposes of Section 6."),
            ("h", "6. Severance"),
            ("p", "Upon a termination without Cause (including non-renewal by the Company), and subject to the "
                  "Executive's execution and non-revocation of a general release of claims, the Executive shall "
                  "receive (i) continued payment of Base Salary for six (6) months and (ii) Company-paid COBRA "
                  "premiums for up to six (6) months."),
            ("h", "7. Restrictive Covenants"),
            ("p", "During employment and for twelve (12) months thereafter, the Executive shall not (a) engage in any "
                  "business competitive with the Company in North America, or (b) solicit any employee or customer "
                  "of the Company."),
            ("h", "8. Confidentiality and Inventions"),
            ("p", "The Executive shall comply with the Company's Confidential Information and Invention Assignment "
                  "Agreement, which is incorporated herein by reference."),
            ("h", "9. Notices"),
            ("p", "Notices to the Executive shall be sent to Daniel O'Connor at daniel.oconnor@example.com. Notices to "
                  "the Company shall be sent to the General Counsel at legal@aster-robotics.example."),
            ("h", "10. Governing Law"),
            ("p", "This Agreement is governed by the laws of the State of New York. Any dispute shall be resolved "
                  "exclusively in the state or federal courts located in New York County."),
            ("sig", [("Aster Robotics Inc.\nBy: Helena Brandt, Chief Executive Officer", "Helena Brandt"),
                     ("Executive\nDaniel O'Connor", "Daniel O'Connor")]),
        ],
        truth=dict(
            document_kind="employment_agreement", is_employment_related=True, language="en",
            language_name="English", is_scanned=False, title="Executive Employment Agreement",
            summary=(f"Executive agreement for Daniel O'Connor as VP Sales (Americas). Initial term ends "
                     f"{en(d_end)} and auto-renews for 12-month periods unless either side gives 90 days' notice. "
                     f"Company non-renewal is treated as termination without cause and triggers 6 months' "
                     f"severance plus COBRA."),
            employee_name="Daniel O'Connor", employee_email="daniel.oconnor@example.com",
            job_title="Vice President, Sales (Americas)", department="Sales",
            employer_entity="Aster Robotics Inc.", country="US", work_location="New York, NY",
            governing_law="New York, USA", contract_type="fixed_term",
            start_date=iso(d_start), end_date=iso(d_end), auto_renewal=True,
            renewal_terms="Auto-renews for successive 12-month terms unless either party gives written notice of "
                          "non-renewal at least 90 days before expiry.",
            notice_period_days=90,
            notice_terms="90 days' written notice of non-renewal; 30 days' notice for termination without cause.",
            notice_deadline=iso(d_deadline), probation_end_date=None,
            compensation=dict(amount=270000, currency="USD", period="year",
                              text="$270,000 base salary; 40% target bonus"),
            non_compete="12 months post-employment, North America (plus non-solicit).",
            termination_terms="For cause anytime; without cause on 30 days' notice. Company non-renewal = "
                              "termination without cause → severance.",
            amends_document=None,
            key_dates=[dict(date=iso(d_deadline), label="Last day to give notice of non-renewal"),
                       dict(date=iso(d_end), label="Initial term ends (auto-renews 12 months)")],
            key_clauses=[
                clause("2. Term; Automatic Renewal",
                       "…shall automatically renew for successive twelve (12) month periods… unless either party "
                       "delivers written notice of non-renewal… at least ninety (90) days prior to the expiration…"),
                clause("5(c) Non-Renewal by the Company",
                       "A notice of non-renewal delivered by the Company under Section 2 shall be treated as a "
                       "termination without Cause for purposes of Section 6."),
                clause("6. Severance",
                       "…continued payment of Base Salary for six (6) months and… COBRA premiums for up to six (6) "
                       "months.", page=2),
            ],
            risk_flags=[
                dict(severity="high", title="Non-renewal notice due in 6 days",
                     detail=f"Notice must reach the Executive by {en(d_deadline)} or the agreement renews to "
                            f"{en(add_years(d_end, 1))}."),
                dict(severity="medium", title="Non-renewal triggers severance",
                     detail="Company non-renewal counts as termination without cause: ~$135,000 salary "
                            "continuation plus 6 months COBRA, subject to a release."),
            ],
            recommended_action="renew",
            recommendation_rationale="Renewal avoids a ~$135k severance obligation and there is no performance "
                                     "concern on file. The Board asked Legal for a recommendation before the "
                                     "deadline, so decide this week.",
            confidence="high", signed=True,
        )))

    # 2 ── Arjun Sharma: consultancy, auto-renews 6m, 30-day notice → MISSED
    a_end = A + timedelta(days=26)
    a_deadline = a_end - timedelta(days=30)
    a_start = add_years(a_end, -1) + timedelta(days=1)
    in_lh = "ASTER ROBOTICS INDIA PRIVATE LIMITED  ·  4th Floor, Indigo Tech Park, Outer Ring Road, Bengaluru 560103"
    docs.append(Doc(
        path="HR/Contracts/APAC/Consultancy_Agreement_Arjun_Sharma.pdf", fmt="typed", letterhead=in_lh,
        blocks=[
            ("title", "CONSULTANCY AGREEMENT"),
            ("subtitle", f"Made at Bengaluru on {en_dmy(a_start - timedelta(days=7))}"),
            ("p", "This Consultancy Agreement is made between Aster Robotics India Private Limited, a company "
                  "incorporated under the Companies Act, 2013, having its registered office at 4th Floor, Indigo Tech "
                  "Park, Outer Ring Road, Bengaluru 560103 (the \"Company\"), and Mr. Arjun Sharma, an individual "
                  "residing in Bengaluru (the \"Consultant\")."),
            ("h", "1. Services"),
            ("p", "The Consultant shall provide full-stack software development services for the Company's "
                  "fleet-management platform, as directed by the Company's Engineering Manager."),
            ("h", "2. Manner of Performance"),
            ("p", "The Consultant shall perform the Services from the Company's Bengaluru office, Monday to Friday, "
                  "between 9:30 and 18:30, shall attend the team's daily stand-up meetings and shall apply for leave "
                  "in accordance with the Company's leave policy."),
            ("h", "3. Term and Renewal"),
            ("p", f"The initial term commences on {en_dmy(a_start)} and ends on {en_dmy(a_end)} (the \"Initial "
                  f"Term\"). Thereafter this Agreement shall automatically renew for successive periods of six (6) "
                  f"months unless either party gives written notice of non-renewal at least thirty (30) days before "
                  f"the end of the then-current term."),
            ("h", "4. Termination for Convenience"),
            ("p", "Either party may terminate this Agreement at any time upon sixty (60) days' prior written notice "
                  "to the other party."),
            ("h", "5. Fees"),
            ("p", "The Company shall pay the Consultant a fee of INR 2,50,000 (Rupees Two Lakh Fifty Thousand) per "
                  "month plus applicable GST, against monthly invoices payable within fifteen (15) days."),
            ("h", "6. Independent Contractor"),
            ("p", "The Consultant is an independent contractor and not an employee of the Company, and shall not be "
                  "entitled to provident fund, gratuity, or any other employee benefit."),
            ("h", "7. Intellectual Property and Confidentiality"),
            ("p", "All work product created in performing the Services is assigned to the Company. The Consultant "
                  "shall keep all Company information strictly confidential during and after the term."),
            ("h", "8. Governing Law and Dispute Resolution"),
            ("p", "This Agreement is governed by the laws of India. Disputes shall be referred to a sole arbitrator "
                  "seated in Bengaluru under the Arbitration and Conciliation Act, 1996."),
            ("h", "9. Notices"),
            ("p", "Notices to the Consultant: arjun.sharma@example.com. Notices to the Company: "
                  "legal@aster-robotics.example."),
            ("sig", [("For Aster Robotics India Pvt. Ltd.\nVikram Rao, Director", "Vikram Rao"),
                     ("Consultant\nArjun Sharma", "Arjun Sharma")]),
        ],
        truth=dict(
            document_kind="consultancy_agreement", is_employment_related=True, language="en",
            language_name="English", is_scanned=False, title="Consultancy Agreement",
            summary=(f"Consultancy agreement with Arjun Sharma for full-stack development at INR 2.5 lakh/month. "
                     f"Initial term ends {en(a_end)} and auto-renews for 6-month periods unless 30 days' notice is "
                     f"given; either side may also terminate for convenience on 60 days' notice."),
            employee_name="Arjun Sharma", employee_email="arjun.sharma@example.com",
            job_title="Full-stack Software Developer (consultant)", department="Engineering",
            employer_entity="Aster Robotics India Pvt. Ltd.", country="IN", work_location="Bengaluru, India",
            governing_law="India (arbitration, Bengaluru)", contract_type="contractor",
            start_date=iso(a_start), end_date=iso(a_end), auto_renewal=True,
            renewal_terms="Auto-renews for successive 6-month periods unless 30 days' written notice of "
                          "non-renewal.",
            notice_period_days=30,
            notice_terms="30 days' notice of non-renewal; 60 days' notice for termination for convenience.",
            notice_deadline=iso(a_deadline), probation_end_date=None,
            compensation=dict(amount=250000, currency="INR", period="month", text="INR 2,50,000 per month + GST"),
            non_compete=None,
            termination_terms="Either party may terminate for convenience on 60 days' written notice.",
            amends_document=None,
            key_dates=[dict(date=iso(a_deadline), label="Last day for non-renewal notice (passed)"),
                       dict(date=iso(a_end), label="Initial term ends; renews 6 months"),
                       dict(date=iso(add_months(a_end, 6)), label="End of first renewal period")],
            key_clauses=[
                clause("2. Manner of Performance",
                       "…from the Company's Bengaluru office, Monday to Friday, between 9:30 and 18:30, shall attend "
                       "the team's daily stand-up meetings and shall apply for leave…"),
                clause("3. Term and Renewal",
                       "…automatically renew for successive periods of six (6) months unless either party gives "
                       "written notice of non-renewal at least thirty (30) days before the end…"),
                clause("4. Termination for Convenience",
                       "Either party may terminate this Agreement at any time upon sixty (60) days' prior written "
                       "notice."),
            ],
            risk_flags=[
                dict(severity="high", title="Non-renewal window missed",
                     detail=f"The 30-day notice deadline was {en(a_deadline)}. The agreement will renew on "
                            f"{en(a_end)} to {en(add_months(a_end, 6))} unless terminated for convenience (60 days)."),
                dict(severity="high", title="Possible misclassification as contractor",
                     detail="Fixed office hours, daily stand-ups and leave approval indicate employment-like "
                            "control; risk of PF/gratuity claims and deemed employment."),
            ],
            recommended_action="terminate",
            recommendation_rationale="Non-renewal is no longer possible for this term. Either give 60 days' "
                                     "termination-for-convenience notice now, or cure the misclassification risk by "
                                     "offering an employment contract.",
            confidence="high", signed=True,
        )))

    # 3 ── Lukas Becker: German fixed-term (TzBfG), scanned, max duration reached
    l_end = A + timedelta(days=42)
    l_start = add_years(l_end, -2) + timedelta(days=1)
    l_sign = l_start - timedelta(days=24)
    docs.append(Doc(
        path="Scans/scan_0042.pdf", fmt="scanned", lang="de",
        blocks=[
            ("title", "BEFRISTETER ARBEITSVERTRAG"),
            ("subtitle", "zwischen der Aster Robotics GmbH, Torstraße 140, 10119 Berlin, vertreten durch die "
                         "Geschäftsführerin Helena Brandt – nachfolgend „Arbeitgeber“ – und Herrn Lukas Becker, "
                         "wohnhaft in Berlin – nachfolgend „Arbeitnehmer“"),
            ("h", "§ 1 Beginn und Befristung"),
            ("p", f"(1) Das Arbeitsverhältnis beginnt am {de(l_start)}. Es ist gemäß § 14 Abs. 2 TzBfG ohne "
                  f"sachlichen Grund befristet und endet mit Ablauf des {de(l_end)}, ohne dass es einer Kündigung "
                  f"bedarf."),
            ("p", "(2) Eine Verlängerung ist nur im Rahmen der gesetzlichen Höchstdauer von zwei Jahren zulässig."),
            ("h", "§ 2 Tätigkeit"),
            ("p", "Der Arbeitnehmer wird als Senior Hardware-Entwicklungsingenieur im Bereich Robotik-Hardware "
                  "eingestellt. Arbeitsort ist Berlin."),
            ("h", "§ 3 Probezeit"),
            ("p", "Die ersten sechs Monate gelten als Probezeit. Während der Probezeit kann das Arbeitsverhältnis mit "
                  "einer Frist von zwei Wochen gekündigt werden."),
            ("h", "§ 4 Vergütung"),
            ("p", "Der Arbeitnehmer erhält ein Bruttojahresgehalt von 78.000 €, zahlbar in zwölf gleichen "
                  "Monatsraten jeweils zum Monatsende."),
            ("h", "§ 5 Arbeitszeit und Urlaub"),
            ("p", "Die regelmäßige Arbeitszeit beträgt 40 Stunden pro Woche. Der Arbeitnehmer hat Anspruch auf 28 "
                  "Arbeitstage Erholungsurlaub im Kalenderjahr."),
            ("h", "§ 6 Kündigung"),
            ("p", "(1) Das Arbeitsverhältnis kann von beiden Seiten auch während der Befristung ordentlich unter "
                  "Einhaltung der gesetzlichen Kündigungsfristen (§ 622 BGB) gekündigt werden."),
            ("p", "(2) Jede Kündigung bedarf der Schriftform (§ 623 BGB); die elektronische Form ist ausgeschlossen."),
            ("h", "§ 7 Nachvertragliches Wettbewerbsverbot"),
            ("p", "Der Arbeitnehmer verpflichtet sich, für die Dauer von zwölf Monaten nach Beendigung des "
                  "Arbeitsverhältnisses nicht für ein Konkurrenzunternehmen tätig zu werden. Der Arbeitgeber zahlt "
                  "für die Dauer des Verbots eine Karenzentschädigung in Höhe von 50 % der zuletzt bezogenen "
                  "vertragsmäßigen Leistungen (§ 74 Abs. 2 HGB)."),
            ("h", "§ 8 Verschwiegenheit"),
            ("p", "Der Arbeitnehmer ist verpflichtet, über alle Betriebs- und Geschäftsgeheimnisse während und nach "
                  "dem Arbeitsverhältnis Stillschweigen zu bewahren."),
            ("h", "§ 9 Schlussbestimmungen"),
            ("p", "Änderungen und Ergänzungen dieses Vertrages bedürfen der Schriftform. Es gilt deutsches Recht. "
                  "Mitteilungen an den Arbeitnehmer: lukas.becker@example.com."),
            ("p", f"Berlin, den {de(l_sign)}"),
            ("sig", [("Arbeitgeber\nAster Robotics GmbH", "H. Brandt"), ("Arbeitnehmer\nLukas Becker", "L. Becker")]),
        ],
        truth=dict(
            document_kind="employment_agreement", is_employment_related=True, language="de",
            language_name="German", is_scanned=True, title="Befristeter Arbeitsvertrag (Fixed-term employment contract)",
            summary=(f"German fixed-term contract (no objective reason, § 14(2) TzBfG) for Lukas Becker as Senior "
                     f"Hardware Engineer in Berlin, {en(l_start)} – {en(l_end)}. The two-year statutory maximum is "
                     f"already used, so it cannot be extended again on a fixed-term basis."),
            employee_name="Lukas Becker", employee_email="lukas.becker@example.com",
            job_title="Senior Hardware Engineer", department="Robotics Hardware",
            employer_entity="Aster Robotics GmbH", country="DE", work_location="Berlin, Germany",
            governing_law="Germany", contract_type="fixed_term",
            start_date=iso(l_start), end_date=iso(l_end), auto_renewal=False,
            renewal_terms="Extension only within the 2-year statutory maximum (§ 14(2) TzBfG) — already reached.",
            notice_period_days=None,
            notice_terms="Ends automatically at term; ordinary termination possible with statutory notice "
                         "(§ 622 BGB); termination must be in wet-ink written form (§ 623 BGB).",
            notice_deadline=None, probation_end_date=iso(add_months(l_start, 6) - timedelta(days=1)),
            compensation=dict(amount=78000, currency="EUR", period="year", text="€78,000 gross per year"),
            non_compete="12 months post-contractual; compensation 50% of last remuneration (§ 74 HGB).",
            termination_terms="Statutory notice (§ 622 BGB); written form mandatory (§ 623 BGB).",
            amends_document=None,
            key_dates=[dict(date=iso(l_end), label="Fixed term ends (no further fixed-term extension possible)")],
            key_clauses=[
                clause("§ 1 Beginn und Befristung",
                       f"Es ist gemäß § 14 Abs. 2 TzBfG ohne sachlichen Grund befristet und endet mit Ablauf des "
                       f"{de(l_end)}, ohne dass es einer Kündigung bedarf.",
                       f"It is fixed-term without objective reason under § 14(2) TzBfG and ends at the close of "
                       f"{en(l_end)} without the need for notice."),
                clause("§ 6 Kündigung",
                       "Jede Kündigung bedarf der Schriftform (§ 623 BGB); die elektronische Form ist ausgeschlossen.",
                       "Any termination must be in written form (§ 623 BGB); electronic form is excluded."),
                clause("§ 7 Nachvertragliches Wettbewerbsverbot",
                       "…für die Dauer von zwölf Monaten… Karenzentschädigung in Höhe von 50 %…",
                       "…for twelve months… compensation of 50% of the last contractual remuneration…", page=2),
            ],
            risk_flags=[
                dict(severity="high", title="Statutory maximum for fixed-term reached",
                     detail="Two years without objective reason is the § 14(2) TzBfG maximum; another fixed-term "
                            "extension would be invalid and create an indefinite contract."),
                dict(severity="medium", title="Continued work after end date = indefinite contract",
                     detail="If Lukas keeps working after the end date with the employer's knowledge, the contract "
                            "becomes indefinite by law (§ 15(6) TzBfG)."),
                dict(severity="medium", title="Costly post-contractual non-compete",
                     detail="The 12-month non-compete obliges ~€39,000 compensation unless waived in writing "
                            "before the employment ends (§ 75a HGB)."),
            ],
            recommended_action="convert_to_permanent",
            recommendation_rationale="Senior hardware engineer in a critical role; the fixed-term route is "
                                     "exhausted. Convert to indefinite employment (the EMEA hiring freeze exempts "
                                     "retention of critical engineering roles), or let it end and waive the "
                                     "non-compete in writing to avoid ~€39k compensation.",
            confidence="high", signed=True,
        )))

    # 4 ── Maya Chen: fixed-term + amendment that moves the deadline
    m_end = A + timedelta(days=57)
    m_deadline = m_end - timedelta(days=30)
    m_orig_end = add_months(m_end, -6)
    m_start = add_years(m_orig_end, -1) + timedelta(days=1)
    m_sign = m_start - timedelta(days=10)
    m_amend = m_orig_end - timedelta(days=25)
    tx_lh = f"{COMPANY.upper()} INC.  ·  Austin Office, 2100 Lakeshore Drive, Austin, TX 78702"
    docs.append(Doc(
        path="HR/Contracts/US/Chen_Maya_Fixed_Term_Employment_Agreement.pdf", fmt="typed", letterhead=tx_lh,
        blocks=[
            ("title", "FIXED-TERM EMPLOYMENT AGREEMENT"),
            ("subtitle", f"Dated {en(m_sign)}"),
            ("p", "This Fixed-Term Employment Agreement (the \"Agreement\") is made between Aster Robotics Inc., a "
                  "Delaware corporation (the \"Company\"), and Maya Chen (the \"Employee\")."),
            ("h", "1. Position"),
            ("p", "The Employee shall serve as Senior Machine Learning Engineer on the Perception team, reporting to "
                  "the Director of Autonomy, at the Company's Austin, Texas office."),
            ("h", "2. Term"),
            ("p", f"The term of employment commences on {en(m_start)} and ends on {en(m_orig_end)} (the \"Term\"), "
                  f"unless extended by mutual written agreement. The Company shall notify the Employee in writing of "
                  f"its intention to extend or not to extend the Term at least thirty (30) days before the end of the "
                  f"Term."),
            ("h", "3. Compensation"),
            ("p", "The Employee shall receive a base salary of $172,000 per year and shall be eligible for an annual "
                  "performance bonus of up to fifteen percent (15%) of base salary."),
            ("h", "4. Early Termination"),
            ("p", "Either party may terminate this Agreement before the end of the Term on thirty (30) days' written "
                  "notice. The Company may terminate immediately for cause."),
            ("h", "5. Non-Solicitation"),
            ("p", "For twelve (12) months after the end of employment, the Employee shall not solicit any employee of "
                  "the Company to leave the Company."),
            ("h", "6. Confidentiality and Inventions"),
            ("p", "The Employee shall keep confidential all non-public information of the Company and assigns to the "
                  "Company all inventions made in the course of employment."),
            ("h", "7. Governing Law; Notices"),
            ("p", "This Agreement is governed by the laws of the State of Texas. Notices to the Employee: "
                  "maya.chen@example.com."),
            ("sig", [("Aster Robotics Inc.\nBy: Helena Brandt, CEO", "Helena Brandt"), ("Employee\nMaya Chen", "Maya Chen")]),
        ],
        truth=dict(
            document_kind="employment_agreement", is_employment_related=True, language="en",
            language_name="English", is_scanned=False, title="Fixed-Term Employment Agreement",
            summary=(f"Fixed-term agreement for Maya Chen, Senior ML Engineer (Austin), {en(m_start)} – "
                     f"{en(m_orig_end)} at $172,000. Extension by mutual written agreement; the Company must state "
                     f"its intention 30 days before the end of the Term."),
            employee_name="Maya Chen", employee_email="maya.chen@example.com",
            job_title="Senior Machine Learning Engineer", department="Autonomy / Perception",
            employer_entity="Aster Robotics Inc.", country="US", work_location="Austin, TX",
            governing_law="Texas, USA", contract_type="fixed_term",
            start_date=iso(m_start), end_date=iso(m_orig_end), auto_renewal=False,
            renewal_terms="Extension only by mutual written agreement; Company to give 30 days' notice of its "
                          "intention to extend or not.",
            notice_period_days=30, notice_terms="30 days' notice of intention to extend / not extend; 30 days' "
                                                "notice for early termination.",
            notice_deadline=iso(m_orig_end - timedelta(days=30)), probation_end_date=None,
            compensation=dict(amount=172000, currency="USD", period="year", text="$172,000 base; up to 15% bonus"),
            non_compete=None, termination_terms="30 days' notice by either party; immediate for cause.",
            amends_document=None,
            key_dates=[dict(date=iso(m_orig_end), label="Original end of Term (later amended)")],
            key_clauses=[clause("2. Term",
                                "The Company shall notify the Employee in writing of its intention to extend or not "
                                "to extend the Term at least thirty (30) days before the end of the Term.")],
            risk_flags=[dict(severity="low", title="End date superseded",
                             detail="This original end date was extended by Amendment No. 1.")],
            recommended_action="review",
            recommendation_rationale="Read together with Amendment No. 1, which extends the Term.",
            confidence="high", signed=True,
        )))
    docs.append(Doc(
        path="HR/Contracts/US/Chen_Maya_Amendment_No1.pdf", fmt="typed", letterhead=tx_lh,
        blocks=[
            ("title", "AMENDMENT NO. 1 TO FIXED-TERM EMPLOYMENT AGREEMENT"),
            ("subtitle", f"Effective {en(m_amend)}"),
            ("p", f"This Amendment No. 1 (the \"Amendment\") is made effective as of {en(m_amend)} between Aster "
                  f"Robotics Inc. (the \"Company\") and Maya Chen (the \"Employee\") and amends the Fixed-Term "
                  f"Employment Agreement between them dated {en(m_sign)} (the \"Agreement\")."),
            ("h", "1. Extension of Term"),
            ("p", f"Section 2 of the Agreement is amended so that the Term ends on {en(m_end)}."),
            ("h", "2. Base Salary"),
            ("p", f"Effective {en(m_amend)}, the Employee's base salary is increased to $185,000 per year."),
            ("h", "3. Other Terms"),
            ("p", "Except as expressly amended, the Agreement remains in full force and effect. The notice provisions "
                  "of Section 2 apply to the extended Term."),
            ("sig", [("Aster Robotics Inc.\nBy: Helena Brandt, CEO", "Helena Brandt"), ("Employee\nMaya Chen", "Maya Chen")]),
        ],
        truth=dict(
            document_kind="amendment", is_employment_related=True, language="en", language_name="English",
            is_scanned=False, title="Amendment No. 1 to Fixed-Term Employment Agreement",
            summary=(f"Extends Maya Chen's fixed term to {en(m_end)} and raises base salary to $185,000 from "
                     f"{en(m_amend)}. The 30-day notice-of-intention clause applies to the extended Term."),
            employee_name="Maya Chen", employee_email=None, job_title="Senior Machine Learning Engineer",
            department="Autonomy / Perception", employer_entity="Aster Robotics Inc.", country="US",
            work_location="Austin, TX", governing_law="Texas, USA", contract_type="fixed_term",
            start_date=None, end_date=iso(m_end), auto_renewal=False,
            renewal_terms="Extension by mutual written agreement (unchanged).", notice_period_days=30,
            notice_terms="30 days' notice of intention to extend / not extend (applies to extended Term).",
            notice_deadline=iso(m_deadline), probation_end_date=None,
            compensation=dict(amount=185000, currency="USD", period="year", text="$185,000 base salary"),
            non_compete=None, termination_terms=None,
            amends_document=f"Fixed-Term Employment Agreement dated {en(m_sign)} (Maya Chen)",
            key_dates=[dict(date=iso(m_deadline), label="Notify intention to extend / not extend"),
                       dict(date=iso(m_end), label="Extended Term ends")],
            key_clauses=[clause("1. Extension of Term",
                                f"Section 2 of the Agreement is amended so that the Term ends on {en(m_end)}.")],
            risk_flags=[],
            recommended_action="renew",
            recommendation_rationale="Strong performer (raise granted mid-term); extend for 12 months if the "
                                     "Perception roadmap continues.",
            confidence="high", signed=True,
        )))

    # 5 ── Sofía Martínez: Spanish fixed-term "circunstancias de la producción", 18 months (> legal max)
    s_end = A + timedelta(days=101)
    s_deadline = s_end - timedelta(days=15)
    s_start = add_months(s_end, -18) + timedelta(days=1)
    s_sign = s_start - timedelta(days=5)
    docs.append(Doc(
        path="HR/Contracts/EMEA/Contrato_Martinez_Sofia.pdf", fmt="typed", lang="es",
        letterhead="ASTER ROBOTICS ESPAÑA, S.L.  ·  Calle de Serrano 41, 28001 Madrid  ·  CIF B-87654321",
        blocks=[
            ("title", "CONTRATO DE TRABAJO DE DURACIÓN DETERMINADA"),
            ("subtitle", f"En Madrid, a {es(s_sign)}"),
            ("h", "REUNIDOS"),
            ("p", "De una parte, Aster Robotics España, S.L., con CIF B-87654321 y domicilio en Calle de Serrano 41, "
                  "28001 Madrid, representada por D.ª Helena Brandt en calidad de administradora (en adelante, «la "
                  "Empresa»). De otra parte, D.ª Sofía Martínez García, mayor de edad (en adelante, «la "
                  "Trabajadora»)."),
            ("h", "PRIMERA.– Objeto"),
            ("p", "La Trabajadora prestará sus servicios como Responsable de Marketing (Marketing Manager), grupo "
                  "profesional 1, en el centro de trabajo de Madrid."),
            ("h", "SEGUNDA.– Modalidad y duración"),
            ("p", f"El presente contrato se celebra por circunstancias de la producción, al amparo del artículo 15.2 "
                  f"del Estatuto de los Trabajadores, para atender el incremento ocasional de actividad derivado del "
                  f"lanzamiento comercial en el mercado ibérico. Tendrá una duración de dieciocho (18) meses, desde el "
                  f"{es(s_start)} hasta el {es(s_end)}."),
            ("h", "TERCERA.– Periodo de prueba"),
            ("p", "Se establece un periodo de prueba de dos (2) meses."),
            ("h", "CUARTA.– Retribución"),
            ("p", "La Trabajadora percibirá una retribución de 52.000 € brutos anuales, distribuidos en catorce (14) "
                  "pagas."),
            ("h", "QUINTA.– Jornada"),
            ("p", "La jornada será de 40 horas semanales, de lunes a viernes."),
            ("h", "SEXTA.– Extinción"),
            ("p", "A la finalización del contrato, la parte que formule la denuncia deberá notificarlo a la otra con "
                  "una antelación mínima de quince (15) días. A la extinción por expiración del tiempo convenido, la "
                  "Trabajadora tendrá derecho a una indemnización de doce (12) días de salario por cada año de "
                  "servicio."),
            ("h", "SÉPTIMA.– Convenio colectivo"),
            ("p", "Será de aplicación el Convenio colectivo de oficinas y despachos de la Comunidad de Madrid."),
            ("h", "OCTAVA.– Legislación y notificaciones"),
            ("p", "El presente contrato se rige por la legislación española. Notificaciones a la Trabajadora: "
                  "sofia.martinez@example.com."),
            ("sig", [("La Empresa\nAster Robotics España, S.L.", "Helena Brandt"), ("La Trabajadora\nSofía Martínez García", "Sofía Martínez")]),
        ],
        truth=dict(
            document_kind="employment_agreement", is_employment_related=True, language="es",
            language_name="Spanish", is_scanned=False,
            title="Contrato de trabajo de duración determinada (Fixed-term employment contract)",
            summary=(f"Spanish fixed-term contract 'por circunstancias de la producción' for Sofía Martínez, "
                     f"Marketing Manager in Madrid, for 18 months ({en(s_start)} – {en(s_end)}) at €52,000 in 14 "
                     f"payments. 15 days' notice of expiry; 12 days' salary per year on expiry."),
            employee_name="Sofía Martínez García", employee_email="sofia.martinez@example.com",
            job_title="Marketing Manager", department="Marketing",
            employer_entity="Aster Robotics España, S.L.", country="ES", work_location="Madrid, Spain",
            governing_law="Spain", contract_type="fixed_term",
            start_date=iso(s_start), end_date=iso(s_end), auto_renewal=False,
            renewal_terms="No renewal clause; fixed 18-month term.", notice_period_days=15,
            notice_terms="15 days' notice before expiry (art. 49.1.c ET).",
            notice_deadline=iso(s_deadline), probation_end_date=iso(add_months(s_start, 2) - timedelta(days=1)),
            compensation=dict(amount=52000, currency="EUR", period="year", text="€52,000 gross in 14 payments"),
            non_compete=None,
            termination_terms="Expiry with 15 days' notice; severance of 12 days' salary per year of service.",
            amends_document=None,
            key_dates=[dict(date=iso(s_deadline), label="Last day to give 15 days' notice of expiry"),
                       dict(date=iso(s_end), label="Contract ends")],
            key_clauses=[
                clause("SEGUNDA.– Modalidad y duración",
                       "…por circunstancias de la producción, al amparo del artículo 15.2 del Estatuto de los "
                       "Trabajadores… Tendrá una duración de dieciocho (18) meses…",
                       "…for production circumstances under article 15.2 of the Workers' Statute… for a duration of "
                       "eighteen (18) months…"),
                clause("SEXTA.– Extinción",
                       "…deberá notificarlo a la otra con una antelación mínima de quince (15) días…",
                       "…must notify the other party at least fifteen (15) days in advance…"),
            ],
            risk_flags=[dict(severity="high", title="Term exceeds legal maximum for this contract type",
                             detail="Since the 2022 labour reform, art. 15.2 ET contracts may last up to 6 months "
                                    "(12 if the collective agreement allows). An 18-month term risks the contract "
                                    "being deemed indefinite (fijo).")],
            recommended_action="convert_to_permanent",
            recommendation_rationale="The contract is likely already treated as indefinite; letting it 'expire' "
                                     "risks an unfair-dismissal claim. Regularise by converting to an indefinite "
                                     "contract (or take local advice before any exit).",
            confidence="medium", signed=True,
        )))

    # 6 ── Camille Dubois: French CDD, NOT signed by the employee
    c_end = A + timedelta(days=132)
    c_start = add_months(c_end, -12) + timedelta(days=1)
    c_sign = c_start - timedelta(days=3)
    docs.append(Doc(
        path="HR/Contracts/EMEA/CDD_Dubois_Camille.pdf", fmt="typed", lang="fr",
        letterhead="ASTER ROBOTICS SAS  ·  18 rue de la Pépinière, 75008 Paris  ·  RCS Paris 912 345 678",
        blocks=[
            ("title", "CONTRAT DE TRAVAIL À DURÉE DÉTERMINÉE"),
            ("subtitle", "Entre les soussignés"),
            ("p", "La société Aster Robotics SAS, au capital de 50 000 €, immatriculée au RCS de Paris sous le "
                  "n° 912 345 678, dont le siège est situé 18 rue de la Pépinière, 75008 Paris, représentée par "
                  "Mme Helena Brandt, Présidente, ci-après « l'Employeur », et Mme Camille Dubois, ci-après « la "
                  "Salariée »."),
            ("h", "Article 1 – Motif du recours"),
            ("p", "Le présent contrat est conclu pour faire face à un accroissement temporaire d'activité lié au "
                  "lancement de la plateforme Atlas (article L.1242-2 du Code du travail)."),
            ("h", "Article 2 – Durée"),
            ("p", f"Le contrat prend effet le {fr(c_start)} et prend fin le {fr(c_end)}. Il pourra être renouvelé "
                  f"deux fois par avenant signé avant son terme, sans que la durée totale, renouvellements inclus, ne "
                  f"dépasse dix-huit (18) mois."),
            ("h", "Article 3 – Période d'essai"),
            ("p", "Le contrat comporte une période d'essai d'un (1) mois."),
            ("h", "Article 4 – Fonctions"),
            ("p", "La Salariée est engagée en qualité de Designer Produit Senior, statut cadre, position 2.2 de la "
                  "convention collective Syntec."),
            ("h", "Article 5 – Rémunération"),
            ("p", "La Salariée percevra une rémunération brute mensuelle de 4 200 €."),
            ("h", "Article 6 – Indemnité de fin de contrat"),
            ("p", "À l'issue du contrat, la Salariée percevra une indemnité de fin de contrat égale à 10 % de la "
                  "rémunération totale brute perçue, sauf si la relation contractuelle se poursuit par un contrat à "
                  "durée indéterminée."),
            ("h", "Article 7 – Rupture anticipée"),
            ("p", "Le contrat ne peut être rompu avant l'échéance du terme qu'en cas d'accord des parties, de faute "
                  "grave, de force majeure, d'inaptitude constatée par le médecin du travail ou d'embauche en CDI."),
            ("h", "Article 8 – Droit applicable et notifications"),
            ("p", "Le présent contrat est soumis au droit français. Notifications à la Salariée : "
                  "camille.dubois@example.com."),
            ("p", f"Fait à Paris, le {fr(c_sign)}, en deux exemplaires."),
            ("sig", [("L'Employeur\nAster Robotics SAS", "Helena Brandt"), ("La Salariée\nCamille Dubois", None)]),
        ],
        truth=dict(
            document_kind="employment_agreement", is_employment_related=True, language="fr",
            language_name="French", is_scanned=False,
            title="Contrat de travail à durée déterminée — CDD (Fixed-term employment contract)",
            summary=(f"French CDD for Camille Dubois, Senior Product Designer in Paris, {en(c_start)} – {en(c_end)}, "
                     f"for a temporary increase in activity. Renewable twice by signed amendment (max 18 months). 10% "
                     f"end-of-contract allowance. The employee signature line is blank."),
            employee_name="Camille Dubois", employee_email="camille.dubois@example.com",
            job_title="Senior Product Designer", department="Design",
            employer_entity="Aster Robotics SAS", country="FR", work_location="Paris, France",
            governing_law="France", contract_type="fixed_term",
            start_date=iso(c_start), end_date=iso(c_end), auto_renewal=False,
            renewal_terms="Renewable twice by amendment signed before the term ends; 18 months maximum in total.",
            notice_period_days=None,
            notice_terms="Ends automatically at term; renewal requires an avenant signed before the end date.",
            notice_deadline=None, probation_end_date=iso(add_months(c_start, 1) - timedelta(days=1)),
            compensation=dict(amount=4200, currency="EUR", period="month", text="€4,200 gross per month"),
            non_compete=None,
            termination_terms="Early termination only by agreement, gross misconduct, force majeure, unfitness or "
                              "permanent hire elsewhere.",
            amends_document=None,
            key_dates=[dict(date=iso(c_end), label="CDD ends — renewal avenant must be signed before this date")],
            key_clauses=[
                clause("Article 2 – Durée",
                       "Il pourra être renouvelé deux fois par avenant signé avant son terme, sans que la durée "
                       "totale… ne dépasse dix-huit (18) mois.",
                       "It may be renewed twice by an amendment signed before its term, with a total duration not "
                       "exceeding eighteen (18) months."),
                clause("Article 6 – Indemnité de fin de contrat",
                       "…une indemnité de fin de contrat égale à 10 % de la rémunération totale brute perçue…",
                       "…an end-of-contract allowance equal to 10% of total gross remuneration received…", page=2),
            ],
            risk_flags=[dict(severity="high", title="CDD not signed by the employee",
                             detail="A CDD without the employee's signature can be requalified as a permanent "
                                    "contract (CDI) by French courts. Obtain a signed copy or regularise now.")],
            recommended_action="review",
            recommendation_rationale="Fix the missing signature first; then decide between a renewal avenant (max "
                                     "18 months total) and conversion to CDI, which also avoids the 10% allowance.",
            confidence="high", signed=False,
        )))

    # 7 ── Yuki Tanaka: Japanese fixed-term, scanned, 30-day non-renewal notice
    y_end = A + timedelta(days=132)
    y_start = add_years(y_end, -1) + timedelta(days=1)
    y_first = add_years(y_start, -2)
    y_deadline = y_end - timedelta(days=30)
    y_sign = y_start - timedelta(days=20)
    docs.append(Doc(
        path="HR/Contracts/APAC/雇用契約書_田中由紀.pdf", fmt="scanned", lang="ja",
        blocks=[
            ("title", "有期雇用契約書"),
            ("subtitle", "アスター・ロボティクス株式会社（以下「甲」という。）と田中由紀（以下「乙」という。）は、"
                         "次のとおり有期雇用契約を締結する。"),
            ("h", "第1条（契約期間）"),
            ("p", f"1. 契約期間は、{ja(y_start)}から{ja(y_end)}までとする。"),
            ("p", f"2. 乙の初回雇入れ日は{ja(y_first)}とし、本契約は2回目の更新契約である。"),
            ("h", "第2条（契約の更新）"),
            ("p", "1. 本契約は、更新する場合があり得る。"),
            ("p", "2. 更新の有無は、契約期間満了時の業務量、乙の勤務成績・態度・能力及び会社の経営状況により判断する。"),
            ("p", "3. 甲は、本契約を更新しない場合には、少なくとも契約期間満了日の30日前までに乙に対してその旨を予告する。"),
            ("h", "第3条（就業の場所及び業務内容）"),
            ("p", "就業場所：東京都港区芝浦三丁目4番1号　アスター・ロボティクス株式会社 東京オフィス"),
            ("p", "業務内容：営業部長として日本国内における営業活動の統括"),
            ("h", "第4条（賃金）"),
            ("p", "基本給　月額850,000円。毎月末日締め、翌月25日支払とする。"),
            ("h", "第5条（労働時間及び休日）"),
            ("p", "始業午前9時、終業午後6時（休憩1時間）。休日は土曜日、日曜日、国民の祝日とする。"),
            ("h", "第6条（退職に関する事項）"),
            ("p", "1. 乙が自己の都合により退職する場合は、少なくとも30日前に申し出るものとする。"),
            ("p", "2. 解雇の事由及び手続は、就業規則の定めるところによる。"),
            ("h", "第7条（無期転換）"),
            ("p", "通算契約期間が5年を超える場合、乙の申込みにより期間の定めのない労働契約に転換される（労働契約法第18条）。"),
            ("h", "第8条（その他）"),
            ("p", "本契約に定めのない事項は、甲の就業規則及び労働関係法令による。乙の連絡先：yuki.tanaka@example.com"),
            ("p", f"本契約の成立を証するため本書2通を作成し、甲乙記名押印の上、各1通を保有する。　{ja(y_sign)}"),
            ("sig", [("甲：アスター・ロボティクス株式会社\n代表取締役　ヘレナ・ブラント", "代表"),
                     ("乙：田中由紀", "田中")]),
        ],
        truth=dict(
            document_kind="employment_agreement", is_employment_related=True, language="ja",
            language_name="Japanese", is_scanned=True, title="有期雇用契約書 (Fixed-term employment contract)",
            summary=(f"Japanese fixed-term contract for Yuki Tanaka, Sales Director (Tokyo), {en(y_start)} – "
                     f"{en(y_end)} at ¥850,000/month. Second renewal since first hire on {en(y_first)}; the company "
                     f"must give 30 days' advance notice if it will not renew."),
            employee_name="Yuki Tanaka (田中由紀)", employee_email="yuki.tanaka@example.com",
            job_title="Sales Director, Japan (営業部長)", department="Sales",
            employer_entity="Aster Robotics K.K.", country="JP", work_location="Tokyo, Japan",
            governing_law="Japan", contract_type="fixed_term",
            start_date=iso(y_start), end_date=iso(y_end), auto_renewal=False,
            renewal_terms="May be renewed, based on workload, performance and business conditions.",
            notice_period_days=30, notice_terms="Company must give 30 days' advance notice of non-renewal.",
            notice_deadline=iso(y_deadline), probation_end_date=None,
            compensation=dict(amount=850000, currency="JPY", period="month", text="¥850,000 base salary per month"),
            non_compete=None, termination_terms="Dismissal per work rules; employee resignation on 30 days' notice.",
            amends_document=None,
            key_dates=[dict(date=iso(y_deadline), label="Last day to give 30 days' notice of non-renewal"),
                       dict(date=iso(y_end), label="Contract period ends"),
                       dict(date=iso(add_years(y_first, 5)), label="5-year conversion right (Labour Contract Act §18)")],
            key_clauses=[
                clause("第2条（契約の更新）3",
                       "甲は、本契約を更新しない場合には、少なくとも契約期間満了日の30日前までに乙に対してその旨を予告する。",
                       "If the Company will not renew this contract, it shall notify the Employee at least 30 days "
                       "before the contract end date."),
                clause("第7条（無期転換）",
                       "通算契約期間が5年を超える場合、乙の申込みにより期間の定めのない労働契約に転換される。",
                       "If the total contract period exceeds 5 years, the contract converts to an indefinite one at "
                       "the Employee's request.", page=2),
            ],
            risk_flags=[dict(severity="low", title="Expectation of renewal",
                             detail="Two prior renewals create a reasonable expectation of renewal; non-renewal "
                                    "needs objectively reasonable grounds (Labour Contract Act §19).")],
            recommended_action="renew",
            recommendation_rationale="Senior revenue role with renewals on record; renew for another year and "
                                     "track the 5-year conversion date.",
            confidence="high", signed=True,
        )))

    # 8 ── Emily Johnson: California at-will with void non-compete
    e_start = add_months(A, -32)
    docs.append(Doc(
        path="HR/Contracts/US/Johnson_Emily_Employment_Agreement.pdf", fmt="typed",
        letterhead=f"{COMPANY.upper()} INC.  ·  3200 Alder Way, Palo Alto, CA 94304",
        blocks=[
            ("title", "EMPLOYMENT AGREEMENT"),
            ("subtitle", f"Dated {en(e_start - timedelta(days=21))}"),
            ("p", "This Employment Agreement is made between Aster Robotics Inc., a Delaware corporation (the "
                  "\"Company\"), and Emily Johnson (the \"Employee\")."),
            ("h", "1. Position and Start Date"),
            ("p", f"The Employee will serve as Director of People Operations at the Company's Palo Alto, California "
                  f"office, starting on {en(e_start)}."),
            ("h", "2. At-Will Employment"),
            ("p", "Employment with the Company is at-will. Either the Employee or the Company may terminate the "
                  "employment relationship at any time, with or without cause or advance notice."),
            ("h", "3. Compensation"),
            ("p", "The Employee will receive a base salary of $198,000 per year, subject to standard withholdings."),
            ("h", "4. Non-Competition"),
            ("p", "For a period of twelve (12) months following the termination of employment for any reason, the "
                  "Employee shall not, directly or indirectly, engage in or be employed by any business that competes "
                  "with the Company anywhere in the United States."),
            ("h", "5. Non-Solicitation"),
            ("p", "For twelve (12) months following termination, the Employee shall not solicit any Company employee "
                  "to leave the Company."),
            ("h", "6. Confidentiality and Inventions"),
            ("p", "The Employee shall sign the Company's Confidential Information and Invention Assignment Agreement, "
                  "including the notice required by California Labor Code section 2870."),
            ("h", "7. Arbitration"),
            ("p", "Any dispute arising out of the employment shall be resolved by final and binding arbitration "
                  "administered by JAMS. The parties waive any right to bring claims on a class or collective basis."),
            ("h", "8. Governing Law; Notices"),
            ("p", "This Agreement is governed by the laws of the State of California. Notices to the Employee: "
                  "emily.johnson@example.com."),
            ("sig", [("Aster Robotics Inc.\nBy: Helena Brandt, CEO", "Helena Brandt"), ("Employee\nEmily Johnson", "Emily Johnson")]),
        ],
        truth=dict(
            document_kind="employment_agreement", is_employment_related=True, language="en",
            language_name="English", is_scanned=False, title="Employment Agreement (at-will)",
            summary=(f"At-will employment of Emily Johnson as Director of People Operations in Palo Alto since "
                     f"{en(e_start)} at $198,000. Contains a 12-month nationwide non-compete, which is void under "
                     f"California law."),
            employee_name="Emily Johnson", employee_email="emily.johnson@example.com",
            job_title="Director of People Operations", department="People",
            employer_entity="Aster Robotics Inc.", country="US", work_location="Palo Alto, CA",
            governing_law="California, USA", contract_type="at_will",
            start_date=iso(e_start), end_date=None, auto_renewal=False, renewal_terms=None,
            notice_period_days=None, notice_terms="At-will; no notice required.", notice_deadline=None,
            probation_end_date=None,
            compensation=dict(amount=198000, currency="USD", period="year", text="$198,000 base salary"),
            non_compete="12 months post-employment, anywhere in the United States (void in California).",
            termination_terms="At-will.", amends_document=None, key_dates=[],
            key_clauses=[clause("4. Non-Competition",
                                "For a period of twelve (12) months following the termination of employment… shall "
                                "not… engage in or be employed by any business that competes with the Company "
                                "anywhere in the United States.")],
            risk_flags=[dict(severity="high", title="Void non-compete still in a California contract",
                             detail="Cal. Bus. & Prof. Code §16600 voids employee non-competes, and §16600.1 required "
                                    "employers to notify affected employees that such clauses are void (deadline "
                                    "Feb 14, 2024). Keeping or enforcing it exposes the company to a private right "
                                    "of action and attorney's fees.")],
            recommended_action="amend",
            recommendation_rationale="Amend to remove the non-compete (keep confidentiality and non-solicit of "
                                     "employees narrowly drawn) and send the §16600.1 notice now.",
            confidence="high", signed=True,
        )))

    # 9 ── Priya Nair: India offer letter (DOCX), probation ends soon
    p_prob_end = A + timedelta(days=11)
    p_join = add_months(p_prob_end, -6) + timedelta(days=1)
    p_offer = p_join - timedelta(days=30)
    docs.append(Doc(
        path="HR/Offer Letters/Priya_Nair_Offer_Letter.docx", fmt="docx",
        letterhead="ASTER ROBOTICS INDIA PRIVATE LIMITED — 4th Floor, Indigo Tech Park, Outer Ring Road, Bengaluru 560103",
        blocks=[
            ("p", f"Date: {en_dmy(p_offer)}"),
            ("p", "Ms. Priya Nair\nEmail: priya.nair@example.com"),
            ("title", "Offer of Employment – Data Analyst"),
            ("p", "Dear Priya,"),
            ("p", "We are pleased to offer you the position of Data Analyst in our Analytics team at Bengaluru, on "
                  "the following terms and conditions:"),
            ("h", "1. Date of Joining"),
            ("p", f"Your date of joining will be {en_dmy(p_join)}."),
            ("h", "2. Compensation"),
            ("p", "Your annual cost to company (CTC) will be INR 18,00,000 (Rupees Eighteen Lakhs only), as detailed "
                  "in Annexure A."),
            ("h", "3. Probation"),
            ("p", f"You will be on probation for six (6) months from your date of joining, i.e., until "
                  f"{en_dmy(p_prob_end)}. On successful completion, your employment will be confirmed in writing. The "
                  f"Company may, at its discretion, extend the probation period by up to three (3) months by written "
                  f"notice issued before the end of the probation period."),
            ("h", "4. Notice Period"),
            ("p", "During probation, either party may terminate the employment with fifteen (15) days' written "
                  "notice. After confirmation, the notice period is sixty (60) days, or salary in lieu thereof."),
            ("h", "5. Working Hours and Location"),
            ("p", "40 hours per week, Monday to Friday, at our Bengaluru office."),
            ("h", "6. Confidentiality and Intellectual Property"),
            ("p", "You will sign the Company's standard confidentiality and IP assignment undertaking."),
            ("h", "7. Governing Law"),
            ("p", "This offer is governed by the laws of India; the courts at Bengaluru have exclusive jurisdiction."),
            ("p", "Please sign and return a copy of this letter as your acceptance."),
            ("sig", [("For Aster Robotics India Pvt. Ltd.\nVikram Rao, Director", "Vikram Rao"),
                     (f"Accepted — Priya Nair, {en_dmy(p_offer + timedelta(days=2))}", "Priya Nair")]),
        ],
        truth=dict(
            document_kind="offer_letter", is_employment_related=True, language="en", language_name="English",
            is_scanned=False, title="Offer of Employment – Data Analyst",
            summary=(f"Offer letter for Priya Nair, Data Analyst in Bengaluru, joining {en(p_join)} at INR 18 lakh "
                     f"CTC. Six-month probation ends {en(p_prob_end)}; it can be extended by up to 3 months only by "
                     f"written notice before that date."),
            employee_name="Priya Nair", employee_email="priya.nair@example.com", job_title="Data Analyst",
            department="Analytics", employer_entity="Aster Robotics India Pvt. Ltd.", country="IN",
            work_location="Bengaluru, India", governing_law="India", contract_type="indefinite",
            start_date=iso(p_join), end_date=None, auto_renewal=False, renewal_terms=None,
            notice_period_days=15, notice_terms="15 days during probation; 60 days after confirmation.",
            notice_deadline=iso(p_prob_end), probation_end_date=iso(p_prob_end),
            compensation=dict(amount=1800000, currency="INR", period="year", text="INR 18,00,000 CTC per year"),
            non_compete=None, termination_terms="15 days' notice in probation; 60 days after confirmation.",
            amends_document=None,
            key_dates=[dict(date=iso(p_prob_end), label="Probation ends — confirm or extend in writing")],
            key_clauses=[clause("3. Probation",
                                "The Company may, at its discretion, extend the probation period by up to three (3) "
                                "months by written notice issued before the end of the probation period.")],
            risk_flags=[dict(severity="medium", title="Probation decision due",
                             detail=f"Probation ends {en(p_prob_end)}. Without a written extension before then, "
                                    f"she is treated as confirmed with a 60-day notice period.")],
            recommended_action="confirm_probation",
            recommendation_rationale="No performance concerns on file; issue the written confirmation before the "
                                     "probation end date.",
            confidence="high", signed=True,
        )))

    # 10 ── distractors and context documents
    lease_end = A + timedelta(days=223)
    docs.append(Doc(
        path="Legal/Real Estate/Office_Lease_Berlin_Mitte.pdf", fmt="typed", lang="de",
        letterhead="MITTE OFFICE PROPERTIES GMBH & CO. KG — Commercial Lease (English convenience version)",
        blocks=[
            ("title", "COMMERCIAL LEASE AGREEMENT"),
            ("subtitle", "Premises: Torstraße 140, 3rd floor, 10119 Berlin"),
            ("p", "Landlord: Mitte Office Properties GmbH & Co. KG. Tenant: Aster Robotics GmbH."),
            ("h", "§ 3 Lease Term"),
            ("p", f"The lease term ends on {en(lease_end)}. The Tenant has an option to extend the lease by three (3) "
                  f"years, exercisable by written notice received by the Landlord no later than six (6) months before "
                  f"expiry."),
            ("h", "§ 4 Rent"),
            ("p", "Monthly base rent: €18,400 plus service charges and VAT, indexed annually to the German CPI."),
            ("sig", [("Landlord", "M. Keller"), ("Tenant — Aster Robotics GmbH", "Helena Brandt")]),
        ],
        truth=dict(document_kind="lease", is_employment_related=False, language="en", language_name="English",
                   is_scanned=False, title="Commercial Lease Agreement — Berlin office",
                   summary=f"Berlin office lease ending {en(lease_end)}; 3-year extension option requires notice "
                           f"6 months before expiry.")))
    docs.append(Doc(
        path="Legal/Vendor NDAs/Mutual_NDA_Quantico_Sensors.pdf", fmt="typed",
        blocks=[
            ("title", "MUTUAL NON-DISCLOSURE AGREEMENT"),
            ("p", f"This Mutual Non-Disclosure Agreement is entered into on {en(A - timedelta(days=300))} between Aster "
                  f"Robotics Inc. and Quantico Sensors LLC to evaluate a potential lidar supply partnership."),
            ("h", "1. Confidential Information"),
            ("p", "Each party shall protect the other's Confidential Information with at least reasonable care and use "
                  "it solely for the purpose of evaluating the partnership."),
            ("h", "2. Term"),
            ("p", "This Agreement remains in effect for two (2) years from the effective date; confidentiality "
                  "obligations survive for three (3) years thereafter."),
            ("h", "3. Governing Law"),
            ("p", "This Agreement is governed by the laws of the State of Delaware."),
            ("sig", [("Aster Robotics Inc.", "Helena Brandt"), ("Quantico Sensors LLC", "R. Okafor")]),
        ],
        truth=dict(document_kind="nda", is_employment_related=False, language="en", language_name="English",
                   is_scanned=False, title="Mutual NDA — Quantico Sensors",
                   summary="Vendor mutual NDA for a lidar supply evaluation; not an employment document.")))
    docs.append(Doc(
        path="Finance/Invoices/Invoice_INV-20931.pdf", fmt="typed",
        letterhead="NORTHBEAM CLOUD SERVICES LTD.",
        blocks=[
            ("title", "INVOICE INV-20931"),
            ("p", f"Invoice date: {en(A - timedelta(days=20))}. Bill to: Aster Robotics Inc., Accounts Payable."),
            ("p", "GPU training cluster (October): $11,200.00. Object storage: $1,280.00. Total due: $12,480.00 "
                  "within 30 days."),
        ],
        truth=dict(document_kind="invoice", is_employment_related=False, language="en", language_name="English",
                   is_scanned=False, title="Invoice INV-20931 (Northbeam Cloud Services)",
                   summary="Cloud services invoice for $12,480; not an employment document.")))
    docs.append(Doc(
        path="Board/Board_Minutes_2026_Q3.pdf", fmt="typed", letterhead=us_lh,
        blocks=[
            ("title", "MINUTES OF THE BOARD OF DIRECTORS"),
            ("subtitle", f"Aster Robotics Inc. — Q3 meeting held {en(A - timedelta(days=50))}"),
            ("h", "1. Financial update"),
            ("p", "The CFO presented the Q3 results. Revenue grew 38% year over year; cash runway is 26 months."),
            ("h", "2. Headcount"),
            ("p", "The Board approved a hiring freeze for EMEA through Q2 2027, with exceptions for the retention of "
                  "critical engineering roles, which require CEO approval."),
            ("h", "3. Executive compensation"),
            ("p", f"The Board noted that the VP Sales employment agreement renews automatically unless notice of "
                  f"non-renewal is delivered by {en(d_deadline)}, and asked management, together with Legal, to present "
                  f"a recommendation before that date."),
            ("h", "4. Legal"),
            ("p", f"The General Counsel role has been vacant since the summer. The new in-house counsel starts on "
                  f"{en(A - timedelta(days=3))} and will review all employment agreements as a first priority."),
        ],
        truth=dict(document_kind="board_minutes", is_employment_related=False, language="en",
                   language_name="English", is_scanned=False, title="Board minutes — Q3 2026",
                   summary=f"Board approved an EMEA hiring freeze (critical engineering retention exempt) and asked "
                           f"Legal for a recommendation on the VP Sales renewal before {en(d_deadline)}.")))
    docs.append(Doc(
        path="HR/Policies/Employee_Handbook_2026.pdf", fmt="typed", letterhead=us_lh,
        blocks=[
            ("title", "EMPLOYEE HANDBOOK 2026 (EXCERPT)"),
            ("h", "7. Separations"),
            ("p", "Terminations and non-renewals require approval by the CEO and the Head of People. Every termination "
                  "or non-renewal letter must be reviewed by Legal before it is sent. Notice periods follow the "
                  "employment contract and local law, whichever is longer."),
            ("h", "8. Contract renewals"),
            ("p", "Managers must request renewals of fixed-term contracts at least 60 days before the contract end "
                  "date. HR keeps the signed original of every renewal in the employee file."),
        ],
        truth=dict(document_kind="policy", is_employment_related=False, language="en", language_name="English",
                   is_scanned=False, title="Employee Handbook 2026 (excerpt)",
                   summary="Separations need CEO + Head of People approval and Legal review of every letter; "
                           "renewal requests due 60 days before contract end.")))

    contracts = [
        dict(employee_name="Daniel O'Connor", primary="HR/Contracts/US/OConnor_Daniel_Executive_Employment_Agreement.pdf",
             supporting=["Board/Board_Minutes_2026_Q3.pdf"], effective_end_date=iso(d_end),
             action_deadline=iso(d_deadline),
             action_deadline_reason="Last day to deliver 90-day notice of non-renewal; otherwise renews 12 months.",
             current_compensation_text="$270,000 base + 40% target bonus",
             status_note="Board minutes ask Legal for a recommendation before the notice deadline."),
        dict(employee_name="Arjun Sharma", primary="HR/Contracts/APAC/Consultancy_Agreement_Arjun_Sharma.pdf",
             supporting=[], effective_end_date=iso(a_end), action_deadline=iso(a_deadline),
             action_deadline_reason="30-day non-renewal window closed; renews for 6 months on the end date. "
                                    "60-day termination for convenience still available.",
             current_compensation_text="INR 2,50,000 per month + GST",
             status_note=f"Missed: renews to {en(add_months(a_end, 6))}. Next non-renewal deadline "
                         f"{en(add_months(a_end, 6) - timedelta(days=30))}."),
        dict(employee_name="Lukas Becker", primary="Scans/scan_0042.pdf", supporting=[],
             effective_end_date=iso(l_end), action_deadline=iso(l_end),
             action_deadline_reason="Fixed term ends; decide (convert or exit) before the last working day.",
             current_compensation_text="€78,000 gross per year", status_note="Found as an unnamed scan."),
        dict(employee_name="Maya Chen", primary="HR/Contracts/US/Chen_Maya_Fixed_Term_Employment_Agreement.pdf",
             supporting=["HR/Contracts/US/Chen_Maya_Amendment_No1.pdf"], effective_end_date=iso(m_end),
             action_deadline=iso(m_deadline),
             action_deadline_reason="Amendment No. 1 moved the end date; 30-day notice of intention applies.",
             current_compensation_text="$185,000 base (after Amendment No. 1)",
             status_note="Original agreement looks expired; the amendment extends it."),
        dict(employee_name="Sofía Martínez García", primary="HR/Contracts/EMEA/Contrato_Martinez_Sofia.pdf",
             supporting=[], effective_end_date=iso(s_end), action_deadline=iso(s_deadline),
             action_deadline_reason="15 days' notice before expiry (art. 49.1.c ET).",
             current_compensation_text="€52,000 gross in 14 payments", status_note=None),
        dict(employee_name="Camille Dubois", primary="HR/Contracts/EMEA/CDD_Dubois_Camille.pdf", supporting=[],
             effective_end_date=iso(c_end), action_deadline=iso(c_end),
             action_deadline_reason="Renewal avenant must be signed before the CDD ends.",
             current_compensation_text="€4,200 gross per month", status_note="Employee signature missing."),
        dict(employee_name="Yuki Tanaka (田中由紀)", primary="HR/Contracts/APAC/雇用契約書_田中由紀.pdf", supporting=[],
             effective_end_date=iso(y_end), action_deadline=iso(y_deadline),
             action_deadline_reason="30 days' advance notice required for non-renewal.",
             current_compensation_text="¥850,000 per month", status_note=None),
        dict(employee_name="Emily Johnson", primary="HR/Contracts/US/Johnson_Emily_Employment_Agreement.pdf",
             supporting=[], effective_end_date=None, action_deadline=None, action_deadline_reason=None,
             current_compensation_text="$198,000 base", status_note="No deadline; compliance fix needed."),
        dict(employee_name="Priya Nair", primary="HR/Offer Letters/Priya_Nair_Offer_Letter.docx", supporting=[],
             effective_end_date=None, action_deadline=iso(p_prob_end),
             action_deadline_reason="Probation ends; extension only by written notice before this date.",
             current_compensation_text="INR 18,00,000 CTC per year", status_note=None),
    ]
    issues = [
        dict(severity="medium", employee_name=None, title="Out of scope: Berlin lease option",
             detail=f"Not an employment matter, but the Berlin office lease extension option must be exercised by "
                    f"{en(add_months(lease_end, -6))}."),
        dict(severity="medium", employee_name="Lukas Becker", title="Misfiled contract",
             detail="Lukas Becker's employment contract was only stored as 'Scans/scan_0042.pdf'; file it under "
                    "HR/Contracts/EMEA."),
        dict(severity="low", employee_name=None, title="Process: Legal must review every exit letter",
             detail="The 2026 handbook requires Legal review plus CEO and Head of People approval for every "
                    "termination or non-renewal."),
    ]
    return docs, dict(anchor=A.isoformat(), contracts=contracts, issues=issues)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--anchor", help="demo 'today' (YYYY-MM-DD); default: real today")
    args = ap.parse_args()
    A = date.fromisoformat(args.anchor) if args.anchor else date.today()

    docs, meta = build(A)
    if DRIVE.exists():
        shutil.rmtree(DRIVE)
    DRIVE.mkdir(parents=True)

    truth = {"anchor": meta["anchor"], "documents": {}, "contracts": meta["contracts"],
             "issues": meta["issues"]}
    for i, doc in enumerate(docs):
        target = DRIVE / doc.path
        if doc.fmt == "typed":
            render_typed_pdf(doc, target)
        elif doc.fmt == "scanned":
            render_scanned_pdf(doc, target, seed=1000 + i)
        else:
            render_docx(doc, target)
        if target.exists():
            truth["documents"][doc.path] = doc.truth
            print(f"  ✓ {doc.path}  ({doc.fmt}, {doc.lang})")

    (OUT / "ground_truth.json").write_text(json.dumps(truth, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nDemo Drive ready: {len(truth['documents'])} files, anchor {A}  →  {DRIVE}")


if __name__ == "__main__":
    main()
