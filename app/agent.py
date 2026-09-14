"""The employee. A model, a handful of tools, a log. No pipeline.

The model decides what to read, what to look up, what to compute, when to ask. We only guarantee that
numbers come from the fact store (render.py) and that everything it did is in the work log (db.log)."""
import json
import re
import traceback
from datetime import date

from psycopg.types.json import Jsonb

from . import db, llm, render

PAGE = 15_000

TOOLS = [
    {"type": "function", "name": "read_firm_notes", "description": "How this firm works: funds, companies, terms, where files and numbers live.", "parameters": {"type": "object", "properties": {}}},
    {"type": "function", "name": "list_skills", "description": "Your skills: name and one line on when to use each. Read the ones that apply before starting.", "parameters": {"type": "object", "properties": {}}},
    {"type": "function", "name": "read_skill", "description": "Read one skill in full.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"type": "function", "name": "list_documents", "description": "Documents you have: sources, past reviews, and previous outputs. Past reviews show the expected layout.", "parameters": {"type": "object", "properties": {}}},
    {"type": "function", "name": "read_document", "description": "Read a document's text, one page of ~15k characters at a time.", "parameters": {"type": "object", "properties": {"document_id": {"type": "integer"}, "page": {"type": "integer", "description": "1-based, default 1"}}, "required": ["document_id"]}},
    {"type": "function", "name": "search_facts", "description": "Search the fact store. Case-insensitive match across entity, metric, period, and source. Returns fact ids you cite as {{fact:ID}}.", "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "words to match, e.g. 'EBITDA Q2 2026' or a company name"}, "limit": {"type": "integer", "description": "default 60"}}, "required": ["query"]}},
    {"type": "function", "name": "compute", "description": "Derive a number from stored facts. Arithmetic only: + - * / ( ) and fact references like f123. Result is stored as a new fact you can cite. Example: '(f12 - f9) / f9 * 100' with unit '%'. Arrange the expression so the result is positive and put the direction in words: for a fall compute (old - new) / old and write 'fell {{fact:ID}}'.", "parameters": {"type": "object", "properties": {"expression": {"type": "string"}, "metric": {"type": "string", "description": "what the result is, e.g. 'Revenue growth QoQ'"}, "entity": {"type": ["string", "null"]}, "period": {"type": ["string", "null"]}, "unit": {"type": ["string", "null"]}, "decimals": {"type": "integer", "description": "rounding, default 1"}}, "required": ["expression", "metric"]}},
    {"type": "function", "name": "ask_user", "description": "Stop and ask the person who gave you the task. Use only when the answer changes a number or a table and the documents cannot tell you. Put every question in one message. Work pauses until they answer.", "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}},
    {"type": "function", "name": "write_review", "description": "Write the finished document. Every number must be a fact reference: {{fact:ID}} prints the stored value with its unit; {{fact:ID:v}} prints the value alone (use it in table cells when the column header already carries the unit). The renderer rejects any other digits except years, quarter labels and list numbering. If rejected, fix and call again.", "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "filename": {"type": "string", "description": "e.g. 'Fund III Q2 2026 Portfolio Review.docx'"}, "sections": {"type": "array", "items": {"type": "object", "properties": {"heading": {"type": "string"}, "paragraphs": {"type": "array", "items": {"type": "string"}}, "table": {"type": ["object", "null"], "properties": {"columns": {"type": "array", "items": {"type": "string"}}, "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}}}}, "required": ["heading"]}}}, "required": ["title", "filename", "sections"]}},
]

INSTRUCTIONS = """You are {name}, a {role} at {firm}. Today is {today}.

You work from documents and a fact store. You have skills (plain text, read them), firm notes, and a work log kept for you.

Non-negotiable:
- Numbers in anything you write come only from the fact store, referenced as {{{{fact:ID}}}}. You never type a figure yourself. Derived figures come from the compute tool.
- If you do not know something that changes a number or a table, use ask_user. Otherwise proceed and record the gap.
- Start by reading list_skills and the skills that apply, then the firm notes, then the documents you need. Past reviews set the layout, length and tone.
- Finish by calling write_review. Then reply with a short plain-language note in three parts: what you are sure of, what you guessed at, what you could not find. That note is read by an operator, not the client.

You are one employee doing one job well. Do not pad. Do not invent."""


def instructions_for(emp):
    return INSTRUCTIONS.format(name=emp["name"], role=emp["role"], firm=emp["firm"], today=date.today().isoformat())


# ---------- tools ----------

def fact_line(f):
    src = f.get("filename") or f.get("formula") or ""
    loc = f.get("source_location") or ""
    dt = f.get("doc_date") or ""
    return f"#{f['id']} | {f.get('entity') or '-'} | {f['metric']} | {f.get('period') or '-'} | {render.fact_text(f)} | {src} {loc} {dt}".strip()


def get_fact(employee_id, fid):
    return db.q(
        """select f.*, d.filename from facts f left join documents d on d.id = f.document_id
           where f.id = %s and f.employee_id = %s""",
        (fid, employee_id), one=True,
    )


