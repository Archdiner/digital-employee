"""Google Workspace over plain REST: Drive, Docs, Sheets, Slides. Functions take a bearer token."""
import json

import requests

from . import textract

DRIVE = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"
DOCS = "https://docs.googleapis.com/v1"
SHEETS = "https://sheets.googleapis.com/v4"
SLIDES = "https://slides.googleapis.com/v1"
G = {"doc": "application/vnd.google-apps.document", "sheet": "application/vnd.google-apps.spreadsheet",
     "slides": "application/vnd.google-apps.presentation", "folder": "application/vnd.google-apps.folder"}
OFFICE = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _call(method, url, token, **kw):
    r = requests.request(method, url, headers={**_h(token), **kw.pop("headers", {})}, timeout=120, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"Google API {r.status_code}: {r.text[:500]}")
    return r


def link(file_id):
    return f"https://drive.google.com/open?id={file_id}"


def search(token, query, limit=25):
    """Name or full-text match, across My Drive, shared drives, and files shared with me."""
    q = query.replace("'", "\\'")
    params = {"q": f"(name contains '{q}' or fullText contains '{q}') and trashed = false", "pageSize": limit,
              "fields": "files(id,name,mimeType,modifiedTime,owners(emailAddress),webViewLink,size)",
              "includeItemsFromAllDrives": "true", "supportsAllDrives": "true", "orderBy": "modifiedTime desc"}
    files = _call("GET", f"{DRIVE}/files", token, params=params).json().get("files", [])
    return [{"id": f["id"], "name": f["name"], "type": kind(f["mimeType"]), "modified": f.get("modifiedTime", "")[:10],
             "owner": (f.get("owners") or [{}])[0].get("emailAddress", ""), "link": f.get("webViewLink")} for f in files]


def kind(mime):
    return {v: k for k, v in G.items()}.get(mime) or {v: k for k, v in OFFICE.items()}.get(mime) or mime.split("/")[-1]


def meta(token, file_id):
    return _call("GET", f"{DRIVE}/files/{file_id}", token, params={"fields": "id,name,mimeType,modifiedTime,webViewLink", "supportsAllDrives": "true"}).json()


def read(token, file_id):
    """(name, text). Native Google files are read through their own APIs so cell/slide positions survive."""
    m = meta(token, file_id)
    mime = m["mimeType"]
    if mime == G["doc"]:
        text = _call("GET", f"{DRIVE}/files/{file_id}/export", token, params={"mimeType": "text/plain"}).text
    elif mime == G["sheet"]:
        text = _sheet_text(token, file_id)
    elif mime == G["slides"]:
        text = _slides_text(token, file_id)
    else:
        data = _call("GET", f"{DRIVE}/files/{file_id}", token, params={"alt": "media", "supportsAllDrives": "true"}).content
        text = textract.extract_text(m["name"], data)
    return m["name"], text


def download(token, file_id):
    """Bytes for storage: native files exported to their Office equivalent."""
    m = meta(token, file_id)
    mime = m["mimeType"]
    export = {G["doc"]: ("docx", OFFICE["docx"]), G["sheet"]: ("xlsx", OFFICE["xlsx"]), G["slides"]: ("pptx", OFFICE["pptx"])}.get(mime)
    if export:
        ext, out_mime = export
        data = _call("GET", f"{DRIVE}/files/{file_id}/export", token, params={"mimeType": out_mime}).content
        return f"{m['name']}.{ext}", out_mime, data
    data = _call("GET", f"{DRIVE}/files/{file_id}", token, params={"alt": "media", "supportsAllDrives": "true"}).content
    return m["name"], mime, data


