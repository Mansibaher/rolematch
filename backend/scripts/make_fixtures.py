"""Generate the binary resume fixtures (DOCX / PDF / scanned-style PDF) from the synthetic text resumes.

Run: python scripts/make_fixtures.py
All people, employers and schools in the fixtures are fictional.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RES = REPO / "fixtures" / "resumes"


def make_docx(src: Path, dst: Path) -> None:
    import docx
    from docx.shared import Pt

    d = docx.Document()
    style = d.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)
    for line in src.read_text().splitlines():
        if line.startswith("• "):
            d.add_paragraph(line[2:], style="List Bullet")
        elif line.isupper() and line.strip():
            p = d.add_paragraph()
            run = p.add_run(line)
            run.bold = True
        else:
            d.add_paragraph(line)
    d.save(dst)


def make_pdf(src: Path, dst: Path) -> None:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    font = "Helvetica"
    for candidate in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",):
        if Path(candidate).exists():
            pdfmetrics.registerFont(TTFont("DejaVu", candidate))
            font = "DejaVu"
    c = canvas.Canvas(str(dst), pagesize=LETTER)
    width, height = LETTER
    y = height - 54
    for line in src.read_text().splitlines():
        if y < 54:
            c.showPage()
            y = height - 54
        is_heading = line.isupper() and line.strip()
        c.setFont(font, 11 if is_heading else 9.5)
        c.drawString(54, y, line)
        y -= 15 if is_heading else 12.5
    c.save()


def make_scanned_pdf(dst: Path) -> None:
    """A PDF with only vector drawings and no text layer, imitating a scanned resume."""
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(dst), pagesize=LETTER)
    for i in range(40):
        c.rect(54, 700 - i * 15, 300 + (i * 37) % 180, 6, fill=1, stroke=0)
    c.save()


def main() -> None:
    src = RES / "demo_jordan_lee.txt"
    make_docx(src, RES / "demo_jordan_lee.docx")
    make_pdf(src, RES / "demo_jordan_lee.pdf")
    make_scanned_pdf(RES / "scanned_no_text.pdf")
    (RES / "not_really_a.pdf").write_bytes(b"MZ\x90\x00 this is not a pdf")
    print("fixtures written to", RES)


if __name__ == "__main__":
    main()