def tool_read_firm_notes(run, emp, args):
    db.log(run["id"], "read_firm_notes")
    return emp["firm_notes"] or "(no firm notes yet)"


def tool_list_skills(run, emp, args):
    rows = db.q("select name, split_part(body, E'\\n', 1) as when_to_use from skills where employee_id = %s order by name", (emp["id"],))
    db.log(run["id"], "list_skills", count=len(rows))
    return "\n".join(f"- {r['name']}: {r['when_to_use']}" for r in rows) or "(no skills)"


def tool_read_skill(run, emp, args):
    r = db.q("select body from skills where employee_id = %s and name = %s", (emp["id"], args["name"]), one=True)
    db.log(run["id"], "read_skill", name=args["name"], found=bool(r))
    return r["body"] if r else f"no skill named {args['name']}"


def tool_list_documents(run, emp, args):
    rows = db.q(
        """select d.id, d.filename, d.kind, d.doc_date, length(d.text) as chars,
                  (select count(*) from facts f where f.document_id = d.id) as facts
           from documents d where d.employee_id = %s order by d.kind, d.doc_date nulls last, d.id""",
        (emp["id"],),
    )
    db.log(run["id"], "list_documents", count=len(rows))
    return "\n".join(
        f"#{r['id']} | {r['kind']} | {r['filename']} | date {r['doc_date'] or '?'} | {(r['chars'] or 0)//PAGE + 1} pages | {r['facts']} facts"
        for r in rows
    ) or "(no documents)"