def _sheet_text(token, sheet_id):
    ss = _call("GET", f"{SHEETS}/spreadsheets/{sheet_id}", token, params={"fields": "sheets.properties.title"}).json()
    titles = [s["properties"]["title"] for s in ss.get("sheets", [])]
    out = []
    for t in titles:
        vals = _call("GET", f"{SHEETS}/spreadsheets/{sheet_id}/values/{requests.utils.quote(t, safe='')}", token,
                     params={"valueRenderOption": "FORMATTED_VALUE"}).json().get("values", [])
        out.append(f"\n[sheet: {t}]")
        for i, row in enumerate(vals, 1):
            if any(str(v).strip() for v in row):
                out.append(f"row {i}\t" + "\t".join(str(v) for v in row))
    return "\n".join(out)


def _slides_text(token, pres_id):
    p = _call("GET", f"{SLIDES}/presentations/{pres_id}", token).json()
    out = []
    for i, slide in enumerate(p.get("slides", []), 1):
        out.append(f"\n[slide {i}]")
        for el in slide.get("pageElements", []):
            shape = el.get("shape") or {}
            for te in (shape.get("text") or {}).get("textElements", []):
                run = te.get("textRun")
                if run and run.get("content", "").strip():
                    out.append(run["content"].rstrip("\n"))
            table = el.get("table")
            if table:
                for row in table.get("tableRows", []):
                    cells = []
                    for cell in row.get("tableCells", []):
                        cells.append("".join(te.get("textRun", {}).get("content", "") for te in (cell.get("text") or {}).get("textElements", [])).strip())
                    out.append("\t".join(cells))
    return "\n".join(out)


# ---------- create ----------

def upload(token, name, data, mime, folder_id=None, convert_to=None):
    """Upload bytes. convert_to='doc'|'sheet'|'slides' turns an Office file into a native Google file."""
    metadata = {"name": name, **({"parents": [folder_id]} if folder_id else {}), **({"mimeType": G[convert_to]} if convert_to else {})}
    files = {"metadata": ("metadata", json.dumps(metadata), "application/json"), "file": (name, data, mime)}
    f = _call("POST", f"{UPLOAD}/files", token, params={"uploadType": "multipart", "supportsAllDrives": "true", "fields": "id,name,webViewLink"}, files=files).json()
    return f["id"], f.get("webViewLink") or link(f["id"])


def create_doc(token, title, sections, folder_id=None):
    """Native Google Doc from the review spec (title, sections[heading, paragraphs, table]). Text already substituted."""
    doc = _call("POST", f"{DOCS}/documents", token, json={"title": title}).json()
    doc_id = doc["documentId"]
    reqs, idx = [], 1

    def add(text, style=None):
        nonlocal idx
        text = text + "\n"
        reqs.append({"insertText": {"location": {"index": idx}, "text": text}})
        if style:
            reqs.append({"updateParagraphStyle": {"range": {"startIndex": idx, "endIndex": idx + len(text)},
                                                  "paragraphStyle": {"namedStyleType": style}, "fields": "namedStyleType"}})
        idx += len(text)

    add(title, "TITLE")
    for sec in sections:
        if sec.get("heading"):
            add(sec["heading"], "HEADING_1")
        for p in sec.get("paragraphs") or []:
            add(p, "NORMAL_TEXT")
        table = sec.get("table")
        if table and table.get("columns"):
            add(" | ".join(table["columns"]), "NORMAL_TEXT")
            for row in table.get("rows") or []:
                add(" | ".join(str(c) for c in row), "NORMAL_TEXT")
    _call("POST", f"{DOCS}/documents/{doc_id}:batchUpdate", token, json={"requests": reqs})
    if folder_id:
        _call("PATCH", f"{DRIVE}/files/{doc_id}", token, params={"addParents": folder_id, "supportsAllDrives": "true"})
    return doc_id, f"https://docs.google.com/document/d/{doc_id}/edit"


