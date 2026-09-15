"""Microsoft 365 over Microsoft Graph: OneDrive/SharePoint files, Excel workbook API for in-place edits."""
import io

import requests
from docx import Document

from . import textract

GRAPH = "https://graph.microsoft.com/v1.0"
OFFICE = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}


def _call(method, url, token, **kw):
    r = requests.request(method, url, headers={"Authorization": f"Bearer {token}", **kw.pop("headers", {})}, timeout=120, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"Graph {r.status_code}: {r.text[:500]}")
    return r


def _item(f):
    return {"id": f["id"], "drive_id": f.get("parentReference", {}).get("driveId") or (f.get("remoteItem") or {}).get("parentReference", {}).get("driveId"),
            "name": f["name"], "type": "folder" if "folder" in f else f["name"].rsplit(".", 1)[-1].lower(),
            "modified": f.get("lastModifiedDateTime", "")[:10], "owner": ((f.get("createdBy") or {}).get("user") or {}).get("email", ""),
            "link": f.get("webUrl")}


def search(token, query, limit=25):
    """My OneDrive plus files shared with me. SharePoint sites the user can reach show up through shared items."""
    mine = _call("GET", f"{GRAPH}/me/drive/root/search(q='{query}')", token, params={"$top": limit}).json().get("value", [])
    shared = _call("GET", f"{GRAPH}/me/drive/sharedWithMe", token).json().get("value", [])
    q = query.lower()
    shared = [{**s.get("remoteItem", {}), "name": s["name"], "webUrl": s.get("webUrl"), "lastModifiedDateTime": s.get("lastModifiedDateTime", "")}
              for s in shared if q in s["name"].lower()]
    return [_item(f) for f in (mine + shared)[:limit]]


def meta(token, drive_id, item_id):
    return _call("GET", f"{GRAPH}/drives/{drive_id}/items/{item_id}", token).json()


def download(token, drive_id, item_id):
    m = meta(token, drive_id, item_id)
    data = _call("GET", f"{GRAPH}/drives/{drive_id}/items/{item_id}/content", token).content
    return m["name"], (m.get("file") or {}).get("mimeType", "application/octet-stream"), data


def read(token, drive_id, item_id):
    name, _, data = download(token, drive_id, item_id)
    return name, textract.extract_text(name, data)


def upload(token, name, data, folder_path="Digital Employee", drive_id=None, parent_id=None):
    """Upload (create or replace). Small files in one PUT, larger through an upload session."""
    if drive_id and parent_id:
        base = f"{GRAPH}/drives/{drive_id}/items/{parent_id}:/{name}:"
    else:
        base = f"{GRAPH}/me/drive/root:/{folder_path.strip('/')}/{name}:"
    if len(data) < 4_000_000:
        f = _call("PUT", f"{base}/content", token, data=data, headers={"Content-Type": "application/octet-stream"}).json()
    else:
        sess = _call("POST", f"{base}/createUploadSession", token, json={"item": {"@microsoft.graph.conflictBehavior": "replace"}}).json()
        url, chunk, pos = sess["uploadUrl"], 5 * 1024 * 1024, 0
        while pos < len(data):
            part = data[pos : pos + chunk]
            r = requests.put(url, data=part, headers={"Content-Length": str(len(part)), "Content-Range": f"bytes {pos}-{pos + len(part) - 1}/{len(data)}"}, timeout=300)
            r.raise_for_status()
            pos += len(part)
        f = r.json()
    return f["id"], f.get("parentReference", {}).get("driveId"), f.get("webUrl")


# ---------- Excel, in place ----------

def excel_sheets(token, drive_id, item_id):
    ws = _call("GET", f"{GRAPH}/drives/{drive_id}/items/{item_id}/workbook/worksheets", token).json().get("value", [])
    out = []
    for w in ws:
        rng = _call("GET", f"{GRAPH}/drives/{drive_id}/items/{item_id}/workbook/worksheets/{w['id']}/usedRange(valuesOnly=true)", token,
                    params={"$select": "address,text"}).json()
        out.append(f"\n[sheet: {w['name']}] {rng.get('address', '')}")
        for i, row in enumerate(rng.get("text") or [], 1):
            if any(str(v).strip() for v in row):
                out.append(f"row {i}\t" + "\t".join(str(v) for v in row))
    return "\n".join(out)


def excel_update(token, drive_id, item_id, sheet, address, values):
    """Write values into a range of an existing workbook. Co-authoring safe: Graph merges into the live file."""
    url = f"{GRAPH}/drives/{drive_id}/items/{item_id}/workbook/worksheets/{requests.utils.quote(sheet, safe='')}/range(address='{address}')"
    r = _call("PATCH", url, token, json={"values": values}).json()
    return r.get("address"), r.get("cellCount")


# ---------- Word, download-edit-upload ----------

def docx_append(token, drive_id, item_id, paragraphs):
    """Append paragraphs to an existing .docx. Graph keeps the previous version; if someone has it open, Word merges on their next save."""
    name, _, data = download(token, drive_id, item_id)
    doc = Document(io.BytesIO(data))
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    f = _call("PUT", f"{GRAPH}/drives/{drive_id}/items/{item_id}/content", token, data=buf.getvalue(),
              headers={"Content-Type": OFFICE["docx"]}).json()
    return f.get("webUrl")


def share_link(token, drive_id, item_id, scope="organization"):
    r = _call("POST", f"{GRAPH}/drives/{drive_id}/items/{item_id}/createLink", token, json={"type": "edit", "scope": scope}).json()
    return r["link"]["webUrl"]
