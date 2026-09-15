"""Operator UI. Create an employee, give it firm notes, skills and documents, ask it for work.
The client (the CFO) never sees this. He sees Teams and a document."""
import os
import secrets
import threading
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import agent, connections, db, extract, gws, llm, m365, textract

HERE = Path(__file__).parent
app = FastAPI(title="Digital Employee")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
security = HTTPBasic(auto_error=False)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
MODELS = [m.strip() for m in os.environ.get("MODELS", llm.DEFAULT_MODEL).split(",") if m.strip()]


def auth(creds: HTTPBasicCredentials | None = Depends(security)):
    if not ADMIN_PASSWORD:
        return
    if not creds or not secrets.compare_digest(creds.password, ADMIN_PASSWORD):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})


@app.on_event("startup")
def startup():
    db.init()


def page(request, name, **ctx):
    return templates.TemplateResponse(request, name, ctx)


def in_thread(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ---------- employees ----------

@app.get("/", response_class=HTMLResponse, dependencies=[Depends(auth)])
def index(request: Request):
    emps = db.q(
        """select e.*, (select count(*) from runs r where r.employee_id = e.id) as runs,
                  (select count(*) from documents d where d.employee_id = e.id) as docs
           from employees e order by e.id"""
    )
    return page(request, "index.html", employees=emps)


@app.get("/employees/new", response_class=HTMLResponse, dependencies=[Depends(auth)])
def employee_new(request: Request):
    general = db.q("select id, name, split_part(body, E'\\n', 1) as when_to_use from skills where employee_id is null order by name")
    return page(request, "employee_new.html", general=general, models=MODELS)


@app.post("/employees", dependencies=[Depends(auth)])
def employee_create(
    name: str = Form(...), role: str = Form(...), firm: str = Form(...), firm_notes: str = Form(""),
    model: str = Form(llm.DEFAULT_MODEL), skill_ids: list[int] = Form([]),
    custom_skill_name: str = Form(""), custom_skill_body: str = Form(""),
):
    with db.conn() as c:
        emp = c.execute(
            "insert into employees (name, role, firm, firm_notes, model) values (%s, %s, %s, %s, %s) returning id",
            (name.strip(), role.strip(), firm.strip(), firm_notes.strip(), model),
        ).fetchone()
        if skill_ids:
            c.execute(
                """insert into skills (employee_id, name, body)
                   select %s, name, body from skills where employee_id is null and id = any(%s)""",
                (emp["id"], skill_ids),
            )
        if custom_skill_name.strip() and custom_skill_body.strip():
            c.execute("insert into skills (employee_id, name, body) values (%s, %s, %s)", (emp["id"], custom_skill_name.strip(), custom_skill_body.strip()))
    return RedirectResponse(f"/employees/{emp['id']}", status_code=303)


@app.get("/employees/{eid}", response_class=HTMLResponse, dependencies=[Depends(auth)])
def employee(request: Request, eid: int):
    emp = db.q("select * from employees where id = %s", (eid,), one=True)
    if not emp:
        raise HTTPException(404)
    skills = db.q("select * from skills where employee_id = %s order by name", (eid,))
    docs = db.q(
        """select d.id, d.filename, d.kind, d.doc_date, d.created_at, length(d.text) as chars,
                  (select count(*) from facts f where f.document_id = d.id) as facts
           from documents d where d.employee_id = %s order by d.id desc""", (eid,))
    runs = db.q("select id, task, status, created_at, finished_at, output_document_id from runs where employee_id = %s order by id desc", (eid,))
    nfacts = db.q("select count(*) as n from facts where employee_id = %s", (eid,), one=True)["n"]
    linked = connections.list_for(eid)
    providers = [{"key": k, "label": v["label"], "configured": connections.configured(k), "linked": linked.get(k)} for k, v in connections.PROVIDERS.items()]
    return page(request, "employee.html", emp=emp, skills=skills, docs=docs, runs=runs, nfacts=nfacts, models=MODELS, providers=providers)


@app.post("/employees/{eid}/settings", dependencies=[Depends(auth)])
def employee_settings(eid: int, firm_notes: str = Form(""), model: str = Form(llm.DEFAULT_MODEL), role: str = Form(...)):
    db.q("update employees set firm_notes = %s, model = %s, role = %s where id = %s", (firm_notes.strip(), model, role.strip(), eid))
    return RedirectResponse(f"/employees/{eid}", status_code=303)


@app.post("/employees/{eid}/skills", dependencies=[Depends(auth)])
def skill_add(eid: int, name: str = Form(...), body: str = Form(...)):
    db.q(
        """insert into skills (employee_id, name, body) values (%s, %s, %s)
           on conflict (employee_id, name) where employee_id is not null do update set body = excluded.body, updated_at = now()""",
        (eid, name.strip(), body.strip()),
    )
    return RedirectResponse(f"/employees/{eid}#skills", status_code=303)


@app.post("/employees/{eid}/skills/{sid}/delete", dependencies=[Depends(auth)])
def skill_delete(eid: int, sid: int):
    db.q("delete from skills where id = %s and employee_id = %s", (sid, eid))
    return RedirectResponse(f"/employees/{eid}#skills", status_code=303)


# ---------- documents + facts ----------

@app.post("/employees/{eid}/documents", dependencies=[Depends(auth)])
async def document_upload(eid: int, files: list[UploadFile], kind: str = Form("source")):
    emp = db.q("select model from employees where id = %s", (eid,), one=True)
    for f in files:
        data = await f.read()
        if not data:
            continue
        text = textract.extract_text(f.filename, data)
        row = db.q(
            "insert into documents (employee_id, filename, kind, content_type, bytes, text) values (%s, %s, %s, %s, %s, %s) returning id",
            (eid, f.filename, kind, f.content_type, data, text), one=True,
        )
        in_thread(extract.extract_facts, eid, row["id"], emp["model"])
    return RedirectResponse(f"/employees/{eid}#documents", status_code=303)


@app.post("/employees/{eid}/documents/{did}/reextract", dependencies=[Depends(auth)])
def document_reextract(eid: int, did: int):
    emp = db.q("select model from employees where id = %s", (eid,), one=True)
    in_thread(extract.extract_facts, eid, did, emp["model"])
    return RedirectResponse(f"/employees/{eid}#documents", status_code=303)


@app.post("/employees/{eid}/documents/{did}/delete", dependencies=[Depends(auth)])
def document_delete(eid: int, did: int):
    db.q("delete from documents where id = %s and employee_id = %s", (did, eid))
    return RedirectResponse(f"/employees/{eid}#documents", status_code=303)


@app.get("/documents/{did}", dependencies=[Depends(auth)])
def document_download(did: int):
    d = db.q("select filename, content_type, bytes from documents where id = %s", (did,), one=True)
    if not d:
        raise HTTPException(404)
    return Response(d["bytes"], media_type=d["content_type"] or "application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{d["filename"]}"'})


@app.get("/employees/{eid}/facts", response_class=HTMLResponse, dependencies=[Depends(auth)])
def facts(request: Request, eid: int, q: str = ""):
    emp = db.q("select * from employees where id = %s", (eid,), one=True)
    words = [w for w in q.split() if w]
    clauses = " and ".join(["concat_ws(' ', f.entity, f.metric, f.period, f.source_location, d.filename, f.formula) ilike %s"] * len(words)) or "true"
    rows = db.q(
        f"""select f.*, d.filename from facts f left join documents d on d.id = f.document_id
            where f.employee_id = %s and {clauses} order by f.id desc limit 500""",
        [eid, *[f"%{w}%" for w in words]],
    )
    return page(request, "facts.html", emp=emp, rows=rows, q=q)


# ---------- runs ----------

@app.post("/employees/{eid}/runs", dependencies=[Depends(auth)])
def run_create(eid: int, task: str = Form(...)):
    row = db.q("insert into runs (employee_id, task) values (%s, %s) returning id", (eid, task.strip()), one=True)
    in_thread(agent.run, row["id"])
    return RedirectResponse(f"/runs/{row['id']}", status_code=303)


@app.get("/runs/{rid}", response_class=HTMLResponse, dependencies=[Depends(auth)])
def run_view(request: Request, rid: int, explained: str = ""):
    r = db.q("select r.*, e.name as emp_name, e.firm from runs r join employees e on e.id = r.employee_id where r.id = %s", (rid,), one=True)
    if not r:
        raise HTTPException(404)
    out = db.q("select id, filename from documents where id = %s", (r["output_document_id"],), one=True) if r["output_document_id"] else None
    log = db.q("select at, action, detail from worklog where run_id = %s order by id", (rid,))
    return page(request, "run.html", r=r, out=out, log=log, explained=explained)


@app.post("/runs/{rid}/answer", dependencies=[Depends(auth)])
def run_answer(rid: int, answer: str = Form(...)):
    in_thread(agent.answer, rid, answer.strip())
    return RedirectResponse(f"/runs/{rid}", status_code=303)


@app.post("/runs/{rid}/retry", dependencies=[Depends(auth)])
def run_retry(rid: int):
    db.q("update runs set status = 'running', error = null, finished_at = null where id = %s", (rid,))
    in_thread(agent.run, rid)
    return RedirectResponse(f"/runs/{rid}", status_code=303)


@app.post("/runs/{rid}/explain", response_class=HTMLResponse, dependencies=[Depends(auth)])
def run_explain(request: Request, rid: int, question: str = Form(...)):
    text = agent.explain(rid, question.strip())
    return run_view(request, rid, explained=text)


# ---------- linked accounts ----------

@app.get("/connect/{provider}", dependencies=[Depends(auth)])
def connect_start(provider: str, employee_id: int):
    if provider not in connections.PROVIDERS or not connections.configured(provider):
        raise HTTPException(400, f"{provider} is not configured (client id/secret missing)")
    return RedirectResponse(connections.auth_url(employee_id, provider), status_code=303)


@app.get("/connect/{provider}/callback")
def connect_callback(provider: str, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if error:
        return HTMLResponse(f"<h3>{provider} sign-in failed</h3><pre>{error}: {error_description}</pre>", status_code=400)
    eid, prov = connections.parse_state(state)
    who = connections.save(eid, prov, connections.exchange(prov, code))
    return RedirectResponse(f"/employees/{eid}#connections", status_code=303)


@app.post("/employees/{eid}/connections/{provider}/delete", dependencies=[Depends(auth)])
def connect_delete(eid: int, provider: str):
    connections.delete(eid, provider)
    return RedirectResponse(f"/employees/{eid}#connections", status_code=303)


@app.get("/employees/{eid}/workspace", response_class=HTMLResponse, dependencies=[Depends(auth)])
def workspace(request: Request, eid: int, provider: str = "google", q: str = ""):
    emp = db.q("select * from employees where id = %s", (eid,), one=True)
    hits, err = [], None
    if q:
        try:
            tok = connections.token(eid, provider)
            hits = gws.search(tok, q) if provider == "google" else m365.search(tok, q)
        except Exception as e:  # noqa: BLE001
            err = str(e)[:500]
    return page(request, "workspace.html", emp=emp, provider=provider, q=q, hits=hits, err=err, linked=connections.list_for(eid))


@app.post("/employees/{eid}/workspace/import", dependencies=[Depends(auth)])
def workspace_import(eid: int, provider: str = Form(...), file_id: str = Form(...), drive_id: str = Form(""), kind: str = Form("source")):
    emp = db.q("select model from employees where id = %s", (eid,), one=True)
    tok = connections.token(eid, provider)
    if provider == "google":
        name, mime, data = gws.download(tok, file_id)
        _, text = gws.read(tok, file_id)
    else:
        name, mime, data = m365.download(tok, drive_id, file_id)
        text = textract.extract_text(name, data)
    row = db.q("insert into documents (employee_id, filename, kind, content_type, bytes, text) values (%s, %s, %s, %s, %s, %s) returning id",
               (eid, name, kind, mime, data, text), one=True)
    in_thread(extract.extract_facts, eid, row["id"], emp["model"])
    return RedirectResponse(f"/employees/{eid}#documents", status_code=303)