def create_sheet(token, title, sheets, folder_id=None):
    """sheets = [{name, rows}] with values already substituted and coerced."""
    body = {"properties": {"title": title}, "sheets": [{"properties": {"title": (s.get("name") or f"Sheet{i}")[:100]}} for i, s in enumerate(sheets, 1)]}
    ss = _call("POST", f"{SHEETS}/spreadsheets", token, json=body).json()
    sid = ss["spreadsheetId"]
    data = [{"range": f"'{(s.get('name') or f'Sheet{i}')[:100]}'!A1", "values": s.get("rows") or [[]]} for i, s in enumerate(sheets, 1)]
    _call("POST", f"{SHEETS}/spreadsheets/{sid}/values:batchUpdate", token, json={"valueInputOption": "USER_ENTERED", "data": data})
    if folder_id:
        _call("PATCH", f"{DRIVE}/files/{sid}", token, params={"addParents": folder_id, "supportsAllDrives": "true"})
    return sid, f"https://docs.google.com/spreadsheets/d/{sid}/edit"


def create_slides(token, title, slides, folder_id=None):
    """slides = [{title, bullets, table}] already substituted. Tables become bullet lines (Slides API tables are verbose)."""
    pres = _call("POST", f"{SLIDES}/presentations", token, json={"title": title}).json()
    pid = pres["presentationId"]
    reqs = []
    first = pres["slides"][0]
    for el in first.get("pageElements", []):
        ph = (el.get("shape") or {}).get("placeholder", {})
        if ph.get("type") == "CENTERED_TITLE":
            reqs.append({"insertText": {"objectId": el["objectId"], "text": title}})
    for i, s in enumerate(slides, 1):
        sid, tid, bid = f"s{i}", f"s{i}t", f"s{i}b"
        reqs.append({"createSlide": {"objectId": sid, "slideLayoutReference": {"predefinedLayout": "TITLE_AND_BODY"},
                                     "placeholderIdMappings": [{"layoutPlaceholder": {"type": "TITLE", "index": 0}, "objectId": tid},
                                                               {"layoutPlaceholder": {"type": "BODY", "index": 0}, "objectId": bid}]}})
        reqs.append({"insertText": {"objectId": tid, "text": s.get("title", "")}})
        lines = list(s.get("bullets") or [])
        table = s.get("table")
        if table and table.get("columns"):
            lines.append(" | ".join(table["columns"]))
            lines += [" | ".join(str(c) for c in row) for row in table.get("rows") or []]
        if lines:
            reqs.append({"insertText": {"objectId": bid, "text": "\n".join(lines)}})
    _call("POST", f"{SLIDES}/presentations/{pid}:batchUpdate", token, json={"requests": reqs})
    if folder_id:
        _call("PATCH", f"{DRIVE}/files/{pid}", token, params={"addParents": folder_id, "supportsAllDrives": "true"})
    return pid, f"https://docs.google.com/presentation/d/{pid}/edit"


# ---------- edit existing (collaborative) ----------

def update_sheet_values(token, sheet_id, a1_range, values):
    """Write a block of values into an existing spreadsheet. Other people's cells are untouched."""
    r = _call("PUT", f"{SHEETS}/spreadsheets/{sheet_id}/values/{requests.utils.quote(a1_range, safe='')}", token,
              params={"valueInputOption": "USER_ENTERED"}, json={"values": values}).json()
    return r.get("updatedRange"), r.get("updatedCells")


def append_doc_text(token, doc_id, text):
    """Append paragraphs at the end of an existing Google Doc."""
    d = _call("GET", f"{DOCS}/documents/{doc_id}", token, params={"fields": "body.content.endIndex"}).json()
    end = d["body"]["content"][-1]["endIndex"]
    _call("POST", f"{DOCS}/documents/{doc_id}:batchUpdate", token,
          json={"requests": [{"insertText": {"location": {"index": end - 1}, "text": "\n" + text}}]})
    return f"https://docs.google.com/document/d/{doc_id}/edit"


def add_comment(token, file_id, text):
    r = _call("POST", f"{DRIVE}/files/{file_id}/comments", token, params={"fields": "id"}, json={"content": text}).json()
    return r["id"]


def share(token, file_id, email, role="writer"):
    _call("POST", f"{DRIVE}/files/{file_id}/permissions", token, params={"supportsAllDrives": "true", "sendNotificationEmail": "false"},
          json={"type": "user", "role": role, "emailAddress": email})
    return link(file_id)
