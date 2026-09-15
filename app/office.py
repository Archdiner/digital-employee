"""Build .docx / .pptx / .xlsx bytes from a spec. Every string passes through the fact substitution, so the same
rule holds everywhere: a number gets in only as {{fact:ID}}."""
import io

from docx import Document
from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches, Pt

from . import render


def _sub(text, get_fact, cited, problems, where):
    return render.substitute(str(text), get_fact, cited, problems, where)


def build_docx(spec, get_fact):
    return render.build(spec, get_fact)


def build_pptx(spec, get_fact):
    """spec = {title, slides:[{title, bullets:[str], table:{columns, rows}|None}]}"""
    problems, cited = [], set()
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = _sub(spec.get("title", ""), get_fact, cited, problems, "title")
    if spec.get("subtitle"):
        s.placeholders[1].text = _sub(spec["subtitle"], get_fact, cited, problems, "subtitle")
    for i, sl in enumerate(spec.get("slides", []), 1):
        where = f"slide {i}"
        table = sl.get("table")
        s = prs.slides.add_slide(prs.slide_layouts[5 if table else 1])
        s.shapes.title.text = _sub(sl.get("title", ""), get_fact, cited, problems, where)
        bullets = sl.get("bullets") or []
        if bullets and not table:
            tf = s.placeholders[1].text_frame
            tf.text = _sub(bullets[0], get_fact, cited, problems, where)
            for b in bullets[1:]:
                p = tf.add_paragraph()
                p.text = _sub(b, get_fact, cited, problems, where)
        if table and table.get("columns"):
            cols, rows = table["columns"], table.get("rows") or []
            shape = s.shapes.add_table(1 + len(rows), len(cols), Inches(0.6), Inches(1.6), Inches(12), Inches(0.4) * (1 + len(rows)))
            t = shape.table
            for j, c in enumerate(cols):
                t.cell(0, j).text = _sub(c, get_fact, cited, problems, where + " header")
            for r, row in enumerate(rows, 1):
                for j in range(len(cols)):
                    t.cell(r, j).text = _sub(row[j] if j < len(row) else "", get_fact, cited, problems, f"{where} row {r}")
            for cell in (c for row in t.rows for c in row.cells):
                for p in cell.text_frame.paragraphs:
                    for run in p.runs:
                        run.font.size = Pt(12)
            if bullets:
                tb = s.shapes.add_textbox(Inches(0.6), Inches(1.6) + Inches(0.45) * (1 + len(rows)), Inches(12), Inches(1.5)).text_frame
                tb.word_wrap = True
                tb.text = _sub(bullets[0], get_fact, cited, problems, where)
                for b in bullets[1:]:
                    tb.add_paragraph().text = _sub(b, get_fact, cited, problems, where)
    if problems:
        raise render.ReviewError("\n".join(problems[:40]))
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue(), sorted(cited)


def build_xlsx(spec, get_fact):
    """spec = {title, sheets:[{name, rows:[[str]]}]}. Cells that are bare numbers after substitution become numeric."""
    problems, cited = [], set()
    wb = Workbook()
    wb.remove(wb.active)
    for si, sh in enumerate(spec.get("sheets", []), 1):
        ws = wb.create_sheet((sh.get("name") or f"Sheet{si}")[:31])
        for r, row in enumerate(sh.get("rows") or [], 1):
            for c, val in enumerate(row, 1):
                text = _sub(val, get_fact, cited, problems, f"sheet {ws.title} row {r}")
                ws.cell(row=r, column=c, value=_coerce(text))
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(60, max(10, max(len(str(c.value or "")) for c in col) + 2))
    if problems:
        raise render.ReviewError("\n".join(problems[:40]))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), sorted(cited)


def _coerce(text):
    t = text.replace(",", "")
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        n = float(t)
        n = -n if neg else n
        return int(n) if n.is_integer() and "." not in t else n
    except ValueError:
        return text


def substitute_grid(rows, get_fact):
    """For writing into an existing spreadsheet: substitute every cell, coerce numbers, or raise."""
    problems, cited = [], set()
    out = [[_coerce(_sub(v, get_fact, cited, problems, f"row {r}")) for v in row] for r, row in enumerate(rows, 1)]
    if problems:
        raise render.ReviewError("\n".join(problems[:40]))
    return out, sorted(cited)


def substitute_text(text, get_fact):
    problems, cited = [], set()
    out = _sub(text, get_fact, cited, problems, "text")
    if problems:
        raise render.ReviewError("\n".join(problems[:40]))
    return out, sorted(cited)
