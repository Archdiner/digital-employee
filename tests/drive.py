"""Drive the deployed operator UI like a person would (Playwright, screenshots saved), to evaluate the employee.
Usage: python tests/drive.py hire|upload|ask|wait|show ...   (see main). BASE and ADMIN_PASSWORD from env."""
import os
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE", "https://emp-app.jollymushroom-dec64f0f.centralus.azurecontainerapps.io").rstrip("/")
PW = os.environ.get("ADMIN_PASSWORD", "")
SHOTS = Path(os.environ.get("SHOTS", "/private/tmp/claude-501/-Users-asadr/403f3d0d-e8b4-46db-9ddf-a6f6723a9cfc/scratchpad/shots"))
SHOTS.mkdir(parents=True, exist_ok=True)
FIX = Path(__file__).parent / "fixtures"


def browser(pw):
    b = pw.chromium.launch()
    ctx = b.new_context(http_credentials={"username": "admin", "password": PW}, viewport={"width": 1280, "height": 900})
    return b, ctx.new_page()


def shot(page, name):
    p = SHOTS / f"{int(time.time())}_{name}.png"
    page.screenshot(path=str(p), full_page=True)
    print("shot:", p)


def hire(jd_path, firm, name=None):
    with sync_playwright() as pw:
        b, page = browser(pw)
        page.goto(f"{BASE}/employees/new")
        page.fill("input[name=firm]", firm)
        page.fill("textarea[name=job_description]", Path(jd_path).read_text())
        shot(page, "hire_1_jd_pasted")
        page.click("text=Draft the setup", no_wait_after=True)
        page.wait_for_selector("text=Approve the setup", timeout=300_000)
        shot(page, "hire_2_draft")
        role = page.input_value("input[name=role]")
        general = [c.get_attribute("value") for c in page.query_selector_all("input[name=skill_ids]:checked")]
        new = page.eval_on_selector_all("input[name=new_skill_name]", "els => els.map(e => e.value)")
        qs = page.eval_on_selector_all(".question li", "els => els.map(e => e.textContent)")
        print("role:", role); print("general ticked:", len(general)); print("new skills:", new); print("questions:", *qs, sep="\n  ")
        if name:
            page.fill("input[name=name]", name)
        page.fill("textarea[name=firm_notes]", FIRM_NOTES)
        page.click("button:has-text('Create employee')", no_wait_after=True)
        page.wait_for_url(re.compile(r"/employees/\d+$"), timeout=60_000)
        shot(page, "hire_3_employee")
        print("employee:", page.url)
        b.close()


FIRM_NOTES = """Entity: Gulf Facilities Services LLC (GFS), a facilities management operating company. Reporting currency AED, packs in AED 000s.
Monthly pack: file GFS_MA_<Mon>_<Year>_FINAL.xlsx, Summary tab. Columns: month actual, month budget, YTD actual, YTD budget, FY budget, latest forecast. Commentary tab explains movements.
Budget: GFS_FY26_Budget_Book.xlsx, Assumptions tab (owners named) and Monthly phasing tab. The approved budget is the December 2025 version; the June 2026 re-forecast is the latest forecast.
EBITDA is as shown in the pack (after overheads, before depreciation and finance costs).
Variance threshold: 5% or AED 200k, whichever is smaller; any sign change.
Reports go to the Vice President - Financial Management. Recommendations must name the assumption owner from the budget book.
Audit: external auditors' request list from last year is uploaded; the same items are expected again."""


def upload(eid, kind, *files):
    with sync_playwright() as pw:
        b, page = browser(pw)
        page.goto(f"{BASE}/employees/{eid}")
        page.set_input_files("input[name=files]", [str(FIX / f) for f in files])
        page.select_option("select[name=kind]", kind)
        page.click("form[enctype] button")
        page.wait_for_url(re.compile(rf"/employees/{eid}"))
        shot(page, f"upload_{kind}")
        b.close()


def ask(eid, text):
    with sync_playwright() as pw:
        b, page = browser(pw)
        page.goto(f"{BASE}/employees/{eid}")
        page.fill("textarea[name=task]", text)
        page.click("form[action$='/runs'] button")
        page.wait_for_url(re.compile(r"/runs/\d+"))
        rid = int(page.url.rsplit("/", 1)[1])
        shot(page, f"run{rid}_sent")
        print("run:", rid)
        b.close()
        return rid


def say(rid, text):
    with sync_playwright() as pw:
        b, page = browser(pw)
        page.goto(f"{BASE}/runs/{rid}")
        page.fill("textarea[name=text]", text)
        page.click("form.composer button")
        page.wait_for_load_state()
        shot(page, f"run{rid}_reply_sent")
        b.close()


def wait(rid, timeout=1500):
    with sync_playwright() as pw:
        b, page = browser(pw)
        t = time.time()
        while time.time() - t < timeout:
            page.goto(f"{BASE}/runs/{rid}")
            status = page.text_content("h1 .pill").strip()
            if status in ("done", "failed", "waiting"):
                break
            time.sleep(15)
        shot(page, f"run{rid}_{status}")
        print("status:", status, f"({time.time()-t:.0f}s)")
        for m in page.query_selector_all(".msg"):
            who = "YOU" if "you" in m.get_attribute("class") else "EMP"
            print(f"[{who}] {m.inner_text().strip()[:1500]}\n")
        b.close()
        return status


def show(rid):
    """Print the work log actions for a run."""
    with sync_playwright() as pw:
        b, page = browser(pw)
        page.goto(f"{BASE}/runs/{rid}")
        rows = page.eval_on_selector_all("details.log table tr", "rows => rows.map(r => Array.from(r.querySelectorAll('td')).map(td => td.innerText.slice(0,160)).join(' | '))")
        print("\n".join(rows))
        b.close()


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    fn = {"hire": hire, "upload": upload, "ask": ask, "say": say, "wait": wait, "show": show}[cmd]
    conv = [(int(a) if a.isdigit() else a) for a in args]
    fn(*conv)
