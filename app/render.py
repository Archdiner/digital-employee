"""Turn the model's review spec into a .docx. Numbers are looked up from the fact store, never copied
from the model. If the text still contains a number that is not a label, the document is refused."""
import io
import re

from docx import Document
from docx.shared import Pt

FACT_REF = re.compile(r"\{\{\s*fact:(\d+)(?::(v))?\s*\}\}")  # {{fact:12}} = value + unit, {{fact:12:v}} = value only
# Numbers that are labels, not facts: years, quarters/halves, FY labels, list numbering, ordinals.
LABEL_OK = re.compile(r"^0+$|^(19|20)\d\d$|^(q|h)[1-4]$|^fy\d{2,4}$|^\d{1,2}\.$|^\d{1,2}(st|nd|rd|th)$", re.I)
MONTH = re.compile(r"\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", re.I)
NUMBER = re.compile(r"(?<![a-z0-9])[-(]?\d[\d,]*\.?\d*%?\)?(st|nd|rd|th)?", re.I)
# Period labels are removed before scanning: Aug-26, August 2026, FY26, FY 2026, Q2, Q2 2026, H1 26, 2026/27.
LABELS = re.compile(
    r"\b(?:(?:\d{1,2}\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*[-' ]?\d{2,4}"
    r"|fy\s?\d{2,4}(?:/\d{2,4})?|(?:q|h)[1-4](?:\s?\d{2,4})?|[12]h\s?\d{2,4}|(?:19|20)\d\d(?:/\d{2,4})?)\b", re.I)


class ReviewError(Exception):
    pass


PLAIN_NUMBER = re.compile(r"^\(?-?\d+(\.\d+)?\)?$")


def fact_value(f):
    """The stored value, with thousands separators added when the source wrote a bare amount. Precision is never changed.
    Only values with a unit get separators: a value without one may be a code (account 4000, ref 2024-03)."""
    v = f["value"].strip()
    if (f.get("unit") or "").strip() and PLAIN_NUMBER.match(v) and "," not in v:
        neg = v.startswith("(")
        num = v.strip("()")
        whole, _, frac = num.partition(".")
        if abs(int(whole)) >= 1000:
            num = f"{int(whole):,}" + (f".{frac}" if frac else "")
            v = f"({num})" if neg else num
    return v


def fact_text(f, bare=False):
    v = fact_value(f)
    u = (f.get("unit") or "").strip()
    if bare or not u or u in v:
        return v
    return f"{v}{u}" if u in ("%", "x", "bps", "pts", "pp") else f"{v} {u}"


MARK = "\x00"  # marks stored values so the stray-number check skips them


def substitute(text, get_fact, cited, problems, where):
    def repl(m):
        fid = int(m.group(1))
        f = get_fact(fid)
        if not f:
            problems.append(f"{where}: fact {fid} does not exist")
            return "[missing fact]"
        cited.add(fid)
        return MARK + fact_text(f, bare=m.group(2) == "v") + MARK

    out = FACT_REF.sub(repl, text)
    scan = re.sub(MARK + "[^" + MARK + "]*" + MARK, " ", out)
    scan = LABELS.sub(" ", scan)
    for m in NUMBER.finditer(scan):
        raw = m.group(0).strip("()")
        tok = raw.rstrip(".,;:")
        prev = scan[max(0, m.start() - 1) : m.start()]
        after = scan[m.end() : m.end() + 12]
        if prev in ("Q", "q", "H", "h") or LABEL_OK.match(raw) or LABEL_OK.match(tok) or LABEL_OK.match(prev + tok):
            continue
        if tok.isdigit() and 1 <= int(tok) <= 31 and MONTH.match(after):  # "30 June 2026" is a date
            continue
        problems.append(f"{where}: '{tok}' is a number that is not a fact reference. Use {{{{fact:ID}}}} or the compute tool.")
    return out.replace(MARK, "")


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


def fill(text, get_fact):
    """For chat and questions: replace fact references with their values. No rejection, no checking."""
    def repl(m):
        f = get_fact(int(m.group(1)))
        return fact_text(f, bare=m.group(2) == "v") if f else m.group(0)
    return FACT_REF.sub(repl, text or "")
