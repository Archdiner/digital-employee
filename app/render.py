"""Turn the model's review spec into a .docx. Numbers are looked up from the fact store, never copied
from the model. If the text still contains a number that is not a label, the document is refused."""
import io
import re

from docx import Document
from docx.shared import Pt

FACT_REF = re.compile(r"\{\{\s*fact:(\d+)\s*\}\}")
# Numbers that are labels, not facts: years, quarters/halves, FY labels, list numbering, ordinals.
LABEL_OK = re.compile(r"^(19|20)\d\d$|^(q|h)[1-4]$|^fy\d{2,4}$|^\d{1,2}\.$|^\d{1,2}(st|nd|rd|th)$", re.I)
NUMBER = re.compile(r"(?<![a-z])[-(]?\d[\d,]*\.?\d*%?\)?(st|nd|rd|th)?", re.I)


class ReviewError(Exception):
    pass


def fact_text(f):
    v = f["value"]
    u = f.get("unit") or ""
    if not u or u in v:
        return v
    return f"{v}{u}" if u in ("%", "x", "bps") else f"{v} {u}"


def substitute(text, get_fact, cited, problems, where):
    def repl(m):
        fid = int(m.group(1))
        f = get_fact(fid)
        if not f:
            problems.append(f"{where}: fact {fid} does not exist")
            return "[missing fact]"
        cited.add(fid)
        return "⁠" + fact_text(f) + "⁠"  # word-joiners mark stored values so the checker skips them

    out = FACT_REF.sub(repl, text)
    # strip the marked spans before scanning for stray numbers
    scan = re.sub("⁠[^⁠]*⁠", " ", out)
    for m in NUMBER.finditer(scan):
        tok = m.group(0).strip("()")
        prev = scan[max(0, m.start() - 1) : m.start()]
        if prev in ("Q", "q", "H", "h") or LABEL_OK.match(tok) or LABEL_OK.match(prev + tok):
            continue
        problems.append(f"{where}: '{tok}' is a number that is not a fact reference. Use {{{{fact:ID}}}} or the compute tool.")
    return out.replace("⁠", "")


def build(spec, get_fact):
    """spec = {title, sections:[{heading, paragraphs:[str], table:{columns:[str], rows:[[str]]}|None}]}
    Returns (docx_bytes, cited_fact_ids). Raises ReviewError listing every problem at once."""
    problems, cited = [], set()
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    doc.add_heading(substitute(spec.get("title", "Review"), get_fact, cited, problems, "title"), level=0)
    for si, sec in enumerate(spec.get("sections", []), 1):
        where = f"section {si} ({sec.get('heading', '')})"
        if sec.get("heading"):
            doc.add_heading(substitute(sec["heading"], get_fact, cited, problems, where), level=1)
        for para in sec.get("paragraphs") or []:
            doc.add_paragraph(substitute(para, get_fact, cited, problems, where))
        table = sec.get("table")
        if table and table.get("columns"):
            cols = table["columns"]
            rows = table.get("rows") or []
            t = doc.add_table(rows=1 + len(rows), cols=len(cols))
            t.style = "Light Grid Accent 1"
            for j, col in enumerate(cols):
                t.cell(0, j).text = substitute(str(col), get_fact, cited, problems, where + " table header")
            for i, row in enumerate(rows, 1):
                for j in range(len(cols)):
                    cell = str(row[j]) if j < len(row) else ""
                    t.cell(i, j).text = substitute(cell, get_fact, cited, problems, f"{where} table row {i}")
    if problems:
        raise ReviewError("\n".join(problems[:40]))
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue(), sorted(cited)