def tool_read_document(run, emp, args):
    r = db.q("select filename, kind, text from documents where id = %s and employee_id = %s", (args["document_id"], emp["id"]), one=True)
    if not r:
        return "no such document"
    page = max(1, int(args.get("page") or 1))
    text = r["text"] or ""
    pages = max(1, (len(text) - 1) // PAGE + 1)
    db.log(run["id"], "read_document", document_id=args["document_id"], filename=r["filename"], page=page, pages=pages)
    body = text[(page - 1) * PAGE : page * PAGE]
    return f"[{r['filename']} | {r['kind']} | page {page} of {pages}]\n{body}"


def tool_search_facts(run, emp, args):
    words = [w for w in re.split(r"\s+", args["query"].strip()) if w]
    limit = min(int(args.get("limit") or 60), 200)
    clauses = " and ".join(["concat_ws(' ', f.entity, f.metric, f.period, f.source_location, d.filename, f.formula) ilike %s"] * len(words)) or "true"
    rows = db.q(
        f"""select f.*, d.filename from facts f left join documents d on d.id = f.document_id
            where f.employee_id = %s and {clauses} order by f.doc_date desc nulls last, f.id limit %s""",
        [emp["id"], *[f"%{w}%" for w in words], limit],
    )
    db.log(run["id"], "search_facts", query=args["query"], hits=len(rows), fact_ids=[r["id"] for r in rows])
    return "\n".join(fact_line(r) for r in rows) or "no facts match"


_NUM = re.compile(r"[-+]?\d*\.?\d+")


def tool_compute(run, emp, args):
    expr = args["expression"]
    ids = sorted({int(x) for x in re.findall(r"f(\d+)", expr)})
    values = {}
    for fid in ids:
        f = get_fact(emp["id"], fid)
        if not f:
            return f"fact {fid} does not exist"
        raw = f["value"].replace(",", "")
        neg = raw.startswith("(") and raw.endswith(")")
        m = _NUM.search(raw)
        if not m:
            return f"fact {fid} value '{f['value']}' is not numeric"
        values[fid] = -float(m.group(0)) if neg else float(m.group(0))
    safe = re.sub(r"f(\d+)", lambda m: repr(values[int(m.group(1))]), expr)
    if not re.fullmatch(r"[\d\.\s+\-*/()e]+", safe):
        return "expression may only contain numbers, fact references like f12, and + - * / ( )"
    try:
        result = eval(safe, {"__builtins__": {}}, {})  # noqa: S307 - input is validated to arithmetic only
    except ZeroDivisionError:
        return "division by zero"
    except Exception as e:  # noqa: BLE001
        return f"could not evaluate: {e}"
    dec = int(args.get("decimals", 1))
    value = f"{result:,.{dec}f}"
    row = db.q(
        """insert into facts (employee_id, document_id, entity, metric, period, value, unit, source_location, formula, derived_from, doc_date)
           values (%s, null, %s, %s, %s, %s, %s, %s, %s, %s, current_date) returning id""",
        (emp["id"], args.get("entity"), args["metric"], args.get("period"), value, args.get("unit"), "computed", expr, ids),
        one=True,
    )
    db.log(run["id"], "compute", expression=expr, inputs=values, result=value, fact_id=row["id"])
    return f"#{row['id']} = {value}{' ' + args['unit'] if args.get('unit') else ''}  (from {expr} with {values})"


def tool_ask_user(run, emp, args):
    db.log(run["id"], "ask_user", question=args["question"])
    return "__ASK__"


def tool_write_review(run, emp, args):
    try:
        data, cited = render.build(args, lambda fid: get_fact(emp["id"], fid))
    except render.ReviewError as e:
        db.log(run["id"], "write_review_rejected", problems=str(e))
        return f"Document rejected. Fix these and call write_review again:\n{e}"
    filename = args.get("filename") or f"{args['title']}.docx"
    if not filename.lower().endswith(".docx"):
        filename += ".docx"
    row = db.q(
        """insert into documents (employee_id, filename, kind, content_type, bytes)
           values (%s, %s, 'output', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', %s) returning id""",
        (emp["id"], filename, data), one=True,
    )
    db.q("update runs set output_document_id = %s where id = %s", (row["id"], run["id"]))
    db.log(run["id"], "write_review", document_id=row["id"], filename=filename, cited_fact_ids=cited, sections=[s.get("heading") for s in args["sections"]])
    return f"Document written: #{row['id']} {filename}. {len(cited)} facts cited. Now reply with your note (sure / guessed / could not find)."


TOOL_FNS = {t["name"]: globals()["tool_" + t["name"]] for t in TOOLS}


# ---------- the loop ----------

def run(run_id: int, max_turns: int = 60):
    r = db.q("select * from runs where id = %s", (run_id,), one=True)
    emp = db.q("select * from employees where id = %s", (r["employee_id"],), one=True)
    state = list(r["state"] or [])
    if not state:
        state = [{"role": "user", "content": r["task"]}]
        db.log(run_id, "start", task=r["task"], model=emp["model"])
    db.q("update runs set status = 'running', question = null where id = %s", (run_id,))

    try:
        for _ in range(max_turns):
            resp = llm.respond(model=emp["model"], instructions=instructions_for(emp), input_items=state, tools=TOOLS)
            items = llm.output_items(resp)
            state.extend(items)
            calls = [i for i in items if i.get("type") == "function_call"]
            if not calls:
                note = resp.output_text.strip()
                done = bool(db.q("select output_document_id from runs where id = %s", (run_id,), one=True)["output_document_id"])
                db.q("update runs set status = %s, notes = %s, state = %s, finished_at = now() where id = %s",
                     ("done" if done else "failed", note, Jsonb(state), run_id))
                db.log(run_id, "finish", done=done)
                return
            asked = None
            for call in calls:
                args = json.loads(call["arguments"] or "{}")
                out = TOOL_FNS[call["name"]](r, emp, args)
                if out == "__ASK__":
                    asked = args["question"]
                    out = "Question sent. Waiting for the answer."
                state.append({"type": "function_call_output", "call_id": call["call_id"], "output": out})
            db.q("update runs set state = %s where id = %s", (Jsonb(state), run_id))
            if asked:
                db.q("update runs set status = 'waiting', question = %s where id = %s", (asked, run_id))
                return
        db.q("update runs set status = 'failed', error = 'turn limit reached', state = %s, finished_at = now() where id = %s", (Jsonb(state), run_id))
    except Exception:  # noqa: BLE001
        err = traceback.format_exc()
        db.q("update runs set status = 'failed', error = %s, state = %s, finished_at = now() where id = %s", (err[-4000:], Jsonb(state), run_id))
        db.log(run_id, "error", error=err[-2000:])


def answer(run_id: int, text: str):
    """The user answered the question. Append and continue."""
    r = db.q("select state from runs where id = %s", (run_id,), one=True)
    state = list(r["state"] or [])
    state.append({"role": "user", "content": text})
    db.q("update runs set state = %s, answer = %s where id = %s", (Jsonb(state), text, run_id))
    db.log(run_id, "answer", text=text)
    run(run_id)


def explain(run_id: int, question: str) -> str:
    """'Where did this number come from?' Answered from the work log and the facts, in plain words."""
    r = db.q("select r.*, e.name, e.role, e.firm, e.model from runs r join employees e on e.id = r.employee_id where r.id = %s", (run_id,), one=True)
    log = db.q("select at, action, detail from worklog where run_id = %s order by id", (run_id,))
    cited = set()
    for row in log:
        cited.update(row["detail"].get("cited_fact_ids") or [])
    facts = [fact_line(get_fact(r["employee_id"], fid)) for fid in sorted(cited)] if cited else []
    text = (
        f"Task: {r['task']}\n\nWork log:\n" + "\n".join(f"{row['at']:%H:%M:%S} {row['action']} {json.dumps(row['detail'], default=str)[:600]}" for row in log)
        + "\n\nFacts cited in the document (id | entity | metric | period | value | source):\n" + "\n".join(facts)
        + f"\n\nQuestion from the reader: {question}"
    )
    resp = llm.respond(
        model=r["model"],
        instructions=f"You are {r['name']}, {r['role']} at {r['firm']}. Answer the reader's question about your own work using only the work log and facts below. Plain words, two to five sentences, name the source file and where in it the number sits. If the log does not show it, say so.",
        input_items=[{"role": "user", "content": text}],
        effort="medium",
    )
    db.log(run_id, "explain", question=question, answer=resp.output_text)
    return resp.output_text
