"""Fill the fact store from a document. One model call per chunk, structured output, rows in Postgres.
The model reads; the database remembers. Nothing here knows what a P&L is."""
from . import db, llm

CHUNK = 40_000

SCHEMA = {
    "name": "facts",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["document_date", "facts"],
        "properties": {
            "document_date": {"type": ["string", "null"], "description": "Date the document is as of, ISO YYYY-MM-DD, or null"},
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["entity", "metric", "period", "value", "unit", "source_location"],
                    "properties": {
                        "entity": {"type": ["string", "null"]},
                        "metric": {"type": "string"},
                        "period": {"type": ["string", "null"]},
                        "value": {"type": "string", "description": "Exactly as written in the source"},
                        "unit": {"type": ["string", "null"]},
                        "source_location": {"type": ["string", "null"]},
                    },
                },
            },
        },
    },
}

INSTRUCTIONS = """You are filling a fact store from one financial document for a private equity firm.
Record every quantitative fact a reviewer might later cite: financial metrics, KPIs, headcount, dates of events, plan and forecast figures, covenant levels.
Rules:
- value is copied exactly as written, including brackets, decimals, and signs. Never convert units or round.
- metric and period use the document's own labels.
- entity is the company, fund, or portfolio the figure belongs to, as the document names it. Null if the document is about one entity and never names it.
- source_location says where a reader can find it: sheet and row, page and table, or section heading.
- Skip page numbers, footnote markers, and formatting artefacts.
Return document_date as the as-of date of the document if it states one."""


def extract_facts(employee_id: int, document_id: int, model: str):
    doc = db.q("select id, filename, text from documents where id = %s", (document_id,), one=True)
    text = doc["text"] or ""
    if not text.strip():
        return 0
    chunks = [text[i : i + CHUNK] for i in range(0, len(text), CHUNK)]
    total = 0
    doc_date = None
    with db.conn() as c:
        c.execute("delete from facts where document_id = %s", (document_id,))
        for n, chunk in enumerate(chunks, 1):
            out = llm.structured(
                model=model,
                instructions=INSTRUCTIONS,
                text=f"Document: {doc['filename']} (part {n} of {len(chunks)})\n\n{chunk}",
                schema=SCHEMA,
            )
            doc_date = doc_date or out.get("document_date")
            for f in out["facts"]:
                c.execute(
                    """insert into facts (employee_id, document_id, entity, metric, period, value, unit, source_location, doc_date)
                       values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (employee_id, document_id, f["entity"], f["metric"], f["period"], f["value"], f["unit"], f["source_location"], doc_date),
                )
                total += 1
        if doc_date:
            c.execute("update documents set doc_date = %s where id = %s", (doc_date, document_id))
    return total
